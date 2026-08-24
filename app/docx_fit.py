"""Fit Mathpix's DOCX to the measure it is actually laid out in.

Mathpix's extraction is not the problem this module solves, and it deliberately
does not touch it: no text, no maths, no table content, no reading order, and
not one of its image crops is altered. What is altered is the *geometry* Mathpix
writes around that content, because Mathpix states it in absolute units that
stop being true the moment the document is read at any measure other than the
one it assumed.

Three facts about a Mathpix .docx, all of them measurable in the file:

  * **Every image is sized at its crop's pixel count divided by 96.** Mathpix
    discards how large the figure was on the source page and re-derives a size
    from the crop resolution, which is not 96 DPI — it is whatever Mathpix
    rendered the page at. A figure occupying 1.8 inches of a two-column paper is
    cropped at ~420px and lands in Word at 4.4 inches. The crop is right; only
    the number attached to it is wrong.
  * **Every table is pinned to an absolute grid** summing to the content width,
    with no ``w:tblW``, no ``w:tcW`` and no ``w:tblLayout``. Word therefore
    ignores the grid, falls back to autofit, and recomputes the columns from
    cell content — which is what it looks like when a table "breaks".
  * **No display equation carries a break opportunity.** ``m:brkBin`` is set and
    ``m:wrapIndent`` is a full inch, but there is not one ``m:brk`` in the
    document, so Word has nowhere to wrap a long equation and runs it off the
    measure.

Each of those is the same mistake — an absolute number where a relative one
belongs — and each stops being survivable the instant the document is narrowed,
which is why putting a Mathpix .docx into two columns breaks everything at once
rather than breaking something new.

The repair is correspondingly narrow. Images are restored to the size they
occupied on the source page, which is *recoverable rather than guessable*:
``lines.json`` reports the pixel dimensions of each rendered page, the PDF
reports the same page in points, and the ratio of the two is the resolution
Mathpix cropped at. Tables are restated in percentages of whatever measure they
find themselves in. Long equations are given the break points Word is already
configured to use. Nothing here invents a layout; it re-expresses Mathpix's own
one in units that survive being resized.

Two further repairs are not about the measure at all. Every empty maths
argument Mathpix writes — the alignment cells of a matrix, the missing half of a
one-sided script — is filled with a zero-width space, because Word draws nothing
for an empty ``<m:e/>`` while LibreOffice draws its missing-operand placeholder
and the same file reads with an inverted question mark in every matrix row. And
when the document is asked for in columns, the section is restated as the page
the source was laid out on, read out of ``lines.json``: at the source book's own
column width essentially nothing overflows, because nothing overflowed in the
book. That is the difference between a document narrowed by hand and one laid
out where its content already fitted.

Mathpix's file is never modified in place. ``mathpix/document.docx`` remains the
bytes Mathpix returned, and the fitted document is written beside it, so any
defect can still be attributed to whichever of the two produced it.
"""

from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

EMU_PER_INCH = 914400
TWIPS_PER_INCH = 1440
POINTS_PER_INCH = 72.0

DOCUMENT = "word/document.xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
SETTINGS = "word/settings.xml"

# A US Letter page with Mathpix's own margins, used only when a document has no
# readable section properties. Better than refusing to fit at all, and the value
# never applies to a file that states its own.
DEFAULT_CONTENT_TWIPS = 8640

# Below this, an image is a symbol sitting in a line of text rather than a
# figure, and shrinking it further makes it unreadable. Restoration is clamped
# rather than skipped, because the goal is a document a person can read.
MIN_IMAGE_INCHES = 0.18

# A display equation longer than this many characters of maths is a candidate
# for wrapping. Short equations are left entirely alone: an unnecessary break
# point is a worse defect than the overflow it was meant to prevent, because it
# fires on a document that was never too wide.
MATH_BREAK_CHARS = 90

# Where a long equation may be broken. These are relations and the top-level
# connectives that read as relations; breaking before one is what a typesetter
# would do and what `m:brkBin w:val="before"` already tells Word to expect.
# More than this many breaks in one equation stops being a wrap and starts
# being a re-typesetting of it, which is not this module's business.
MAX_MATH_BREAKS = 6

MATH_BREAK_TOKENS = ("=", "≠", "≤", "≥", "<", ">", "≈", "≡", "⇒", "⇔", "→", "∴", "±")

# Arguments a Mathpix export leaves with nothing on their left. An empty
# `<m:e/>`, or the missing half of a one-sided script written as `<m:sub/>` or
# `<m:sup/>`, has no operand at all; a matrix cell opening on `=` or `⇒` has one,
# but on the previous line, because that is how a multi-line derivation is
# aligned. Word supplies the missing operand silently. LibreOffice supplies its
# placeholder, which is the inverted question mark that appears once per line of
# every worked example. A zero-width space is an operand in both readers and ink
# in neither.
MATH_GAP_ELEMENTS = ("m:e", "m:sub", "m:sup")
MATH_LEADING_OPERATORS = (
    "=", "≠", "≤", "≥", "<", ">", "≈", "≅", "≡", "⇒", "⇔", "→", "∴", "∵",
    "±", "∓", "+", "−", "-", "×", "÷", "·", "∝",
)
ZERO_WIDTH_SPACE = "\u200b"

# The runs a matrix cell opens with, if it opens with runs at all. `m:r` cannot
# contain another, so the non-greedy close is exact — and anchoring at the start
# is what keeps this to the cell's own text rather than the first character of
# some fraction nested inside it.
LEADING_RUNS_RE = re.compile(rb"\A(?:<m:r\b[^>]*>.*?</m:r>)+", re.S)

# What has to be true of `lines.json` before its boxes are read as a page
# layout. Below these the answer is "the geometry could not be read", which
# leaves the section alone — a document laid out against a page it was never on
# is a worse defect than one left at Mathpix's assumed measure.
MIN_LAYOUT_LINES = 40
# How much of the document has to agree on the column count. A book with a
# handful of full-width pages still has two columns; a document where the
# structure changes page to page is not one this can speak for.
MIN_LAYOUT_PAGE_SHARE = 0.5
# A column seen on fewer lines than this is a stray label, not a column.
MIN_LAYOUT_COLUMN_LINES = 10
# Edges are read at these percentiles rather than at the extremes, because one
# line reaching into the gutter would otherwise become the margin.
LAYOUT_EDGE_PERCENTILE = 0.05
LAYOUT_HEAD_PERCENTILE = 0.02
MAX_LAYOUT_COLUMNS = 4
MIN_PAGE_INCHES = 3.0
MAX_PAGE_INCHES = 30.0
MIN_COLUMN_INCHES = 1.5
# No derived margin is trusted below this: text running to the paper's edge is a
# misread of the boxes rather than a page with no margin.
MIN_MARGIN_INCHES = 0.25

