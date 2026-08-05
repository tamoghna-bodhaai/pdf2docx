"""Work out what a positioned page *is*, so that it can be written as a document.

Replica mode reproduces a page by refusing to flow: every line becomes a frame
pinned to a coordinate. That is exact, and it is also not a document — a
sentence cannot be edited without editing a box, and a word added to one line
never pushes the next one along. This module does the opposite. It reads the same
positioned model and recovers the structure the coordinates imply: which lines
are one paragraph, which is a heading, which of the scattered fragments are the
pieces of one equation, and which lines are the running header that appears on
every page and belongs in none of them.

Nothing here reads the page image or asks a model anything. Every decision is
made from geometry and from the font each span was set in, both of which the PDF
states outright.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .pdf_extract import Box, ImageItem, MathItem, PageLayout, TableItem, TextLine, mark_scripts

# --------------------------------------------------------------------------- #
# Document model
# --------------------------------------------------------------------------- #


@dataclass
class TextBlock:
    bbox: Box
    lines: list[TextLine]
    kind: str = "paragraph"  # paragraph | heading
    level: int = 1  # heading level, when kind is "heading"
    marker: str = ""  # the "12." of a numbered item, set apart in its own column
    align: str = ""  # "center" when the block was set that way


@dataclass
class MathBlock:
    bbox: Box
    math_id: int
    label: str = ""  # the "…(1)" a displayed equation is numbered with
    marker: str = ""


@dataclass
class ImageBlock:
    bbox: Box
    item: ImageItem
    marker: str = ""


@dataclass
class TableBlock:
    bbox: Box
    item: TableItem
    marker: str = ""


Block = TextBlock | MathBlock | ImageBlock | TableBlock


@dataclass
class PageBlocks:
    number: int
    blocks: list[Block] = field(default_factory=list)
    # Nothing was set on this page at all — as opposed to a page left empty by
    # having its one paragraph joined onto the page before it.
    blank: bool = False


# --------------------------------------------------------------------------- #
# Tuning
# --------------------------------------------------------------------------- #

# How much of a page's height counts as the header/footer band.
_MARGIN_BAND = 0.075
# What share of pages a line must appear on, in that band, to be running furniture.
# A header that only runs on the right-hand pages, or that a full-page figure
# displaces here and there, still repeats far too often to be part of the text.
_RUNNING_SHARE = 0.25

# Two lines belong to the same visual row when their vertical spans overlap by
# this much of the shorter one. A superscript sits almost entirely inside its
# base line's band, which is what this recovers.
_ROW_OVERLAP = 0.55

# A gap larger than this many line pitches starts a new paragraph.
_PARAGRAPH_GAP = 1.35
# A line ending this far short of the column's right edge ends its paragraph.
_SHORT_LINE_RATIO = 0.055
# Text this much larger than the body is a heading.
_HEADING_RATIO = 1.22

# A line must reach this share of the page width before it is taken as evidence
# of where the column's margins are.
_FULL_LINE_RATIO = 0.35
# How much of a block must lie inside a picture before the picture is taken to
# have drawn it already.
_COVERED = 0.6
# Artwork no bigger than this is a piece of an equation — a radical, a fraction
# bar, an extensible brace — which the equation redraws for itself. Touching one
# at all is enough to know it would be printed twice.
_FRAGMENT_SIDE = 36.0
_FRAGMENT_COVERED = 0.25

_MARKER_RE = re.compile(r"^\(?(?:\d{1,3}|[A-Za-z]|[ivxIVX]{1,5})[.)]$")
# An equation number: dots, brackets and a numeral, and nothing else.
_EQUATION_LABEL_RE = re.compile(r"^[.…\s]*\(\s*[\dA-Za-z.]{1,6}\s*\)[.…\s]*$")
_MAX_MARKER_WIDTH = 34.0
# How far below a marker its item may start.
_MARKER_REACH = 22.0
# The space that sets a list number apart from the text it labels. Without it,
# the "1." of "1.5 m" would be read as the start of an item.
_MARKER_GAP = 3.0

# A centred block sits this close to the middle of the column, and takes up no
# more of it than this.
_CENTRE_TOLERANCE = 0.04
_CENTRE_MAX_WIDTH = 0.6

# The right margin is where the longest lines end, not where the average one
# does: every paragraph ends with a short line, and half a page of mathematics
# is short lines only.
_RIGHT_PERCENTILE = 0.9
# Margins the writer will not go outside, however the page was set.
_MIN_MARGIN, _MAX_MARGIN = 36.0, 108.0

# Sizes and positions are rounded to this many points before two pictures are
# called the same picture.
_PICTURE_KEY_STEP = 4.0

_DIGITS_RE = re.compile(r"\d+")
_SPACE_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Running headers and footers
# --------------------------------------------------------------------------- #


def _furniture_key(text: str) -> str:
    """A line's identity for the purpose of spotting repetition.

    Digits are dropped so that a page number changing from page to page does not
    disguise the fact that the same footer is on all of them. That leaves any
    line of bare numerals looking identical to every other, which matters
    because a numbered document is full of them: with its digits gone, the "4."
    heading an answer is indistinguishable from the "12." heading another, and
    a rule meant for footers deletes the numbering of every list in the book.
    They are told apart by their punctuation — a number labelling an item
    carries a "." or a ")", a folio never does.
    """
    key = _SPACE_RE.sub(" ", _DIGITS_RE.sub("", text)).strip().lower()
    if any(character.isalpha() for character in key):
        return key
    return "#" if text.strip().isdigit() else ""


def running_furniture(layouts: list[PageLayout]) -> set[str]:
    """Lines that repeat in the margins of page after page.

    A running header is part of the page, not of the document. Carried into
    flowing text it interrupts a sentence every time a page turns.
    """
    if len(layouts) < 3:
        return set()  # too few pages for repetition to mean anything
    counts: Counter[str] = Counter()
    for layout in layouts:
        band = layout.height * _MARGIN_BAND
        seen: set[str] = set()
        for line in layout.lines:
            if line.bbox[1] > band and line.bbox[3] < layout.height - band:
                continue
            key = _furniture_key(line.text)
            if key and key not in seen:
                seen.add(key)
                counts[key] += 1
    threshold = max(3, int(len(layouts) * _RUNNING_SHARE))
    return {key for key, count in counts.items() if count >= threshold}


def _picture_key(bbox: Box) -> tuple[int, int, int]:
    """A picture's identity for the purpose of spotting repetition.

    Its size and the height it sits at, rounded: a page number in a printed tab
    is drawn at the same size on every page, and moves from one side of the head
    to the other between the left- and right-hand ones.
    """
    step = _PICTURE_KEY_STEP
    return (
        round((bbox[2] - bbox[0]) / step),
        round((bbox[3] - bbox[1]) / step),
        round(bbox[1] / step),
    )


def running_pictures(layouts: list[PageLayout]) -> set[tuple[int, int, int]]:
    """Marks drawn in the margins of page after page.

    Not every running head is text. This document sets its page numbers in a
    grey tab, which arrives as artwork and so survives every test written for a
    repeated *line* — and lands in the middle of the flowing text as a picture
    of the number 4.
    """
    if len(layouts) < 3:
        return set()
    counts: Counter[tuple[int, int, int]] = Counter()
    for layout in layouts:
        band = layout.height * _MARGIN_BAND
        seen: set[tuple[int, int, int]] = set()
        for image in layout.images:
            # Wholly within the band: a figure that merely starts high on the
            # page is not furniture.
            if image.bbox[3] > band and image.bbox[1] < layout.height - band:
                continue
            key = _picture_key(image.bbox)
            if key not in seen:
                seen.add(key)
                counts[key] += 1
    threshold = max(3, int(len(layouts) * _RUNNING_SHARE))
    return {key for key, count in counts.items() if count >= threshold}


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


def _line_size(line: TextLine) -> float:
    sizes = [span.size for span in line.spans if span.text.strip()]
    return max(sizes) if sizes else 10.0


def _vertical_overlap(a: Box, b: Box) -> float:
    top, bottom = max(a[1], b[1]), min(a[3], b[3])
    shorter = min(a[3] - a[1], b[3] - b[1])
    if shorter <= 0:
        return 0.0
    return max(0.0, bottom - top) / shorter


def _merge_rows(lines: list[TextLine]) -> list[TextLine]:
    """Rejoin the pieces a typesetter left on the same visual row.

    An extractor splits a line wherever the type changes position — a superscript,
    a unit, a raised footnote mark — so `3 ms` and its `-1` arrive as two lines
    several points apart. Read in sequence they become two paragraphs.
    """
    rows: list[list[TextLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        for row in rows:
            if _vertical_overlap(row[0].bbox, line.bbox) >= _ROW_OVERLAP:
                row.append(line)
                break
        else:
            rows.append([line])

    merged: list[TextLine] = []
    for row in rows:
        parts = sorted(row, key=lambda item: item.bbox[0])
        spans = [span for part in parts for span in part.spans]
        bbox = (
            min(part.bbox[0] for part in parts),
            min(part.bbox[1] for part in parts),
            max(part.bbox[2] for part in parts),
            max(part.bbox[3] for part in parts),
        )
        math_ids = {part.math_id for part in parts if part.math_id is not None}
        spans = sorted(spans, key=lambda span: span.bbox[0])
        # Now that the row is whole again, the scripts on it can be told from the
        # type they ride on. Judged line by line the exponent of `3 ms` has no
        # full-size text beside it to be smaller than, because the extractor put
        # it on a line of its own — which is exactly why it is being rejoined.
        mark_scripts(spans)
        merged.append(
            TextLine(
                bbox=bbox,
                spans=spans,
                math_id=math_ids.pop() if len(math_ids) == 1 else None,
            )
        )
    return sorted(merged, key=lambda item: (item.bbox[1], item.bbox[0]))


# --------------------------------------------------------------------------- #
# Page metrics
# --------------------------------------------------------------------------- #


@dataclass
class Metrics:
    """What this document's ordinary text looks like, measured across all pages."""

    size: float = 10.0
    pitch: float = 12.0
    left: float = 72.0
    right: float = 540.0
    # Where list numbers are set, when they have a column of their own. Equal to
    # `left` in a document that does not indent its lists.
    marker_left: float = 72.0
    page_width: float = 612.0
    page_height: float = 792.0
    top: float = 72.0
    bottom: float = 72.0

    @property
    def width(self) -> float:
        return max(self.right - self.left, 1.0)

    @property
    def indent(self) -> float:
        """How far the body column is set in from the list numbers."""
        return max(self.left - self.marker_left, 0.0)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _cluster_peak(counts: Counter[float], tolerance: float = 2.0) -> float:
    """The dominant value, counting near neighbours as the same value.

    Line lefts do not repeat exactly — 76.4 and 76.6 are the same margin set by
    two different first letters — and splitting them lets a genuinely different
    column win on a plurality of one.
    """
    if not counts:
        return 0.0
    best, score = 0.0, -1.0
    for value in counts:
        weight = sum(count for other, count in counts.items() if abs(other - value) <= tolerance)
        if weight > score:
            best, score = value, weight
    return best


