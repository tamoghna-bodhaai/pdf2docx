"""How large a source page is, and how large its preview render should be."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from .config import settings


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def page_zoom(width_pt: float, height_pt: float) -> float:
    """The zoom a page is rendered at: `settings.dpi`, capped by `settings.max_edge`.

    Written down once so the viewer's page images and anything else that sizes
    the same page agree about how large it is.
    """
    zoom = settings.dpi / 72.0
    long_edge = max(width_pt, height_pt)
    if long_edge * zoom > settings.max_edge:
        zoom = settings.max_edge / long_edge
    return zoom
