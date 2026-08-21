"""Ask a second time where the figures on a page actually are.

The transcription pass reports a box for every figure it describes, and those
boxes are the weakest thing it produces. Measured against boxes read off the
page by hand, they land at a mean IoU of about 0.13 — good enough to name a
figure, not good enough to cut one out. What arrives in the document is then a
crop of the paragraph beside the diagram, or of the equation above it.

Two things fix most of that, and both are here.

The first is that a model reads a coordinate off a line drawn through the page
far more reliably than it estimates one. So the page is rendered a second time
with a numbered grid ruled over it, and the model is asked to read the box off
the rules rather than to judge it.

The second is that locating and transcribing compete. Asked to do both at once
the same model scores about 0.38; asked only to locate, on the same gridded
image, about 0.62 — every figure on the test page found and usable. So this is
its own request, made only for pages that have figures at all.

The located boxes are matched back to the transcription's figure lines by
reading order, which is the one thing both passes agree on and neither has to
estimate. Anything that does not line up keeps the box it already had: this
pass is an improvement on the transcription's guess, never a precondition for
it, and a page still converts if the model, the network or the parse fails.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import fitz
from openai import OpenAI

from .config import settings
from .figures import BOX_COMMENT_RE, FIGURE_RE, page_scale
from .model_policy import require_model_allowed
from .pdf_render import page_zoom
from .vision import _usage_fields

# A rule every 50 units, numbered every 100 — dense enough to read a small
# figure off, sparse enough not to bury the page under its own ruler.
GRID_STEP = 50
LABEL_STEP = 100
_GRID = 1000.0

# The answer itself is a handful of short lines, but a reasoning model spends
# this budget thinking before it writes any of them: given only enough for the
# answer, it is cut off mid-reasoning and returns a truncated list — or a
# fragment of the reasoning itself. Budgeted for the thinking, not the answer.
MAX_TOKENS = 8000

PROMPT = """\
This is a page from a book, with a coordinate grid ruled over it. Red vertical \
lines give x, numbered along the top; blue horizontal lines give y, numbered down \
the left side. Both run from 0 to 1000, with a line every 50.

Find every figure on the page: diagrams, charts, illustrations, photographs, \
circuit and ray drawings. A display equation is NOT a figure. A table is NOT a \
figure. Body text is NOT a figure.

For each one, READ ITS BOUNDING BOX OFF THE RULED LINES — do not estimate it. The \
box must enclose the whole figure, including every arrow, axis, tick value and \
label that belongs to it, and must exclude the surrounding paragraph text and any \
numbered equation that is not part of it.

Output one line per figure, in reading order — for a multi-column page that means \
the whole of the left column top to bottom, then the next column. Use exactly this \
format, with the four numbers on the 0-1000 grid:

x0,y0,x1,y1 | a short description

