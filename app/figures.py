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

A crop leaves here as a file, and a file has to say how big it is: the writers
place it at its natural size, and the natural size of a PNG is its pixel count
divided by the resolution written in its header. So the resolution is recorded,
and it is chosen rather than assumed — no higher than the detail the page can
actually supply, because enlarging a scan enlarges only the scan.
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

# Crops are written here, under the job's working directory, and referred to by
# this path — which is what keeps the reference portable.
FIGURE_DIR = "figures"

# Boxes are reported on a 0–1000 grid, independent of the page's real size.
_GRID = 1000.0
# How far past the grid or the rendered image a coordinate may sit and still be
# read as rounding rather than as a different unit.
_SLACK = 1.02
# A few points of air, so a stroke on the boundary is not sliced off.
_PAD_PT = 4.0
# Plausibility limits: below the first a box is a scrap of a letter, above the
# second the model has simply boxed the whole page.
_MIN_SIDE = 0.04  # of the page's own width/height
_MAX_AREA = 0.92  # of the page's area
# A scan coarser than this is rendered at this resolution anyway. Below it the
# crop is measured in tens of pixels, and the few bytes saved by honouring the
# page's own resolution are not worth handing Word an image it cannot draw.
_MIN_CROP_DPI = 72.0


class _PageDetail:
    """How much detail a page really holds, measured once and asked many times.

    Rendering resolution is a choice with two failure modes. Too low and vector
    artwork — a diagram the typesetter drew, which is a set of instructions and
    has no resolution of its own — comes out ragged. Too high and a scanned
    page, which has exactly as much detail as the pixels behind it, is
    interpolated up into a blur: the paper grain, the JPEG blocks and the
    show-through of the page behind all get larger, the figure gains nothing,
    and the file is several times the size for it.

    So the page is asked which kind of region it is being cropped from before
    the crop is taken. Text and drawings mean there is no ceiling. Nothing but
    an image means the ceiling is that image's own resolution.
    """

    def __init__(self, page: fitz.Page) -> None:
        self._words: list[fitz.Rect] = []
        self._drawings: list[fitz.Rect] = []
        self._images: list[tuple[fitz.Rect, float, float]] = []
        try:
            # Gathered once per page: `get_drawings` in particular walks the
            # whole content stream, and a page can carry several figures.
            self._words = [fitz.Rect(word[:4]) for word in page.get_text("words")]
            self._drawings = [fitz.Rect(drawing["rect"]) for drawing in page.get_drawings()]
            self._images = [
                (fitz.Rect(info["bbox"]), info["width"], info["height"])
                for info in page.get_image_info()
            ]
        except Exception:
            # A page whose contents will not parse has said nothing about its
            # own resolution, and nothing known already means do not cap. The
            # figure is still cut; it is cut the way it was before this existed.
            self._words = self._drawings = self._images = []

    def ceiling(self, rect: fitz.Rect) -> float | None:
        """Resolution beyond which `rect` gains nothing, or None if there is none."""
        if any(word.intersects(rect) for word in self._words):
            return None
        if any(drawing.intersects(rect) for drawing in self._drawings):
            return None

        # Where images overlap, the finest of them is what the region can show.
        best = 0.0
        for bbox, pixels_across, pixels_down in self._images:
            if bbox.is_empty or not bbox.intersects(rect):
                continue
            best = max(
                best,
                72.0 * pixels_across / bbox.width,
                72.0 * pixels_down / bbox.height,
            )
        return best or None


def _crop_dpi(detail: _PageDetail, rect: fitz.Rect) -> int:
    """The resolution to rasterise `rect` at, in DPI."""
    dpi = float(settings.diagram_dpi)
    if settings.crop_native_dpi:
        ceiling = detail.ceiling(rect)
        if ceiling is not None:
            dpi = min(dpi, max(ceiling, _MIN_CROP_DPI))
    return max(1, round(dpi))


def strip_boxes(markdown: str) -> str:
    """Remove the box comments, leaving the captions they were attached to."""
    return BOX_COMMENT_RE.sub("", markdown)


def _caption(text: str) -> str:
    """The readable part of a figure line: no `_Figure:` label, no mark-up."""
    caption = text.strip().strip("*_").strip()
    caption = re.sub(r"^_?Figure\s*[:.\-]\s*", "", caption, flags=re.I).strip().strip("*_")
    # Square brackets would close the alt text of the image link it becomes.
    return caption.replace("[", "(").replace("]", ")").strip()


def _values(raw: str) -> list[float] | None:
    """The four numbers of a box comment, or None if it is not four numbers."""
    try:
        values = [float(part) for part in raw.split(",")]
    except ValueError:
        return None
    return values if len(values) == 4 else None


