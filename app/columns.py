"""How many columns each page of the source PDF was set in.

Marker, like every other transcriber here, returns one linear stream of
Markdown: it reads a two-column page in the right order and then writes the two
columns out one after the other. That is the correct reading order, but it is
not the page's layout, and a textbook set in two columns comes back as one long
single-column document.

The layout is still in the PDF, though, and it is read here rather than
recovered from anyone's transcription — no model, no sidecar, just where the
text blocks actually sit. The count is then handed to the writer, which sets the
Word section to match, so a two-column page flows back into two Word columns.

What this deliberately does not try to do is reproduce the original column
*breaks*. A .docx is reflowable; where one column ends and the next begins is
decided by Word when the document is laid out, and forcing it would be guessing
at a page size the reader may not be using.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

# A gutter has to be at least this wide, in points and as a fraction of the
# body's width, before it is a column separator rather than the ragged edge of a
# paragraph or the space either side of a centred equation.
MIN_GUTTER_PT = 9.0
MIN_GUTTER_RATIO = 0.02

# A band with less of the page's text than this in it is not a column. It is
# usually a marginal note, a line of verse, or a figure label that happens to
# sit clear of everything else.
MIN_BAND_SHARE = 0.15
MIN_BAND_BLOCKS = 2

# A block covering more of the body than this spans the columns rather than
# sitting in one: a running head, a banner heading, a full-width figure.
SPANNING_RATIO = 0.7

# Spanning blocks are only discounted while they stay a minority. On a page that
# really is set in one column, nearly every paragraph is "full width", and
# discounting them all would leave a handful of short lines and a gutter
# invented out of the gaps between them.
MAX_SPANNING_SHARE = 0.35

# A band narrower than this share of the body cannot be a column of text.
MIN_BAND_WIDTH_RATIO = 0.15
# How far a gutter may sit from where an even division of the page would put it,
# as a fraction of the page's width.
MAX_GUTTER_OFFSET = 0.10

# Two is the most this will report. Not because three-column layouts do not
# exist, but because in the pages this converts — textbook and paper pages — a
# third column is far more often a misread of one: an inset, a table of options
# set beside the text, a scan whose margin came out dark. A page taken as one
# column still reads correctly; a page cut into three that were never there does
# not.
MAX_COLUMNS = 2

# Blocks shorter than this are page furniture — folios, rules, stray marks —
# and too small to say anything about where the columns are.
MIN_BLOCK_HEIGHT_PT = 4.0
MIN_BLOCKS = 4

# How much of an uploaded PDF is read just to answer "is this set in columns?".
# The answer only decides whether the browser offers the multi-column choice, and
# a book is not set one way for its first dozen pages and another way after, so
# reading the whole of a 400-page scan to settle it would be paid for every
# upload and change nothing.
PROBE_PAGES = 12


@dataclass(frozen=True)
class _Block:
    x0: float
    x1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0


def _text_blocks(page: fitz.Page) -> list[_Block]:
    """The page's text blocks, as horizontal extents."""
    blocks: list[_Block] = []
    for x0, y0, x1, y1, text, _number, kind in page.get_text("blocks"):
        if kind != 0 or not str(text).strip():
            continue
        if y1 - y0 < MIN_BLOCK_HEIGHT_PT or x1 - x0 <= 0:
            continue
        blocks.append(_Block(float(x0), float(x1)))
    return blocks


def _gutters(
    blocks: list[_Block], left: float, right: float, min_absolute: float = MIN_GUTTER_PT
) -> list[tuple[float, float]]:
    """The clear vertical lanes between `left` and `right` that no block enters.

    Worked on the blocks' shadows on the x axis rather than on a bitmap of the
    page: a column separator is a range of x that *nothing* is written in, top to
    bottom, and that is exactly what is left over when every block's extent is
    merged.

    `min_absolute` is in whatever units the extents are. The ink path measures
    in pixels of its own raster and passes 0, leaving only the proportional
    floor, which is the one that means the same thing at any scale.
    """
    span = right - left
    if span <= 0:
        return []
    threshold = max(min_absolute, span * MIN_GUTTER_RATIO)

    merged: list[list[float]] = []
    for block in sorted(blocks, key=lambda b: b.x0):
        if merged and block.x0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], block.x1)
        else:
            merged.append([block.x0, block.x1])

    return [
        (merged[index][1], merged[index + 1][0])
        for index in range(len(merged) - 1)
        if merged[index + 1][0] - merged[index][1] >= threshold
    ]


def _bands(gutters: list[tuple[float, float]], left: float, right: float) -> list[tuple[float, float]]:
    edges = [left, *[edge for gutter in gutters for edge in gutter], right]
    return [(edges[index], edges[index + 1]) for index in range(0, len(edges) - 1, 2)]


