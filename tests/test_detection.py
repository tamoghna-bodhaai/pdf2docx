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
# marker — read out of its JSON, in whatever coordinates it chose
# --------------------------------------------------------------------------- #


MARKER_JSON = json.dumps(
    {
        "block_type": "Document",
        "metadata": {},
        "children": [
            {
                "id": "/page/0",
                "block_type": "Page",
                "html": "",
                # Deliberately not at the origin, and not in points: marker
                # reports pixels of its own rendering, and the viewer must not
                # need to know that.
                "bbox": [0.0, 0.0, 1000.0, 1414.0],
                "polygon": [],
                "children": [
                    {
                        "id": "/page/0/SectionHeader/1",
                        "block_type": "SectionHeader",
                        "html": "<h1>Heat equation</h1>",
                        "bbox": [100.0, 120.0, 900.0, 170.0],
                        "polygon": [],
                        "children": None,
                    },
                    {
                        "id": "/page/0/Equation/2",
                        "block_type": "Equation",
                        "html": "<p>$$x^2$$</p>",
                        "bbox": [200.0, 300.0, 800.0, 380.0],
                        "polygon": [],
                        "children": None,
                    },
                ],
            },
            {
                "id": "/page/1",
                "block_type": "Page",
                "html": "",
                # A second page reported in a running coordinate space, i.e. not
                # starting at zero.
                "bbox": [0.0, 1414.0, 1000.0, 2828.0],
                "polygon": [],
                "children": [
                    {
                        "id": "/page/1/Picture/0",
                        "block_type": "Picture",
                        "html": "<p>figure</p>",
                        "bbox": [50.0, 1500.0, 500.0, 1900.0],
                        "polygon": [],
                        "children": None,
                    }
                ],
            },
        ],
    }
)


def test_marker_boxes_come_back_in_their_own_pages_frame():
    pages = detection.from_marker_json(MARKER_JSON)

    assert [page.number for page in pages] == [1, 2]
    assert (pages[0].width, pages[0].height) == (1000.0, 1414.0)
    assert inside(pages[0])
    # The second page's boxes were reported 1414 down the document and come back
    # measured from the top of their own page.
    second = pages[1].blocks[0]
    assert second.bbox == (50.0, 86.0, 500.0, 486.0)
    assert inside(pages[1])


def test_markers_own_vocabulary_is_translated_into_the_viewers():
    pages = detection.from_marker_json(MARKER_JSON)

    assert [block.kind for block in pages[0].blocks] == ["heading", "equation"]
    assert pages[1].blocks[0].kind == "figure"
    # The html a block carries is shown as the words it holds, not as mark-up.
    assert pages[0].blocks[0].text == "Heat equation"


def test_a_block_type_marker_adds_later_is_still_drawn():
    payload = json.loads(MARKER_JSON)
    payload["children"][0]["children"][0]["block_type"] = "BlockTypes.SomethingNew"
    pages = detection.from_marker_json(json.dumps(payload))

    assert pages[0].blocks[0].kind == "text"


@pytest.mark.parametrize("raw", ["", "not json", "[]", '{"children": [{"bbox": [0, 0, 0, 0]}]}'])
def test_unreadable_marker_output_is_no_pages_rather_than_an_error(raw):
    assert detection.from_marker_json(raw) == []


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


def test_markdown_can_be_attached_to_pages_detected_apart_from_it():
    pages = detection.from_marker_json(MARKER_JSON)
    detection.attach_markdown(pages, ["page one", "page two", "page three"])

    assert [page.markdown for page in pages] == ["page one", "page two"]
