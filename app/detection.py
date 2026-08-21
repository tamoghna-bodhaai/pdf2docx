"""What the converter saw, per page: block boxes, kinds, reading order.

Every mode already builds a detailed picture of each page on its way to the
.docx and then drops it — `PageLayout` for the replica modes, `reflow`'s ordered
`PageBlocks` for the structured one, marker's own JSON for the marker one. This
module is the one shape all three can be written in, so the browser can draw
them over a render of the page without knowing which path produced them.

Boxes are kept in the page's own coordinates, with `width`/`height` alongside
them, so the viewer can scale the page to any size and let the SVG viewBox do
the arithmetic.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .pdf_extract import Box, PageLayout
from .reflow import ImageBlock, MathBlock, PageBlocks, TableBlock, TextBlock

# How much of a block's text is kept for the tooltip and the JSON pane. Enough
# to recognise the block by; not so much that a document's whole text is stored
# twice over.
PREVIEW_CHARS = 240

# The kinds a box can be drawn as. The viewer colours and toggles by these, so
# every builder below maps whatever vocabulary it reads into this set.
KINDS = ("heading", "paragraph", "text", "equation", "figure", "table", "rule")


@dataclass(frozen=True)
class DetectedBlock:
    index: int  # reading order where the mode has one, draw order where it does not
    kind: str
    bbox: Box
    text: str = ""
    level: int = 0  # heading level, when kind is "heading"
    label: str = ""  # the "(1)" a displayed equation is numbered with


@dataclass
class DetectedPage:
    number: int
    width: float
    height: float
    scanned: bool = False
    # False when the blocks are in the order they happen to be drawn rather than
    # the order they are read: the replica modes put every item back at its own
    # coordinate and never decide what follows what.
    ordered: bool = True
    markdown: str = ""
    blocks: list[DetectedBlock] = field(default_factory=list)


def _clip(text: str) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= PREVIEW_CHARS else collapsed[: PREVIEW_CHARS - 1] + "…"


def _box(bbox) -> Box:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


# --------------------------------------------------------------------------- #
# structured — reflow's blocks, already in reading order
# --------------------------------------------------------------------------- #


def from_blocks(pages: list[PageBlocks], layouts: list[PageLayout]) -> list[DetectedPage]:
    """Serialise the blocks `reflow.build` produced for the structured writer.

    Nothing is recomputed: the same list the .docx was written from is the list
    described here, so what the viewer draws is what the document was built out
    of rather than a second opinion about the page.
    """
    by_number = {layout.number: layout for layout in layouts}
    detected: list[DetectedPage] = []

    for page in pages:
        layout = by_number.get(page.number)
        if layout is None:
            continue
        blocks: list[DetectedBlock] = []
        for index, block in enumerate(page.blocks):
            blocks.append(_from_block(index, block, layout))
        detected.append(
            DetectedPage(
                number=page.number,
                width=layout.width,
                height=layout.height,
                scanned=layout.scanned,
                ordered=True,
                markdown=layout.content,
                blocks=blocks,
            )
        )
    return detected


def _from_block(index: int, block, layout: PageLayout) -> DetectedBlock:
    if isinstance(block, TextBlock):
        return DetectedBlock(
            index=index,
            kind="heading" if block.kind == "heading" else "paragraph",
            bbox=_box(block.bbox),
            text=_clip(" ".join(line.text for line in block.lines)),
            level=block.level if block.kind == "heading" else 0,
        )
    if isinstance(block, MathBlock):
        item = layout.maths[block.math_id] if 0 <= block.math_id < len(layout.maths) else None
        return DetectedBlock(
            index=index,
            kind="equation",
            bbox=_box(block.bbox),
            text=_clip(item.latex or "") if item else "",
            label=block.label,
        )
    if isinstance(block, ImageBlock):
        return DetectedBlock(index=index, kind="figure", bbox=_box(block.bbox))
    if isinstance(block, TableBlock):
        rows = block.item.rows
        return DetectedBlock(
            index=index,
            kind="table",
            bbox=_box(block.bbox),
            text=_clip(" | ".join(rows[0])) if rows else "",
        )
    return DetectedBlock(index=index, kind="text", bbox=_box(block.bbox))


# --------------------------------------------------------------------------- #
# replica — the extracted items themselves, which is what that mode writes
# --------------------------------------------------------------------------- #


def from_layouts(layouts: list[PageLayout]) -> list[DetectedPage]:
    """Describe the items `ReplicaWriter` positions, as it positions them.

    There is deliberately no reading order here. A replica puts every line,
    picture, rule and equation back at the coordinate it was found at and never
    decides what follows what, so numbering the boxes as a sequence would claim
    a decision the mode never made. They come back sorted top-to-bottom purely
    so the list reads in the direction the page does.
    """
    detected: list[DetectedPage] = []

    for layout in layouts:
        items: list[tuple[str, Box, str]] = []
        for line in layout.lines:
            if line.text.strip():
                items.append(("text", _box(line.bbox), _clip(line.text)))
        for image in layout.images:
            items.append(("figure", _box(image.bbox), ""))
        for math_id, item in enumerate(layout.maths):
            if item.dropped:
                continue
            items.append(("equation", _box(item.bbox), _clip(item.latex or "")))
        for table in layout.tables:
            first = table.rows[0] if table.rows else []
            items.append(("table", _box(table.bbox), _clip(" | ".join(first))))
        for rule in layout.rules:
            items.append(("rule", _box(rule.bbox), ""))

        items.sort(key=lambda entry: (entry[1][1], entry[1][0]))
        detected.append(
            DetectedPage(
                number=layout.number,
                width=layout.width,
                height=layout.height,
                scanned=layout.scanned,
                ordered=False,
                markdown=layout.content,
                blocks=[
                    DetectedBlock(index=index, kind=kind, bbox=bbox, text=text)
                    for index, (kind, bbox, text) in enumerate(items)
                ],
            )
        )
    return detected


# --------------------------------------------------------------------------- #
# marker — read out of its JSON renderer, never written back to
# --------------------------------------------------------------------------- #

# marker's block vocabulary, mapped onto the kinds the viewer draws. Anything
# not named here is drawn as plain text rather than dropped, so a block type
# marker adds later still appears on the page.
MARKER_KINDS = {
    "sectionheader": "heading",
    "title": "heading",
    "text": "paragraph",
    "textinlinemath": "paragraph",
    "listitem": "paragraph",
    "listgroup": "paragraph",
    "caption": "paragraph",
    "footnote": "paragraph",
    "pagefooter": "text",
    "pageheader": "text",
    "equation": "equation",
    "picture": "figure",
    "figure": "figure",
    "diagram": "figure",
    "picturegroup": "figure",
    "figuregroup": "figure",
    "table": "table",
    "tablegroup": "table",
    "form": "table",
    "line": "rule",
    "handwriting": "text",
    "code": "paragraph",
}

_TAGS = re.compile(r"<[^>]+>")


def _plain_html(html: str) -> str:
    """The words a marker block carries, without its mark-up."""
    return _clip(_TAGS.sub(" ", html or ""))


def _marker_kind(block_type: str) -> str:
    # marker spells its types as "BlockTypes.SectionHeader" in some renderers
    # and bare "SectionHeader" in others.
    name = str(block_type or "").rsplit(".", 1)[-1].strip().lower()
    return MARKER_KINDS.get(name, "text")


def _marker_leaves(children, frame: Box, out: list[DetectedBlock]) -> None:
    """Flatten marker's block tree, keeping the outermost block that has a box.

    A group block (a list, a figure with its caption) is the unit worth drawing;
    descending into it would cover the page in nested rectangles that all say
    the same thing.
    """
    x0, y0 = frame[0], frame[1]
    for child in children or []:
        if not isinstance(child, dict):
            continue
        bbox = child.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            _marker_leaves(child.get("children"), frame, out)
            continue
        left, top, right, bottom = _box(bbox)
        out.append(
            DetectedBlock(
                index=len(out),
                kind=_marker_kind(child.get("block_type")),
                bbox=(left - x0, top - y0, right - x0, bottom - y0),
                text=_plain_html(child.get("html")),
            )
        )


def from_marker_json(raw: str) -> list[DetectedPage]:
    """Read page boxes out of marker's JSON output.

    Each page is described in whatever coordinates marker chose to report, so
    the page block's own bbox is taken as the frame and its children are
    translated into it. The viewer scales that frame to the rendered page, which
    means the overlay lines up without this module having to know marker's
    units.

    Best effort by design: this reads a copy of marker's output and never writes
    to it, and anything unreadable comes back as no pages rather than an error.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []

    pages: list[DetectedPage] = []
    for number, page in enumerate(payload.get("children") or [], start=1):
        if not isinstance(page, dict):
            continue
        bbox = page.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            continue
        frame = _box(bbox)
        width, height = frame[2] - frame[0], frame[3] - frame[1]
        if width <= 0 or height <= 0:
            continue
        blocks: list[DetectedBlock] = []
        _marker_leaves(page.get("children"), frame, blocks)
        pages.append(
            DetectedPage(number=number, width=width, height=height, ordered=True, blocks=blocks)
        )
    return pages