def _columns_in(
    bands: list[tuple[float, float]],
    weights: list[float],
    floor: float,
    page_centre: float,
    page_width: float,
) -> int:
    """How many of these bands are columns of the body, and not something else.

    A band at the edge of the page carrying almost none of its content is not a
    thin column, it is furniture standing outside the text: the black chapter tab
    printed down the outer edge of a textbook, a strip of marginal keywords, the
    ragged dark border of a scan. Nor is a band too narrow to set a line of text
    in — the numbers of a numbered list, set in a column of their own to the left
    of everything they label, are the common case and they are not a column of
    the page. Left in, each such band adds a column *and* the margin beside it
    becomes a gutter, so a two-column page reports four. They are pared off the
    ends instead, which is where they occur.

    What survives has to carry its share, all of it. A band that falls short in
    the middle of the page is not furniture — it is the sign that the gutters
    were misread — and there is nothing to be done with the page but take it as
    one column.
    """
    if not bands:
        return 1
    span = bands[-1][1] - bands[0][0]

    def is_column(index: int) -> bool:
        start, end = bands[index]
        return weights[index] >= floor and end - start >= span * MIN_BAND_WIDTH_RATIO

    first, last = 0, len(bands) - 1
    while first < last and not is_column(first):
        first += 1
    while last > first and not is_column(last):
        last -= 1
    kept = range(first, last + 1)
    if not all(is_column(index) for index in kept):
        return 1

    columns = len(kept)
    if columns > MAX_COLUMNS:
        return 1
    if columns == 1:
        return 1

    # Columns are set to the same measure, so the gutter between them falls where
    # an even division of the page falls — down the middle, for two of them. The
    # page is the thing to measure that against, not the text: a column's extent
    # is its longest line, and a column that happens to end high or set short
    # comes out narrower than its neighbour without being any less a column. It
    # is the test that separates a layout from a page of numbered items whose
    # numbers hang in the margin, whose "gutter" sits a fifth of the way across.
    for offset, index in enumerate(list(kept)[:-1]):
        gutter = (bands[index][1] + bands[index + 1][0]) / 2
        expected = page_centre + (offset + 1 - columns / 2) * (page_width / columns)
        if abs(gutter - expected) > page_width * MAX_GUTTER_OFFSET:
            return 1
    return columns


def _columns_from(blocks: list[_Block], page_centre: float, page_width: float) -> int:
    """The column count implied by these blocks, or 1 if they imply nothing."""
    if len(blocks) < MIN_BLOCKS:
        return 1
    left = min(block.x0 for block in blocks)
    right = max(block.x1 for block in blocks)
    gutters = _gutters(blocks, left, right)
    if not gutters:
        return 1

    # A gutter can be real and the layout still be one column — an indented
    # quotation beside nothing else will do it — so each band has to carry its
    # share of the page before it is counted as one.
    bands = _bands(gutters, left, right)
    weights = [
        float(sum(1 for block in blocks if block.x0 >= start - 1 and block.x1 <= end + 1))
        for start, end in bands
    ]
    return _columns_in(
        bands,
        weights,
        max(MIN_BAND_BLOCKS, len(blocks) * MIN_BAND_SHARE),
        page_centre,
        page_width,
    )


# --------------------------------------------------------------------------- #
# Scanned pages
# --------------------------------------------------------------------------- #

# A scan has no text blocks to read, so the columns are found in its ink.
#
# Rendered to a fixed width rather than a fixed DPI. A scan's page size is
# whatever the scanner said it was — the book pages here are 390pt wide, half a
# letter sheet — and at a fixed DPI the small ones come out too coarse to show a
# gutter at all. Normalising the width instead means every page arrives at the
# same resolution, and a threshold in pixels means the same thing on all of them.
INK_WIDTH_PX = 800
INK_ROW_STEP = 2
# On a grey scan, paper is not 255 and toner is not 0.
INK_LEVEL = 200

# The share of inked rows a lane must stay under to be a gutter, as a fraction
# of what the body text manages. Relative because the absolute figure is a
# property of the scan, not the layout: the tighter the type, the fewer rows any
# one column of pixels appears in. Not zero either — a running head crosses the
# gutter on a handful of rows, a page ruled with a border box has a horizontal
# line at every x there is, and speckle is everywhere.
GUTTER_COVERAGE_SHARE = 0.3
MIN_GUTTER_COVERAGE = 0.004
# Below this, a page is blank or nearly so and has no layout to report.
MIN_INKED_ROWS = 8
# Narrower than this share of the page and a band of ink is a rule, a folio or a
# marginal mark, not a column of text.
MIN_COLUMN_RATIO = 0.06


def _ink_coverage(page: fitz.Page) -> tuple[list[float], int]:
    """For each column of pixels, the share of the page's inked rows it appears in.

    Counted per row rather than as a total so that the things which run *across*
    a page cannot fill in the lane that runs down it. A running head sits on
    perhaps twenty rows out of a thousand, and a ruled border on two; measured as
    a share of the rows that carry text, neither is enough to close a gutter,
    while a genuine column of body text is in nearly every one.
    """
    if page.rect.width <= 0:
        return [], 0
    zoom = INK_WIDTH_PX / page.rect.width
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
    width, height, stride = pixmap.width, pixmap.height, pixmap.stride
    samples = pixmap.samples
    if width < 40 or height < 40:
        return [], 0

    counts = [0] * width
    rows = 0
    for y in range(0, height, INK_ROW_STEP):
        start = y * stride
        row = samples[start : start + width]
        inked = False
        for x, value in enumerate(row):
            if value < INK_LEVEL:
                counts[x] += 1
                inked = True
        if inked:
            rows += 1

    if rows < MIN_INKED_ROWS:
        return [], rows
    return [count / rows for count in counts], rows


