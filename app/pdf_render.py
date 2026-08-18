"""Rasterise PDF pages to PNG images sized for vision input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from .config import settings


@dataclass
class RenderedPage:
    number: int  # 1-based
    path: Path
    width: int
    height: int


def page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def page_zoom(width_pt: float, height_pt: float) -> float:
    """The zoom a page is rendered at: `settings.dpi`, capped by `settings.max_edge`.

    Written down once because anything that shows a model a second view of the
    same page has to agree with the first about how large that page is.
    """
    zoom = settings.dpi / 72.0
    long_edge = max(width_pt, height_pt)
    if long_edge * zoom > settings.max_edge:
        zoom = settings.max_edge / long_edge
    return zoom


def render_pages(pdf_path: Path, out_dir: Path) -> list[RenderedPage]:
    """Render every page of `pdf_path` to a PNG under `out_dir`.

    Pages are rendered at `settings.dpi`, then re-rendered at a reduced zoom if
    the long edge would exceed `settings.max_edge` (keeps image tokens bounded
    and stays inside the model's high-resolution limit).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[RenderedPage] = []

    with fitz.open(pdf_path) as doc:
        limit = doc.page_count
        if settings.max_pages > 0:
            limit = min(limit, settings.max_pages)

        for index in range(limit):
            page = doc.load_page(index)
            zoom = page_zoom(page.rect.width, page.rect.height)

            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            path = out_dir / f"page-{index + 1:04d}.png"
            pix.save(path)
            pages.append(
                RenderedPage(number=index + 1, path=path, width=pix.width, height=pix.height)
            )

    return pages