# Attribute order inside `w:tblPr` and `w:tcPr` is fixed by the schema, so an
# inserted element cannot simply be prepended — Word rejects the part outright.
# These are the elements each insertion must sit in front of.
TBLPR_ORDER = (
    "w:tblStyle", "w:tblpPr", "w:tblOverlap", "w:bidiVisual",
    "w:tblStyleRowBandSize", "w:tblStyleColBandSize", "w:tblW", "w:jc",
    "w:tblCellSpacing", "w:tblInd", "w:tblBorders", "w:shd", "w:tblLayout",
    "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription",
)
TCPR_ORDER = (
    "w:cnfStyle", "w:tcW", "w:gridSpan", "w:hMerge", "w:vMerge", "w:tcBorders",
    "w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText",
    "w:vAlign", "w:hideMark",
)

SECTPR_ORDER = (
    "w:footnotePr", "w:endnotePr", "w:type", "w:pgSz", "w:pgMar", "w:paperSrc",
    "w:pgBorders", "w:lnNumType", "w:pgNumType", "w:cols", "w:formProt",
    "w:vAlign", "w:noEndnote", "w:titlePg", "w:textDirection", "w:bidi",
    "w:rtlGutter", "w:docGrid", "w:printerSettings", "w:sectPrChange",
)
MATHPR_ORDER = (
    "m:mathFont", "m:brkBin", "m:brkBinSub", "m:smallFrac", "m:dispDef",
    "m:lMargin", "m:rMargin", "m:defJc", "m:preSp", "m:postSp", "m:wrapIndent",
    "m:wrapRight", "m:intLim", "m:naryLim",
)

TAG_RE = re.compile(rb"<(/?)([A-Za-z_][\w.:-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>")


@dataclass(frozen=True)
class Fit:
    """What fitting one document changed, so the job can record it."""

    images_resized: int = 0
    images_capped: int = 0
    images_skipped: int = 0
    tables_fitted: int = 0
    cells_fitted: int = 0
    equations_broken: int = 0
    math_gaps_filled: int = 0
    render_dpi: float = 0.0
    measure_inches: float = 0.0
    # 0 when the section was left as Mathpix wrote it; otherwise the column
    # count the document was laid out in, and the page it was laid out on.
    columns: int = 0
    page_inches: tuple[float, float] | None = None
    applied: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "images_resized": self.images_resized,
            "images_capped": self.images_capped,
            "images_skipped": self.images_skipped,
            "tables_fitted": self.tables_fitted,
            "cells_fitted": self.cells_fitted,
            "equations_broken": self.equations_broken,
            "math_gaps_filled": self.math_gaps_filled,
            "render_dpi": round(self.render_dpi, 1) or None,
            "measure_inches": round(self.measure_inches, 2) or None,
            "columns": self.columns or None,
            "page_inches": (
                [round(self.page_inches[0], 2), round(self.page_inches[1], 2)]
                if self.page_inches
                else None
            ),
            "applied": self.applied,
            "reason": self.reason or None,
        }


def _attr(raw: bytes, name: str) -> str | None:
    """One attribute off a raw tag body, without parsing the whole element."""
    match = re.search(
        rf"""\b{re.escape(name)}\s*=\s*("([^"]*)"|'([^']*)')""".encode(), raw
    )
    if match is None:
        return None
    value = match.group(2) if match.group(2) is not None else match.group(3)
    return value.decode("utf-8", "replace")


def _int_attr(raw: bytes, name: str) -> int | None:
    value = _attr(raw, name)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _insert_offset(offset: int, body: bytes, element: str, order: tuple) -> int:
    """Where a new child may legally go, given the schema's fixed sequence.

    Word rejects the whole part when a property element is out of order, so this
    finds the first sibling that must follow the new element and puts it
    immediately before that one, falling back to the end of the container.
    """
    position = order.index(element)
    for later in order[position + 1:]:
        found = body.find(f"<{later}".encode())
        if found != -1:
            return offset + found
    return offset + len(body)


def _percentile(values: list[float], fraction: float) -> float:
    """A trimmed edge rather than an extreme one, so one stray box is not the page."""
    ordered = sorted(values)
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[min(max(index, 0), len(ordered) - 1)]


