"""Read a PDF page into a positioned layout model.

This is the replica pipeline's front end. Where the flow pipeline only ever sees a
picture of the page, this reads what the file actually contains: text spans with
their font, size, colour and bounding box; embedded raster images; and vector
artwork. Everything keeps its coordinates so the writer can put it back in the
same place.

Coordinates are PDF points with the origin at the top-left of the visible page,
which is also how Word positions a floating shape — so no conversion is needed
beyond subtracting the crop box offset.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import fitz

from .config import settings
from .symbols import SYMBOL_FONT, decode_symbol_pua
from .xml_text import xml_safe

# --------------------------------------------------------------------------- #
# Span classification
# --------------------------------------------------------------------------- #

# PyMuPDF span flag bits.
_SUPERSCRIPT, _ITALIC, _SERIF, _MONO, _BOLD = 1, 2, 4, 8, 16

# Font-name fragments that mark a span as mathematical rather than prose. TeX
# splits maths across several fonts (CMMI italic letters, CMSY symbols, CMEX
# extensible braces), and OpenType maths fonts all carry "Math" in the name.
MATH_FONT_MARKERS = (
    "CMMI", "CMSY", "CMEX", "MSAM", "MSBM", "RSFS", "EUFM", "EUSM",
    "MTMI", "MTSY", "MTEX", "MATH", "SYMBOL",
)

# TeX draws `picture`-environment diagrams out of glyphs from these fonts rather
# than with vector paths. The "text" is line segments and arrowheads — often
# literal NUL characters — so it has to be rasterised, never written as text.
GRAPHIC_FONT_STEMS = {
    "LINE", "LINEW", "LCIRCLE", "LCIRCLEW", "LASY", "LASYB", "LCMSS", "BBDING",
}

# PDF font families mapped onto fonts Word can actually be expected to have.
# Anything unmapped is cleaned up structurally instead.
_FONT_MAP = {
    "TIMES": "Times New Roman",
    "TIMESNEWROMAN": "Times New Roman",
    "TIMESNEWROMANPS": "Times New Roman",
    "NIMBUSROMAN": "Times New Roman",
    "HELVETICA": "Arial",
    "NIMBUSSANS": "Arial",
    "ARIAL": "Arial",
    "COURIER": "Courier New",
    "COURIERNEW": "Courier New",
    "NIMBUSMONO": "Courier New",
    # Computer Modern / Latin Modern — the LaTeX defaults. These are mapped onto
    # fonts every machine has: naming a font the reader lacks means Word picks a
    # substitute of its own, which is exactly the arbitrary restyling we are
    # trying to avoid. Set PDF2DOCX_FONT_MAP=off to keep the original names.
    "CMR": "Times New Roman",
    "CMB": "Times New Roman",
    "CMBX": "Times New Roman",
    "CMTI": "Times New Roman",
    "CMSL": "Times New Roman",
    "CMCSC": "Times New Roman",
    "CMSS": "Arial",
    "CMSSBX": "Arial",
    "CMTT": "Courier New",
    "LMROMAN": "Times New Roman",
    "LMSANS": "Arial",
    "LMMONO": "Courier New",
    "SFRM": "Times New Roman",
    "SFTT": "Courier New",
}

_FALLBACK_FONT = "Times New Roman"

# Commercial faces are not in the table above and never will be, so an unknown
# name is resolved to the base family it most resembles. The name is the better
# evidence: the serif bit in a PDF's font descriptor is set by whatever produced
# the file and is often simply wrong — Gill Sans and Mundo Sans both claim to be
# serifed in the textbooks this was tested against.
_SANS_MARKERS = (
    "SANS",
    "GOTHIC",
    "GROTESK",
    "GROTESQUE",
    "ARIAL",
    "HELVETICA",
    "GILL",
    "FRUTIGER",
    "MYRIAD",
    "FUTURA",
    "VERDANA",
    "TAHOMA",
    "CALIBRI",
    "SEGOE",
    "UNIVERS",
    "AVENIR",
    "OPTIMA",
    "ROBOTO",
    "LATO",
)
_MONO_MARKERS = ("MONO", "COURIER", "CONSOLAS", "TYPEWRITER", "INCONSOLATA", "MENLO")

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_STYLE_SUFFIX_RE = re.compile(
    r"(?:[-,_ ]?(?:PS|MT|PSMT|Regular|Book|Roman|Bold|Italic|Oblique|Light|Medium|"
    r"SemiBold|DemiBold|ExtraBold|Black|Heavy|Thin|Condensed|Narrow))+$",
    re.I,
)
_TRAILING_DIGITS_RE = re.compile(r"\d+$")

# Typesetters set the letters of an expression in a maths font but leave its
# punctuation in the body font: `u(x,t)` is CMMI `u`, roman `(`, CMMI `x` … So a
# span made only of these characters, wedged between two maths spans, belongs to
# the equation rather than to the prose around it.
_MATH_BRIDGE_RE = re.compile(r"^[\s()\[\]{}<>,.;:!|/+\-=*0-9'`^_]*$")

# Shortest run of maths characters worth treating as an equation, and the
# shortest typical run before a page's maths fonts stop meaning "equation".
_MIN_MATH_CHARS = 3
_MIN_MEDIAN_MATH_CHARS = 2

# Words long enough to be prose rather than variables, and how many of them turn
# a candidate equation back into a line of text.
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{4,}")
_MAX_PROSE_WORDS = 3


def _substitute(name: str, flags: int) -> str:
    """The base family an unresolvable face should be rendered in."""
    upper = name.upper()
    if flags & _MONO or any(marker in upper for marker in _MONO_MARKERS):
        return "Courier New"
    if any(marker in upper for marker in _SANS_MARKERS):
        return "Arial"
    return _FALLBACK_FONT


def normalise_font(raw: str, flags: int = 0) -> str:
    """Turn a PDF font name into something Word can resolve.

    `ABCDEF+TimesNewRomanPS-BoldMT` -> `Times New Roman`; `CMR10` -> `Times New
    Roman`; `PalatinoLTPro-Roman`, which nobody has, -> the closest base family.
    """
    name = _SUBSET_RE.sub("", (raw or "").strip())
    if not name:
        return _FALLBACK_FONT
    if not settings.map_fonts:
        return name

    base = re.split(r"[-,]", name)[0]
    stem = _TRAILING_DIGITS_RE.sub("", base)

    mapped = _FONT_MAP.get(stem.upper()) or _FONT_MAP.get(base.upper())
    if mapped:
        return mapped

    cleaned = _STYLE_SUFFIX_RE.sub("", base) or base
    cleaned = _TRAILING_DIGITS_RE.sub("", cleaned) or cleaned
    mapped = _FONT_MAP.get(cleaned.upper())
    if mapped:
        return mapped

    # Handing Word a name it cannot resolve is how a document gets silently
    # restyled: the reader substitutes a face of its own choosing and its own
    # widths, so every word after the first on a line walks out of place — and
    # the widths the run is compensated with are wrong too, because they are
    # measured against a font that is not the one being drawn.
    return _substitute(name, flags)


def is_math_font(raw: str) -> bool:
    upper = (raw or "").upper()
    return any(marker in upper for marker in MATH_FONT_MARKERS)


def _font_stem(raw: str) -> str:
    base = re.split(r"[-,]", _SUBSET_RE.sub("", (raw or "").strip()))[0]
    return _TRAILING_DIGITS_RE.sub("", base).upper()


def is_graphic_font(raw: str) -> bool:
    return _font_stem(raw) in GRAPHIC_FONT_STEMS


def is_renderable(text: str) -> bool:
    """False for glyph runs that carry no real characters (NULs, control codes)."""
    return any(character.isprintable() and not character.isspace() for character in text)


def _hex_colour(value: int | None) -> str:
    if not isinstance(value, int):
        return "000000"
    return f"{value & 0xFFFFFF:06X}"


def _hex_from_floats(components) -> str | None:
    if not components:
        return None
    try:
        values = list(components)
    except TypeError:
        return None
    if len(values) == 1:
        values = values * 3
    if len(values) < 3:
        return None
    return "".join(f"{int(max(0.0, min(1.0, float(v))) * 255):02X}" for v in values[:3])


# --------------------------------------------------------------------------- #
# Layout model
# --------------------------------------------------------------------------- #

Box = tuple[float, float, float, float]


@dataclass
class Span:
    text: str
    font: str
    size: float
    colour: str
    bold: bool
    italic: bool
    superscript: bool
    math: bool
    bbox: Box
    math_id: int | None = None
    subscript: bool = False
    baseline: float = 0.0  # y of the line the glyphs actually sit on


@dataclass
class TextLine:
    bbox: Box
    spans: list[Span]
    math_id: int | None = None  # set when the whole line is one display equation

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)


@dataclass
class ImageItem:
    """An embedded raster, or a rasterised crop of vector artwork."""

    bbox: Box
    data: bytes
    ext: str = "png"


@dataclass
class RuleItem:
    """A hairline: table ruling, underline, border, divider."""

    bbox: Box
    colour: str = "000000"


@dataclass
class MathItem:
    bbox: Box
    image: bytes  # crop of the equation — the LaTeX source, and the fallback
    display: bool = True
    latex: str | None = None
    # Set when the transcription turned out not to be mathematics; the writer
    # skips the item and the text underneath it is drawn instead.
    dropped: bool = False


@dataclass
class TableItem:
    bbox: Box
    rows: list[list[str]]
    col_x: list[float]
    row_y: list[float]


@dataclass
class PageLayout:
    number: int
    width: float
    height: float
    scanned: bool = False
    lines: list[TextLine] = field(default_factory=list)
    images: list[ImageItem] = field(default_factory=list)
    rules: list[RuleItem] = field(default_factory=list)
    maths: list[MathItem] = field(default_factory=list)
    tables: list[TableItem] = field(default_factory=list)
    page_image: bytes | None = None  # whole-page raster, for scanned pages
    # A scanned page has no text layer to extract, so its content arrives as the
    # Markdown a vision model read off the raster. It is kept as Markdown — with
    # its headings, emphasis and `$maths$` intact — rather than being flattened
    # into lines, because the mark-up is the only structure the page has.
    markdown: str = ""

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text.strip())

    @property
    def content(self) -> str:
        """This page as Markdown, whichever path produced it."""
        return self.markdown or self.text

    def release_math(self, math_id: int) -> None:
        """Hand an equation back to the text path.

        Detection runs on fonts alone and has to decide before anything has been
        read, so some crops turn out to hold no mathematics. The words were never
        thrown away — the lines and spans are still in the model, only marked as
        belonging to an equation — so clearing the marks is all it takes to have
        them drawn as ordinary positioned text again.
        """
        self.maths[math_id].dropped = True
        for line in self.lines:
            if line.math_id == math_id:
                line.math_id = None
            for span in line.spans:
                if span.math_id == math_id:
                    span.math_id = None


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #


def _rect(box) -> fitz.Rect:
    return fitz.Rect(box)


def _area(rect: fitz.Rect) -> float:
    return max(0.0, rect.width) * max(0.0, rect.height)


def _overlap_ratio(inner: fitz.Rect, outer: fitz.Rect) -> float:
    """How much of `inner` lies inside `outer`, 0..1."""
    inner_area = _area(inner)
    if inner_area <= 0:
        return 0.0
    return _area(inner & outer) / inner_area


def _cluster(rects: list[fitz.Rect], gap: float = 6.0, passes: int = 12) -> list[fitz.Rect]:
    """Merge rectangles that touch or nearly touch into connected regions."""
    boxes = list(rects)
    for _ in range(passes):
        merged: list[fitz.Rect] = []
        changed = False
        for box in boxes:
            probe = fitz.Rect(box)
            probe += (-gap, -gap, gap, gap)
            for index, existing in enumerate(merged):
                if probe.intersects(existing):
                    merged[index] = existing | box
                    changed = True
                    break
            else:
                merged.append(fitz.Rect(box))
        boxes = merged
        if not changed:
            break
    return boxes


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

_MAX_PATHS = 1500  # complex vector pages: stop clustering rather than stall
_RULE_THICKNESS = 3.0  # pt; thinner than this is a line, not a shape
_AXIS_TOLERANCE = 0.3  # pt of slope allowed before a "line" counts as diagonal
_DIAGRAM_TEXT_RATIO = 0.35  # above this, a cluster is text with decoration, not art
_DIAGRAM_PAGE_RATIO = 0.70  # a cluster this large is a border/background, not art
_GLYPH_MARK_PT = 24.0  # a drawn mark no bigger than this is a character, not a diagram


def render_size(page_width: float, page_height: float, dpi: int) -> tuple[int, int]:
    """The pixel size `_render` gives a whole page at `dpi`.

    Kept beside `_render` so the zoom is written down once. Callers use it to
    tell a model's pixel coordinates apart from grid ones, which only needs the
    size to within a rounding step of the pixmap's own.
    """
    zoom = dpi / 72.0
    return round(page_width * zoom), round(page_height * zoom)


def _render(page: fitz.Page, clip: fitz.Rect | None, dpi: int, alpha: bool = False) -> bytes:
    """Rasterise a region of the page.

    Artwork is drawn on a transparent backdrop: it is padded slightly so strokes
    are not sliced, and an opaque white margin would paint over the neighbouring
    word.

    Equation crops are not, and must not be. Where a page is transparent its
    colour channels are left at zero, so anything that composites the crop onto
    black — or simply ignores the alpha channel, which is what a vision model
    does — sees black glyphs on black. The crop reads as an empty rectangle, the
    model has nothing to transcribe, and every equation in the document falls
    back to its picture. On a white page an opaque white backdrop is invisible
    anyway.

    Every crop records the resolution it was rendered at. These ones are placed
    at a width taken from their own bounding box rather than at their natural
    size, so nothing here depends on it today — but PyMuPDF's default of 96 DPI
    is simply untrue, and a crop that misreports its size is a trap for the next
    caller that trusts it.
    """
    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=alpha)
    pixmap.set_dpi(dpi, dpi)
    return pixmap.tobytes("png")


def _table_is_trustworthy(rows: list[list[str]]) -> bool:
    """Reject detections that did not really resolve the grid.

    A table ruled only at its outer edge comes back with several visual rows
    crammed into one cell, newlines and all. Rendering that as a Word table would
    silently restructure the page, so such a detection is dropped and the cells
    are left to the ordinary positioned-text path, which stays exact.
    """
    if len(rows) < 2 or max(len(row) for row in rows) < 2:
        return False
    # A grid with a single filled cell is not a table anyone wrote: it is a
    # chart's axes and gridlines, read as ruling, with every tick label swept
    # into whichever cell they happened to fall in.
    if sum(1 for row in rows for cell in row if cell) < 2:
        return False
    return not any("\n" in cell for row in rows for cell in row)


def _collect_tables(page: fitz.Page) -> list[TableItem]:
    try:
        found = page.find_tables()
    except Exception:
        return []

    tables: list[TableItem] = []
    for table in getattr(found, "tables", []):
        try:
            rows = [[xml_safe(cell or "").strip() for cell in row] for row in table.extract()]
        except Exception:
            continue
        if not rows or not any(any(cell for cell in row) for row in rows):
            continue
        if not _table_is_trustworthy(rows):
            continue

        bbox = tuple(fitz.Rect(table.bbox))
        col_x: list[float] = []
        row_y: list[float] = []
        # `cells` is a flat list of per-cell rects (None where cells are merged);
        # the distinct edges give real column widths and row heights.
        for cell in getattr(table, "cells", []) or []:
            if not cell:
                continue
            rect = fitz.Rect(cell)
            col_x.extend([round(rect.x0, 1), round(rect.x1, 1)])
            row_y.extend([round(rect.y0, 1), round(rect.y1, 1)])
        tables.append(
            TableItem(
                bbox=bbox,
                rows=rows,
                col_x=sorted(set(col_x)),
                row_y=sorted(set(row_y)),
            )
        )
    return tables


# Artwork smaller than this is a fraction bar or a bracket piece, not a figure,
# and must never pull the text around it into a picture.
_FIGURE_MIN_SIDE = 24.0
# How far outside a figure a label may sit and still belong to it.
_LABEL_REACH = 7.0
# A line wider than this share of the page is prose, whatever it is next to.
_LABEL_MAX_WIDTH = 0.33
# And so is anything longer than a label ever is.
_LABEL_MAX_CHARS = 24
# A figure may not grow past this share of the page in either direction.
_FIGURE_MAX_WIDTH, _FIGURE_MAX_HEIGHT = 0.85, 0.7

_CAPTION_RE = re.compile(r"^\(?(?:fig(?:ure)?|table|chart|graph|diagram)\b", re.IGNORECASE)


def _is_figure(rect: fitz.Rect) -> bool:
    return rect.width >= _FIGURE_MIN_SIDE and rect.height >= _FIGURE_MIN_SIDE


def _absorb_labels(
    rect: fitz.Rect, text_lines: list[tuple[fitz.Rect, str]], page_rect: fitz.Rect
) -> fitz.Rect:
    """Grow a figure over the labels that are part of it.

    The extent of vector artwork is the extent of its strokes, so the words
    drawn among them — axis names, arrow annotations, the units on a vector —
    hang off the edges. Whatever is left outside is not merely missing from the
    picture: it is read back as body text, and an answer key acquires a
    paragraph that says `3 ms`.

    A caption is deliberately left out. It is a sentence about the figure, not a
    part of it, and it should stay text that can be edited and searched.
    """
    grown = fitz.Rect(rect)
    limit = fitz.Rect(
        0, 0, _FIGURE_MAX_WIDTH * page_rect.width, _FIGURE_MAX_HEIGHT * page_rect.height
    )
    for _ in range(4):
        reach = grown + (-_LABEL_REACH, -_LABEL_REACH, _LABEL_REACH, _LABEL_REACH)
        widened = False
        for line, text in text_lines:
            if not text or _CAPTION_RE.match(text):
                continue
            if line.width > _LABEL_MAX_WIDTH * page_rect.width:
                continue  # a line of prose that merely runs past the figure
            if len(text) > _LABEL_MAX_CHARS:
                continue
            if not reach.intersects(line) or _overlap_ratio(line, grown) > 0.99:
                continue
            # A label hugs the drawing. A line of the body column that happens to
            # reach under it does not, and swallowing it would take a sentence
            # out of the document and bake it into a picture.
            centre = 0.5 * (line.x0 + line.x1)
            if not grown.x0 - _LABEL_REACH <= centre <= grown.x1 + _LABEL_REACH:
                continue
            candidate = grown | line
            if candidate.width > limit.width or candidate.height > limit.height:
                continue
            grown, widened = candidate, True
        if not widened:
            break
    return grown & page_rect


def _clear_of_text(rect: fitz.Rect, text_lines: list[tuple[fitz.Rect, str]]) -> fitz.Rect:
    """Pull a crop back off any line of text it does not contain.

    A figure and the sentence introducing it can be a fraction of a point apart,
    and a crop that reaches over the gap rasterises the bottom of that sentence
    — which is then printed twice: once as the text it is, and once as a sliver
    of grey along the top of the picture.
    """
    trimmed = fitz.Rect(rect)
    for line, text in text_lines:
        if not text or not trimmed.intersects(line):
            continue
        if _overlap_ratio(line, trimmed) > 0.75:
            continue  # a label the figure has taken in
        if min(line.x1, trimmed.x1) - max(line.x0, trimmed.x0) <= 0:
            continue
        centre = 0.5 * (line.y0 + line.y1)
        if centre <= trimmed.y0:
            trimmed.y0 = max(trimmed.y0, line.y1)
        elif centre >= trimmed.y1:
            trimmed.y1 = min(trimmed.y1, line.y0)
    return trimmed if trimmed.height > 1.0 else fitz.Rect(rect)


def _is_axis_aligned(path: dict) -> bool:
    """Is every stroke in this path horizontal or vertical?

    A rule is reproduced in Word as a filled rectangle covering its bounding box,
    which is faithful only when the ink really does fill that box. A diagonal has
    a thin bounding box too — the hook of a radical sign, the leg of a triangle,
    the stem of an arrowhead — and filling it paints a solid black lozenge where
    the page had a stroke. The √ in front of an expression is the usual casualty.
    """
    items = path.get("items") or []
    if not items:
        return False
    for item in items:
        kind = item[0]
        if kind == "re":
            continue  # an axis-aligned rectangle by construction
        if kind != "l":
            return False  # a curve or a quad is artwork, whatever shape its box is
        start, end = item[1], item[2]
        if (
            abs(start.x - end.x) > _AXIS_TOLERANCE
            and abs(start.y - end.y) > _AXIS_TOLERANCE
        ):
            return False
    return True


def _collect_vectors(
    page: fitz.Page, page_rect: fitz.Rect, text_rects: list[fitz.Rect], owned_rects: list[fitz.Rect]
) -> tuple[list[RuleItem], list[fitz.Rect]]:
    """Split vector artwork into hairline rules and diagram-shaped clusters.

    Rules are re-drawn as Word shapes, so they never conflict with the text on top
    of them. Everything else is a candidate for rasterising.
    """
    try:
        paths = page.get_drawings()
    except Exception:
        return [], []

    rules: list[RuleItem] = []
    shapes: list[fitz.Rect] = []
    # Glyph-sized marks that are drawn rather than set: radicals, arrowheads,
    # hand-built braces. They sit inside a line of text, so the "mostly text"
    # test below would throw them away along with the character they are.
    marks: list[fitz.Rect] = []
    for path in paths[:_MAX_PATHS]:
        rect = fitz.Rect(path.get("rect") or (0, 0, 0, 0))
        # A stroked line has no area — its bbox is the centre line and the visible
        # thickness comes from the pen. Inflate it, or every rule on the page
        # (table ruling, fraction bars, underlines) is discarded as empty.
        stroke = float(path.get("width") or 0.0) if path.get("type") != "f" else 0.0
        if stroke > 0 or rect.width == 0 or rect.height == 0:
            pen = max(stroke, 0.4) / 2.0
            rect += (-pen, -pen, pen, pen)
        rect = rect & page_rect
        if rect.is_empty or max(rect.width, rect.height) < 1.0:
            continue
        short, long = min(rect.width, rect.height), max(rect.width, rect.height)
        if short <= _RULE_THICKNESS and long >= 2.0 and _is_axis_aligned(path):
            colour = _hex_from_floats(path.get("fill")) or _hex_from_floats(path.get("color"))
            rules.append(RuleItem(bbox=tuple(rect), colour=colour or "000000"))
        else:
            shapes.append(rect)
            if max(rect.width, rect.height) <= _GLYPH_MARK_PT:
                marks.append(rect)

    page_area = _area(page_rect)
    diagrams: list[fitz.Rect] = []
    for cluster in _cluster(shapes):
        if any(_overlap_ratio(cluster, owned) > 0.8 for owned in owned_rects):
            continue  # a table or diagram renderer already covers this region
        if _area(cluster) > _DIAGRAM_PAGE_RATIO * page_area:
            continue  # page border or background wash — keep the text instead
        covered = sum(
            _area(text & cluster) for text in text_rects if _overlap_ratio(text, cluster) > 0.5
        )
        if _area(cluster) > 0 and covered / _area(cluster) > _DIAGRAM_TEXT_RATIO:
            # Mostly text with some decoration; rules already carry the lines —
            # unless the cluster is itself no bigger than a character and was
            # drawn with strokes no rule can stand in for. Then it *is* the
            # character, and it overlaps text precisely because it sits in a
            # word: a √ shares its box with the number underneath it.
            glyph_mark = max(cluster.width, cluster.height) <= _GLYPH_MARK_PT and any(
                cluster.intersects(mark) for mark in marks
            )
            if not glyph_mark:
                continue
        diagrams.append(cluster)
    return rules, diagrams


# A script is set smaller than the text it rides on, and far enough off its
# baseline to be unmistakable.
_SCRIPT_SIZE_RATIO = 0.86
_SCRIPT_MIN_RATIO = 0.4
_SCRIPT_SHIFT_RATIO = 0.12
_SCRIPT_MAX_SHIFT = 0.9


def mark_scripts(spans: list[Span]) -> None:
    """Recover super- and subscripts from where the glyphs sit.

    The flag a PDF can carry for this is almost never set — a typesetter writes
    `ms^-1` by choosing a smaller size and moving the pen up, and says nothing
    about what it meant. Read literally the exponent lands on the baseline, so a
    document of units and powers comes out as `ms-1` throughout. The intent is
    in the geometry: smaller than the line's own type, and off its baseline.
    """
    body = [span for span in spans if span.text.strip()]
    if len(body) < 2:
        return
    largest = max(span.size for span in body)
    baselines = [span.baseline for span in body if span.size > _SCRIPT_SIZE_RATIO * largest]
    if not baselines:
        return
    baseline = statistics.median(baselines)
    for span in body:
        if not _SCRIPT_MIN_RATIO * largest <= span.size <= _SCRIPT_SIZE_RATIO * largest:
            continue
        shift = span.baseline - baseline
        if abs(shift) > _SCRIPT_MAX_SHIFT * largest:
            continue  # a whole line away: two rows the extractor ran together
        if shift < -_SCRIPT_SHIFT_RATIO * largest:
            span.superscript, span.subscript = True, False
        elif shift > _SCRIPT_SHIFT_RATIO * largest:
            span.superscript, span.subscript = False, True


def _build_spans(raw_spans: list[dict], offset: fitz.Point) -> list[Span]:
    spans: list[Span] = []
    for raw in raw_spans:
        text = xml_safe(raw.get("text") or "")
        font = raw.get("font") or ""
        # Symbol-encoded glyphs are decoded first, before anything is allowed to
        # judge the text. Their private-use code points report themselves as
        # unprintable, so a span of nothing but mathematics — `π + θ` — looks to
        # the check below exactly like a run of control characters and is thrown
        # away entirely.
        decoded = decode_symbol_pua(text)
        symbolic = decoded != text
        text = decoded
        # Safety net: drawing glyphs or control-character runs that escaped the
        # artwork clustering must never be written out as text.
        if not text or is_graphic_font(font) or not is_renderable(text):
            continue
        rect = fitz.Rect(raw["bbox"]) - fitz.Rect(offset.x, offset.y, offset.x, offset.y)
        flags = int(raw.get("flags") or 0)
        spans.append(
            Span(
                text=text,
                # A recovered symbol is only as good as the font asked to draw
                # it: the text faces have no glyph for a bracket piece, so the
                # span would come out blank all over again.
                font=SYMBOL_FONT if symbolic else normalise_font(font, flags),
                size=round(float(raw.get("size") or 11.0), 2),
                colour=_hex_colour(raw.get("color")),
                bold=bool(flags & _BOLD),
                italic=bool(flags & _ITALIC),
                # The flag the extractor supplies is not a record of what the
                # document says: it is set for anything sitting above the line's
                # dominant baseline — the whole of a sentence that shares a line
                # with the low half of a fraction — and left clear for the raised
                # `-1` of a unit. `mark_scripts` decides this from the geometry.
                superscript=False,
                math=is_math_font(font),
                bbox=tuple(rect),
                baseline=float((raw.get("origin") or (0.0, rect.y1))[1]) - offset.y,
            )
        )
    mark_scripts(spans)

    # Typesetters position words independently, so adjacent spans often carry no
    # separating space at all. Recover it from the horizontal gap, or every line
    # comes out as onelongrunofwords.
    for previous, following in zip(spans, spans[1:]):
        if previous.text.endswith(" ") or following.text.startswith(" "):
            continue
        gap = following.bbox[0] - previous.bbox[2]
        if gap > 0.18 * max(previous.size, 1.0):
            previous.text += " "
    return spans


def _bridge_math_spans(line: TextLine) -> None:
    """Absorb the punctuation that a typesetter left in the body font.

    Without this every bracket and comma splits an expression, and `u(x,t)` is
    cropped as three unreadable slivers instead of one equation.
    """
    spans = line.spans
    if not any(span.math for span in spans):
        return

    def bridgeable(index: int) -> bool:
        return not spans[index].math and bool(_MATH_BRIDGE_RE.match(spans[index].text))

    # Punctuation with maths on both sides is interior to the expression.
    for index in range(1, len(spans) - 1):
        if bridgeable(index) and spans[index - 1].math and spans[index + 1].math:
            spans[index].math = True

    # Then take in delimiters butted directly against an expression's edge, so a
    # closing bracket joins `u(x, t` instead of being stranded outside it. The
    # gap must be near zero — that is what stops ordinary sentence punctuation
    # further along the line from being swallowed.
    for index in range(len(spans)):
        if not bridgeable(index):
            continue
        touches_left = (
            index > 0
            and spans[index - 1].math
            and (spans[index].bbox[0] - spans[index - 1].bbox[2]) <= 0.35 * max(spans[index].size, 1.0)
        )
        touches_right = (
            index + 1 < len(spans)
            and spans[index + 1].math
            and (spans[index + 1].bbox[0] - spans[index].bbox[2]) <= 0.35 * max(spans[index].size, 1.0)
        )
        if touches_left or touches_right:
            spans[index].math = True


def _math_runs(line: TextLine) -> list[str]:
    """The contiguous stretches of maths-font characters in a line."""
    runs: list[str] = []
    index = 0
    while index < len(line.spans):
        if not line.spans[index].math:
            index += 1
            continue
        start = index
        while index < len(line.spans) and line.spans[index].math:
            index += 1
        runs.append("".join("".join(span.text.split()) for span in line.spans[start:index]))
    return runs


def _signal_is_trustworthy(lines: list[TextLine]) -> bool:
    """Do this page's maths fonts mark expressions, or only symbols?

    A TeX document sets whole expressions in maths fonts, so the runs are long.
    A publisher sets the expression in the body font and reaches for a symbol
    font only for the operators and the odd Greek letter, so every `=` and `λ`
    on the page looks like an equation of its own while the expression around it
    looks like prose. Cropping those replaces correctly positioned text with
    oversized fragments of an equation editor's idea of the same thing — and
    bills for a model call each. When the typical run is a single glyph, the
    fonts are describing decoration, and the page is better left as text.
    """
    lengths = [len(run) for line in lines for run in _math_runs(line)]
    if not lengths:
        return False
    return statistics.median(lengths) >= _MIN_MEDIAN_MATH_CHARS


def _is_expression(text: str) -> bool:
    """Is this run of maths characters worth handing to the equation editor?

    Publishers set equations in the body font and reach for a symbol font only
    for the operators, so the maths-font test alone finds `=`, `⇒`, `λ` on their
    own — hundreds of them in a worked-solutions book. There is no structure in a
    lone symbol for OMML to express and Word draws it perfectly well as text, so
    cropping one buys nothing and costs a model call. An expression has an
    operand as well as an operator.
    """
    dense = "".join(text.split())
    return len(dense) >= _MIN_MATH_CHARS and any(character.isalnum() for character in dense)


def _looks_like_prose(text: str) -> bool:
    """Is this run of "maths" actually a line of words?

    Font evidence alone cannot separate a display equation from a heading or a
    sentence set in a face whose name happens to carry a maths marker, and the
    display test — most of the line's width is in a maths font — says yes to both.
    Getting it wrong is expensive twice over: the line is cropped and billed for,
    and it comes back as an equation object where the document had text, which is
    no longer editable as text. Variables run to a letter or two, so several long
    words in a row are the giveaway.
    """
    return len(_PROSE_WORD_RE.findall(text)) >= _MAX_PROSE_WORDS


def _mark_maths(page: fitz.Page, lines: list[TextLine], offset: fitz.Point) -> list[MathItem]:
    """Find equations among the extracted lines and crop each one.

    A line whose spans are predominantly set in a maths font is a display
    equation; a shorter run of maths spans inside ordinary prose is inline maths.
    Consecutive display lines are merged so multi-line equations stay one object.
    """
    maths: list[MathItem] = []
    display_runs: list[list[TextLine]] = []
    current: list[TextLine] = []

    def crop(bbox: Box, display: bool) -> int:
        # A little air so glyphs are not clipped. The stored box must match the
        # rendered one, or the picture is squeezed into the wrong rectangle.
        # Tight horizontally so a padded crop does not reach into the next word;
        # looser vertically, where tall glyphs and rules need the room.
        padded = fitz.Rect(bbox) + (-0.5, -1.5, 0.5, 1.5)
        rect = padded + fitz.Rect(offset.x, offset.y, offset.x, offset.y)
        maths.append(
            MathItem(
                bbox=tuple(padded),
                image=_render(page, rect, settings.math_dpi),
                display=display,
            )
        )
        return len(maths) - 1

    for line in lines:
        _bridge_math_spans(line)

    if not _signal_is_trustworthy(lines):
        return []

    for line in lines:
        width = sum(span.bbox[2] - span.bbox[0] for span in line.spans) or 1.0
        math_width = sum(
            span.bbox[2] - span.bbox[0] for span in line.spans if span.math
        )
        if line.spans and math_width / width > 0.6:
            current.append(line)
            continue
        if current:
            display_runs.append(current)
            current = []

        # Inline maths: contiguous runs of maths spans inside a prose line.
        index = 0
        while index < len(line.spans):
            if not line.spans[index].math:
                index += 1
                continue
            start = index
            while index < len(line.spans) and line.spans[index].math:
                index += 1
            group = line.spans[start:index]
            phrase = "".join(span.text for span in group)
            if not _is_expression(phrase) or _looks_like_prose(phrase):
                continue  # a bare operator, or words; it stays as text
            rect = fitz.Rect(group[0].bbox)
            for span in group[1:]:
                rect |= fitz.Rect(span.bbox)
            math_id = crop(tuple(rect), display=False)
            for span in group:
                span.math_id = math_id
    if current:
        display_runs.append(current)

    # A display run is not held to `_is_expression`: a line that is mostly maths
    # is an equation even when it is the one-glyph tail of one, and the page-level
    # check above has already excluded documents where that signal means nothing.
    # It is held to the prose test, because that signal cannot tell an equation
    # from a sentence that happens to be set in the same face.
    for run in display_runs:
        if _looks_like_prose("".join(line.text for line in run)):
            continue
        rect = fitz.Rect(run[0].bbox)
        for line in run[1:]:
            rect |= fitz.Rect(line.bbox)
        math_id = crop(tuple(rect), display=True)
        for line in run:
            line.math_id = math_id

    return maths


# --------------------------------------------------------------------------- #
# Equation detection by geometry
# --------------------------------------------------------------------------- #

# Two marks survive into the text layer whatever produced the PDF: a symbol font,
# which is where every ≥, θ and √ comes from, and italic, which is how variables
# have been set since the practice was invented. Neither needs a font to be
# *named* helpfully, which is what the font-marker test depends on and what
# commercial typesetting never provides.
_MAX_VARIABLE_CHARS = 3  # longer than this, an italic run is emphasis, not a variable
_MAX_FRACTION_BAR_PT = 200.0  # wider than this, a rule is a divider, not a fraction bar
_FRACTION_REACH_PT = 10.0  # how far above and below a bar its operands may sit
_FRACTION_OVERHANG_PT = 4.0  # how far an operand may stick out past the bar's ends
_MAX_RADICAL_BAR_PT = 80.0  # a radical's overbar is short; a table rule is not
_RADICAL_HOOK_REACH = 8.0  # how far left of the bar its drawn hook may sit
_SIDE_BY_SIDE_GAP_PT = 3.0  # vertical gap two parts of one expression may have
_SIDE_BY_SIDE_OVERLAP = 0.25  # above this they are stacked, so separate equations
_MATH_GAP_X = 7.0  # horizontal gap that still counts as one expression
# Vertically an expression barely reaches at all. Stacking is licensed by the
# fraction bar, which brings its numerator and denominator in as one rect, so
# nothing else needs the reach — and any reach cascades: a region grows onto the
# fragment below, which lets it reach the one below that, and by the foot of the
# page one "equation" has swallowed six lines of prose. Consecutive display
# equations can be set less than a point apart, which is the budget here.
_MATH_GAP_Y = 0.5
# Artwork this size, drawn beside an expression and on its rows, is part of it:
# a radical over its radicand, an extensible bracket, a stacked fraction the
# extractor could only describe as strokes. Larger than this it is a figure.
_MATH_ART_MAX_W = 260.0
_MATH_ART_MAX_H = 72.0


def _is_math_span(span: Span) -> bool:
    if span.font == SYMBOL_FONT:
        return True
    return span.italic and len(span.text.strip()) <= _MAX_VARIABLE_CHARS


def _seed_rects(lines: list[TextLine]) -> list[fitz.Rect]:
    """Where on the page there is evidence of mathematics.

    A line with no prose on it is seeded whole. Half an expression carries no
    evidence of its own — `sin`, a bracket and a digit are just characters — and
    seeding only the symbols would leave `v ≥ u sin θ` as two lone glyphs too
    small to be worth cropping, so a standalone equation would come out as text
    while the equation below it came out as an object. Inside a sentence only the
    symbols are seeded, because there the prose really is prose.
    """
    seeds: list[fitz.Rect] = []
    for line in lines:
        marks = [span for span in line.spans if _is_math_span(span)]
        if not marks:
            continue
        if _is_fragment(line.text):
            seeds.append(fitz.Rect(line.bbox))
        else:
            seeds.extend(fitz.Rect(span.bbox) for span in marks)
    return seeds


def _fraction_bars(
    rules: list[RuleItem], lines: list[TextLine], artwork: list[fitz.Rect]
) -> list[fitz.Rect]:
    """Rules with text stacked directly above and below them.

    A fraction is the one part of an expression that survives into the text layer
    as pure geometry: a numerator, a hairline and a denominator, none of which
    know they belong together. Finding the bar is what lets the three be cropped
    as one thing rather than read as three unrelated fragments.
    """
    bars: list[fitz.Rect] = []
    for rule in rules:
        rect = fitz.Rect(rule.bbox)
        if rect.height > _RULE_THICKNESS or not (3.0 <= rect.width <= _MAX_FRACTION_BAR_PT):
            continue
        above: list[fitz.Rect] = []
        below: list[fitz.Rect] = []
        for line in lines:
            for span in line.spans:
                box = fitz.Rect(span.bbox)
                # An operand sits *within* the bar — the bar is drawn to span the
                # wider of the two. Merely overlapping it is not enough: the
                # sentence on the row below overlaps a small bar too, and taking
                # that in makes the "fraction" as wide as the page and swallows
                # everything on it. Spans rather than lines, because an inline
                # fraction shares its rows with the sentence it sits in — the
                # numerator is one span of a line that runs the full column.
                if (
                    box.x0 < rect.x0 - _FRACTION_OVERHANG_PT
                    or box.x1 > rect.x1 + _FRACTION_OVERHANG_PT
                ):
                    continue
                # By edge where the operand keeps to its own side of the bar, and
                # by centre where it does not. A numerator set inside a sentence
                # is raised type in a full-size box: the glyph is above the bar
                # but the box it arrives in reaches below it, so measured edge to
                # edge the "1" of `1/6` is neither above nor below, the fraction
                # is not found, and the "6" is left as a paragraph of its own.
                middle = 0.5 * (box.y0 + box.y1)
                if 0.0 <= rect.y0 - box.y1 <= _FRACTION_REACH_PT:
                    above.append(box)
                elif 0.0 <= box.y0 - rect.y1 <= _FRACTION_REACH_PT:
                    below.append(box)
                elif 0.0 <= rect.y0 - middle <= _FRACTION_REACH_PT:
                    above.append(box)
                elif 0.0 <= middle - rect.y1 <= _FRACTION_REACH_PT:
                    below.append(box)
        if below and not above and _radical_hook(rect, artwork):
            # A radical's overbar has a radicand under it and nothing over it, and
            # the hook at its left end is drawn rather than set. Without this the
            # radicand is a row of its own and √(g/ℓ) comes out as two equations.
            above = []
        elif not (above and below):
            continue
        span = fitz.Rect(rect)
        for box in above + below:
            span |= box
        bars.append(span)
    return bars


def _radical_hook(bar: fitz.Rect, artwork: list[fitz.Rect]) -> bool:
    """Is there a drawn mark at this bar's left end, of the size a √ hook is?"""
    if bar.width > _MAX_RADICAL_BAR_PT:
        return False
    for art in artwork:
        if max(art.width, art.height) > _MAX_RADICAL_BAR_PT:
            continue
        if abs(art.x1 - bar.x0) <= _RADICAL_HOOK_REACH and art.y1 >= bar.y0 - 1.0:
            return True
    return False


