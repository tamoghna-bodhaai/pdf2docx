"""Fit Mathpix's DOCX to the measure it is actually laid out in.

Mathpix's extraction is not the problem this module solves, and it deliberately
does not touch it: no text, no maths, no table content, no reading order, and
not one of its image crops is altered. What is altered is the *geometry* Mathpix
writes around that content, because Mathpix states it in absolute units that
stop being true the moment the document is read at any measure other than the
one it assumed. Oversized derivations are the one structural exception: they are
subdivided only at complete top-level relation runs and remain one editable OMML
equation with aligned rows.

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
  * **Oversized derivations arrive as one unbroken expression.** ``m:brkBin``
    is set and ``m:wrapIndent`` is a full inch, but the OMML carries neither
    structural rows nor usable break opportunities, so a long equation runs off
    a narrow measure.

Each of those is the same mistake — an absolute number where a relative one
belongs — and each stops being survivable the instant the document is narrowed,
which is why putting a Mathpix .docx into two columns breaks everything at once
rather than breaking something new.

The repair is correspondingly narrow. Images are restored to the size they
occupied on the source page, which is *recoverable rather than guessable*:
``lines.json`` reports the pixel dimensions of each rendered page, the PDF
reports the same page in points, and the ratio of the two is the resolution
Mathpix cropped at. Tables are restated in percentages of whatever measure they
find themselves in. Genuinely oversized derivations become aligned equation-array
rows without splitting nested mathematical objects; a soft break is retained as
a fallback for safe flat expressions that cannot be represented structurally.
Nothing here invents a layout; it re-expresses Mathpix's own one in units that
survive being resized.

Several further repairs are not about the measure at all. Every empty maths
argument Mathpix writes — the alignment cells of a matrix, the missing half of a
one-sided script — is filled with a zero-width space, because Word draws nothing
for an empty ``<m:e/>`` while LibreOffice draws its missing-operand placeholder
and the same file reads with an inverted question mark in every matrix row. The
body size is restated everywhere, because Mathpix names a size in
``docDefaults``, names none at all on its 20 000 maths runs, and then hard-codes
21pt on 124 heading runs since ``styles.xml`` defines no heading style to carry
it. Inline maths keeps that body size; display maths and its controls are stated
one point smaller. Explicit sizes stop being an inheritance a reader has to
resolve. This works in Word, but not in LibreOffice, whose importer builds each
equation into a Formula object and draws it at the Math module's own fixed base
size, ignoring ``w:sz`` on the run, on the paragraph mark and in
``docDefaults`` alike; no .docx states that size, so there the only lever is
which size the rest of the document is set at. Centered reading matter and
display equations are also normalized to the left while standalone diagrams,
tables and existing paragraph indents retain their visual structure. A worked
step's
connective — a lone ``⇒`` or ``∴`` sitting on its own line above the equation it
introduces — is joined to that equation, because Mathpix groups it as a line of
its own in some places and inside the equation in others, and the second of
those is what a derivation looks like. And the page breaks Mathpix writes at
every source-page boundary — one dedicated ``<w:br w:type="page"/>`` paragraph
apiece — are pruned wherever one starts a page with nothing on it: two in a
row, one trailing the last of the content, or one standing in front of a
paragraph that already carries ``<w:pageBreakBefore/>``. Every reader paginates
an explicit break the same way, so those are the blank pages in the delivered
file, and only whole empty paragraphs are removed to be rid of them.

And when the document is asked for in columns, the section is restated as the page
the source was laid out on, read out of ``lines.json``: at the source book's own
column width essentially nothing overflows, because nothing overflowed in the
book. That is the difference between a document narrowed by hand and one laid
out where its content already fitted.

The one number in that geometry that is chosen rather than measured is the side
margin, which every section is given at half an inch. A scanned book's own left
and right margins are a binding allowance, and reproducing them on a screen
spends an inch and a half of measure on white space that the figures and
equations overflowing the column would rather have. It is set before the measure
is read, so everything else in this module fits to it.

The typeface is chosen the same way. Mathpix sets the prose in Georgia and the
2 400 runs inside the equations in Cambria Math, so a variable named in a
sentence is not the same letter as the variable in the display equation two
lines below it. Every face the document names is restated as one — Cambria Math,
which is a text face with an OpenType MATH table rather than a symbol font, so
the algebra keeps the metrics Word lays it out with and the prose is set in it
too. It is restated in the runs, in ``docDefaults``, in the styles and in
``numbering.xml``, where a list names the face it counts up in and nowhere else.
The theme names go with the faces: Word reads ``w:hAnsiTheme`` instead of the
attribute beside it, and Mathpix's package ships no theme part for it to
resolve. Two things are left: a Symbol or Wingdings run, whose character is a
glyph picked out of that font's own encoding rather than a letter wearing a
face, and ``m:mathFont``, which selects the MATH table rather than a face. And
as with the size, LibreOffice draws each equation with the Math module's own
font whatever the file says; in Word the document is one face throughout.

Mathpix's file is never modified in place. ``mathpix/document.docx`` remains the
bytes Mathpix returned, and the fitted document is written beside it, so any
defect can still be attributed to whichever of the two produced it.
"""

from __future__ import annotations

import html
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
STYLES = "word/styles.xml"
NUMBERING = "word/numbering.xml"

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

# A structural row has to carry enough expression on both sides of a break to
# remain a line rather than an orphaned operator or tail. This is deliberately
# independent of the overall threshold above: the former asks whether an
# equation is oversized, while this asks whether a proposed subdivision is
# readable.
MIN_MATH_ROW_CHARS = 30

# Where a long equation may be broken. These are relations and the top-level
# connectives that read as relations; breaking before one is what a typesetter
# would do and what `m:brkBin w:val="before"` already tells Word to expect.
# More than this many breaks in one equation stops being a wrap and starts
# being a re-typesetting of it, which is not this module's business.
MAX_MATH_BREAKS = 6

MATH_BREAK_TOKENS = ("=", "≠", "≤", "≥", "<", ">", "≈", "≡", "⇒", "⇔", "→", "∴", "±")
MATH_RPR_ORDER = ("m:aln", "m:brk", "m:lit", "m:nor", "m:scr", "m:sty")
# Direct children the OMML schema permits in an equation argument. An unknown
# extension is safe to leave in place and soft-break around, but not to move into
# a newly constructed equation-array row whose content model is known.
MATH_ARG_ELEMENTS = {
    "m:argPr", "m:acc", "m:bar", "m:borderBox", "m:box", "m:d", "m:f",
    "m:func", "m:groupChr", "m:limLow", "m:limUpp", "m:nary", "m:phant",
    "m:rad", "m:r", "m:sPre", "m:sSub", "m:sSubSup", "m:sSup",
}

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

# The tokens a worked step is introduced by. Mathpix sometimes writes one of
# these as a paragraph of its own, immediately above the display equation it
# introduces, and sometimes writes it inside the equation — ``\therefore \quad
# CA = a`` lands as one ``m:oMathPara`` in the same file. The second is what a
# derivation looks like on paper, so the first is joined to it. Kept short and
# closed deliberately: anything longer is a sentence, and a sentence introducing
# an equation is prose that belongs on its own line.
STEP_CONNECTIVES = (
    "⇒", "⇔", "⟹", "∴", "∵", "→", "or", "and", "i.e.", "i.e.,",
)
# The longest of those, so a paragraph is measured before it is read.
MAX_CONNECTIVE_CHARS = max(len(token) for token in STEP_CONNECTIVES)


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

# `w:rPr` has the same fixed sequence, and it is the one an inserted size has to
# respect: `w:sz` sits after `w:position` and before `w:highlight`, and `w:szCs`
# immediately follows `w:sz`, which is why the pair is inserted as one blob.
RPR_ORDER = (
    "w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs", "w:caps",
    "w:smallCaps", "w:strike", "w:dstrike", "w:outline", "w:shadow", "w:emboss",
    "w:imprint", "w:noProof", "w:snapToGrid", "w:vanish", "w:webHidden",
    "w:color", "w:spacing", "w:w", "w:kern", "w:position", "w:sz", "w:szCs",
    "w:highlight", "w:u", "w:effect", "w:bdr", "w:shd", "w:fitText",
    "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
    "w:specVanish", "w:oMath",
)