def page_scale(
    boxes: list[list[float]], render: tuple[int, int] | None
) -> tuple[float, float] | None:
    """What one page's boxes must be multiplied by to land on the 0–1000 grid.

    The grid is asked for, but a model handed a picture frequently answers in
    that picture's pixels instead, and one number alone cannot tell the two
    apart — 400 is a legal grid coordinate and a legal pixel column. A
    coordinate past the top of the grid is the giveaway, so the unit is settled
    once for the whole page rather than box by box: a model does not change
    units halfway down a page, and one oversized box therefore reveals the
    convention its well-behaved neighbours were also using.

    Pixel readings are believed only if they fit the image the model was
    actually shown. Numbers that fit neither the grid nor the render mean
    nothing at all, and None asks the caller to drop them — the captions
    survive, which beats cropping a confidently wrong region of the page.
    """
    xs = [value for box in boxes for value in (box[0], box[2])]
    ys = [value for box in boxes for value in (box[1], box[3])]
    if not xs or max(xs + ys) <= _GRID:
        return 1.0, 1.0
    if render is None:
        return None
    render_width, render_height = render
    if render_width <= 0 or render_height <= 0:
        return None
    if max(xs) > render_width * _SLACK or max(ys) > render_height * _SLACK:
        return None
    return _GRID / render_width, _GRID / render_height


def _rect(
    raw: str,
    width: float,
    height: float,
    scale: tuple[float, float] = (1.0, 1.0),
) -> fitz.Rect | None:
    """A reported box as a page rectangle, or None if it cannot be believed."""
    values = _values(raw)
    if values is None:
        return None

    scale_x, scale_y = scale
    scaled = [values[0] * scale_x, values[1] * scale_y, values[2] * scale_x, values[3] * scale_y]
    x0, y0, x1, y1 = (max(0.0, min(_GRID, value)) for value in scaled)
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


def place_figures(
    pdf_path: Path,
    page_index: int,
    markdown: str,
    work_dir: Path,
    render: tuple[int, int] | None = None,
) -> str:
    """Replace each located figure in `markdown` with the crop it points at.

    Lines the model marked with a box become ordinary Markdown images, which the
    document writers already know how to place. A line whose box does not survive
    inspection keeps its caption and loses only the comment.

    Crops are written under `work_dir` and referred to relative to it, never by
    the path they happen to have on this machine. The Markdown outlives the
    process that made it: it is saved beside the document and handed back over
    the API, and a conversion whose pages are transcribed by one worker and
    assembled by another has no filesystem in common. An absolute path would
    also publish the host's directory layout to whoever downloads the `.md`.

    `render` is the pixel size of the page image the model was shown. It is what
    lets a box reported in that image's pixels be recognised and converted; pass
    it whenever it is known, or boxes in pixels have to be thrown away.
    """
    lines = markdown.splitlines()
    targets = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := FIGURE_RE.match(line)) is not None
    ]
    if not targets:
        return strip_boxes(markdown)

    # Settled before any box is used, because it is a property of the page's
    # answer as a whole rather than of any one line of it.
    parsed = [values for match in (m for _, m in targets) if (values := _values(match.group("box")))]
    scale = page_scale(parsed, render)
    if scale is None:
        return strip_boxes(markdown)

    out_dir = work_dir / FIGURE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as doc:
        if page_index >= doc.page_count:
            return strip_boxes(markdown)
        page = doc.load_page(page_index)
        width, height = page.rect.width, page.rect.height
        detail = _PageDetail(page)

        for count, (index, match) in enumerate(targets, start=1):
            caption = _caption(match.group("caption"))
            rect = _rect(match.group("box"), width, height, scale)
            if rect is None or rect.is_empty:
                lines[index] = strip_boxes(lines[index])
                continue
            name = f"page-{page_index + 1:04d}-figure-{count}.png"
            path = out_dir / name
            try:
                dpi = _crop_dpi(detail, rect)
                zoom = dpi / 72.0
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
                # PyMuPDF stamps every pixmap 96 DPI whatever it was rendered
                # at, and a PNG that understates its resolution is placed at
                # `dpi / 96` times the size the page drew it — a figure cut at
                # 300 DPI arrives in Word three times too large, carrying no
                # more detail than it did before. Say what it really is.
                pixmap.set_dpi(dpi, dpi)
                pixmap.save(path)
            except Exception:
                lines[index] = strip_boxes(lines[index])
                continue
            lines[index] = f"![{caption}]({FIGURE_DIR}/{name})"

    return "\n".join(lines)