def _cluster_xy(rects: list[fitz.Rect], gap_x: float, gap_y: float, passes: int = 12):
    """Merge rectangles that are close, with separate horizontal and vertical reach.

    An expression is much wider than it is tall, and its pieces sit closer
    vertically — a numerator to its denominator — than the lines of a paragraph
    do. One scalar gap cannot express that: wide enough to join `u` to `v` across
    a fraction bar, it also joins the equation to the sentence beside it.
    """
    boxes = list(rects)
    for _ in range(passes):
        merged: list[fitz.Rect] = []
        changed = False
        for box in boxes:
            probe = fitz.Rect(box) + (-gap_x, -gap_y, gap_x, gap_y)
            for index, existing in enumerate(merged):
                if probe.intersects(existing):
                    merged[index] = existing | box
                    changed = True
                    break
            else:
                merged.append(fitz.Rect(box))
        boxes = merged
        if not changed:
            break
    return boxes


def _is_fragment(text: str) -> bool:
    """Is this line a piece of an expression rather than a phrase of prose?"""
    return not _PROSE_WORD_RE.search(text)


def _absorb_fragments(region: fitz.Rect, lines: list[TextLine], gap_x: float) -> fitz.Rect:
    """Grow a region over the loose fragments an expression scatters around itself.

    Superscripts, stray brackets and the lone digits of a coefficient arrive as
    their own lines. They carry no evidence of being mathematics — a `2` is a `2`
    — so they are recognised by their company: on the region's own rows, beside
    it, and too short to be prose. The row test is what stops the growth running
    away: a fragment may only be taken in if the region already reaches its rows,
    so absorbing one can never open the door to the line beneath it.
    """
    grown = fitz.Rect(region)
    for _ in range(6):
        before = fitz.Rect(grown)
        for line in lines:
            box = fitz.Rect(line.bbox)
            if not _is_fragment(line.text):
                continue
            if box.y0 >= grown.y1 or box.y1 <= grown.y0:
                continue  # different rows
            if box.x0 - grown.x1 <= gap_x and grown.x0 - box.x1 <= gap_x:
                grown |= box
        if grown == before:
            break
    return grown