PPR_ORDER = (
    "w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore", "w:framePr",
    "w:widowControl", "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd",
    "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
    "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
    "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
    "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
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
MATHPARAPR_ORDER = ("m:jc", "m:ctrlPr")

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
    steps_joined: int = 0
    # Redundant Mathpix page breaks removed, one per blank page no longer shown.
    blank_pages_pruned: int = 0
    # Literal Mathpix LaTeX heading commands turned into Word headings.
    headings_repaired: int = 0
    # Every size the document states, plus every maths run given one.
    sizes_restated: int = 0
    # The size it was all stated at; 0 when sizing was turned off.
    font_points: float = 0.0
    # The size used for display maths; 0 when sizing was turned off.
    display_equation_points: float = 0.0
    # Every typeface the document named, restated as one.
    fonts_restated: int = 0
    # The face it was all stated in; "" when the fonts were left alone.
    font_name: str = ""
    # The side margin every section was given; 0 when they were left alone.
    side_margin_inches: float = 0.0
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
            "steps_joined": self.steps_joined,
            "blank_pages_pruned": self.blank_pages_pruned,
            "headings_repaired": self.headings_repaired,
            "sizes_restated": self.sizes_restated,
            "font_points": self.font_points or None,
            "display_equation_points": self.display_equation_points or None,
            "fonts_restated": self.fonts_restated,
            "font_name": self.font_name or None,
            "side_margin_inches": round(self.side_margin_inches, 2) or None,
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


def _set_margin_attr(markup: bytes, name: bytes, twips: int) -> bytes:
    """Restate one attribute of a ``w:pgMar``, adding it if it is not there."""
    pattern = re.compile(rb"\b" + re.escape(name) + rb'="[^"]*"')
    stated = f'{name.decode()}="{twips}"'.encode()
    restated, found = pattern.subn(stated, markup)
    if found:
        return restated
    return markup[: -len(b"/>")] + b" " + stated + b"/>"


def _set_side_margins(document: bytes, inches: float) -> tuple[bytes, int]:
    """Give every section the same left and right margin.

    The side margins are the one part of the geometry that is a decision rather
    than a measurement. Everything else this module writes is recovered from the
    source — the page it was printed on, the columns it was set in, the size its
    figures occupied — but how much white space to leave beside the text is a
    property of the document being made, not of the book it came from, and a
    scanned page's own margins are usually the printer's binding allowance
    rather than anything worth reproducing on screen.

    So this runs last of the geometry passes and has the final word, over
    Mathpix's margins and over the ones ``_set_section`` derived alike. It runs
    before the measure is read, so the width it leaves is the width every image,
    table and equation is then fitted to.

    The gutter goes to zero with it. Word adds the gutter to the binding edge on
    top of the margin, so a section that states one does not have the margin it
    was just given.
    """
    twips = _twips(inches)
    edits: list[tuple[int, int, bytes]] = []
    changed = 0
    for section in re.finditer(rb"<w:sectPr[ >].*?</w:sectPr>", document, re.S):
        body = section.group(0)
        inner_start = body.find(b">") + 1
        inner_end = body.rfind(b"</w:sectPr>")
        if inner_start <= 0 or inner_end < inner_start:
            continue
        inner = body[inner_start:inner_end]
        base = section.start() + inner_start

        margin = re.search(rb"<w:pgMar\b[^>]*?/>", inner)
        if margin is None:
            # A section stating no margins at all is read at the importer's own
            # default, which is not this one. Top and bottom are left at the
            # inch that default already is, because only the sides were asked
            # for.
            markup = (
                f'<w:pgMar w:top="{TWIPS_PER_INCH}" w:right="{twips}"'
                f' w:bottom="{TWIPS_PER_INCH}" w:left="{twips}"'
                f' w:header="720" w:footer="720" w:gutter="0"/>'
            ).encode()
            edits.append((_insert_offset(base, inner, "w:pgMar", SECTPR_ORDER), 0, markup))
            changed += 1
            continue

        restated = margin.group(0)
        for name, value in ((b"w:left", twips), (b"w:right", twips), (b"w:gutter", 0)):
            restated = _set_margin_attr(restated, name, value)
        if restated != margin.group(0):
            edits.append((base + margin.start(), len(margin.group(0)), restated))
            changed += 1
    return _apply(document, edits), changed


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

    Only matrix cells, equation-array rows and a whole equation are treated
    this way. A leading ``−`` inside ``<m:d>`` is the sign of the number it
    precedes, not a subtraction missing its left-hand side, and giving it an
    operand would turn ``(−b)`` into ``( − b)``.

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
            if (
                name == "m:e" and parent in ("m:mr", "m:eqArr")
            ) or name == "m:oMath":
                if _needs_an_operand(document[opened:match.start()]):
                    edits.append((opened, 0, MATH_OPERAND))
            continue

        stack.append((name, match.end()))

    return _apply(document, edits), len(edits)


def _left_align_paragraphs(document: bytes) -> tuple[bytes, int]:
    """Left-align centred text paragraphs while retaining visual centring.

    A blanket replacement also changes table justification and the paragraph
    that positions a standalone diagram. This pass only visits a paragraph's
    own properties, and regards a drawing-only paragraph as visual content
    whose centring is intentional. Text, headings, captions, solution steps and
    display equations are ordinary reading matter and normalize to the left.
    No indentation property is added, removed or rewritten.
    """
    edits: list[tuple[int, int, bytes]] = []

    for paragraph in PARAGRAPH_RE.finditer(document):
        body = paragraph.group(0)
        has_figure = b"<w:drawing" in body or b"<w:pict" in body
        has_text = bool(_paragraph_text(body).strip())
        if has_figure and not has_text and b"<m:oMath" not in body:
            continue

        properties = re.search(
            rb"<w:pPr\b[^>]*>.*?</w:pPr>|<w:pPr\b[^>]*/>", body, re.S
        )
        if properties is None:
            continue
        justification = re.search(rb"<w:jc\b([^>]*)/>", properties.group(0))
        if justification is None or _attr(justification.group(1), "w:val") != "center":
            continue
        tag = justification.group(0)
        replacement = re.sub(
            rb'(\bw:val\s*=\s*)(["\x27])center\2',
            lambda match: match.group(1) + match.group(2) + b"left" + match.group(2),
            tag,
            count=1,
        )
        at = paragraph.start() + properties.start() + justification.start()
        edits.append((at, len(tag), replacement))

    return _apply(document, edits), len(edits)


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


# --- one stated type size, everywhere ------------------------------------------

SIZE_RE = re.compile(rb"<w:(sz|szCs)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)/>")
# A `w:sz` with nothing following it is a size for Latin text and no size for
# complex-script text, which then keeps whatever it inherited. The two travel
# together or the repair is half done.
UNPAIRED_SIZE_RE = re.compile(rb"(<w:sz\b[^>]*/>)(?!\s*<w:szCs\b)")


def _size_markup(half_points: int) -> bytes:
    """The pair, as one blob: `w:szCs` immediately follows `w:sz` in the schema."""
    return (
        f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/>'
    ).encode()


def _restate_sizes(part: bytes, half_points: int) -> tuple[bytes, int]:
    """Rewrite every type size a part states to the one size the document is set in.

    This is not a shrink. The document already resolves to one size for its body
    text; what it does not do is *say* so anywhere a reader might look, and the
    124 heading runs that do say something say 21pt as direct formatting because
    ``styles.xml`` defines no heading styles to say it for them. Restating both
    the defaults and those runs leaves a document whose size is a fact rather
    than an inheritance, and headings keep their ``w:b`` to remain headings.
    """
    changed = 0

    def restate(match: "re.Match[bytes]") -> bytes:
        nonlocal changed
        if _int_attr(match.group(2), "w:val") == half_points:
            return match.group(0)
        changed += 1
        return f'<w:{match.group(1).decode()} w:val="{half_points}"/>'.encode()

    part = SIZE_RE.sub(restate, part)
    part, paired = UNPAIRED_SIZE_RE.subn(
        rb"\1" + f'<w:szCs w:val="{half_points}"/>'.encode(), part
    )
    return part, changed + paired


def _rpr_size_edit(offset: int, body: bytes, half_points: int) -> tuple[int, int, bytes]:
    """Where a size goes inside a `w:rPr` that has none, and what goes there."""
    return (
        _insert_offset(offset, body, "w:sz", RPR_ORDER),
        0,
        _size_markup(half_points),
    )


def _size_math_runs(
    document: bytes, body_half_points: int, display_half_points: int
) -> tuple[bytes, int]:
    """State body size on inline maths and the reduced size on display maths.

    Not one of the document's ``<m:r>`` carries a ``w:sz``. Word reads the size
    off ``docDefaults`` and draws the equation at the size of the text around it.
    LibreOffice imports each ``m:oMath`` as an embedded Formula object, which has
    no ``docDefaults`` to read, finds the runs silent, and falls back to the Math
    module's own base size — so the equation sits in a visibly larger box than
    the sentence that introduces it.

    Saying the size on the run itself keeps inline maths at the size of the
    sentence around it. Runs inside ``m:oMathPara`` use the one-point-smaller
    display size, as do ``m:ctrlPr`` controls for fraction bars, brackets and
    radicals. LibreOffice is not reachable this way: measured against 24.2, its
    importer ignores ``w:sz`` on the maths run, on the paragraph mark and in
    ``docDefaults``, and draws every formula at the Math module's fixed base
    size — so there the document's own size is the only thing that can be made to
    agree with it.


    The insertion is ordered, not prepended: ``CT_R`` sequences ``m:rPr`` before
    ``w:rPr`` before ``m:t``, and a ``w:rPr`` written ahead of the ``m:rPr`` that
    is already there makes the part invalid and Word refuses to open it.
    """
    edits: list[tuple[int, int, bytes]] = []
    stack: list[tuple[str, int]] = []

    for match in TAG_RE.finditer(document):
        name = match.group(2).decode("ascii", "replace")
        # As in `_fill_math_gaps`: only the maths vocabulary is tracked, so the
        # `w:` properties nested inside it cannot unbalance the stack.
        if not name.startswith("m:"):
            continue

        if match.group(4) == b"/":
            if name in ("m:r", "m:ctrlPr"):
                half_points = (
                    display_half_points
                    if any(parent == "m:oMathPara" for parent, _ in stack)
                    else body_half_points
                )
                edits.append((
                    match.start(),
                    match.end() - match.start(),
                    f"<{name}>".encode()
                    + b"<w:rPr>" + _size_markup(half_points) + b"</w:rPr>"
                    + f"</{name}>".encode(),
                ))
            continue

        if match.group(1) == b"/":
            if not stack or stack[-1][0] != name:
                continue
            _, opened = stack.pop()
            if name in ("m:r", "m:ctrlPr"):
                half_points = (
                    display_half_points
                    if any(parent == "m:oMathPara" for parent, _ in stack)
                    else body_half_points
                )
                edits += _math_run_size_edits(
                    opened, document[opened:match.start()], half_points
                )
            continue

        stack.append((name, match.end()))

    return _apply(document, edits), len(edits)


def _math_run_size_edits(
    offset: int, body: bytes, half_points: int
) -> list[tuple[int, int, bytes]]:
    """Size one `m:r` or `m:ctrlPr`, whatever properties it already carries.

    Neither element nests inside itself and neither holds anything that could
    contain a second ``w:rPr``, so the first one found in the body is the run's
    own.
    """
    found = re.search(rb"<w:rPr\b[^>]*(/)?>", body)
    if found is not None:
        if found.group(1) == b"/":
            # `<w:rPr/>` states nothing and cannot be inserted into.
            return [(
                offset + found.start(),
                found.end() - found.start(),
                b"<w:rPr>" + _size_markup(half_points) + b"</w:rPr>",
            )]
        closing = body.find(b"</w:rPr>", found.end())
        if closing == -1:
            return []
        inner = body[found.end():closing]
        if b"<w:sz" in inner:
            restated, changed = _restate_sizes(inner, half_points)
            if changed:
                return [(offset + found.end(), len(inner), restated)]
            return []
        return [_rpr_size_edit(offset + found.end(), inner, half_points)]

    # No properties at all: the whole element goes in, after any `m:rPr`.
    at = 0
    props = re.match(rb"<m:rPr\b[^>]*(/)?>", body)
    if props is not None:
        at = props.end()
        if props.group(1) != b"/":
            closing = body.find(b"</m:rPr>", props.end())
            if closing == -1:
                return []
            at = closing + len(b"</m:rPr>")
    return [(offset + at, 0, b"<w:rPr>" + _size_markup(half_points) + b"</w:rPr>")]


# --- one typeface, stated everywhere the document names one --------------------

# The four scripts a run can name a face for. Word picks between them per
# character, so a document that states only `w:ascii` changes face at the first
# character that is not one.
FONT_ATTRS = ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs")
# The whole element is replaced rather than its attributes rewritten, which is
# what takes `w:asciiTheme` and its siblings out with it. A theme name is read
# *instead of* the explicit attribute beside it, so a font restated around one is
# overruled by whatever that theme resolves to — in a package with no theme part,
# as Mathpix's is, the importer's own default rather than anything the document
# asked for.
RFONTS_RE = re.compile(rb"<w:rFonts\b[^>]*/>")
# The faces that are not typefaces. A run naming one of these is holding a glyph
# picked out of that font's own encoding — a list's bullet is U+F0B7 in Symbol
# and nothing at all anywhere else — so restating it does not change the letter's
# face, it changes the character.
SYMBOL_FACES = ("symbol", "wingdings", "webdings", "marlett")


def _xml_attr(value: str) -> str:
    """A font name as it can appear inside a double-quoted attribute."""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )


def _fonts_markup(name: str) -> bytes:
    stated = " ".join(f'{attr}="{_xml_attr(name)}"' for attr in FONT_ATTRS)
    return f"<w:rFonts {stated}/>".encode()


def _restate_fonts(part: bytes, name: str) -> tuple[bytes, int]:
    """Rewrite every typeface a part names to the one the document is set in.

    Mathpix writes two: Georgia for text and Cambria Math for the 2 400 runs
    inside the equations. That is a real difference in an exported document —
    the prose and the algebra in it are set in different faces, and a variable
    named in a sentence does not look like the same variable in the display
    equation two lines below it. Stating one face everywhere makes them the same
    letter.

    The theme names go with it. `w:hAnsiTheme` and its siblings are read
    *instead of* the explicit attribute beside them, so a run restated without
    dropping them keeps the face the theme resolves to — and Mathpix's package
    ships no theme part at all, which is why its `docDefaults` names a face for
    ASCII and leaves everything else to a theme that is not there.

    Only what the part already names is rewritten; a run that names nothing
    inherits, and what it inherits is `docDefaults`, which this restates too.
    A run naming a symbol face is left alone — see `SYMBOL_FACES`.
    """
    changed = 0
    stated = _fonts_markup(name)

    def restate(match: "re.Match[bytes]") -> bytes:
        nonlocal changed
        named = _attr(match.group(0), "w:ascii") or _attr(match.group(0), "w:hAnsi")
        if (named or "").strip().lower().startswith(SYMBOL_FACES):
            return match.group(0)
        if match.group(0) == stated:
            return match.group(0)
        changed += 1
        return stated

    return RFONTS_RE.sub(restate, part), changed


def _set_default_font(styles: bytes, name: str) -> tuple[bytes, int]:
    """Restate the styles, and give `docDefaults` a face if it names none.

    Everything a run does not say for itself is resolved here, so a `docDefaults`
    with no `w:rFonts` leaves the whole document set in whatever the importer
    defaults to.
    """
    styles, changed = _restate_fonts(styles, name)
    found = re.search(rb"<w:rPrDefault>\s*<w:rPr\b[^>]*?>(.*?)</w:rPr>", styles, re.S)
    if found is None or b"<w:rFonts" in found.group(1):
        return styles, changed
    at = _insert_offset(found.start(1), found.group(1), "w:rFonts", RPR_ORDER)
    return styles[:at] + _fonts_markup(name) + styles[at:], changed + 1


# --- a step's connective, on the line of the equation it introduces -------------

MATH_PARA_RE = re.compile(rb"<m:oMathPara\b[^>]*>.*?</m:oMathPara>", re.S)
PARAGRAPH_RE = re.compile(rb"<w:p\b[^>]*?>.*?</w:p>|<w:p\b[^>]*?/>", re.S)
SOFT_BREAK = b'<w:br w:type="textWrapping"/>'
MATH_PARA_JC = b'<m:oMathParaPr><m:jc m:val="left"/></m:oMathParaPr>'


def _paragraph_text(body: bytes) -> str:
    found = re.findall(rb"<w:t[^>]*>(.*?)</w:t>", body, re.S)
    return b"".join(found).decode("utf-8", "replace")


# --- literal LaTeX headings emitted as prose -----------------------------------

RUN_RE = re.compile(rb"<w:r\b[^>]*>.*?</w:r>", re.S)
BREAK_RE = re.compile(rb"<w:(?:br|cr)\b[^>]*/>")
RUN_PROPERTIES_RE = re.compile(
    rb"\s*(?:<w:rPr\b[^>]*>.*?</w:rPr>|<w:rPr\b[^>]*/>)\s*", re.S
)
PARAGRAPH_PROPERTIES_RE = re.compile(
    rb"\s*(<w:pPr\b[^>]*>.*?</w:pPr>|<w:pPr\b[^>]*/>)", re.S
)
HEADING_COMMAND_RE = re.compile(r"\\(title|section|subsection)(\*)?\{")
HEADING_BREAK = b"<w:r><w:br/></w:r>"
HEADING_SPACING = b"<w:spacing w:after=\"220\"/>"
HEADING_JUSTIFICATION = b"<w:jc w:val=\"left\"/>"


def _latex_heading(text: str) -> tuple[str, str] | None:
    """Parse one complete, unambiguous heading command.

    Braces may only occur when escaped, and only LaTeX escapes whose plain-text
    meaning is certain are accepted. That closed grammar is intentional: an
    unknown command in a title is still source text, not permission to guess at
    how it should render.
    """
    value = text.strip()
    opened = HEADING_COMMAND_RE.match(value)
    if opened is None or (opened.group(1) == "title" and opened.group(2)):
        return None

    title: list[str] = []
    index = opened.end()
    escapes = {char: char for char in "#$%&_{}\\"}
    while index < len(value):
        char = value[index]
        if char == "}":
            if index != len(value) - 1:
                return None
            plain = "".join(title).strip()
            return (opened.group(1), plain) if plain else None
        if char == "{":
            return None
        if char == "\\":
            index += 1
            if index >= len(value) or value[index] not in escapes:
                return None
            title.append(escapes[value[index]])
        else:
            title.append(char)
        index += 1
    return None