Output nothing else. If the page has no figures at all, output nothing.
"""

# `520,140,880,430 | a ray diagram`
LINE_RE = re.compile(r"^\s*(?P<box>-?[\d.]+\s*,\s*-?[\d.]+\s*,\s*-?[\d.]+\s*,\s*-?[\d.]+)\s*(?:\|.*)?$")


def gridded_page(pdf_path: Path, page_index: int) -> tuple[bytes, tuple[int, int]]:
    """The page as a PNG with a numbered coordinate grid ruled over it.

    The rules are drawn onto a page loaded for this call and thrown away with it;
    nothing is written back to the PDF. They are drawn in colour and kept thin so
    they read as an overlay rather than as part of the artwork underneath.
    """
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        width, height = page.rect.width, page.rect.height
        for value in range(0, int(_GRID) + 1, GRID_STEP):
            x = value / _GRID * width
            y = value / _GRID * height
            major = value % LABEL_STEP == 0
            page.draw_line(
                fitz.Point(x, 0),
                fitz.Point(x, height),
                color=(1, 0.45, 0.45) if major else (1, 0.82, 0.82),
                width=0.8 if major else 0.4,
            )
            page.draw_line(
                fitz.Point(0, y),
                fitz.Point(width, y),
                color=(0.45, 0.45, 1) if major else (0.82, 0.82, 1),
                width=0.8 if major else 0.4,
            )
            if major:
                page.insert_text(fitz.Point(x + 2, 10), str(value), fontsize=8, color=(0.8, 0, 0))
                page.insert_text(fitz.Point(2, y + 9), str(value), fontsize=8, color=(0, 0, 0.8))

        zoom = page_zoom(width, height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png"), (pixmap.width, pixmap.height)


def parse_boxes(reply: str, render: tuple[int, int]) -> list[tuple[float, float, float, float]]:
    """The boxes in a reply, on the 0-1000 grid, in the order they were given.

    The reply is read in whatever units it turns out to be in — a model shown an
    image commonly answers in that image's pixels — using the same rule the
    transcription's own boxes go through, so both passes are believed the same way.
    """
    raw: list[list[float]] = []
    for line in reply.splitlines():
        match = LINE_RE.match(line.strip().lstrip("-*").strip())
        if match:
            raw.append([float(part) for part in match.group("box").split(",")])
    if not raw:
        return []
    scale = page_scale(raw, render)
    if scale is None:
        return []
    scale_x, scale_y = scale
    return [(b[0] * scale_x, b[1] * scale_y, b[2] * scale_x, b[3] * scale_y) for b in raw]


def _centre(box: tuple[float, float, float, float]) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def align(
    hints: list[tuple[float, float, float, float] | None],
    located: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float] | None]:
    """Which located box belongs to which figure line, by reading order.

    When both passes found the same number of figures they are simply paired off:
    both list them in reading order, and that agreement is worth more than any
    coordinate either of them reported.

    When the counts differ — one pass saw a figure the other missed — the lists
    are aligned as sequences, keeping order, choosing the pairing with the least
    total disagreement overall rather than the one that looks best at each step
    in turn. Taking the nearest box for each figure as it comes is what a greedy
    walk would do, and on a real page it goes wrong: a badly placed hint claims
    the box belonging to the figure after it, and every figure below shifts up by
    one. The hints are unreliable by assumption — that is why this pass exists —
    so no single one of them is allowed to decide the alignment by itself.

    A figure left with no box comes back as None, which is the caller's signal to
    keep what the transcription gave it.
    """
    if len(hints) == len(located):
        return list(located)

    count, found = len(hints), len(located)
    if count == 0 or found == 0:
        return [None] * count

    # Leaving a figure unpaired costs more than any pairing on the grid can, so
    # as many as possible are paired; the alignment only chooses which.
    unpaired = 1e6

    def distance(i: int, j: int) -> float:
        if hints[i] is None:
            return 0.0
        hx, hy = _centre(hints[i])  # type: ignore[arg-type]
        cx, cy = _centre(located[j])
        return ((cx - hx) ** 2 + (cy - hy) ** 2) ** 0.5

    # best[i][j]: least cost having settled the first i figures against the
    # first j located boxes. Moves are: skip a box, pair, or leave a figure.
    best = [[0.0] * (found + 1) for _ in range(count + 1)]
    move: list[list[str]] = [[""] * (found + 1) for _ in range(count + 1)]
    for i in range(1, count + 1):
        best[i][0] = i * unpaired
        move[i][0] = "leave"
    for j in range(1, found + 1):
        move[0][j] = "skip"
    for i in range(1, count + 1):
        for j in range(1, found + 1):
            options = [
                (best[i][j - 1], "skip"),
                (best[i - 1][j - 1] + distance(i - 1, j - 1), "pair"),
                (best[i - 1][j] + unpaired, "leave"),
            ]
            best[i][j], move[i][j] = min(options, key=lambda option: option[0])

    result: list[tuple[float, float, float, float] | None] = [None] * count
    i, j = count, found
    while i > 0 and j > 0:
        step = move[i][j]
        if step == "skip":
            j -= 1
        elif step == "pair":
            result[i - 1] = located[j - 1]
            i, j = i - 1, j - 1
        else:
            i -= 1
    return result

class LocateCost(float):
    """A float-compatible cost carrying metadata for the optional second call."""

    def __new__(
        cls,
        value: float = 0.0,
        *,
        called: bool = False,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        priced: bool = False,
    ):
        result = super().__new__(cls, value)
        result.called = called
        result.prompt_tokens = prompt_tokens
        result.completion_tokens = completion_tokens
        result.priced = priced
        return result



def relocate_figures(
    pdf_path: Path,
    page_index: int,
    markdown: str,
    client: OpenAI,
    model: str | None = None,
) -> tuple[str, float]:
    """Replace the transcription's figure boxes with ones read off a ruled page.

    Returns the Markdown and what the extra request cost. The Markdown comes back
    unchanged — and nothing is charged — when the page has no figures, when the
    feature is off, or when anything at all goes wrong: the boxes that were
    already there are a working fallback, and a page must still convert without
    this.
    """
    if not settings.locate_figures:
        return markdown, LocateCost(0.0)
    called = False

    lines = markdown.splitlines()
    targets = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := FIGURE_RE.match(line)) is not None
    ]
    if not targets:
        return markdown, LocateCost(0.0)

    selected_model = require_model_allowed(
        settings.figure_model or model or settings.model
    )

    try:
        image, render = gridded_page(pdf_path, page_index)
        encoded = base64.standard_b64encode(image).decode("ascii")
        called = True
        response = client.chat.completions.create(
            # A model configured for this pass specifically wins; otherwise the
            # one the conversion is already running on.
            model=selected_model,
            max_tokens=MAX_TOKENS,
            extra_headers=settings.headers,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
        )
        reply = response.choices[0].message.content or ""
        located = parse_boxes(reply, render)
    except Exception:
        return markdown, LocateCost(0.0, called=called)

    cost, prompt_tokens, completion_tokens, priced = _usage_fields(getattr(response, "usage", None))
    cost = LocateCost(cost, called=True, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, priced=priced)
    if not located:
        return markdown, cost

    hints: list[tuple[float, float, float, float] | None] = []
    for _, match in targets:
        try:
            values = [float(part) for part in match.group("box").split(",")]
        except ValueError:
            values = []
        hints.append(tuple(values) if len(values) == 4 else None)  # type: ignore[arg-type]

    # A figure that keeps its original box has to be brought onto the grid as
    # well. The units of a page are decided from all its boxes at once, so a
    # leftover box still in the render's pixels would drag the relocated ones —
    # already on the grid — out of it along with itself.
    hint_scale = page_scale([list(hint) for hint in hints if hint is not None], render)

    for (index, _), box, hint in zip(targets, align(hints, located), hints):
        if box is None:
            if hint is None or hint_scale is None:
                continue
            box = (hint[0] * hint_scale[0], hint[1] * hint_scale[1],
                   hint[2] * hint_scale[0], hint[3] * hint_scale[1])
        numbers = ",".join(str(round(value)) for value in box)
        # Only the numbers change: the caption, and any blockquote or emphasis
        # marker around it, are the transcription's and are left alone.
        lines[index] = BOX_COMMENT_RE.sub(f" <!--box: {numbers}-->", lines[index], count=1)

    return "\n".join(lines), cost
