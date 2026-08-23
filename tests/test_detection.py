"""What the viewer is shown is what the converter actually found.

The point of these is that the overlay is not a second opinion about the page:
every box it draws has to come from the same objects the .docx was written from,
in the same coordinates, so a box that lands in the wrong place is the
conversion being wrong rather than the viewer.
"""

from __future__ import annotations

import json

import fitz
import pytest

from app import detection
from app.pdf_extract import extract_page
from app.reflow import build as reflow_build


def layouts_of(pdf_path, pages: int = 1):
    with fitz.open(pdf_path) as document:
        return [extract_page(document, index, detect_math=True) for index in range(pages)]


def inside(page: detection.DetectedPage) -> bool:
    """Every box lies within the page it is drawn on, give or take a hair."""
    slack = 1.0
    return all(
        -slack <= x0 <= page.width + slack
        and -slack <= y0 <= page.height + slack
        and -slack <= x1 <= page.width + slack
        and -slack <= y1 <= page.height + slack
        and x1 >= x0
        and y1 >= y0
        for x0, y0, x1, y1 in (block.bbox for block in page.blocks)
    )


# --------------------------------------------------------------------------- #
# replica — the items, as positioned
# --------------------------------------------------------------------------- #


def test_a_drawn_page_reports_its_artwork_and_its_words(drawn):
    pages = detection.from_layouts(layouts_of(drawn))

    assert len(pages) == 1
    page = pages[0]
    assert (round(page.width), round(page.height)) == (595, 842)
    kinds = {block.kind for block in page.blocks}
    assert "figure" in kinds
    assert "text" in kinds
    assert any("caption" in block.text for block in page.blocks if block.kind == "text")
    assert inside(page)


def test_positioned_items_claim_no_reading_order(drawn):
    """A replica puts every item back where it was and never decides an order."""
    page = detection.from_layouts(layouts_of(drawn))[0]

    assert page.ordered is False
    # ...but they still come back down the page, so the list reads as it looks.
    tops = [block.bbox[1] for block in page.blocks]
    assert tops == sorted(tops)
    assert [block.index for block in page.blocks] == list(range(len(page.blocks)))


def test_a_scanned_page_is_marked_as_one(scan):
    page = detection.from_layouts(layouts_of(scan(dpi=90)))[0]

    assert page.scanned is True
    assert inside(page)


# --------------------------------------------------------------------------- #
# structured — the blocks the document was written from
# --------------------------------------------------------------------------- #


def test_structured_blocks_are_numbered_in_the_order_they_are_read(drawn):
    layouts = layouts_of(drawn)
    blocks, _ = reflow_build(layouts)
    pages = detection.from_blocks(blocks, layouts)

    assert len(pages) == 1
    page = pages[0]
    assert page.ordered is True
    # The index is the position in the very list the writer walked.
    assert [block.index for block in page.blocks] == list(range(len(blocks[0].blocks)))
    assert inside(page)


def test_every_block_carries_a_kind_the_viewer_can_draw(drawn):
    layouts = layouts_of(drawn)
    blocks, _ = reflow_build(layouts)
    page = detection.from_blocks(blocks, layouts)[0]

    assert page.blocks
    assert all(block.kind in detection.KINDS for block in page.blocks)


def test_a_block_preview_is_a_preview(drawn):
    layouts = layouts_of(drawn)
    layouts[0].lines[0].spans[0].text = "x" * 900
    blocks, _ = reflow_build(layouts)
    page = detection.from_blocks(blocks, layouts)[0]

    assert all(len(block.text) <= detection.PREVIEW_CHARS for block in page.blocks)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def test_what_is_written_is_what_is_read_back(drawn, tmp_path):
    pages = detection.from_layouts(layouts_of(drawn))
    path = detection.write(pages, tmp_path / "detection.json", "replica")

    data = detection.read(path)
    assert data["mode"] == "replica"
    assert len(data["pages"]) == 1
    stored = data["pages"][0]
    assert stored["number"] == 1
    assert stored["ordered"] is False
    assert len(stored["blocks"]) == len(pages[0].blocks)
    assert stored["blocks"][0]["bbox"] == list(pages[0].blocks[0].bbox)


def test_pages_without_geometry_still_carry_their_text():
    pages = detection.from_markdown([(595.0, 842.0), (595.0, 842.0)], ["first", "second"])

    assert [page.number for page in pages] == [1, 2]
    assert [page.markdown for page in pages] == ["first", "second"]
    assert all(page.blocks == [] for page in pages)