def _absorb_artwork(
    region: fitz.Rect, artwork: list[fitz.Rect], absorbed: set[int], gap_x: float
) -> fitz.Rect:
    """Grow a region over the strokes its expression is partly drawn in.

    A radical is a hook and an overbar, not a glyph, so `√(R²+r²−Rr)` reaches the
    extractor as vector artwork with the radicand hidden underneath it. Left as
    artwork it becomes a picture of its own, placed on the line above the
    equation it is half of — and the equation, cut off at the radical, ends at
    its own `=`. Only artwork on the region's own rows and within reach of its
    edge is taken, so a figure beside an equation is still a figure.
    """
    grown = fitz.Rect(region)
    for _ in range(4):
        before = fitz.Rect(grown)
        for index, art in enumerate(artwork):
            if art.width > _MATH_ART_MAX_W or art.height > _MATH_ART_MAX_H:
                continue
            if art.y0 >= grown.y1 or art.y1 <= grown.y0:
                continue  # different rows
            if art.x0 - grown.x1 <= gap_x and grown.x0 - art.x1 <= gap_x:
                grown |= art
                absorbed.add(index)
        if grown == before:
            break
    return grown


def _mark_maths_geometric(
    page: fitz.Page,
    lines: list[TextLine],
    rules: list[RuleItem],
    artwork: list[fitz.Rect],
    offset: fitz.Point,
) -> list[MathItem]:
    """Find equations from how the page is set rather than from what fonts claim.

    Seeded on symbol glyphs, italic variables and fraction bars, grown over the
    fragments around each seed, then split into the expressions that stand alone
    on a line and the ones embedded in a sentence.
    """
    bars = _fraction_bars(rules, lines, artwork)
    seeds = _seed_rects(lines) + bars
    if not seeds:
        return []

    absorbed: set[int] = set()
    regions = [
        _absorb_artwork(
            _absorb_fragments(region, lines, _MATH_GAP_X), artwork, absorbed, _MATH_GAP_X
        )
        for region in _cluster_xy(seeds, _MATH_GAP_X, _MATH_GAP_Y)
    ]
    # Absorbing can bring two regions into genuine overlap; settle those before
    # cropping. No reach here — this pass only unites what already intersects,
    # and a point of slack is enough to weld one equation onto the next.
    regions = _cluster_xy(regions, 0.0, 0.0)
    regions = _join_side_by_side(regions, lines)
    # Joining two regions can draw a rectangle round a third — a tall radical
    # welded to the expression beside it encloses the fragment between them —
    # and the enclosed one would then be cropped, and printed, a second time.
    regions = _drop_contained(regions)

    maths: list[MathItem] = []
    for region in sorted(regions, key=lambda r: (round(r.y0, 1), r.x0)):
        if any(
            index not in absorbed and _overlap_ratio(region, art) > 0.6
            for index, art in enumerate(artwork)
        ):
            continue  # a diagram already draws this; its labels are not equations
        stacked = any(_overlap_ratio(bar, region) > 0.8 for bar in bars)
        if not _worth_cropping(region, lines, stacked):
            continue
        display = _is_display_region(region, lines)
        padded = region + (-1.0, -1.5, 1.0, 1.5)
        maths.append(
            MathItem(
                bbox=tuple(padded),
                image=_render(
                    page,
                    padded + fitz.Rect(offset.x, offset.y, offset.x, offset.y),
                    settings.math_dpi,
                ),
                display=display,
            )
        )
        math_id = len(maths) - 1
        for line in lines:
            inside = [
                span for span in line.spans if _overlap_ratio(fitz.Rect(span.bbox), region) > 0.5
            ]
            if not inside:
                continue
            for span in inside:
                span.math_id = math_id
            if display and len(inside) == len(line.spans):
                line.math_id = math_id
    return maths


