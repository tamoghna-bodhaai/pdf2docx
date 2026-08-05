"""Cut the figures out of a transcribed page and put them back into the text.

A page the vision model reads for us comes back as words. Everything that was
not words — the ray diagram, the circuit, the free-body sketch — comes back as a
sentence describing it, and in a physics or maths book that is most of the
answer thrown away.

So the model is asked to report where each figure sits on the page as well as
what it shows, and the region it points at is cut straight out of the PDF at
diagram resolution and put back where the figure was. The box is a hint from a
model and is treated as one: anything implausible is dropped and the caption is
kept on its own, which is no worse than what the page would have said without it.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from .config import settings

# `> _Figure: a ray diagram_ <!--box: 120,340,860,610-->`
FIGURE_RE = re.compile(
    r"^\s*>?\s*(?P<caption>.*?)\s*<!--\s*box:\s*(?P<box>[\d.,\s-]+?)\s*-->\s*$"
)
# The same comment anywhere else on a line, so a stray one never reaches Word.
BOX_COMMENT_RE = re.compile(r"\s*<!--\s*box:[^>]*-->")

# Boxes are reported on a 0–1000 grid, independent of the page's real size.
_GRID = 1000.0
# A few points of air, so a stroke on the boundary is not sliced off.
_PAD_PT = 4.0
# Plausibility limits: below the first a box is a scrap of a letter, above the
# second the model has simply boxed the whole page.
_MIN_SIDE = 0.04  # of the page's own width/height
_MAX_AREA = 0.92  # of the page's area


def strip_boxes(markdown: str) -> str:
    """Remove the box comments, leaving the captions they were attached to."""
    return BOX_COMMENT_RE.sub("", markdown)


def _caption(text: str) -> str:
    """The readable part of a figure line: no `_Figure:` label, no mark-up."""
    caption = text.strip().strip("*_").strip()
    caption = re.sub(r"^_?Figure\s*[:.\-]\s*", "", caption, flags=re.I).strip().strip("*_")
    # Square brackets would close the alt text of the image link it becomes.
    return caption.replace("[", "(").replace("]", ")").strip()


def _rect(raw: str, width: float, height: float) -> fitz.Rect | None:
    """A reported box as a page rectangle, or None if it cannot be believed."""
    try:
        values = [float(part) for part in raw.split(",")]
    except ValueError:
        return None
    if len(values) != 4:
        return None

    x0, y0, x1, y1 = (max(0.0, min(_GRID, value)) for value in values)
    if x1 <= x0 or y1 <= y0:
        return None
    if (x1 - x0) < _MIN_SIDE * _GRID or (y1 - y0) < _MIN_SIDE * _GRID:
        return None
    if ((x1 - x0) / _GRID) * ((y1 - y0) / _GRID) > _MAX_AREA:
        return None

    rect = fitz.Rect(
        x0 / _GRID * width - _PAD_PT,
        y0 / _GRID * height - _PAD_PT,
        x1 / _GRID * width + _PAD_PT,
        y1 / _GRID * height + _PAD_PT,
    )
    return rect & fitz.Rect(0, 0, width, height)


def place_figures(pdf_path: Path, page_index: int, markdown: str, out_dir: Path) -> str:
    """Replace each located figure in `markdown` with the crop it points at.

    Lines the model marked with a box become ordinary Markdown images, which the
    document writers already know how to place. A line whose box does not survive
    inspection keeps its caption and loses only the comment.
    """
    lines = markdown.splitlines()
    targets = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := FIGURE_RE.match(line)) is not None
    ]
    if not targets:
        return strip_boxes(markdown)

    out_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as doc:
        if page_index >= doc.page_count:
            return strip_boxes(markdown)
        page = doc.load_page(page_index)
        width, height = page.rect.width, page.rect.height
        zoom = settings.diagram_dpi / 72.0

        for count, (index, match) in enumerate(targets, start=1):
            caption = _caption(match.group("caption"))
            rect = _rect(match.group("box"), width, height)
            if rect is None or rect.is_empty:
                lines[index] = strip_boxes(lines[index])
                continue
            path = out_dir / f"page-{page_index + 1:04d}-figure-{count}.png"
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
                pixmap.save(path)
            except Exception:
                lines[index] = strip_boxes(lines[index])
                continue
            lines[index] = f"![{caption}]({path.as_posix()})"

    return "\n".join(lines)