def _run_fragment(opening: bytes, properties: bytes, body: bytes) -> bytes:
    """A split run, retaining its run properties and omitting empty shells."""
    material = body.strip()
    if not material:
        return b""
    return opening + properties + body + b"</w:r>"


def _visual_lines(body: bytes) -> list[bytes] | None:
    """Split paragraph content on Word line breaks without flattening its runs.

    Mathpix normally puts a soft break in a run of its own. The slightly more
    general run split also handles text on either side of that break and repeats
    the run properties so both retained fragments keep their formatting. A
    break outside a run implies a more complicated container; that paragraph is
    left alone rather than risk unbalancing it.
    """
    lines: list[bytes] = []
    current = bytearray()
    cursor = 0

    for run in RUN_RE.finditer(body):
        outside = body[cursor:run.start()]
        if BREAK_RE.search(outside):
            return None
        current += outside

        opening = re.match(rb"<w:r\b[^>]*>", run.group(0))
        if opening is None:
            current += run.group(0)
            cursor = run.end()
            continue
        inner = run.group(0)[opening.end(): -len(b"</w:r>")]
        properties_match = RUN_PROPERTIES_RE.match(inner)
        properties = properties_match.group(0) if properties_match else b""
        content_start = properties_match.end() if properties_match else 0
        content = inner[content_start:]
        breaks = list(BREAK_RE.finditer(content))
        if not breaks:
            current += run.group(0)
            cursor = run.end()
            continue

        at = 0
        for boundary in breaks:
            current += _run_fragment(
                opening.group(0), properties, content[at:boundary.start()]
            )
            lines.append(bytes(current))
            current.clear()
            at = boundary.end()
        current += _run_fragment(opening.group(0), properties, content[at:])
        cursor = run.end()

    tail = body[cursor:]
    if BREAK_RE.search(tail):
        return None
    current += tail
    lines.append(bytes(current))
    return lines


def _heading_line(line: bytes) -> tuple[str, str] | None:
    """Return a heading only when the visual line contains no other object."""
    if any(
        marker in line
        for marker in (
            b"<m:", b"<w:drawing", b"<w:pict", b"<w:object", b"<w:tab",
            b"<w:fldChar", b"<w:instrText", b"<w:bookmark",
        )
    ):
        return None
    text = b"".join(re.findall(rb"<w:t\b[^>]*>(.*?)</w:t>", line, re.S))
    return _latex_heading(html.unescape(text.decode("utf-8", "replace")))


def _ppr_parts(properties: bytes) -> tuple[bytes, bytes, bytes]:
    """Opening, inner XML and closing markup for paragraph properties."""
    opening = re.match(rb"<w:pPr\b[^>]*>", properties)
    if opening is not None:
        closing = b"</w:pPr>"
        return opening.group(0), properties[opening.end(): -len(closing)], closing
    empty = re.match(rb"<w:pPr\b[^>]*/>", properties)
    if empty is not None:
        return empty.group(0)[:-2] + b">", b"", b"</w:pPr>"
    return b"<w:pPr>", b"", b"</w:pPr>"


def _without_ppr_child(properties: bytes, element: str) -> bytes:
    if not properties:
        return properties
    pattern = re.compile(
        rb"\s*<" + element.encode() + rb"\b[^>]*>.*?</" + element.encode()
        + rb">|\s*<" + element.encode() + rb"\b[^>]*/>",
        re.S,
    )
    return pattern.sub(b"", properties)


def _set_ppr_child(properties: bytes, element: str, markup: bytes) -> bytes:
    """Set one paragraph property at its schema-valid position."""
    properties = _without_ppr_child(properties or b"<w:pPr/>", element)
    opening, inner, closing = _ppr_parts(properties)
    at = _insert_offset(0, inner, element, PPR_ORDER)
    return opening + inner[:at] + markup + inner[at:] + closing


def _unnumbered_ppr(properties: bytes) -> bytes:
    return _without_ppr_child(properties, "w:numPr")


def _heading_ppr(properties: bytes) -> bytes:
    """Heading spacing and alignment, without a false list marker or indent."""
    repaired = _unnumbered_ppr(properties)
    repaired = _without_ppr_child(repaired, "w:ind")
    repaired = _set_ppr_child(repaired, "w:spacing", HEADING_SPACING)
    return _set_ppr_child(repaired, "w:jc", HEADING_JUSTIFICATION)


def _text_markup(text: str) -> bytes:
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return escaped.encode("utf-8")


def _heading_run(title: str) -> bytes:
    return (
        b"<w:r><w:rPr><w:b/><w:bCs/></w:rPr><w:t>"
        + _text_markup(title)
        + b"</w:t></w:r>"
    )


def _paragraph_clone(opening: bytes, properties: bytes, body: bytes) -> bytes:
    return opening + properties + body + b"</w:p>"


def _repair_heading_paragraph(paragraph: bytes) -> tuple[bytes, int]:
    """Split and repair the unambiguous heading lines in one Word paragraph."""
    if paragraph.endswith(b"/>"):
        return paragraph, 0
    # Native Word headings already carry their semantics. Bookmarks and section
    # boundaries can span paragraph contents, so neither is safe to split.
    if b"<w:pStyle" in paragraph or b"<w:bookmark" in paragraph or b"<w:sectPr" in paragraph:
        return paragraph, 0
    # These containers may span multiple runs. The common Mathpix heading shape
    # is plain runs; leave richer Word structures byte-for-byte intact.
    complex_containers = (b"<w:hyperlink", b"<w:smartTag", b"<w:sdt", b"<w:customXml")
    if any(tag in paragraph for tag in complex_containers):
        return paragraph, 0

    opening = re.match(rb"<w:p\b[^>]*>", paragraph)
    if opening is None:
        return paragraph, 0
    inner = paragraph[opening.end(): -len(b"</w:p>")]
    property_match = PARAGRAPH_PROPERTIES_RE.match(inner)
    properties = property_match.group(1) if property_match else b""
    content = inner[property_match.end():] if property_match else inner
    lines = _visual_lines(content)
    if lines is None:
        return paragraph, 0

    commands = [_heading_line(line) for line in lines]
    repaired = sum(command is not None for command in commands)
    if not repaired:
        return paragraph, 0

    output: list[bytes] = []
    retained: list[bytes] = []
    heading_seen = False

    def flush_retained() -> None:
        nonlocal retained
        if not retained:
            return
        retained_properties = properties if not heading_seen else _unnumbered_ppr(properties)
        output.append(
            _paragraph_clone(
                opening.group(0), retained_properties, HEADING_BREAK.join(retained)
            )
        )
        retained = []

    for line, command in zip(lines, commands):
        if command is None:
            retained.append(line)
            continue
        flush_retained()
        _, title = command
        output.append(
            _paragraph_clone(
                opening.group(0), _heading_ppr(properties), _heading_run(title)
            )
        )
        heading_seen = True
    flush_retained()
    return b"".join(output), repaired


def _repair_latex_headings(document: bytes) -> tuple[bytes, int]:
    """Turn literal standalone Mathpix heading commands into Word headings."""
    edits: list[tuple[int, int, bytes]] = []
    repaired = 0
    for paragraph in PARAGRAPH_RE.finditer(document):
        replacement, changed = _repair_heading_paragraph(paragraph.group(0))
        if changed:
            edits.append(
                (paragraph.start(), paragraph.end() - paragraph.start(), replacement)
            )
            repaired += changed
    return _apply(document, edits), repaired


def _is_connective(text: str) -> bool:
    """Whether a fragment is one of the step tokens and nothing else.

    Length is checked before membership so a paragraph of prose is rejected on
    sight rather than compared against ten tokens.
    """
    stripped = text.strip()
    return len(stripped) <= MAX_CONNECTIVE_CHARS and stripped in STEP_CONNECTIVES


def _has_content(body: bytes) -> bool:
    """Whether a fragment holds anything a reader would miss if it went."""
    return b"<m:oMath" in body or b"<w:drawing" in body or b"<w:pict" in body


def _lone_math_paragraph(body: bytes) -> re.Match[bytes] | None:
    """The one display equation a paragraph consists of, if that is all it is."""
    found = MATH_PARA_RE.search(body)
    if found is None or MATH_PARA_RE.search(body, found.end()) is not None:
        return None
    rest = body[:found.start()] + body[found.end():]
    if b"<w:t" in rest or b"<w:drawing" in rest or b"<m:oMath" in rest:
        return None
    return found