def _swallows_prose(
    union: fitz.Rect, first: fitz.Rect, second: fitz.Rect, lines: list[TextLine]
) -> bool:
    """Would joining these two regions enclose a word that was in neither?

    Two regions on adjacent rows can be side by side and still have a sentence
    between them — the union is a rectangle, and a rectangle drawn round both
    corners of a page covers everything in between.
    """
    for line in lines:
        for span in line.spans:
            if not _PROSE_WORD_RE.search(span.text):
                continue
            box = fitz.Rect(span.bbox)
            if _overlap_ratio(box, union) <= 0.5:
                continue
            if _overlap_ratio(box, first) > 0.5 or _overlap_ratio(box, second) > 0.5:
                continue
            return True
    return False


def _join_side_by_side(regions: list[fitz.Rect], lines: list[TextLine]) -> list[fitz.Rect]:
    """Reunite the parts of one expression that sit beside each other.

    A tall part — a radical, a stacked fraction, a big operator — is put on a row
    of its own by the extractor, so `V₀ ≈ √(ℓg)` arrives as two regions on
    adjacent rows. Two *separate* display equations are also on adjacent rows,
    and the way to tell them apart is horizontal: consecutive equations both
    start at the left margin and so overlap almost entirely, while the parts of
    one expression stand next to each other and hardly overlap at all.
    """
    boxes = list(regions)
    for _ in range(6):
        merged: list[fitz.Rect] = []
        changed = False
        for box in boxes:
            for index, existing in enumerate(merged):
                gap = max(box.y0 - existing.y1, existing.y0 - box.y1)
                if gap > _SIDE_BY_SIDE_GAP_PT:
                    continue
                shared = min(box.x1, existing.x1) - max(box.x0, existing.x0)
                narrower = min(box.width, existing.width)
                if narrower <= 0 or shared / narrower > _SIDE_BY_SIDE_OVERLAP:
                    continue  # stacked, not beside: two equations, not one
                union = existing | box
                if _swallows_prose(union, existing, box, lines):
                    continue
                merged[index] = union
                changed = True
                break
            else:
                merged.append(fitz.Rect(box))
        boxes = merged
        if not changed:
            break
    return boxes