def image_pixels(data: bytes) -> tuple[int, int] | None:
    """The pixel dimensions of an image, read from its own header.

    Only the formats Mathpix crops to are understood. An image whose header
    cannot be read is left at whatever size Mathpix gave it rather than guessed
    at, because a wrong size is worse than the original defect.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)

    if data[:3] == b"\xff\xd8\xff":
        index = 2
        limit = len(data)
        while index + 9 < limit:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if marker == 0xD9:
                break
            if index + 4 > limit:
                break
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            # SOF0..SOF15, excluding the DHT/JPG/DAC markers interleaved with them.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                if index + 9 <= limit:
                    height, width = struct.unpack(">HH", data[index + 5:index + 9])
                    return int(width), int(height)
                break
            index += 2 + length
        return None

    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8 " and len(data) >= 30:
            width, height = struct.unpack("<HH", data[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            bits = struct.unpack("<I", data[21:25])[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X" and len(data) >= 30:
            width = int.from_bytes(data[24:27], "little") + 1
            height = int.from_bytes(data[27:30], "little") + 1
            return width, height
        return None

    return None


def render_dpi(lines: dict | None, page_sizes: list[tuple[float, float]]) -> float:
    """The resolution Mathpix rendered — and therefore cropped — the pages at.

    This is the number that makes the image repair exact rather than a guess.
    ``lines.json`` reports each page as an image and gives its pixel width; the
    PDF reports the same page in points. Their ratio is the scale every crop off
    that page was taken at, so a crop of *n* pixels is genuinely ``n / dpi``
    inches wide — the size the figure occupied before Mathpix rounded it off to
    96 DPI.

    The median across pages is taken rather than the first, because a document
    can mix page sizes and one unusual page should not resize the whole
    document. Zero means the question could not be answered, and the caller
    falls back to capping images instead of restoring them.
    """
    if not isinstance(lines, dict) or not page_sizes:
        return 0.0
    pages = lines.get("pages")
    if not isinstance(pages, list):
        return 0.0

    found: list[float] = []
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        # Mathpix names this `page_width`; older exports and this repository's
        # own fixtures call the same number `image_width`. Both are the rendered
        # page in pixels, which is the only thing being asked for here.
        pixels = page.get("page_width")
        if not isinstance(pixels, (int, float)) or pixels <= 0:
            pixels = page.get("image_width")
        if not isinstance(pixels, (int, float)) or pixels <= 0:
            continue
        # `page` is one-based when present; fall back to position in the list.
        number = page.get("page")
        position = int(number) - 1 if isinstance(number, int) and number > 0 else index
        if not 0 <= position < len(page_sizes):
            continue
        points = page_sizes[position][0]
        if points <= 0:
            continue
        value = float(pixels) / (points / POINTS_PER_INCH)
        # A plausible render sits between screen and print resolution. Anything
        # outside that is a misread rather than an unusual document, and acting
        # on it would resize every figure in the file by a wrong factor.
        if 50.0 <= value <= 1200.0:
            found.append(value)

    if not found:
        return 0.0
    found.sort()
    middle = len(found) // 2
    if len(found) % 2:
        return found[middle]
    return (found[middle - 1] + found[middle]) / 2.0


@dataclass(frozen=True)
class SourceLayout:
    """The page the document was actually laid out on, in inches.

    Every number here is measured off ``lines.json``: Mathpix reports each page
    as a rendered image and gives every line's box within it, and the same
    file's ``column`` field says which of the page's columns that line belongs
    to. Scaled by the resolution the page was rendered at, that is the source
    book's own geometry — page, margins, columns and gutter — rather than a
    layout chosen here.

    Matching it is the whole point. Nothing overflowed a column in the source
    book, so at the source's own column width essentially nothing overflows in
    the .docx either; the overflow people see is the artefact of forcing that
    content into a narrower US Letter column.
    """

    page_width: float
    page_height: float
    margin_left: float
    margin_right: float
    margin_top: float
    margin_bottom: float
    columns: int
    gutter: float

    @property
    def column_width(self) -> float:
        content = self.page_width - self.margin_left - self.margin_right
        return (content - self.gutter * (self.columns - 1)) / self.columns


def _layout_boxes(pages: list) -> tuple[dict[int, list], list[int], list[float], list[float], list[float], list[float]]:
    """Line boxes grouped by the column they sit in, plus each page's own size.

    Column ``0`` is Mathpix's label for a line that spans the whole measure — a
    heading or a wide table — rather than a column of its own, so it is kept
    apart: it widens the text block but never divides it.
    """
    boxes: dict[int, list[tuple[float, float]]] = {}
    per_page: list[int] = []
    widths: list[float] = []
    heights: list[float] = []
    tops: list[float] = []
    bottoms: list[float] = []

    for page in pages:
        if not isinstance(page, dict):
            continue
        width = page.get("page_width") or page.get("image_width")
        height = page.get("page_height") or page.get("image_height")
        if isinstance(width, (int, float)) and width > 0:
            widths.append(float(width))
        if isinstance(height, (int, float)) and height > 0:
            heights.append(float(height))

        highest = 1
        lines = page.get("lines")
        for line in lines if isinstance(lines, list) else []:
            if not isinstance(line, dict):
                continue
            region = line.get("region")
            if not isinstance(region, dict):
                continue
            values = [
                region.get(key)
                for key in ("top_left_x", "top_left_y", "width", "height")
            ]
            if not all(isinstance(value, (int, float)) for value in values):
                continue
            left, top, span, tall = (float(value) for value in values)
            if span <= 0 or tall <= 0:
                continue
            column = line.get("column")
            index = int(column) if isinstance(column, (int, float)) and column > 0 else 0
            highest = max(highest, index)
            boxes.setdefault(index, []).append((left, left + span))
            tops.append(top)
            bottoms.append(top + tall)
        per_page.append(highest)

    return boxes, per_page, widths, heights, tops, bottoms


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def source_layout(
    lines: dict | None, page_sizes: list[tuple[float, float]]
) -> SourceLayout | None:
    """The source page's own geometry, or ``None`` when it cannot be read.

    ``None`` is the important half of this function. A document whose boxes do
    not describe a stable layout — too few lines to measure, pages that disagree
    about how many columns they have, columns that overlap, a page size outside
    anything paper comes in — gets no answer rather than a guessed one, and the
    caller leaves the section exactly as Mathpix wrote it.
    """
    if not isinstance(lines, dict):
        return None
    pages = lines.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    dpi = render_dpi(lines, page_sizes)
    if dpi <= 0:
        return None

    boxes, per_page, widths, heights, tops, bottoms = _layout_boxes(pages)
    if not per_page or not widths or not heights or not tops:
        return None
    if sum(len(group) for group in boxes.values()) < MIN_LAYOUT_LINES:
        return None

    count = max(per_page)
    if count > MAX_LAYOUT_COLUMNS:
        return None
    if count > 1:
        agreed = sum(1 for value in per_page if value == count)
        if agreed < MIN_LAYOUT_PAGE_SHARE * len(per_page):
            return None

    page_width_px = _median(widths)
    page_height_px = _median(heights)
    page_width = page_width_px / dpi
    page_height = page_height_px / dpi
    if not MIN_PAGE_INCHES <= page_width <= MAX_PAGE_INCHES:
        return None
    if not MIN_PAGE_INCHES <= page_height <= MAX_PAGE_INCHES:
        return None

    spanning = boxes.get(0, [])
    edges: list[tuple[float, float]] = []
    for index in range(1, count + 1):
        group = list(boxes.get(index, []))
        if count == 1:
            # A single-column page labels everything as spanning, so the one
            # column *is* the text block.
            group += spanning
        if len(group) < MIN_LAYOUT_COLUMN_LINES:
            return None
        edges.append(
            (
                _percentile([left for left, _ in group], LAYOUT_EDGE_PERCENTILE),
                _percentile([right for _, right in group], 1 - LAYOUT_EDGE_PERCENTILE),
            )
        )
    edges.sort()
    for previous, current in zip(edges, edges[1:]):
        if current[0] <= previous[1]:
            # Overlapping columns are a misread of the labels, not a layout.
            return None

    left_px = min(edge[0] for edge in edges)
    right_px = max(edge[1] for edge in edges)
    if count > 1 and spanning:
        # A full-width heading reaches the margins even where no column does.
        left_px = min(left_px, _percentile([l for l, _ in spanning], LAYOUT_EDGE_PERCENTILE))
        right_px = max(right_px, _percentile([r for _, r in spanning], 1 - LAYOUT_EDGE_PERCENTILE))

    margin_left = max(left_px / dpi, MIN_MARGIN_INCHES)
    margin_right = max((page_width_px - right_px) / dpi, MIN_MARGIN_INCHES)
    gutter = 0.0
    if count > 1:
        gaps = [edges[index + 1][0] - edges[index][1] for index in range(count - 1)]
        gutter = max(sum(gaps) / len(gaps), 0.0) / dpi

    content = page_width - margin_left - margin_right
    if content <= 0:
        return None
    if (content - gutter * (count - 1)) / count < MIN_COLUMN_INCHES:
        return None

    margin_top = _percentile(tops, LAYOUT_HEAD_PERCENTILE) / dpi
    margin_bottom = (page_height_px - _percentile(bottoms, 1 - LAYOUT_HEAD_PERCENTILE)) / dpi
    ceiling = page_height / 3.0
    margin_top = min(max(margin_top, MIN_MARGIN_INCHES), ceiling)
    margin_bottom = min(max(margin_bottom, MIN_MARGIN_INCHES), ceiling)

    return SourceLayout(
        page_width=page_width,
        page_height=page_height,
        margin_left=margin_left,
        margin_right=margin_right,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        columns=count,
        gutter=gutter,
    )


def measure_twips(document: bytes) -> int:
    """The width one line of text actually gets, in twips.

    Read from the document's own final section rather than assumed, and reduced
    by the column count when the section is already in columns, because that —
    not the page width — is the width everything in this module is being fitted
    to.
    """
    sections = re.findall(rb"<w:sectPr[ >].*?</w:sectPr>", document, re.S)
    if not sections:
        return DEFAULT_CONTENT_TWIPS
    section = sections[-1]

    size = re.search(rb"<w:pgSz\b([^>]*)>", section)
    margin = re.search(rb"<w:pgMar\b([^>]*)>", section)
    width = _int_attr(size.group(1), "w:w") if size else None
    if not width or width <= 0:
        return DEFAULT_CONTENT_TWIPS

    left = right = gutter = 0
    if margin:
        left = _int_attr(margin.group(1), "w:left") or 0
        right = _int_attr(margin.group(1), "w:right") or 0
        gutter = _int_attr(margin.group(1), "w:gutter") or 0
    content = width - left - right - gutter
    if content <= 0:
        return DEFAULT_CONTENT_TWIPS

    columns = re.search(rb"<w:cols\b([^>]*)>", section)
    if columns:
        count = _int_attr(columns.group(1), "w:num") or 1
        space = _int_attr(columns.group(1), "w:space") or 0
        if count > 1:
            content = (content - space * (count - 1)) // count
    return max(content, 1)


def _twips(inches: float) -> int:
    return max(int(round(inches * TWIPS_PER_INCH)), 1)


def _set_one_section(document: bytes, start: int, end: int, layout: SourceLayout) -> list:
    """Restate a single ``w:sectPr`` as the source page it was laid out on."""
    section = document[start:end]
    inner_start = section.find(b">") + 1
    inner_end = section.rfind(b"</w:sectPr>")
    if inner_start <= 0 or inner_end < inner_start:
        return []
    body = section[inner_start:inner_end]
    base = start + inner_start

    width = _twips(layout.page_width)
    height = _twips(layout.page_height)
    orientation = "landscape" if width > height else "portrait"
    top = _twips(layout.margin_top)
    bottom = _twips(layout.margin_bottom)

    header = footer = 720
    margin = re.search(rb"<w:pgMar\b([^>]*?)/>", body)
    if margin is not None:
        header = _int_attr(margin.group(1), "w:header") or header
        footer = _int_attr(margin.group(1), "w:footer") or footer
    # A header printed below the top margin overlaps the text it heads.
    header = min(header, max(top - 1, 1))
    footer = min(footer, max(bottom - 1, 1))

    elements = {
        "w:pgSz": (
            f'<w:pgSz w:w="{width}" w:h="{height}" w:orient="{orientation}"/>'
        ).encode(),
        "w:pgMar": (
            f'<w:pgMar w:top="{top}" w:right="{_twips(layout.margin_right)}"'
            f' w:bottom="{bottom}" w:left="{_twips(layout.margin_left)}"'
            f' w:header="{header}" w:footer="{footer}" w:gutter="0"/>'
        ).encode(),
    }
    if layout.columns > 1:
        elements["w:cols"] = (
            f'<w:cols w:num="{layout.columns}" w:space="{_twips(layout.gutter)}"'
            ' w:equalWidth="1"/>'
        ).encode()

    edits: list[tuple[int, int, bytes]] = []
    # Replacements are computed against the original body, so an element that is
    # rewritten in place leaves the offsets of the others untouched; only the
    # insertions consult the order the schema fixes.
    for element, markup in elements.items():
        pattern = (
            rb"<" + element.encode() + rb"\b[^>]*?/>|"
            rb"<" + element.encode() + rb"\b[^>]*?>.*?</" + element.encode() + rb">"
        )
        found = re.search(pattern, body, re.S)
        if found is not None:
            if found.group(0) != markup:
                edits.append((base + found.start(), len(found.group(0)), markup))
        else:
            edits.append((_insert_offset(base, body, element, SECTPR_ORDER), 0, markup))

    # A section left in more columns than the source has would keep dividing a
    # measure this has just widened.
    if layout.columns <= 1:
        for found in re.finditer(
            rb"<w:cols\b[^>]*?/>|<w:cols\b[^>]*?>.*?</w:cols>", body, re.S
        ):
            edits.append((base + found.start(), len(found.group(0)), b""))
    return edits


def _set_section(document: bytes, layout: SourceLayout) -> bytes:
    """Lay the document out on the page its content was written for.

    Every ``w:sectPr`` is restated, not only the last: a document read at one
    geometry in its body and another in a mid-document section is two documents.
    Mathpix writes exactly one, so in practice this rewrites that one.

    Nothing else in this module needs telling that the measure has changed.
    ``measure_twips`` reads the section back and divides by the column count, so
    images, tables and equations are all fitted to the new column by the same
    arithmetic they already used for the old one.
    """
    edits: list[tuple[int, int, bytes]] = []
    for found in re.finditer(rb"<w:sectPr[ >].*?</w:sectPr>", document, re.S):
        edits += _set_one_section(document, found.start(), found.end(), layout)
    return _apply(document, edits)


# `xml:space="preserve"` is not decoration. Without it LibreOffice's importer
# reads U+200B as whitespace, trims the run to nothing and discards it, and the
# placeholder comes straight back — which is the difference between this repair
# working and appearing to.
MATH_OPERAND = (
    f'<m:r><m:t xml:space="preserve">{ZERO_WIDTH_SPACE}</m:t></m:r>'
).encode()


def _math_gap_markup(element: str) -> bytes:
    return f"<{element}>".encode() + MATH_OPERAND + f"</{element}>".encode()


def _math_text(body: bytes) -> str:
    found = re.findall(rb"<m:t[^>]*>(.*?)</m:t>", body, re.S)
    return b"".join(found).decode("utf-8", "replace")


def _needs_an_operand(body: bytes) -> bool:
    """Whether a line of maths has nothing on its left that a reader can see.

    Two ways to end up there. The line may hold nothing but spaces — Mathpix
    pads a matrix out to a rectangle that way, and LibreOffice trims the padding
    to nothing and is then short an argument. Or it may open on a relation,
    whose left-hand side is on the row above because that is what a multi-line
    derivation looks like.

    A zero-width space is not whitespace to ``str.strip``, so a line this has
    already been run over is not run over twice.
    """
    text = _math_text(body).strip()
    if not text:
        return True
    runs = LEADING_RUNS_RE.match(body)
    if runs is None:
        return False
    lead = _math_text(runs.group(0)).strip()
    return bool(lead) and lead[0] in MATH_LEADING_OPERATORS


def _fill_math_gaps(document: bytes) -> tuple[bytes, int]:
    """Give every maths argument that has no left operand an invisible one.

    Two shapes, one cause. An argument written as a single empty tag —
    ``<m:e/>``, or the unused half of a one-sided script as ``<m:sub/>`` or
    ``<m:sup/>`` — has no content at all. A matrix cell reading ``= 1 − h²/a²``
    has content, but its left-hand side is on the row above: that is how Mathpix
    writes a multi-line derivation, one row per step, the relation leading each
    continuation.

    Word supplies the missing operand itself and draws nothing. LibreOffice
    substitutes its placeholder glyph and, for a relation, draws it *instead of*
    the operator — so ``= 1 − h²/a²`` renders as ``¿1 − h²/a²`` and a chapter of
    worked examples acquires one inverted question mark per line. Both readers
    accept a zero-width space as the operand, and neither gives it any width, so
    the maths is unchanged in Word and repaired everywhere else.

    Only matrix cells are treated this way. A leading ``−`` inside ``<m:d>`` is
    the sign of the number it precedes, not a subtraction missing its left-hand
    side, and giving it an operand would turn ``(−b)`` into ``( − b)``.

    ``<m:deg/>`` is deliberately left alone: every one of them belongs to a
    radical carrying ``<m:degHide m:val="1"/>``, where empty is what a square
    root is supposed to be.
    """
    edits: list[tuple[int, int, bytes]] = []
    stack: list[tuple[str, int]] = []

    for match in TAG_RE.finditer(document):
        name = match.group(2).decode("ascii", "replace")
        # Only the maths vocabulary is tracked. Its elements are balanced among
        # themselves, so the `w:` properties nested inside them cannot unbalance
        # this stack, and skipping them keeps the pass to one cheap scan.
        if not name.startswith("m:"):
            continue

        if match.group(4) == b"/":
            if name in MATH_GAP_ELEMENTS:
                edits.append(
                    (match.start(), match.end() - match.start(), _math_gap_markup(name))
                )
            continue

        if match.group(1) == b"/":
            if not stack or stack[-1][0] != name:
                continue
            _, opened = stack.pop()
            parent = stack[-1][0] if stack else ""
            # A matrix cell and a whole equation are the two places a line of
            # maths starts. Anywhere else — a numerator, a script, the inside of
            # a bracket — a leading sign belongs to what follows it.
            if (name == "m:e" and parent == "m:mr") or name == "m:oMath":
                if _needs_an_operand(document[opened:match.start()]):
                    edits.append((opened, 0, MATH_OPERAND))
            continue

        stack.append((name, match.end()))

    return _apply(document, edits), len(edits)


def _left_align(document: bytes) -> tuple[bytes, int]:
    """Stop centring what now has a column's width rather than a page's.

    Mathpix centres its display equations, its figures and its one table across
    a six-inch measure. Centred across three and a half, the same content reads
    as ragged on both sides and wastes the width it was narrowed to use.
    """
    return re.subn(rb'(<w:jc\b[^>]*?w:val=")center(")', rb"\1left\2", document)


def _set_default_justification(settings: bytes, value: str = "left") -> bytes:
    """The same question for maths, which answers it in `settings.xml` instead.

    ``m:defJc`` is ``centerGroup`` in every Mathpix export, and it decides where
    a display equation sits for the whole document at once.
    """
    markup = f'<m:defJc m:val="{value}"/>'.encode()
    match = re.search(rb"<m:defJc\b([^>]*)/>", settings)
    if match is not None:
        if _attr(match.group(1), "m:val") == value:
            return settings
        return settings[:match.start()] + markup + settings[match.end():]

    opening = re.search(rb"<m:mathPr\b[^>]*>", settings)
    if opening is None or opening.group(0).endswith(b"/>"):
        return settings
    closing = settings.find(b"</m:mathPr>", opening.end())
    if closing == -1:
        return settings
    offset = _insert_offset(
        opening.end(), settings[opening.end():closing], "m:defJc", MATHPR_ORDER
    )
    return settings[:offset] + markup + settings[offset:]


@dataclass
class _Cell:
    """One table cell's share of the measure, while the walk is inside it.

    The width cannot be written when the cell opens, because ``w:gridSpan`` — the
    thing that decides how much of the grid the cell covers — is itself inside
    the properties the width has to go into. So the offsets are remembered and
    the arithmetic is done at the closing tag, by which point the span is known.
    """

    twips: int
    span: int = 1
    body: int = -1  # just after `<w:tc>`, where properties would have to be created
    props: int = -1  # just after `<w:tcPr>`, where a width may be inserted
    # A `<w:tcPr/>` written as one empty tag holds no width and cannot be
    # inserted into; its span is kept so it can be replaced by a real one.
    empty: tuple[int, int] | None = None


def _media_map(rels: bytes) -> dict[str, str]:
    """`r:embed` identifier to the part it names, for image relationships only."""
    found: dict[str, str] = {}
    for match in re.finditer(rb"<Relationship\b([^>]*)>", rels):
        raw = match.group(1)
        identifier = _attr(raw, "Id")
        target = _attr(raw, "Target")
        mode = _attr(raw, "TargetMode")
        if not identifier or not target or mode == "External":
            continue
        found[identifier] = target
    return found


def _part_name(target: str) -> str:
    """Resolve a relationship target against `word/`, the part that declared it."""
    cleaned = target.lstrip("/")
    if target.startswith("/"):
        return cleaned
    return str(PurePosixPath("word") / cleaned)


class _Walk:
    """One pass over `document.xml`, collecting every edit before applying any.

    A single ordered walk is what makes the table arithmetic correct: a cell's
    share of the measure depends on the grid of the table it is in, that table
    may be nested inside a cell of another, and an image's available width is
    the innermost cell's rather than the page's. None of that survives being
    matched with independent regular expressions, and all of it falls out of
    keeping a stack.
    """

    def __init__(self, document: bytes, measure: int):
        self.document = document
        self.measure = measure
        self.edits: list[tuple[int, int, bytes]] = []
        self.tables: list[dict] = []
        self.cells: list[_Cell] = []
        self.drawings: list[tuple[int, int, int]] = []  # start, end, available twips
        self.math: list[tuple[int, int]] = []
        self.tables_fitted = 0
        self.cells_fitted = 0

    @property
    def available(self) -> int:
        """The width the current position may occupy, in twips."""
        if self.cells:
            return max(self.cells[-1].twips, 1)
        return self.measure

    def run(self) -> None:
        drawing_start: list[int] = []
        math_start: list[int] = []

        for match in TAG_RE.finditer(self.document):
            closing = match.group(1) == b"/"
            name = match.group(2).decode("ascii", "replace")
            raw = match.group(3)
            empty = match.group(4) == b"/"

            if name == "w:tbl":
                if closing:
                    if self.tables:
                        self._fit_table(self.tables.pop())
                elif not empty:
                    self.tables.append(
                        {
                        "grid": [],
                        "columns_at": [],
                        "column": 0,
                        "width": self.available,
                        "body": match.end(),
                        "pr": None,
                        "empty": None,
                    }
                    )
                continue

            if not self.tables:
                # Outside a table the only structures that matter are drawings
                # and display equations.
                self._structural(
                    name, closing, empty, match, drawing_start, math_start
                )
                continue

            table = self.tables[-1]

            if name == "w:gridCol" and not closing:
                value = _int_attr(raw, "w:w")
                if value and value > 0:
                    table["grid"].append(value)
                    table["columns_at"].append((match.start(), match.end(), value))
                continue

            if name == "w:tblPr":
                if empty:
                    table["empty"] = (match.start(), match.end())
                elif not closing:
                    table["pr"] = match.end()
                continue

            if name == "w:tr" and not closing and not empty:
                table["column"] = 0
                continue

            if name == "w:tc":
                if closing:
                    if self.cells:
                        cell = self.cells.pop()
                        if cell.props == -1:
                            self._create_cell_props(table, cell)
                        table["column"] += cell.span
                elif not empty:
                    self.cells.append(
                        _Cell(self._cell_twips(table, 1), body=match.end())
                    )
                continue

            if name == "w:gridSpan" and not closing and self.cells:
                span = _int_attr(raw, "w:val") or 1
                self.cells[-1].span = max(span, 1)
                self.cells[-1].twips = self._cell_twips(table, max(span, 1))
                continue

            if name == "w:tcPr" and self.cells:
                if empty:
                    self.cells[-1].empty = (match.start(), match.end())
                elif closing:
                    self._fit_cell(table, self.cells[-1])
                else:
                    self.cells[-1].props = match.end()
                continue

            self._structural(name, closing, empty, match, drawing_start, math_start)

    def _structural(self, name, closing, empty, match, drawing_start, math_start):
        if name == "w:drawing":
            if closing:
                if drawing_start:
                    start = drawing_start.pop()
                    self.drawings.append((start, match.start(), self.available))
            elif not empty:
                drawing_start.append(match.end())
        elif name == "m:oMathPara":
            if closing:
                if math_start:
                    self.math.append((math_start.pop(), match.start()))
            elif not empty:
                math_start.append(match.end())

    def _cell_twips(self, table: dict, span: int) -> int:
        """A cell's width, from the grid when there is one and evenly when not."""
        grid = table["grid"]
        start = table["column"]
        if grid:
            total = sum(grid) or 1
            taken = sum(grid[start:start + span]) or grid[min(start, len(grid) - 1)]
            return max(int(table["width"] * taken / total), 1)
        return max(table["width"] // max(span, 1), 1)

    def _rescale_grid(self, table: dict) -> bool:
        """Bring the absolute grid to the measure the table is actually in.

        This is the half that cannot be stated as a percentage. Under
        ``tblLayout="fixed"`` — which is what makes the percentage widths below
        binding rather than advisory — Word and LibreOffice both lay the columns
        out from ``w:tblGrid``, and Mathpix's grid is an absolute one summing to
        the six inches it assumed. Declaring the table 100% wide while leaving
        that grid in place therefore does not narrow it at all: the grid wins,
        and the table still runs off a column half that width.

        The proportions are Mathpix's and are kept exactly; only the total
        changes. The last column absorbs the rounding so the grid sums to the
        measure to the twip rather than to within a few of it.
        """
        columns = table["columns_at"]
        total = sum(table["grid"])
        target = table["width"]
        if not columns or total <= 0 or target <= 0 or total == target:
            return False

        running = 0
        widths: list[int] = []
        for index, (_, _, value) in enumerate(columns):
            if index == len(columns) - 1:
                widths.append(max(target - running, 1))
                continue
            width = max(int(round(value * target / total)), 1)
            running += width
            widths.append(width)

        changed = False
        for (start, stop, value), width in zip(columns, widths):
            if width == value:
                continue
            # Only the number is replaced, so an attribute this module has never
            # heard of survives on the element it was written on.
            self.edits.append(
                (
                    start,
                    stop - start,
                    re.sub(
                        rb'w:w="\d+"',
                        f'w:w="{width}"'.encode(),
                        self.document[start:stop],
                        count=1,
                    ),
                )
            )
            changed = True
        return changed

    def _fit_table(self, table: dict) -> None:
        """State the table as a share of its measure instead of an absolute width.

        Mathpix writes a grid summing to the content width and stops there — no
        ``w:tblW``, so Word has no declared table width to honour, and no
        ``w:tblLayout``, so it autofits and lets cell content decide the columns.
        Both are supplied here, and the width is supplied as a percentage, which
        is the only form that stays correct when the same file is later read at
        a different measure. The grid is rescaled to match, because a fixed
        layout reads that in preference to either of them.
        """
        rescaled = self._rescale_grid(table)
        if table["pr"] is not None:
            offset = table["pr"]
            end = self.document.find(b"</w:tblPr>", offset)
            body = self.document[offset:end if end != -1 else offset]
        else:
            offset, body = -1, b""

        additions: list[tuple[str, bytes]] = []
        if b"<w:tblW" not in body:
            # 5000 fiftieths of a percent is 100%.
            additions.append(("w:tblW", b'<w:tblW w:w="5000" w:type="pct"/>'))
        if b"<w:tblLayout" not in body:
            additions.append(("w:tblLayout", b'<w:tblLayout w:type="fixed"/>'))
        if not additions:
            if rescaled:
                self.tables_fitted += 1
            return
        markup = b"".join(element for _, element in additions)

        if offset != -1:
            for element, one in additions:
                self.edits.append(
                    (self._insert_at(offset, body, element, TBLPR_ORDER), 0, one)
                )
        elif table["empty"] is not None:
            # `<w:tblPr/>` carries nothing and cannot be inserted into, so it is
            # replaced by the properties it would have held.
            start, stop = table["empty"]
            self.edits.append(
                (start, stop - start, b"<w:tblPr>" + markup + b"</w:tblPr>")
            )
        else:
            # `w:tblPr` is the first child of `w:tbl` when present at all.
            self.edits.append(
                (table["body"], 0, b"<w:tblPr>" + markup + b"</w:tblPr>")
            )
        self.tables_fitted += 1

    def _cell_percent(self, table: dict, cell: _Cell) -> int:
        """The cell's share of its table, in fiftieths of a percent.

        A percentage rather than a measurement, because that is the whole point:
        the same cell has to stay proportionate whether the document is read at
        six inches or half that.
        """
        grid = table["grid"]
        total = sum(grid) or 1
        start = table["column"]
        taken = sum(grid[start:start + cell.span]) if grid else 0
        if taken:
            share = taken / total
        else:
            share = cell.span / max(len(grid), 1) if grid else 1.0
        return min(max(int(round(share * 5000)), 1), 5000)

    def _fit_cell(self, table: dict, cell: _Cell) -> None:
        """Give the cell an explicit width, as a share of the table.

        Without one Word treats the row as having nothing to honour and sizes
        the columns from their contents, which is the observable failure: a cell
        holding a long equation widens until the rest of the row is unreadable.
        """
        if cell.props == -1:
            return
        end = self.document.find(b"</w:tcPr>", cell.props)
        body = self.document[cell.props:end if end != -1 else cell.props]
        if b"<w:tcW" in body:
            return
        markup = f'<w:tcW w:w="{self._cell_percent(table, cell)}" w:type="pct"/>'.encode()
        self.edits.append(
            (self._insert_at(cell.props, body, "w:tcW", TCPR_ORDER), 0, markup)
        )
        self.cells_fitted += 1

    def _create_cell_props(self, table: dict, cell: _Cell) -> None:
        """Give a cell that has no usable properties the ones a width needs.

        A cell may carry `<w:tcPr/>` as a single empty tag, which holds nothing
        and cannot be inserted into; replacing it is the only way to give it a
        width without ending up with two `w:tcPr` elements, which Word rejects.
        A cell with no properties at all gets them at the point it opens, since
        `w:tcPr` is the first child of `w:tc`.
        """
        markup = (
            f'<w:tcPr><w:tcW w:w="{self._cell_percent(table, cell)}" w:type="pct"/>'
            "</w:tcPr>"
        ).encode()
        if cell.empty is not None:
            start, stop = cell.empty
            self.edits.append((start, stop - start, markup))
        elif cell.body != -1:
            self.edits.append((cell.body, 0, markup))
        else:
            return
        self.cells_fitted += 1

    def _insert_at(self, offset: int, body: bytes, element: str, order: tuple) -> int:
        return _insert_offset(offset, body, element, order)


def _fit_images(
    document: bytes,
    walk: _Walk,
    media: dict[str, bytes],
    rels: dict[str, str],
    dpi: float,
    max_fraction: float,
) -> tuple[list[tuple[int, int, bytes]], int, int, int]:
    """Restore each image to the size it occupied on the source page.

    The crop is never touched — only the two numbers Word reads its size from.
    When the render resolution is known the true size is computed from it, which
    is a restoration rather than a correction; when it is not, the image is left
    alone unless it overruns the measure, and is then scaled down to fit. Both
    paths preserve the aspect ratio Mathpix cropped at, because the crop is the
    part Mathpix got right.
    """
    edits: list[tuple[int, int, bytes]] = []
    resized = capped = skipped = 0

    for start, end, available_twips in walk.drawings:
        block = document[start:end]
        extent = re.search(rb"<wp:extent\b([^>]*)>", block)
        if extent is None:
            skipped += 1
            continue
        current_cx = _int_attr(extent.group(1), "cx")
        current_cy = _int_attr(extent.group(1), "cy")
        if not current_cx or not current_cy or current_cx <= 0 or current_cy <= 0:
            skipped += 1
            continue

        limit = int(available_twips / TWIPS_PER_INCH * EMU_PER_INCH * max_fraction)
        target_cx = current_cx

        embed = re.search(rb'r:embed="([^"]+)"', block)
        if dpi > 0 and embed is not None:
            part = rels.get(embed.group(1).decode("ascii", "replace"))
            data = media.get(_part_name(part)) if part else None
            pixels = image_pixels(data) if data else None
            if pixels and pixels[0] > 0:
                inches = max(pixels[0] / dpi, MIN_IMAGE_INCHES)
                target_cx = int(round(inches * EMU_PER_INCH))

        if target_cx > limit:
            target_cx = limit
            was_capped = True
        else:
            was_capped = False

        if target_cx <= 0 or target_cx == current_cx:
            skipped += 1
            continue

        target_cy = max(int(round(current_cy * target_cx / current_cx)), 1)
        replacement = f'cx="{target_cx}" cy="{target_cy}"'.encode()

        # `wp:extent` is what Word lays the image out with; the `a:ext` inside the
        # picture's own transform has to agree or the bitmap is drawn at one size
        # inside a frame of another. Only the first of each is touched: a grouped
        # drawing carries an `a:ext` per shape, and the outer one is the frame
        # being resized while the inner ones are positions within it.
        for pattern in (rb"<wp:extent\b([^>]*?)/?>", rb"<a:ext\b([^>]*?)/?>"):
            for found in re.finditer(pattern, block):
                raw = found.group(1)
                if _int_attr(raw, "cx") is None or _int_attr(raw, "cy") is None:
                    continue
                updated = re.sub(rb'cx="\d+"\s+cy="\d+"', replacement, found.group(0))
                if updated != found.group(0):
                    edits.append(
                        (start + found.start(), found.end() - found.start(), updated)
                    )
                break

        resized += 1
        capped += int(was_capped)

    return edits, resized, capped, skipped


def _break_equations(document: bytes, walk: _Walk) -> tuple[list, int]:
    """Give long display equations somewhere for Word to wrap.

    Word is already told how to wrap — ``m:brkBin`` is ``before`` in every file
    Mathpix produces — but it wraps only where a run says it may, and Mathpix
    writes no such run. A break is marked before top-level relations only:
    inside a fraction, script or radical a break would be wrong, and on a short
    equation it would be gratuitous, so neither gets one. The leading relation is
    skipped too, because that one is where the equation starts rather than a
    place it could continue.
    """
    edits: list[tuple[int, int, bytes]] = []
    changed = 0

    for start, end in walk.math:
        block = document[start:end]
        text = b"".join(re.findall(rb"<m:t[^>]*>(.*?)</m:t>", block, re.S))
        if len(text.decode("utf-8", "replace")) < MATH_BREAK_CHARS:
            continue
        if b"<m:brk" in block:
            continue

        depth = 0
        run_depth: int | None = None
        run_start = -1
        seen = 0
        marked = 0

        for match in TAG_RE.finditer(block):
            closing = match.group(1) == b"/"
            name = match.group(2).decode("ascii", "replace")
            if match.group(4) == b"/":
                continue

            if closing:
                depth -= 1
                # `m:r` opened at `run_depth`, so returning to that level means the
                # run has closed and the next one starts somewhere new.
                if run_depth is not None and depth <= run_depth:
                    run_depth = None
                continue

            # A run sitting directly inside `m:oMath` is at the top level of the
            # equation; one nested in a fraction or a script is not, and is the
            # case this must not break.
            if name == "m:r" and run_depth is None and depth == 1:
                run_depth, run_start = depth, match.end()
            depth += 1

            if name != "m:t":
                continue
            closer = block.find(b"</m:t>", match.end())
            if closer == -1:
                continue
            value = block[match.end():closer].decode("utf-8", "replace")
            stripped = value.strip()
            top_level = run_depth is not None and depth == run_depth + 2

            if (
                top_level
                and stripped
                and stripped[0] in MATH_BREAK_TOKENS
                and seen >= MATH_BREAK_CHARS // 3
                and marked < MAX_MATH_BREAKS
            ):
                edits.append((start + run_start, 0, b"<m:rPr><m:brk/></m:rPr>"))
                marked += 1
                seen = 0
            seen += len(stripped)

        if marked:
            changed += 1

    return edits, changed


def _relax_wrap_indent(settings: bytes, twips: int) -> bytes:
    """Stop a wrapped equation losing an inch of a measure it has little of.

    Mathpix sets ``m:wrapIndent`` to 1440 — a full inch — which is affordable
    across a six-inch measure and absurd across a column half that wide. The
    setting only takes effect on lines this module has just made wrappable, so
    lowering it here changes nothing about how the document reads until it is
    narrowed.
    """
    match = re.search(rb"<m:wrapIndent\b([^>]*)/>", settings)
    if match is None:
        return settings
    current = _int_attr(match.group(1), "m:val")
    if current is None or current <= twips:
        return settings
    replacement = f'<m:wrapIndent m:val="{twips}"/>'.encode()
    return settings[:match.start()] + replacement + settings[match.end():]


def _apply(document: bytes, edits: list[tuple[int, int, bytes]]) -> bytes:
    """Apply every edit in one pass, so no offset is invalidated by another."""
    if not edits:
        return document
    out = bytearray()
    cursor = 0
    for offset, length, markup in sorted(edits, key=lambda item: (item[0], item[1])):
        if offset < cursor:
            # Overlapping edits are a bug in a collector, not something to
            # resolve silently by preferring one — dropping it leaves the
            # document exactly as Mathpix wrote it in that one place.
            continue
        out += document[cursor:offset]
        out += markup
        cursor = offset + length
    out += document[cursor:]
    return bytes(out)


def fit_docx(
    data: bytes,
    *,
    page_sizes: list[tuple[float, float]] | None = None,
    lines: dict | None = None,
    max_image_fraction: float = 1.0,
    wrap_indent_twips: int = 360,
    fit_images: bool = True,
    fit_tables: bool = True,
    fit_equations: bool = True,
    multi_column: bool = False,
    fill_math_gaps: bool = True,
) -> tuple[bytes, Fit]:
    """Return Mathpix's document restated in units that survive being resized.

    Every part of the original archive is copied through; only
    ``word/document.xml`` and ``word/settings.xml`` are rewritten, and only in
    the specific places described at the top of this module. A file that cannot
    be read as a .docx is returned exactly as it arrived with the reason
    recorded, because a job that has a slightly wrong document is in better
    shape than one that has none.

    ``multi_column`` is the only switch here that changes what the document
    *is* rather than repairing what it says. With it on, the section is restated
    as the page the source was laid out on — its size, its margins, its columns
    — and everything else in this module then fits to that narrower measure
    without being told, because it reads the measure back out of the section.
    With it off the document stays single-column and only the defects that are
    wrong at any measure are repaired. The section is rewritten only when the
    source geometry could actually be read; otherwise the document is left
    single-column rather than laid out on a guess.
    """
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
            if DOCUMENT not in names:
                return data, Fit(reason="no word/document.xml")
            parts = {name: archive.read(name) for name in names}
            infos = {info.filename: info for info in archive.infolist()}
    except (zipfile.BadZipFile, OSError) as exc:
        return data, Fit(reason=f"unreadable archive: {exc}")

    original = parts[DOCUMENT]
    document = original

    layout = source_layout(lines, page_sizes or []) if multi_column else None
    if layout is not None:
        document = _set_section(document, layout)

    measure = measure_twips(document)
    dpi = render_dpi(lines, page_sizes or []) if fit_images else 0.0

    walk = _Walk(document, measure)
    walk.run()

    edits = list(walk.edits) if fit_tables else []
    resized = capped = skipped = 0
    if fit_images:
        rels = _media_map(parts.get(DOCUMENT_RELS, b""))
        image_edits, resized, capped, skipped = _fit_images(
            document, walk, parts, rels, dpi, max_image_fraction
        )
        edits += image_edits

    broken = 0
    if fit_equations:
        math_edits, broken = _break_equations(document, walk)
        edits += math_edits

    document = _apply(document, edits)

    # Both of these are whole-document rewrites rather than positioned edits, so
    # they run after the offsets collected above have been spent.
    if layout is not None:
        document, _ = _left_align(document)
    gaps = 0
    if fill_math_gaps:
        document, gaps = _fill_math_gaps(document)

    settings_part = parts.get(SETTINGS)
    updated = settings_part
    if updated is not None:
        if fit_equations and broken:
            updated = _relax_wrap_indent(updated, wrap_indent_twips)
        if layout is not None:
            updated = _set_default_justification(updated, "left")

    columns = layout.columns if layout is not None else 0
    page_inches = (layout.page_width, layout.page_height) if layout is not None else None

    if document == original and updated == settings_part:
        return data, Fit(
            render_dpi=dpi,
            measure_inches=measure / TWIPS_PER_INCH,
            columns=columns,
            page_inches=page_inches,
            reason="nothing to fit",
        )

    parts[DOCUMENT] = document
    if updated is not None:
        parts[SETTINGS] = updated

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names:
            info = infos[name]
            # Keep each part's original date so a rebuilt file still diffs
            # cleanly against the one Mathpix returned.
            entry = zipfile.ZipInfo(name, date_time=info.date_time)
            entry.compress_type = info.compress_type
            entry.external_attr = info.external_attr
            out.writestr(entry, parts[name])

    return buffer.getvalue(), Fit(
        images_resized=resized,
        images_capped=capped,
        images_skipped=skipped,
        tables_fitted=walk.tables_fitted,
        cells_fitted=walk.cells_fitted,
        equations_broken=broken,
        math_gaps_filled=gaps,
        render_dpi=dpi,
        measure_inches=measure / TWIPS_PER_INCH,
        columns=columns,
        page_inches=page_inches,
        applied=True,
    )