# --------------------------------------------------------------------------- #
# mathpix — read out of its lines.json, never written back to
# --------------------------------------------------------------------------- #

# Mathpix's line vocabulary, mapped onto the kinds the viewer draws. Anything not
# named here is drawn as plain text rather than dropped, so a type Mathpix adds
# later still appears on the page.
MATHPIX_KINDS = {
    "title": "heading",
    "section_header": "heading",
    "sectionheader": "heading",
    "subsection_header": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "list_item": "paragraph",
    "caption": "paragraph",
    "abstract": "paragraph",
    "author": "paragraph",
    "footnote": "text",
    "page_number": "text",
    "header": "text",
    "footer": "text",
    "math": "equation",
    "equation": "equation",
    "equation_number": "equation",
    "table": "table",
    "tabular": "table",
    "diagram": "figure",
    "figure": "figure",
    "image": "figure",
    "chemistry": "figure",
    "chart": "figure",
    "rule": "rule",
    "horizontal_line": "rule",
}


def _mathpix_kind(value) -> str:
    name = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return MATHPIX_KINDS.get(name, "text")


def _mathpix_box(line: dict) -> Box | None:
    """One line's box, from whichever of Mathpix's two geometries it carries.

    `cnt` is a contour — a list of (x, y) vertices, axis-aligned in [TL, TR, BR,
    BL] order for printed text but genuinely polygonal for handwriting — so it is
    reduced to its extent rather than assumed to be a rectangle. `region` is the
    plain box, and is used when there is no contour.
    """
    contour = line.get("cnt")
    if isinstance(contour, (list, tuple)) and contour:
        xs: list[float] = []
        ys: list[float] = []
        for point in contour:
            if not (isinstance(point, (list, tuple)) and len(point) >= 2):
                continue
            try:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
            except (TypeError, ValueError):
                continue
        if xs and ys:
            return (min(xs), min(ys), max(xs), max(ys))

    region = line.get("region")
    if isinstance(region, dict):
        try:
            left = float(region.get("top_left_x", 0.0))
            top = float(region.get("top_left_y", 0.0))
            width = float(region.get("width", 0.0))
            height = float(region.get("height", 0.0))
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return (left, top, left + width, top + height)
    return None