def _drop_contained(regions: list[fitz.Rect], share: float = 0.8) -> list[fitz.Rect]:
    """Keep only the outermost of a nest of regions."""
    kept: list[fitz.Rect] = []
    for region in sorted(regions, key=_area, reverse=True):
        if any(_overlap_ratio(region, other) > share for other in kept):
            continue
        kept.append(region)
    return kept


def _is_display_region(region: fitz.Rect, lines: list[TextLine]) -> bool:
    """Does this expression have its rows to itself, or is it inside a sentence?"""
    for line in lines:
        box = fitz.Rect(line.bbox)
        if box.y1 <= region.y0 or box.y0 >= region.y1:
            continue  # different rows entirely
        for span in line.spans:
            if _overlap_ratio(fitz.Rect(span.bbox), region) > 0.5:
                continue  # part of the expression
            if _PROSE_WORD_RE.search(span.text):
                return False
    return True


def _worth_cropping(region: fitz.Rect, lines: list[TextLine], stacked: bool = False) -> bool:
    """Is there enough here for an equation editor to express?

    A lone italic `v` in a sentence is a variable, and Word draws it perfectly
    well as an italic letter. Cropping it costs a model call and replaces
    editable text with an equation object, which is the trade this mode exists to
    avoid making unnecessarily.

    A fraction is the exception. `u/v` is two characters, and read as text it is
    the word "uv" — the one thing on the page whose meaning is carried entirely
    by an arrangement that flowing text cannot reproduce.
    """
    text = "".join(
        span.text
        for line in lines
        for span in line.spans
        if _overlap_ratio(fitz.Rect(span.bbox), region) > 0.5
    )
    dense = "".join(text.split())
    minimum = 2 if stacked else _MIN_MATH_CHARS
    return len(dense) >= minimum and any(c.isalnum() for c in dense)


