"""What the viewer is shown is what the converter actually reported.

The overlay is not a second opinion about the page: what it draws has to come
from the same result the .docx was written from. Mathpix reports its line
geometry only in a raw export that is never drawn, so today every page carries
its Markdown and an empty block list — and these check that the empty list is a
deliberate, round-tripping shape rather than an accident.
"""

from __future__ import annotations

from app import detection


def test_pages_without_geometry_still_carry_their_text():
    pages = detection.from_markdown([(595.0, 842.0), (595.0, 842.0)], ["first", "second"])

    assert [page.number for page in pages] == [1, 2]
    assert [page.markdown for page in pages] == ["first", "second"]
    assert all(page.blocks == [] for page in pages)


def test_a_page_beyond_the_reported_sizes_still_gets_one():
    """More Markdown than page sizes must not lose a page or raise."""
    pages = detection.from_markdown([(595.0, 842.0)], ["first", "second"])

    assert [page.number for page in pages] == [1, 2]
    assert (pages[1].width, pages[1].height) == (612.0, 792.0)


def test_what_is_written_is_what_is_read_back(tmp_path):
    pages = detection.from_markdown([(595.0, 842.0)], ["only page"])
    path = detection.write(pages, tmp_path / "detection.json", "mathpix")

    data = detection.read(path)
    assert data["mode"] == "mathpix"
    assert len(data["pages"]) == 1
    stored = data["pages"][0]
    assert stored["number"] == 1
    assert stored["width"] == 595.0
    assert stored["markdown"] == "only page"
    assert stored["blocks"] == []


def test_the_detection_file_is_created_with_its_parent(tmp_path):
    pages = detection.from_markdown([(595.0, 842.0)], ["page"])
    path = detection.write(pages, tmp_path / "made" / "up" / "detection.json", "mathpix")

    assert path.is_file()