def from_mathpix_lines(raw: str | bytes, sizes: list[tuple[float, float]]) -> list[DetectedPage]:
    """Read page boxes out of Mathpix's `lines.json`.

    Mathpix reports line geometry in the pixels of the image it OCR'd, and states
    the page's own size nowhere in this file. The PDF's page sizes are therefore
    passed in and used as the frame: the viewer scales the frame to the rendered
    page, so as long as the aspect ratios agree the overlay lines up. Where a page
    carries an explicit `image_width`/`image_height`, that wins — it is Mathpix's
    own statement of the coordinates it used.

    Best effort by design: this reads a copy of Mathpix's output and never writes
    to it, and anything unreadable comes back as no pages rather than an error.
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        return []

    pages: list[DetectedPage] = []
    for index, page in enumerate(raw_pages):
        if not isinstance(page, dict):
            continue
        number = page.get("page")
        number = number if isinstance(number, int) and number > 0 else index + 1
        default = sizes[number - 1] if 0 < number <= len(sizes) else (612.0, 792.0)
        try:
            width = float(page.get("image_width") or 0.0) or default[0]
            height = float(page.get("image_height") or 0.0) or default[1]
        except (TypeError, ValueError):
            width, height = default

        blocks: list[DetectedBlock] = []
        for line in page.get("lines") or []:
            if not isinstance(line, dict):
                continue
            bbox = _mathpix_box(line)
            if bbox is None:
                continue
            blocks.append(
                DetectedBlock(
                    index=len(blocks),
                    kind=_mathpix_kind(line.get("type")),
                    bbox=bbox,
                    text=_clip(str(line.get("text") or line.get("text_display") or "")),
                )
            )
        pages.append(
            DetectedPage(number=number, width=width, height=height, ordered=True, blocks=blocks)
        )
    pages.sort(key=lambda page: page.number)
    return pages


# --------------------------------------------------------------------------- #
# Pages without geometry, and storage
# --------------------------------------------------------------------------- #


def from_markdown(sizes: list[tuple[float, float]], markdown: list[str]) -> list[DetectedPage]:
    """Pages a mode can describe the content of but not the geometry of.

    `flow` retypes each page from its image and reports no boxes at all. The
    page still belongs in the viewer — its render and its Markdown are worth
    seeing side by side — so it is written with an empty block list.
    """
    pages: list[DetectedPage] = []
    for index, text in enumerate(markdown):
        width, height = sizes[index] if index < len(sizes) else (612.0, 792.0)
        pages.append(
            DetectedPage(number=index + 1, width=width, height=height, markdown=text or "")
        )
    return pages


def attach_markdown(pages: list[DetectedPage], markdown: list[str]) -> list[DetectedPage]:
    """Give each page its own Markdown, where the two were produced apart."""
    for page in pages:
        index = page.number - 1
        if 0 <= index < len(markdown):
            page.markdown = markdown[index] or ""
    return pages


def write(pages: list[DetectedPage], path: Path, mode: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": mode, "pages": [asdict(page) for page in pages]}, default=list),
        encoding="utf-8",
    )
    return path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