def _columns_from_ink(page: fitz.Page) -> int:
    """The column count of a page that has no text layer to ask."""
    coverage, _rows = _ink_coverage(page)
    if not coverage:
        return 1

    # What the body manages, taken as the median of the columns that carry any
    # ink at all: robust to the margins on one side and to a heavy rule on the
    # other in a way that the mean and the maximum are not.
    marked = sorted(share for share in coverage if share > MIN_GUTTER_COVERAGE)
    if len(marked) < 2:
        return 1
    body = marked[len(marked) // 2]
    threshold = max(MIN_GUTTER_COVERAGE, body * GUTTER_COVERAGE_SHARE)

    inked = [x for x, share in enumerate(coverage) if share > threshold]
    if len(inked) < 2:
        return 1

    # Everything from here is the block path's reasoning, applied to runs of
    # inked pixels instead of runs of text: the same gutters, the same bands,
    # the same requirement that a band carry its share before it counts.
    runs: list[_Block] = []
    start = previous = inked[0]
    for x in inked[1:]:
        if x > previous + 1:
            runs.append(_Block(float(start), float(previous + 1)))
            start = x
        previous = x
    runs.append(_Block(float(start), float(previous + 1)))

    # A book page is usually printed inside a ruled frame, and a frame's left and
    # right sides are vertical lines: present in every row, so as solid as any
    # column of text, and standing a whole margin clear of the body. Left in,
    # they put a gutter the width of the margin on both sides of the page and
    # the page comes back as four columns. Nothing a finger's width across is a
    # column of anything, so the rules go before the lanes are measured.
    total_span = runs[-1].x1 - runs[0].x0
    body = [run for run in runs if run.width >= max(2.0, total_span * MIN_COLUMN_RATIO)]
    if not body:
        return 1
    left, right = body[0].x0, body[-1].x1

    gutters = _gutters(body, left, right, min_absolute=0.0)
    if not gutters:
        return 1

    bands = _bands(gutters, left, right)
    total = sum(coverage)
    if total <= 0:
        return 1
    weights = [sum(coverage[int(start) : int(end)]) for start, end in bands]
    return _columns_in(
        bands, weights, total * MIN_BAND_SHARE, len(coverage) / 2, float(len(coverage))
    )


def page_columns(page: fitz.Page) -> int:
    """How many columns this page is set in, as far as the page itself shows.

    The text blocks are asked twice. The first pass asks the whole page, which
    settles any page whose columns run its full height. The second discounts the
    blocks that span the page — the running head over a two-column body hides
    the gutter underneath it — but only while those are the minority, because on
    a single-column page they are the body itself.

    A scan has no blocks to ask at all, and falls through to its ink.
    """
    blocks = _text_blocks(page)
    if not blocks:
        return _columns_from_ink(page)

    centre = (page.rect.x0 + page.rect.x1) / 2
    width = page.rect.width
    columns = _columns_from(blocks, centre, width)
    if columns > 1:
        return columns

    left = min(block.x0 for block in blocks)
    right = max(block.x1 for block in blocks)
    span = right - left
    narrow = [block for block in blocks if block.width <= span * SPANNING_RATIO]
    spanning = len(blocks) - len(narrow)
    if spanning == 0 or spanning > len(blocks) * MAX_SPANNING_SHARE:
        return 1
    return _columns_from(narrow, centre, width)


def detect_columns(pdf_path: Path, limit: int = 0) -> list[int]:
    """The column count of each page of `pdf_path`, in order.

    `limit` caps the pages read, to stay in step with a conversion that was
    itself capped.
    """
    counts: list[int] = []
    with fitz.open(pdf_path) as document:
        pages = len(document) if limit <= 0 else min(len(document), limit)
        for number in range(pages):
            counts.append(page_columns(document[number]))
    return counts


def source_columns(pdf_path: Path, limit: int = PROBE_PAGES) -> int:
    """The most columns any of the first `limit` pages of `pdf_path` is set in.

    What the upload needs to know, and all it needs to know: a source set in one
    column has no second column to put anything in, so the only output it can
    have is one column, and offering the choice would be offering a setting that
    cannot do anything. The most, rather than the usual, because a document with
    one two-column page in it is still a document the choice applies to.

    A PDF whose layout cannot be read comes back as one column — the same answer
    the conversion itself falls back to, so the browser is never offered a choice
    the conversion would then decline to act on.
    """
    try:
        return max(detect_columns(pdf_path, limit=limit), default=1)
    except Exception:
        return 1