def _math_para_jc_edits(offset: int, body: bytes) -> list[tuple[int, int, bytes]]:
    """Say where one `m:oMathPara` sits, rather than leaving it to `m:defJc`."""
    opening = re.match(rb"<m:oMathPara\b[^>]*>", body)
    if opening is None:
        return []
    properties = re.search(
        rb"<m:oMathParaPr\b[^>]*>.*?</m:oMathParaPr>|<m:oMathParaPr\b[^>]*/>",
        body,
        re.S,
    )
    if properties is None:
        return [(offset + opening.end(), 0, MATH_PARA_JC)]
    if properties.group(0).endswith(b"/>"):
        return [(
            offset + properties.start(),
            len(properties.group(0)),
            MATH_PARA_JC,
        )]

    justification = re.search(rb"<m:jc\b([^>]*)/>", properties.group(0))
    if justification is not None:
        if _attr(justification.group(1), "m:val") == "left":
            return []
        at = offset + properties.start() + justification.start()
        return [(at, len(justification.group(0)), b'<m:jc m:val="left"/>')]

    property_opening = re.match(rb"<m:oMathParaPr\b[^>]*>", properties.group(0))
    if property_opening is None:
        return []
    closing = properties.group(0).rfind(b"</m:oMathParaPr>")
    inner = properties.group(0)[property_opening.end():closing]
    at = _insert_offset(
        offset + properties.start() + property_opening.end(),
        inner,
        "m:jc",
        MATHPARAPR_ORDER,
    )
    return [(at, 0, b'<m:jc m:val="left"/>')]


def _connective_markup(token: str) -> bytes:
    """The connective, upright, and the gap the source's narrow column stood in.

    ``m:nor`` is what keeps ``or`` from being set as the product of two
    variables, and ``xml:space="preserve"`` is what keeps the gap: LibreOffice
    trims unmarked whitespace out of a maths run, which is the same trap
    ``MATH_OPERAND`` had to be fixed for.
    """
    text = token.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<m:r><m:rPr><m:nor/></m:rPr>'
        f'<m:t xml:space="preserve">{text}&#32;&#32;</m:t></m:r>'
    ).encode()


def _shape_a(body: bytes) -> str | None:
    """A whole paragraph that is nothing but a connective."""
    if _has_content(body) or b"<w:bookmarkStart" in body:
        return None
    text = _paragraph_text(body)
    return text.strip() if _is_connective(text) else None


def _shape_b(body: bytes) -> tuple[int, str] | None:
    """A connective hanging off the end of a text paragraph, after a soft break.

    The break is the last one in the paragraph, and the run holding it must hold
    only it — a run carrying text as well is a line of prose that happens to end
    where the break does, and cutting it would take the prose with it.
    """
    at = body.rfind(SOFT_BREAK)
    if at == -1:
        return None
    start = body.rfind(b"<w:r>", 0, at)
    spaced = body.rfind(b"<w:r ", 0, at)
    start = max(start, spaced)
    if start == -1:
        return None
    closing = body.find(b"</w:r>", at)
    if closing == -1 or b"<w:t" in body[start:closing]:
        return None
    tail = body[start:]
    if _has_content(tail):
        return None
    text = _paragraph_text(tail)
    return (start, text.strip()) if _is_connective(text) else None


def _join_steps_once(document: bytes) -> tuple[bytes, int]:
    """Put a worked step's connective on the line of the equation it introduces.

    Mathpix writes ``⇒``, ``∴`` or ``or`` as a line of its own above the display
    equation it leads into — 148 paragraphs of one delivered document consist of
    nothing else — and then writes the same connective *inside* the equation
    elsewhere in the same file. The second is what a derivation looks like set on
    paper, and it is the one this leaves behind.

    The connective is only ever taken from a paragraph that holds nothing but
    it, and is only ever moved into a paragraph that is one display equation and
    nothing else. Anything with a figure, a bookmark or a second line of maths in
    it is left exactly as Mathpix wrote it: joining is a repair to a line break,
    not licence to eat a paragraph.

    This must run before ``_fill_math_gaps``. An equation that now opens on a
    relation is precisely the case ``_needs_an_operand`` detects, so the operand
    that stops LibreOffice drawing its placeholder is supplied for free.
    """
    paragraphs = [(m.start(), m.end(), m.group(0)) for m in PARAGRAPH_RE.finditer(document)]
    edits: list[tuple[int, int, bytes]] = []
    joined = 0

    for index in range(1, len(paragraphs)):
        start, _, body = paragraphs[index]
        math = _lone_math_paragraph(body)
        if math is None:
            continue

        previous_start, previous_end, previous = paragraphs[index - 1]
        token = _shape_a(previous)
        if token is not None:
            cut = (previous_start, previous_end - previous_start, b"")
        else:
            tail = _shape_b(previous)
            if tail is None:
                continue
            at, token = tail
            closing = previous.rfind(b"</w:p>")
            cut = (previous_start + at, closing - at, b"")

        # `<m:oMathPara>` and `<m:oMath>` both open with `<m:oMath`, so the
        # equation's own opening tag is the second match, not the first.
        inner = re.search(rb"<m:oMath\b(?![A-Za-z])[^>]*>", math.group(0))
        if inner is None:
            continue
        edits.append(cut)
        edits += _math_para_jc_edits(start + math.start(), math.group(0))
        edits.append((start + math.start() + inner.end(), 0, _connective_markup(token)))
        joined += 1

    return _apply(document, edits), joined


def _join_steps(document: bytes) -> tuple[bytes, int]:
    """Join a complete chain of adjacent connective-only paragraphs.

    Mathpix can emit ``and`` and ``⇒`` as two consecutive lines before one
    equation. One positioned-edit pass can consume only the connective directly
    adjacent to the equation; repeating until stable folds the whole finite
    chain in during the same fit and makes refitting idempotent.
    """
    joined = 0
    while True:
        updated, changed = _join_steps_once(document)
        if not changed:
            return document, joined
        document = updated
        joined += changed


def _left_align_math(document: bytes) -> tuple[bytes, int]:
    """Say where every display equation sits, one paragraph at a time.

    ``_set_default_justification`` already writes ``m:defJc="left"`` into
    ``settings.xml``, and the delivered file carries it — yet the equations still
    render centred, so at least one reader is not reading the document default.
    An ``m:jc`` on the paragraph itself is not open to that: the default stays
    where it is, and stops being the only instruction in the file.
    """
    edits: list[tuple[int, int, bytes]] = []
    for found in MATH_PARA_RE.finditer(document):
        edits += _math_para_jc_edits(found.start(), found.group(0))
    return _apply(document, edits), len(edits)


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


@dataclass(frozen=True)
class _XmlChild:
    """One complete direct child of a small XML fragment."""

    name: str
    start: int
    end: int
    inner_start: int
    inner_end: int


def _direct_children(body: bytes) -> list[_XmlChild] | None:
    """Return balanced direct children without normalizing their XML.

    ElementTree is intentionally not used here: parsing and serializing would
    rename namespace prefixes and rewrite every untouched run. The document
    walker already has a quote-aware tag scanner, and a small balanced stack is
    enough to find row boundaries while retaining the source bytes exactly.
    """
    children: list[_XmlChild] = []
    stack: list[tuple[str, int, int]] = []
    covered = 0

    for match in TAG_RE.finditer(body):
        name = match.group(2).decode("ascii", "replace")
        closing = match.group(1) == b"/"
        empty = match.group(4) == b"/"

        if closing:
            if not stack or stack[-1][0] != name:
                return None
            opened_name, opened_at, opened_end = stack.pop()
            if not stack:
                if body[covered:opened_at].strip():
                    return None
                children.append(
                    _XmlChild(
                        opened_name, opened_at, match.end(), opened_end, match.start()
                    )
                )
                covered = match.end()
            continue

        if empty:
            if not stack:
                if body[covered:match.start()].strip():
                    return None
                children.append(
                    _XmlChild(name, match.start(), match.end(), match.end(), match.end())
                )
                covered = match.end()
            continue

        stack.append((name, match.start(), match.end()))

    if stack or body[covered:].strip():
        return None
    return children


def _child_body(source: bytes, child: _XmlChild) -> bytes:
    return source[child.inner_start:child.inner_end]


def _with_child_body(source: bytes, child: _XmlChild, body: bytes) -> bytes:
    """Replace a child's contents while preserving its tag and attributes."""
    if child.inner_start == child.end:
        opening = source[child.start:child.end]
        if not opening.endswith(b"/>"):
            return source[child.start:child.end]
        return opening[:-2] + b">" + body + f"</{child.name}>".encode()
    return (
        source[child.start:child.inner_start]
        + body
        + source[child.inner_end:child.end]
    )


def _visible_math_text(body: bytes) -> str:
    """Visible run text, with XML entities decoded and repair operands ignored."""
    return html.unescape(_math_text(body)).replace(ZERO_WIDTH_SPACE, "")


def _relation_run(body: bytes) -> bool:
    text = _visible_math_text(body).strip()
    return bool(text) and text[0] in MATH_BREAK_TOKENS


