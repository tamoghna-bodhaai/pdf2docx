"""What the converter saw, per page: block boxes, kinds, reading order.

The shape a conversion is written in so the browser can draw it over a render
of the page without knowing which backend produced it. Mathpix reports its line
geometry only in a raw export that is never drawn, so today every page arrives
through `from_markdown` carrying text and no boxes — but the box vocabulary
stays, because it is what the viewer reads and what a backend that does report
geometry would be written into.

Boxes are kept in the page's own coordinates, with `width`/`height` alongside
them, so the viewer can scale the page to any size and let the SVG viewBox do
the arithmetic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

Box = tuple[float, float, float, float]

# How much of a block's text is kept for the tooltip and the JSON pane. Enough
# to recognise the block by; not so much that a document's whole text is stored
# twice over.
PREVIEW_CHARS = 240

# The kinds a box can be drawn as. The viewer colours and toggles by these, so
# any builder that reports geometry maps its own vocabulary into this set.
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
    # the order they are read.
    ordered: bool = True
    markdown: str = ""
    blocks: list[DetectedBlock] = field(default_factory=list)


def from_markdown(sizes: list[tuple[float, float]], markdown: list[str]) -> list[DetectedPage]:
    """Pages whose content is known but whose geometry is not.

    Mathpix reports line boxes only in `lines.json`, which is kept as a raw
    export and never drawn. The page still belongs in the viewer — its render
    and its Markdown are worth seeing side by side — so it is written with an
    empty block list.
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