def extract_page(
    doc: fitz.Document,
    index: int,
    detect_math: bool = True,
    math_strategy: str = "font",
) -> PageLayout:
    """Read page `index` into a positioned layout model."""
    page = doc.load_page(index)
    page_rect = page.rect
    offset = fitz.Point(page_rect.x0, page_rect.y0)
    shift = fitz.Rect(offset.x, offset.y, offset.x, offset.y)
    local_rect = fitz.Rect(0, 0, page_rect.width, page_rect.height)

    layout = PageLayout(
        number=index + 1,
        width=round(page_rect.width, 2),
        height=round(page_rect.height, 2),
    )

    raw = page.get_text("dict")
    text_blocks = [b for b in raw.get("blocks", []) if b.get("type") == 0]
    image_blocks = [b for b in raw.get("blocks", []) if b.get("type") == 1]

    has_text = any(
        (span.get("text") or "").strip()
        for block in text_blocks
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    )
    if not has_text:
        # Nothing to extract — the page is a scan. Keep an exact raster of it and
        # let the caller fall back to the vision model for the words.
        layout.scanned = True
        layout.page_image = _render(page, None, settings.dpi)
        return layout

    # Candidate text rectangles are needed before vectors, so that a cluster can
    # be judged as artwork or as decorated text.
    text_lines = [
        (
            fitz.Rect(line["bbox"]),
            "".join(span.get("text") or "" for span in line["spans"]).strip(),
        )
        for block in text_blocks
        for line in block.get("lines", [])
        if line.get("spans")
    ]
    text_rects = [rect for rect, _ in text_lines]

    # Glyph-drawn artwork has to be identified before anything else: it looks like
    # text to the extractor but must end up as a picture.
    graphic_rects = _cluster(
        [
            fitz.Rect(span["bbox"])
            for block in text_blocks
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if is_graphic_font(span.get("font") or "")
        ],
        gap=12.0,
    )
    graphic_rects = [rect for rect in graphic_rects if _area(rect) > 4.0]

    # Vectors are read before the tables are trusted, not after. A graph is ruled
    # like a table — an axis is a line, and so is a gridline — so `find_tables`
    # answers with a grid of empty cells where the plot was; the plotted curves
    # themselves are the one thing it cannot see. Reading the drawing first means
    # a "table" the artwork is painted across can be recognised for what it is,
    # instead of taking ownership of the region and suppressing the picture.
    layout.tables = _collect_tables(page)
    rules, diagram_rects = _collect_vectors(page, page_rect, text_rects, graphic_rects)
    layout.tables = [
        table
        for table in layout.tables
        if not any(
            _overlap_ratio(fitz.Rect(table.bbox), diagram) > 0.5 for diagram in diagram_rects
        )
    ]
    table_rects = [fitz.Rect(table.bbox) for table in layout.tables]
    diagram_rects = [
        diagram
        for diagram in diagram_rects
        if not any(_overlap_ratio(diagram, table) > 0.8 for table in table_rects)
    ] + graphic_rects
    # A figure's labels are ordinary text sitting on top of the drawing, and the
    # drawing's extent is measured from its strokes alone — so `3 ms` and `√3`
    # fall outside it. Grow each figure over the labels that belong to it before
    # anything else looks at these rectangles: they decide what is rasterised,
    # what is left as text, and what is allowed to be an equation.
    diagram_rects = _cluster(
        [
            _absorb_labels(rect, text_lines, page_rect) if _is_figure(rect) else fitz.Rect(rect)
            for rect in diagram_rects
        ],
        gap=0.0,
    )

    def inside_any(rect: fitz.Rect, regions: list[fitz.Rect]) -> bool:
        return any(_overlap_ratio(rect, region) > 0.75 for region in regions)

    # Rules that a table or diagram already draws would be doubled up.
    layout.rules = [
        RuleItem(bbox=tuple(fitz.Rect(rule.bbox) - shift), colour=rule.colour)
        for rule in rules
        if not inside_any(fitz.Rect(rule.bbox), table_rects + diagram_rects)
    ]

    for rect in diagram_rects:
        # A little margin so labels sitting just outside the artwork come with it.
        clipped = _clear_of_text((fitz.Rect(rect) + (-3.0, -3.0, 3.0, 3.0)) & page_rect, text_lines)
        if clipped.is_empty:
            continue
        layout.images.append(
            ImageItem(
                bbox=tuple(clipped - shift),
                data=_render(page, clipped, settings.diagram_dpi, alpha=True),
            )
        )

    for block in image_blocks:
        rect = fitz.Rect(block["bbox"]) & page_rect
        if rect.is_empty or inside_any(rect, diagram_rects):
            continue  # a diagram crop already contains this image
        data = block.get("image")
        if not data:
            continue
        layout.images.append(
            ImageItem(
                bbox=tuple(rect - shift),
                data=data,
                ext=(block.get("ext") or "png"),
            )
        )

    skip_regions = table_rects + diagram_rects
    for block in text_blocks:
        for line in block.get("lines", []):
            spans = _build_spans(line.get("spans", []), offset)
            if not spans or not any(span.text.strip() for span in spans):
                continue
            if inside_any(fitz.Rect(line["bbox"]), skip_regions):
                continue
            rect = (fitz.Rect(line["bbox"]) - shift) & local_rect
            if rect.is_empty:
                continue
            layout.lines.append(TextLine(bbox=tuple(rect), spans=spans))

    # Tables were extracted from the page, so their coordinates need shifting too.
    layout.tables = [
        TableItem(
            bbox=tuple(fitz.Rect(table.bbox) - shift),
            rows=table.rows,
            col_x=[x - offset.x for x in table.col_x],
            row_y=[y - offset.y for y in table.row_y],
        )
        for table in layout.tables
    ]

    if detect_math:
        if math_strategy == "geometry":
            layout.maths = _mark_maths_geometric(
                page,
                layout.lines,
                layout.rules,
                [fitz.Rect(image.bbox) for image in layout.images],
                offset,
            )
        else:
            layout.maths = _mark_maths(page, layout.lines, offset)
        # Fraction bars and radical strokes belong to the equation: whether it
        # ends up as OMML or as a picture, it draws its own. Leaving them would
        # stamp a second bar across it.
        math_rects = [fitz.Rect(item.bbox) for item in layout.maths]
        layout.rules = [
            rule
            for rule in layout.rules
            if not any(_overlap_ratio(fitz.Rect(rule.bbox), region) > 0.6 for region in math_rects)
        ]

    return layout