def _row_breaks(
    body: bytes, max_breaks: int = MAX_MATH_BREAKS
) -> tuple[list[_XmlChild], list[int]] | None:
    """Find conservative break positions between complete direct children."""
    if (
        max_breaks <= 0
        or b"<m:brk" in body
        or len(_visible_math_text(body)) < MATH_BREAK_CHARS
    ):
        return None
    children = _direct_children(body)
    if not children:
        return None

    breaks: list[int] = []
    seen = 0
    for index, child in enumerate(children):
        child_xml = body[child.start:child.end]
        visible = len(_visible_math_text(child_xml).strip())
        if (
            child.name == "m:r"
            and _relation_run(child_xml)
            and seen >= MIN_MATH_ROW_CHARS
            and len(breaks) < max_breaks
        ):
            breaks.append(index)
            seen = 0
        seen += visible

    # A short final fragment is not promoted to a row of its own. Removing the
    # final boundary merges it back into its predecessor without changing text.
    if breaks and seen < MIN_MATH_ROW_CHARS:
        breaks.pop()
    return (children, breaks) if breaks else None


def _split_row(
    body: bytes, max_breaks: int = MAX_MATH_BREAKS
) -> list[bytes] | None:
    found = _row_breaks(body, max_breaks)
    if found is None:
        return None
    children, breaks = found
    offsets = [0] + [children[index].start for index in breaks] + [len(body)]
    return [body[left:right] for left, right in zip(offsets, offsets[1:])]


def _mark_run_property(run: bytes, property_markup: bytes) -> bytes | None:
    """Put one ordered OMML run property on a complete ``m:r``."""
    children = _direct_children(run)
    if len(children or []) != 1 or children[0].name != "m:r":
        return None
    outer = children[0]
    body = _child_body(run, outer)
    direct = _direct_children(body)
    if direct is None:
        return None
    property_name = re.match(rb"<([A-Za-z_][\w.:-]*)", property_markup)
    if property_name is None:
        return None
    name = property_name.group(1)

    for child in direct:
        if child.name != "m:rPr":
            continue
        props = body[child.start:child.end]
        if re.search(rb"<" + re.escape(name) + rb"\b", props):
            return run
        inner = _child_body(body, child)
        element = name.decode("ascii", "replace")
        at = _insert_offset(0, inner, element, MATH_RPR_ORDER)
        replacement = _with_child_body(
            body, child, inner[:at] + property_markup + inner[at:]
        )
        new_body = body[:child.start] + replacement + body[child.end:]
        return _with_child_body(run, outer, new_body)

    return _with_child_body(
        run, outer, b"<m:rPr>" + property_markup + b"</m:rPr>" + body
    )


def _align_row(body: bytes) -> bytes:
    """Mark the first direct relation as this equation row's alignment point."""
    children = _direct_children(body)
    if children is None:
        return body
    for child in children:
        run = body[child.start:child.end]
        if child.name != "m:r" or not _relation_run(run):
            continue
        marked = _mark_run_property(run, b"<m:aln/>")
        if marked is None:
            return body
        return body[:child.start] + marked + body[child.end:]
    return body


def _flat_equation(body: bytes) -> bytes | None:
    """Turn one oversized flat expression into one editable equation array."""
    direct = _direct_children(body)
    if direct is None or any(child.name not in MATH_ARG_ELEMENTS for child in direct):
        return None
    rows = _split_row(body)
    if rows is None:
        return None
    return b"<m:eqArr>" + b"".join(
        b"<m:e>" + _align_row(row) + b"</m:e>" for row in rows
    ) + b"</m:eqArr>"


def _equation_array(body: bytes) -> bytes | None:
    """Subdivide only oversized direct rows of an existing equation array."""
    direct = _direct_children(body)
    if direct is None:
        return None
    if any(child.name not in ("m:eqArrPr", "m:e") for child in direct):
        return None
    properties = [index for index, child in enumerate(direct) if child.name == "m:eqArrPr"]
    if len(properties) > 1 or (properties and properties[0] != 0):
        return None
    rows = [child for child in direct if child.name == "m:e"]
    if not rows:
        return None

    pieces: list[bytes] = []
    cursor = 0
    changed = False
    remaining = MAX_MATH_BREAKS
    for child in direct:
        pieces.append(body[cursor:child.start])
        original = body[child.start:child.end]
        if child.name != "m:e":
            pieces.append(original)
        else:
            row_body = _child_body(body, child)
            nested = b"<m:eqArr" in row_body or b"<m:m" in row_body
            subdivisions = None if nested else _split_row(row_body, remaining)
            if subdivisions is None:
                pieces.append(original)
            else:
                pieces.append(
                    _with_child_body(body, child, _align_row(subdivisions[0]))
                )
                pieces.extend(
                    b"<m:e>" + _align_row(row) + b"</m:e>"
                    for row in subdivisions[1:]
                )
                remaining -= len(subdivisions) - 1
                changed = True
        cursor = child.end
    pieces.append(body[cursor:])
    return b"".join(pieces) if changed else None


def _matrix_rows(body: bytes) -> tuple[list[_XmlChild], list[list[_XmlChild]]] | None:
    direct = _direct_children(body)
    if direct is None or any(child.name not in ("m:mPr", "m:mr") for child in direct):
        return None
    properties = [index for index, child in enumerate(direct) if child.name == "m:mPr"]
    if len(properties) > 1 or (properties and properties[0] != 0):
        return None
    rows = [child for child in direct if child.name == "m:mr"]
    cells: list[list[_XmlChild]] = []
    for row in rows:
        found = _direct_children(_child_body(body, row))
        if not found or any(child.name != "m:e" for child in found):
            return None
        cells.append(found)
    if len(rows) < 2 or not cells or len(cells[0]) not in (1, 2):
        return None
    if any(len(row_cells) != len(cells[0]) for row_cells in cells):
        return None
    return direct, cells


def _layout_matrix_rows(
    body: bytes, depth: int = 0
) -> tuple[list[bytes], bool] | None:
    """Read Mathpix's sparse derivation matrix as logical equation rows.

    Some real exports use four declared alignment columns with only one or two
    populated cells per row; others put a complete multi-row matrix inside one
    cell of a two-column outer matrix. Those are layout scaffolds, not semantic
    matrices. Flattening is safe only while every level is sparse, contains no
    more than four physical columns, and a nested matrix occupies its cell by
    itself.
    """
    if depth > 3:
        return None
    direct = _direct_children(body)
    if direct is None or any(child.name not in ("m:mPr", "m:mr") for child in direct):
        return None
    properties = [
        index for index, child in enumerate(direct) if child.name == "m:mPr"
    ]
    if len(properties) > 1 or (properties and properties[0] != 0):
        return None
    matrix_rows = [child for child in direct if child.name == "m:mr"]
    if len(matrix_rows) < 2:
        return None

    rows: list[bytes] = []
    widths: list[int] = []
    nested_layout = False
    for matrix_row in matrix_rows:
        row_body = _child_body(body, matrix_row)
        cells = _direct_children(row_body)
        if not cells or any(cell.name != "m:e" for cell in cells):
            return None
        if len(cells) > 4:
            return None
        widths.append(len(cells))
        populated = [
            cell
            for cell in cells
            if _visible_math_text(_child_body(row_body, cell)).strip()
        ]
        if not populated or len(populated) > 2:
            return None

        nested: tuple[_XmlChild, _XmlChild] | None = None
        for cell in populated:
            cell_body = _child_body(row_body, cell)
            children = _direct_children(cell_body)
            if children is None:
                return None
            matrices = [child for child in children if child.name == "m:m"]
            if matrices:
                if (
                    len(populated) != 1
                    or len(children) != 1
                    or len(matrices) != 1
                ):
                    return None
                nested = (cell, matrices[0])
                break
            if any(child.name not in MATH_ARG_ELEMENTS for child in children):
                return None

        if nested is not None:
            cell, matrix = nested
            cell_body = _child_body(row_body, cell)
            flattened = _layout_matrix_rows(_child_body(cell_body, matrix), depth + 1)
            if flattened is None:
                return None
            nested_rows, _ = flattened
            rows.extend(nested_rows)
            nested_layout = True
        else:
            rows.append(b"".join(_child_body(row_body, cell) for cell in populated))

    complex_layout = nested_layout or max(widths) > 2 or len(set(widths)) > 1
    return rows, complex_layout


def _derivation_row(body: bytes) -> bool:
    """Whether one flattened row visibly participates in a derivation."""
    visible = _visible_math_text(body).strip()
    if not visible:
        return False
    if visible[0] in MATH_LEADING_OPERATORS:
        return True
    children = _direct_children(body)
    return bool(children) and any(
        child.name == "m:r" and _relation_run(body[child.start:child.end])
        for child in children
    )


def _complex_matrix_equation(body: bytes) -> bytes | None:
    """Flatten only long, sparse nested/ragged layout matrices into an eqArr."""
    flattened = _layout_matrix_rows(body)
    if flattened is None:
        return None
    rows, complex_layout = flattened
    if (
        not complex_layout
        or len(_visible_math_text(b"".join(rows))) < MATH_BREAK_CHARS
        or sum(_derivation_row(row) for row in rows) < 2
    ):
        return None

    output: list[bytes] = []
    remaining = MAX_MATH_BREAKS
    for row in rows:
        subdivisions = _split_row(row, remaining)
        logical_rows = subdivisions or [row]
        if subdivisions is not None:
            remaining -= len(subdivisions) - 1
        output.extend(
            b"<m:e>" + _align_row(logical_row) + b"</m:e>"
            for logical_row in logical_rows
        )
    return b"<m:eqArr>" + b"".join(output) + b"</m:eqArr>"