def measure(layouts: list[PageLayout]) -> Metrics:
    """Measure the body text, ignoring everything that is not a full line.

    The margins have to be read from lines that actually reach them. A page of
    mathematics is mostly fragments — a lone `2`, a bracket, a superscript — and
    averaging those in puts the right margin somewhere in the middle of the
    column, after which every line looks short and every line starts a paragraph.

    A numbered document has two left margins, not one: the column its numbers
    are set in and the column its text is set in. Measuring them together elects
    whichever has more lines and calls it "the" margin, and every judgement that
    follows — what is a list number, what is an indent — is then made against a
    margin that half the page does not use.
    """
    sizes: Counter[float] = Counter()
    lefts: Counter[float] = Counter()
    marker_lefts: Counter[float] = Counter()
    rights: list[float] = []
    pitches: list[float] = []
    tops: list[float] = []
    bottoms: list[float] = []
    pages: Counter[tuple[float, float]] = Counter()

    for layout in layouts:
        pages[(layout.width, layout.height)] += 1
        rows = _merge_rows([line for line in layout.lines if line.text.strip()])
        for line in rows:
            sizes[round(_line_size(line), 1)] += len(line.text)
        if rows:
            tops.append(min(line.bbox[1] for line in rows))
            bottoms.append(max(line.bbox[3] for line in rows))
        full = [
            line
            for line in rows
            if line.bbox[2] - line.bbox[0] > _FULL_LINE_RATIO * layout.width
        ]
        for line in full:
            marker = _leading_marker(line)
            if marker is not None:
                marker_lefts[round(marker.bbox[0], 1)] += 1
                lefts[round(_after(line, marker).bbox[0], 1)] += 1
            else:
                lefts[round(line.bbox[0], 1)] += 1
            rights.append(line.bbox[2])
        for previous, following in zip(full, full[1:]):
            step = following.bbox[1] - previous.bbox[1]
            if 4.0 < step < 40.0:
                pitches.append(step)

    size = sizes.most_common(1)[0][0] if sizes else 10.0
    width, height = pages.most_common(1)[0][0] if pages else (612.0, 792.0)
    left = _cluster_peak(lefts) if lefts else 72.0
    right = _percentile(rights, _RIGHT_PERCENTILE) if rights else width - 72.0
    marker_left = _cluster_peak(marker_lefts) if marker_lefts else left
    return Metrics(
        size=size,
        pitch=statistics.median(pitches) if pitches else size * 1.25,
        left=left,
        right=right,
        marker_left=min(marker_left, left),
        page_width=width,
        page_height=height,
        top=_clamp(statistics.median(tops) if tops else 72.0, _MIN_MARGIN, _MAX_MARGIN),
        bottom=_clamp(
            height - (statistics.median(bottoms) if bottoms else height - 72.0),
            _MIN_MARGIN,
            _MAX_MARGIN,
        ),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# Segmentation
# --------------------------------------------------------------------------- #


def _visible(line: TextLine) -> list:
    return [span for span in line.spans if span.text.strip()]


def _leading_marker(line: TextLine):
    """The list number at the head of a line, if that is what the head is.

    The number may stand alone on its row, or — far more often, because the two
    are set on the same baseline and the row merge puts them back together —
    it may be the first span of the line it labels. Both have to be recognised,
    or half a list keeps its numbering and half has it swallowed into the prose.
    """
    spans = _visible(line)
    if not spans:
        return None
    head = spans[0]
    if not _MARKER_RE.match(head.text.strip()):
        return None
    if head.bbox[2] - head.bbox[0] > _MAX_MARKER_WIDTH:
        return None
    # Set apart from what follows: "1." labelling a paragraph, not the "1." of
    # a decimal or an initial in a name.
    if len(spans) > 1 and spans[1].bbox[0] - head.bbox[2] < _MARKER_GAP:
        return None
    return head


def _after(line: TextLine, marker) -> TextLine:
    """The line with its list number taken off the front."""
    spans = [span for span in line.spans if span is not marker]
    if not spans or not any(span.text.strip() for span in spans):
        return line
    return TextLine(bbox=_spans_bbox(_visible(line)[1:] or spans), spans=spans)


def _is_marker(line: TextLine, metrics: Metrics) -> bool:
    """A list number set in its own column, to the left of the text it labels."""
    marker = _leading_marker(line)
    if marker is None or len(_visible(line)) > 1:
        return False
    return line.bbox[0] < metrics.left - 2.0


def _split_marker(line: TextLine, metrics: Metrics) -> tuple[str, TextLine]:
    """Separate a leading list number from the text it introduces."""
    if line.bbox[0] >= metrics.left - 2.0:
        return "", line
    marker = _leading_marker(line)
    if marker is None or len(_visible(line)) < 2:
        return "", line
    return marker.text.strip(), _after(line, marker)


def _alignment(bbox: Box, lines: list[TextLine], metrics: Metrics) -> str:
    """Whether a block was set centred in its column."""
    if len(lines) > 1 or bbox[0] <= metrics.left + 2.0:
        return ""
    if bbox[2] - bbox[0] > _CENTRE_MAX_WIDTH * metrics.width:
        return ""
    offset = 0.5 * (bbox[0] + bbox[2]) - 0.5 * (metrics.left + metrics.right)
    return "center" if abs(offset) <= _CENTRE_TOLERANCE * metrics.width else ""


def _starts_paragraph(previous: TextLine, current: TextLine, metrics: Metrics) -> bool:
    gap = current.bbox[1] - previous.bbox[3]
    if gap > _PARAGRAPH_GAP * metrics.pitch:
        return True
    if abs(_line_size(current) - _line_size(previous)) > 0.6:
        return True
    # A line that stops well short of the column's edge has nothing more to say:
    # whatever follows it is the start of something else. Without this every
    # paragraph in a block of evenly spaced prose runs into the next.
    if previous.bbox[2] < metrics.right - _SHORT_LINE_RATIO * metrics.width:
        return True
    # An indent of its own is the other way a paragraph announces itself.
    return current.bbox[0] > previous.bbox[0] + 0.9 * metrics.size


def _heading_level(size: float, metrics: Metrics) -> int:
    ratio = size / max(metrics.size, 1.0)
    if ratio >= 1.8:
        return 1
    if ratio >= 1.45:
        return 2
    return 3


def _overlap(inner: Box, outer: Box) -> float:
    """How much of `inner` lies inside `outer`, 0..1."""
    width = min(inner[2], outer[2]) - max(inner[0], outer[0])
    height = min(inner[3], outer[3]) - max(inner[1], outer[1])
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if width <= 0 or height <= 0 or area <= 0:
        return 0.0
    return (width * height) / area


def _covered_ratio(bbox: Box) -> float:
    """How much of a picture an equation must cover before it is the equation's."""
    if bbox[2] - bbox[0] <= _FRAGMENT_SIDE and bbox[3] - bbox[1] <= _FRAGMENT_SIDE:
        return _FRAGMENT_COVERED
    return _COVERED


def _spans_bbox(spans) -> Box:
    return (
        min(span.bbox[0] for span in spans),
        min(span.bbox[1] for span in spans),
        max(span.bbox[2] for span in spans),
        max(span.bbox[3] for span in spans),
    )


def _math_anchors(rows: list[TextLine], display_ids: set[int]) -> dict[int, int]:
    """Choose one span per inline equation to stand in for the whole of it.

    An inline expression is scattered down the page as much as a display one —
    a numerator on this row, its denominator on the next — so keeping every span
    where it lies turns the denominator into a paragraph containing "2". The
    anchor is preferred on the row the sentence is on, which is where the
    equation reads.
    """
    best: dict[int, tuple] = {}
    for row in rows:
        prose = any(span.math_id is None and span.text.strip() for span in row.spans)
        for span in row.spans:
            math_id = span.math_id
            if math_id is None or math_id in display_ids:
                continue
            key = (0 if prose else 1, round(span.bbox[1], 1), span.bbox[0])
            if math_id not in best or key < best[math_id][0]:
                best[math_id] = (key, id(span))
    return {math_id: ident for math_id, (_, ident) in best.items()}


def segment(
    layout: PageLayout,
    metrics: Metrics,
    furniture: set[str],
    furniture_pictures: set[tuple[int, int, int]] = frozenset(),
) -> PageBlocks:
    """Turn one positioned page into blocks, in reading order."""
    page = PageBlocks(number=layout.number)

    live = [(index, item) for index, item in enumerate(layout.maths) if not item.dropped]
    display_ids = {index for index, item in live if item.display}

    band = layout.height * _MARGIN_BAND

    def in_band(bbox: Box) -> bool:
        return bbox[1] <= band or bbox[3] >= layout.height - band

    # A picture that an equation crop already contains would be drawn twice — and
    # the offenders are the drawn radicals and brace glyphs, which are meaningless
    # on their own.
    pictures = [
        image
        for image in layout.images
        if not any(_overlap(image.bbox, item.bbox) > _covered_ratio(image.bbox) for _, item in live)
        and not (in_band(image.bbox) and _picture_key(image.bbox) in furniture_pictures)
    ]

    # The whole of a running head goes, not only the part of it that repeats
    # verbatim. A head is one row — a title on the left, a section name on the
    # right, a folio at the edge — and the section name changes with the section,
    # so on its own it never repeats often enough to be recognised. Left behind,
    # it opens the text of every chapter with the word CHEMISTRY.
    heads = [
        line.bbox
        for line in layout.lines
        if in_band(line.bbox) and _furniture_key(line.text) in furniture
    ]

    kept: list[TextLine] = []
    for line in layout.lines:
        if not line.text.strip():
            continue
        # Only in the margins. Furniture is recognised with its digits removed,
        # so a footer that is nothing but a page number reduces to the same key
        # as the "4." of a numbered answer — and applied to the whole page that
        # key deletes the numbering of every list in the document.
        if in_band(line.bbox) and (
            _furniture_key(line.text) in furniture
            or any(_vertical_overlap(line.bbox, head) > 0.5 for head in heads)
        ):
            continue
        # Labels inside a diagram travel with the diagram's own picture; read into
        # the text they become a paragraph of loose letters.
        if any(_overlap(line.bbox, picture.bbox) > _COVERED for picture in pictures):
            continue
        kept.append(line)

    # Rows are formed first, numbers and all: a list number shares a baseline
    # with the line it labels, so the two arrive as one row and the number has
    # to be taken off the front of it. Only where the item begins with an
    # equation or a figure does the number end up alone on its row.
    rows = _merge_rows(kept)
    markers = [row for row in rows if _is_marker(row, metrics)]
    marker_set = {id(row) for row in markers}
    rows = [row for row in rows if id(row) not in marker_set]

    # A display equation is placed from its own region, not from the rows its
    # fragments happen to fall in — one equation is scattered over several of
    # them, and emitting it per row would repeat it and leave the leftovers as
    # text. Inline maths stays where it is, for the writer to substitute.
    anchors = _math_anchors(rows, display_ids)
    body: list[TextLine] = []
    row_markers: dict[int, str] = {}
    for row in rows:
        marker, row = _split_marker(row, metrics)
        spans = [
            span
            for span in row.spans
            if span.math_id is None
            or (span.math_id not in display_ids and id(span) == anchors.get(span.math_id))
        ]
        if not spans or not "".join(span.text for span in spans).strip():
            # The row was a number in front of an equation or a figure. Keep the
            # number: the block it belongs to will claim it below.
            if marker:
                markers.append(TextLine(bbox=row.bbox, spans=_visible(row)[:1]))
                markers[-1].spans[0].text = marker
            continue
        line = TextLine(bbox=_spans_bbox(spans), spans=spans)
        if marker:
            row_markers[id(line)] = marker
        body.append(line)

    blocks: list[Block] = []
    run: list[TextLine] = []

    def flush() -> None:
        if not run:
            return
        bbox = (
            min(line.bbox[0] for line in run),
            min(line.bbox[1] for line in run),
            max(line.bbox[2] for line in run),
            max(line.bbox[3] for line in run),
        )
        size = max(_line_size(line) for line in run)
        kind = "heading" if size >= _HEADING_RATIO * metrics.size else "paragraph"
        blocks.append(
            TextBlock(
                bbox=bbox,
                lines=list(run),
                kind=kind,
                level=_heading_level(size, metrics) if kind == "heading" else 1,
                marker=row_markers.get(id(run[0]), ""),
                align=_alignment(bbox, run, metrics),
            )
        )
        run.clear()

    for row in body:
        # A numbered item always starts one, whatever the line before it did.
        if run and (id(row) in row_markers or _starts_paragraph(run[-1], row, metrics)):
            flush()
        run.append(row)
    flush()

    for index, item in live:
        if index in display_ids:
            blocks.append(MathBlock(bbox=item.bbox, math_id=index))
    for image in pictures:
        blocks.append(ImageBlock(bbox=image.bbox, item=image))
    for table in layout.tables:
        blocks.append(TableBlock(bbox=table.bbox, item=table))

    blocks.sort(key=lambda block: (round(block.bbox[1], 1), block.bbox[0]))
    _attach_markers(blocks, markers)
    page.blocks = _attach_equation_labels(blocks)
    page.blank = not page.blocks
    return page


def _attach_equation_labels(blocks: list[Block]) -> list[Block]:
    """Fold an equation's number back onto the equation.

    It is set out at the right margin, so it arrives as a block of its own and
    reads as a paragraph containing "…(1)" stranded on the line below the
    equation it numbers.
    """
    labels = {
        id(block): block
        for block in blocks
        if isinstance(block, TextBlock)
        and not block.marker
        and _EQUATION_LABEL_RE.match(" ".join(line.text for line in block.lines).strip())
    }
    if not labels:
        return blocks

    taken: set[int] = set()
    for block in blocks:
        if not isinstance(block, MathBlock):
            continue
        for key, candidate in labels.items():
            if key in taken:
                continue
            # Same rows as the equation: a number set beside it, not beneath it.
            if _vertical_overlap(block.bbox, candidate.bbox) < 0.3:
                continue
            block.label = " ".join(line.text for line in candidate.lines).strip()
            taken.add(key)
            break
    return [block for block in blocks if id(block) not in taken]


def _attach_markers(blocks: list[Block], markers: list[TextLine]) -> None:
    """Give each list number to the block it labels.

    A number left alone on its row is one whose item does not begin with a
    sentence: answer 6 *is* an equation, answer 1 *is* a diagram. Attaching it
    only to paragraphs strands it — the number becomes a paragraph of its own
    reading "6.", and the equation it introduces is left unnumbered above it.
    """
    for marker in markers:
        best: Block | None = None
        rank: tuple | None = None
        for block in blocks:
            if getattr(block, "marker", None):
                continue
            overlap = _vertical_overlap(block.bbox, marker.bbox)
            offset = block.bbox[1] - marker.bbox[1]
            if overlap < 0.3 and not -2.0 <= offset <= _MARKER_REACH:
                continue
            # Prefer the block the number sits beside, then the nearest below.
            key = (0 if overlap >= 0.3 else 1, abs(offset))
            if rank is None or key < rank:
                best, rank = block, key
        if best is not None:
            best.marker = marker.text.strip()


_SENTENCE_END = ".!?:;”\"’'"


def _joins_across(previous: Block, following: Block) -> bool:
    """Is the second block the rest of the first, cut in two by the page break?"""
    if not isinstance(previous, TextBlock) or not isinstance(following, TextBlock):
        return False
    if previous.kind != "paragraph" or following.kind != "paragraph":
        return False
    if following.marker or following.align or previous.align:
        return False
    if not previous.lines or not following.lines:
        return False
    tail = previous.lines[-1].text.rstrip()
    head = following.lines[0].text.lstrip()
    if not tail or not head:
        return False
    # A sentence that has not finished, continued by a word that does not start
    # one. Anything else is two paragraphs that happen to meet at a page break.
    return tail[-1] not in _SENTENCE_END and head[:1].islower()


def _join_pages(pages: list[PageBlocks]) -> None:
    """Put back together the sentence a page break ran through.

    The last line of one page and the first of the next are one paragraph in
    every sense but position, and leaving them as two shows up in the document
    as a line that stops in the middle and starts again below.
    """
    for previous, following in zip(pages, pages[1:]):
        if not previous.blocks or not following.blocks:
            continue
        if not _joins_across(previous.blocks[-1], following.blocks[0]):
            continue
        tail = previous.blocks[-1]
        head = following.blocks.pop(0)
        tail.lines.extend(head.lines)
        tail.bbox = (tail.bbox[0], tail.bbox[1], max(tail.bbox[2], head.bbox[2]), tail.bbox[3])


def build(layouts: list[PageLayout]) -> tuple[list[PageBlocks], Metrics]:
    """Analyse every page of a document together."""
    metrics = measure(layouts)
    furniture = running_furniture(layouts)
    pictures = running_pictures(layouts)
    pages = [segment(layout, metrics, furniture, pictures) for layout in layouts]
    _join_pages(pages)
    return pages, metrics
