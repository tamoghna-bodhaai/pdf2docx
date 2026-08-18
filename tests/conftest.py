"""Synthetic PDFs to crop from.

The defects these tests cover are about resolution, so the fixtures are built
around it: a page that is nothing but a coarse scan, and a page that is drawn.
Both are made here rather than checked in, so a fixture can never drift away
from the resolution its test claims for it.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

A4 = (595.0, 842.0)


def _speckled(width: int, height: int) -> fitz.Pixmap:
    """An image with content in it, so nothing downstream optimises it away."""
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    pixmap.clear_with(255)
    for step in range(0, min(width, height), 4):
        pixmap.set_rect(fitz.IRect(step, step, step + 2, height), (0, 0, 0))
    return pixmap


@pytest.fixture
def scan(tmp_path: Path):
    """A page whose only content is one image, at a chosen resolution.

    This is what a photographed or photocopied book page looks like to the
    extractor: no text layer, no drawings, one raster covering the page.
    """

    def build(dpi: float = 90.0, name: str = "scan.pdf") -> Path:
        path = tmp_path / name
        document = fitz.open()
        page = document.new_page(width=A4[0], height=A4[1])
        pixels = _speckled(round(A4[0] / 72 * dpi), round(A4[1] / 72 * dpi))
        page.insert_image(page.rect, pixmap=pixels)
        document.save(path)
        document.close()
        return path

    return build


@pytest.fixture
def drawn(tmp_path: Path) -> Path:
    """A page of vector artwork and text — no raster, so no resolution ceiling."""
    path = tmp_path / "drawn.pdf"
    document = fitz.open()
    page = document.new_page(width=A4[0], height=A4[1])
    page.draw_rect(fitz.Rect(100, 100, 300, 300), color=(0, 0, 0), width=1.5)
    page.draw_line(fitz.Point(100, 100), fitz.Point(300, 300), color=(0, 0, 0))
    page.insert_text(fitz.Point(100, 400), "a caption under the figure", fontsize=11)
    document.save(path)
    document.close()
    return path