def _matrix_equation(body: bytes) -> bytes | None:
    """Extend an unambiguous one- or two-column derivation matrix."""
    shape = _matrix_rows(body)
    if shape is None:
        return None
    direct, all_cells = shape
    rows = [child for child in direct if child.name == "m:mr"]

    targets: list[int] = []
    relation_rows = 0
    continuation_rows = 0
    for row_index, (row, cells) in enumerate(zip(rows, all_cells)):
        row_body = _child_body(body, row)
        nonempty = [
            index
            for index, cell in enumerate(cells)
            if _visible_math_text(_child_body(row_body, cell)).strip()
        ]
        if not nonempty:
            return None
        target = nonempty[-1]
        targets.append(target)
        cell_body = _child_body(row_body, cells[target])
        cell_direct = _direct_children(cell_body)
        relations = [
            child
            for child in cell_direct or []
            if child.name == "m:r"
            and _relation_run(cell_body[child.start:child.end])
        ]
        relation_rows += bool(relations)
        visible = _visible_math_text(cell_body).strip()
        continuation_rows += bool(
            row_index and visible and visible[0] in MATH_BREAK_TOKENS
        )

    # A semantic matrix may also contain an equals sign. Requiring relations in
    # multiple rows and a relation-led continuation is what identifies the
    # Mathpix shape as a derivation rather than mathematical matrix data.
    if relation_rows < 2 or continuation_rows == 0:
        return None

    pieces: list[bytes] = []
    cursor = 0
    changed = False
    remaining = MAX_MATH_BREAKS
    row_number = 0
    for child in direct:
        pieces.append(body[cursor:child.start])
        if child.name != "m:mr":
            pieces.append(body[child.start:child.end])
            cursor = child.end
            continue

        row_body = _child_body(body, child)
        cells = all_cells[row_number]
        target = targets[row_number]
        target_body = _child_body(row_body, cells[target])
        subdivisions = _split_row(target_body, remaining)
        row_number += 1
        if subdivisions is None:
            pieces.append(body[child.start:child.end])
            cursor = child.end
            continue

        # The original row, including every non-target cell and its properties,
        # is retained for the first segment.
        target_cell = cells[target]
        first_cell = _with_child_body(
            row_body, target_cell, _align_row(subdivisions[0])
        )
        first_row_body = (
            row_body[:target_cell.start] + first_cell + row_body[target_cell.end:]
        )
        pieces.append(_with_child_body(body, child, first_row_body))

        for subdivision in subdivisions[1:]:
            new_cells = []
            for index in range(len(cells)):
                if index == target:
                    new_cells.append(b"<m:e>" + _align_row(subdivision) + b"</m:e>")
                else:
                    new_cells.append(b"<m:e/>")
            pieces.append(b"<m:mr>" + b"".join(new_cells) + b"</m:mr>")
        remaining -= len(subdivisions) - 1
        changed = True
        cursor = child.end

    pieces.append(body[cursor:])
    return b"".join(pieces) if changed else None


def _structural_equation(block: bytes) -> tuple[bytes | None, bool]:
    """Split one safe display equation; report whether its shape was flat."""
    if b"<m:brk" in block:
        return None, False
    direct = _direct_children(block)
    if direct is None:
        return None, False
    maths = [child for child in direct if child.name == "m:oMath"]
    properties = [
        index for index, child in enumerate(direct) if child.name == "m:oMathParaPr"
    ]
    if (
        len(maths) != 1
        or len(properties) > 1
        or (properties and properties[0] != 0)
        or any(child.name not in ("m:oMathParaPr", "m:oMath") for child in direct)
    ):
        # Mixed-content maths paragraphs and multiple equations are ambiguous:
        # one array cannot replace them without changing their grouping.
        return None, False

    math = maths[0]
    body = _child_body(block, math)
    children = _direct_children(body)
    if children is None:
        return None, False
    replacement: bytes | None
    flat = not any(child.name in ("m:eqArr", "m:m") for child in children)
    if flat:
        replacement = _flat_equation(body)
    elif len(children) == 1 and children[0].name == "m:eqArr":
        child = children[0]
        converted = _equation_array(_child_body(body, child))
        replacement = (
            _with_child_body(body, child, converted) if converted is not None else None
        )
    elif len(children) == 1 and children[0].name == "m:m":
        child = children[0]
        matrix_body = _child_body(body, child)
        flattened = _complex_matrix_equation(matrix_body)
        if flattened is not None:
            replacement = flattened
        else:
            converted = _matrix_equation(matrix_body)
            replacement = (
                _with_child_body(body, child, converted)
                if converted is not None
                else None
            )
    else:
        # Nested arrays/matrices and a structural object mixed with other math
        # are semantic or ambiguous and intentionally remain untouched.
        return None, False

    if replacement is None:
        return None, flat
    return _with_child_body(block, math, replacement), flat


def _soft_break_flat_equation(block: bytes) -> bytes | None:
    """Fallback for a safe flat equation that could not become an array."""
    direct = _direct_children(block)
    maths = [child for child in direct or [] if child.name == "m:oMath"]
    if len(maths) != 1:
        return None
    math = maths[0]
    body = _child_body(block, math)
    found = _row_breaks(body)
    if found is None:
        return None
    children, breaks = found
    pieces: list[bytes] = []
    cursor = 0
    for index in breaks:
        child = children[index]
        pieces.append(body[cursor:child.start])
        run = body[child.start:child.end]
        marked = _mark_run_property(run, b"<m:brk/>")
        if marked is None:
            return None
        pieces.append(marked)
        cursor = child.end
    pieces.append(body[cursor:])
    return _with_child_body(block, math, b"".join(pieces))


def _break_equations(document: bytes, walk: _Walk) -> tuple[list, int]:
    """Structurally subdivide genuinely oversized, unambiguous derivations.

    Each resulting line remains part of one editable OMML equation. Breaks are
    made only between complete top-level children, so fractions, radicals,
    scripts, delimiters, functions and other nested expressions stay atomic.
    Existing arrays are extended row-by-row. Consistent one- or two-column
    derivation matrices retain their columns, while sparse ragged or nested
    layout matrices are flattened into equation-array rows; dense semantic
    matrices are never treated as layout.

    A soft ``m:brk`` remains as a fallback for a safe flat shape if structural
    construction ever has to decline it. Ambiguous, nested, mixed-content and
    manually wrapped equations are left byte-for-byte alone.
    """
    edits: list[tuple[int, int, bytes]] = []
    changed = 0

    for start, end in walk.math:
        block = document[start:end]
        replacement, fallback_safe = _structural_equation(block)
        if replacement is None and fallback_safe:
            replacement = _soft_break_flat_equation(block)
        if replacement is None or replacement == block:
            continue
        edits.append((start, end - start, replacement))
        changed += 1

    return edits, changed


# --- redundant page breaks, and the blank pages they add ----------------------

# Mathpix asks the Files API for page breaks (`include_page_breaks`), so its
# .docx carries one dedicated break paragraph per source-page boundary —
# `<w:p><w:r><w:br w:type="page"/></w:r></w:p>` — alongside empty spacer
# paragraphs around figures. Where a source page held nothing Mathpix could
# read, or where a page's content ended near the foot of the US Letter page it
# was poured onto, two of those breaks land with nothing between them and a
# whole page comes out blank. This is not a rendering choice: every reader
# paginates an explicit break the same way, so the repair is to drop the breaks
# that start a page without putting anything on it. Only whole empty paragraphs
# are removed; a break sharing its paragraph with text, a drawing or a bookmark
# is left exactly as Mathpix wrote it.
PAGE_BREAK_RUN_RE = re.compile(rb'<w:br\b[^>]*\bw:type="page"')
# `<w:pageBreakBefore/>`, but not one explicitly switched off.
PAGE_BREAK_BEFORE_RE = re.compile(
    rb'<w:pageBreakBefore\b(?![^>]*\bw:val="(?:0|false|off)")'
)
# Content a blank-looking paragraph can still carry that a reader would miss, or
# that another pass depends on. A column break is layout, not blank space.
PARAGRAPH_CONTENT_MARKERS = (
    b"<m:oMath", b"<w:drawing", b"<w:pict", b"<w:object", b"<w:bookmarkStart",
    b"<w:hyperlink", b"<w:fldChar", b"<w:instrText", b"<w:footnoteReference",
    b"<w:endnoteReference", b"<w:commentReference", b"<w:sectPr",
    b'w:type="column"',
)


def _paragraph_has_content(body: bytes) -> bool:
    """Whether a paragraph holds anything a reader would miss if it were gone."""
    if _paragraph_text(body).strip():
        return True
    return any(marker in body for marker in PARAGRAPH_CONTENT_MARKERS)


