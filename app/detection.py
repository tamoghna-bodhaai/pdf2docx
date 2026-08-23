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


def write(pages: list[DetectedPage], path: Path, mode: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"mode": mode, "pages": [asdict(page) for page in pages]}, default=list),
        encoding="utf-8",
    )
    return path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