def _collapse_page_breaks(document: bytes) -> tuple[bytes, int]:
    """Remove page breaks that start a page without putting anything on it.

    Three shapes, each measurable in the file and each safe to drop because it
    carries no content:

      * two or more break paragraphs in a row, separated only by empty
        paragraphs — collapsed to the first break;
      * break and empty paragraphs following the last of the document's
        content — removed, with the final ``<w:sectPr>`` left in place;
      * a break paragraph immediately before a paragraph that already carries
        ``<w:pageBreakBefore/>`` — removed, since that paragraph ejects itself
        to a new page without it.

    A paragraph is only ever removed whole, and only when it holds no text, no
    maths, no drawing and no bookmark. The count returned is the number of
    removed break paragraphs — one per blank page the reader no longer sees.

    The whole pass is skipped rather than guessed at when the body cannot be
    read as a balanced sequence of children, or when it has no content at all.
    """
    body_open = re.search(rb"<w:body\b[^>]*>", document)
    if body_open is None:
        return document, 0
    body_close = document.rfind(b"</w:body>")
    if body_close <= body_open.end():
        return document, 0

    base = body_open.end()
    inner = document[base:body_close]
    children = _direct_children(inner)
    if not children:
        return document, 0

    CONTENT, BREAK, BLANK = 1, 2, 3
    cats: list[int] = []
    for child in children:
        if child.name != "w:p":
            cats.append(CONTENT)
            continue
        pbody = inner[child.inner_start:child.inner_end]
        if _paragraph_has_content(pbody):
            cats.append(CONTENT)
        elif PAGE_BREAK_RUN_RE.search(pbody) or PAGE_BREAK_BEFORE_RE.search(pbody):
            cats.append(BREAK)
        else:
            cats.append(BLANK)

    if CONTENT not in cats:
        # An all-empty body is not what this pass is for, and reducing it to
        # bare section properties would be a larger change than the defect.
        return document, 0

    drop: set[int] = set()

    # Consecutive breaks: keep the first, drop every later break in the run and
    # every empty paragraph that trails it.
    index = 0
    while index < len(cats):
        if cats[index] not in (BREAK, BLANK):
            index += 1
            continue
        run_start = index
        while index < len(cats) and cats[index] in (BREAK, BLANK):
            index += 1
        run = range(run_start, index)
        breaks = [position for position in run if cats[position] == BREAK]
        if len(breaks) >= 2:
            for position in run:
                if position > breaks[0]:
                    drop.add(position)

    # Trailing breaks: everything empty after the last content, with the final
    # `<w:sectPr>` child (when the body ends on one) left untouched.
    limit = len(children) - 1 if children[-1].name == "w:sectPr" else len(children)
    last_content = max(
        (position for position in range(limit) if cats[position] == CONTENT),
        default=-1,
    )
    # Only when there is real content to trail. A body that is nothing but empty
    # paragraphs is left as it is rather than reduced to bare section
    # properties.
    if last_content >= 0:
        for position in range(last_content + 1, limit):
            if cats[position] in (BREAK, BLANK):
                drop.add(position)

    # A break immediately before a paragraph that already breaks itself.
    for position in range(1, len(cats)):
        if cats[position] != CONTENT:
            continue
        pbody = inner[children[position].inner_start:children[position].inner_end]
        if not PAGE_BREAK_BEFORE_RE.search(pbody):
            continue
        previous = position - 1
        while previous >= 0 and cats[previous] == BLANK:
            previous -= 1
        if previous < 0 or cats[previous] != BREAK:
            continue
        prev_body = inner[
            children[previous].inner_start:children[previous].inner_end
        ]
        if PAGE_BREAK_RUN_RE.search(prev_body):
            drop.add(previous)

    if not drop:
        return document, 0

    edits = [
        (
            base + children[position].start,
            children[position].end - children[position].start,
            b"",
        )
        for position in sorted(drop)
    ]
    removed_breaks = sum(1 for position in drop if cats[position] == BREAK)
    return _apply(document, edits), removed_breaks


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
    font_points: float = 10.0,
    font_name: str = "Cambria Math",
    join_steps: bool = True,
    collapse_page_breaks: bool = True,
    side_margin_inches: float = 0.5,
) -> tuple[bytes, Fit]:
    """Return Mathpix's document restated in units that survive being resized.

    Every part of the original archive is copied through; only
    ``word/document.xml``, ``word/settings.xml`` and ``word/styles.xml`` are
    rewritten, and only in the specific places described at the top of this
    module. A file that cannot
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

    ``font_points`` is the body and inline-maths size, headings included;
    display equations and their controls are one point smaller. A value of 0
    leaves every size exactly as Mathpix wrote it. ``font_name`` is the one face
    used for prose and equations; "" leaves every face Mathpix wrote.
    Standalone literal LaTeX title and section commands are repaired into bold,
    unnumbered Word paragraphs before those typography passes. ``join_steps``
    puts a lone ``⇒`` on the line of the equation it introduces.
    ``collapse_page_breaks`` removes the page breaks Mathpix writes at every
    source-page boundary that start a new page without any content on it —
    consecutive breaks, breaks trailing the last content, and a break in front
    of a paragraph that already carries ``<w:pageBreakBefore/>`` — which is
    where the blank pages in the delivered file come from. None of these is
    gated on ``multi_column``: all are wrong at any measure.

    ``side_margin_inches`` is the left and right margin every section is given,
    overriding both Mathpix's margins and the ones read off the source page; 0
    leaves whatever the document already states. It is set before the measure is
    read, so the rest of the fitting follows it rather than working around it.
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

    margined = 0
    if side_margin_inches > 0:
        document, margined = _set_side_margins(document, side_margin_inches)

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

    # A structural pass, before any run is sized or refonted: whole empty
    # paragraphs go, so there is less to walk and nothing the later passes
    # inserted can be caught by it.
    pruned = 0
    if collapse_page_breaks:
        document, pruned = _collapse_page_breaks(document)

    # Everything from here is a whole-document rewrite rather than a positioned
    # edit, so it runs after the offsets collected above have been spent — and
    # in this order. Joining comes before the gap filling, so an equation that
    # now opens on a relation picks up its zero-width operand; sizing comes
    # last, so the runs those two passes inserted are sized like the rest.
    # Literal heading commands are split before typography is restated so the
    # inserted bold runs inherit the same configured body face and size.
    document, headings = _repair_latex_headings(document)
    document, _ = _left_align_paragraphs(document)
    joined = 0
    if join_steps:
        document, joined = _join_steps(document)
    if fit_equations:
        document, _ = _left_align_math(document)
    gaps = 0
    if fill_math_gaps:
        document, gaps = _fill_math_gaps(document)

    half_points = max(1, int(round(font_points * 2))) if font_points > 0 else 0
    restated = 0
    display_half_points = max(1, half_points - 2) if half_points > 0 else 0
    styles_part = parts.get(STYLES)
    styled = styles_part
    if half_points > 0:
        document, restated = _restate_sizes(document, half_points)
        document, sized = _size_math_runs(document, half_points, display_half_points)
        restated += sized
        if styled is not None:
            styled, in_styles = _restate_sizes(styled, half_points)
            restated += in_styles

    # The face goes on after the size and after every pass that inserted a run,
    # for the same reason: what those passes wrote inherits it rather than
    # having to be told. The maths font in `settings.xml` is left alone —
    # `m:mathFont` selects the OpenType MATH table an equation is laid out
    # with, not a face for its letters, and only a handful of fonts have one.
    refonted = 0
    numbering_part = parts.get(NUMBERING)
    numbered = numbering_part
    if font_name:
        document, refonted = _restate_fonts(document, font_name)
        if styled is not None:
            styled, in_styles = _set_default_font(styled, font_name)
            refonted += in_styles
        # A numbered list draws its own marker, in a face named here and nowhere
        # else, so a list left out of this counts up in Georgia beside a
        # paragraph set in Cambria.
        if numbered is not None:
            numbered, in_numbering = _restate_fonts(numbered, font_name)
            refonted += in_numbering

    settings_part = parts.get(SETTINGS)
    updated = settings_part
    if updated is not None:
        if fit_equations and broken:
            updated = _relax_wrap_indent(updated, wrap_indent_twips)
        if fit_equations:
            updated = _set_default_justification(updated, "left")

    columns = layout.columns if layout is not None else 0
    page_inches = (layout.page_width, layout.page_height) if layout is not None else None

    if (
        document == original
        and updated == settings_part
        and styled == styles_part
        and numbered == numbering_part
    ):
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
    if styled is not None:
        parts[STYLES] = styled
    if numbered is not None:
        parts[NUMBERING] = numbered

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
        steps_joined=joined,
        blank_pages_pruned=pruned,
        headings_repaired=headings,
        sizes_restated=restated,
        font_points=font_points if half_points > 0 else 0.0,
        display_equation_points=display_half_points / 2 if half_points > 0 else 0.0,
        fonts_restated=refonted,
        font_name=font_name if refonted else "",
        side_margin_inches=side_margin_inches if margined else 0.0,
        render_dpi=dpi,
        measure_inches=measure / TWIPS_PER_INCH,
        columns=columns,
        page_inches=page_inches,
        applied=True,
    )
