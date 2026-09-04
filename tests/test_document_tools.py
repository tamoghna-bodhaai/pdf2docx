"""Behavioural tests for the local PDF processing interface."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PIL import Image

from app import document_tools
from app.document_tools import (
    A4_LANDSCAPE,
    A4_PORTRAIT,
    DocumentToolError,
    images_to_pdf,
    split_pdf,
)


def _image(path: Path, size: tuple[int, int], color: tuple[int, ...], mode: str = "RGB") -> Path:
    Image.new(mode, size, color).save(path)
    return path


def test_images_are_composed_in_order_on_orientation_matched_a4_pages(tmp_path: Path) -> None:
    portrait = _image(tmp_path / "first.png", (100, 200), (220, 20, 30))
    landscape = _image(tmp_path / "second.webp", (240, 100), (20, 50, 220))
    output = tmp_path / "result.pdf"

    result = images_to_pdf([portrait, landscape], output)

    assert result == output
    with fitz.open(output) as document:
        assert document.page_count == 2
        assert tuple(document[0].rect) == pytest.approx((0, 0, *A4_PORTRAIT), abs=0.02)
        assert tuple(document[1].rect) == pytest.approx((0, 0, *A4_LANDSCAPE), abs=0.02)
        first = document[0].get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
        second = document[1].get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
        assert first.pixel(first.width // 2, first.height // 2)[0] > 180
        assert second.pixel(second.width // 2, second.height // 2)[2] > 180


def test_image_fit_is_centered_inside_the_36_point_margin_without_stretching(
    tmp_path: Path,
) -> None:
    source = _image(tmp_path / "tall.png", (100, 200), (20, 90, 160))
    output = tmp_path / "fit.pdf"

    images_to_pdf([source], output)

    with fitz.open(output) as document:
        page = document[0]
        [image] = page.get_images(full=True)
        [drawn] = page.get_image_rects(image[0])
        assert drawn.y0 == pytest.approx(36, abs=0.02)
        assert drawn.y1 == pytest.approx(page.rect.height - 36, abs=0.02)
        assert drawn.x0 == pytest.approx(page.rect.width - drawn.x1, abs=0.02)
        assert drawn.width / drawn.height == pytest.approx(0.5, abs=0.001)


def test_transparency_is_flattened_onto_white(tmp_path: Path) -> None:
    source = _image(tmp_path / "alpha.png", (100, 100), (255, 0, 0, 0), "RGBA")
    output = tmp_path / "alpha.pdf"

    images_to_pdf([source], output)

    with fitz.open(output) as document:
        pixmap = document[0].get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
        assert pixmap.pixel(pixmap.width // 2, pixmap.height // 2) == (255, 255, 255)


def test_exif_orientation_is_applied_before_page_orientation(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (120, 60), "green")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)

    images_to_pdf([source], tmp_path / "rotated.pdf")

    with fitz.open(tmp_path / "rotated.pdf") as document:
        assert document[0].rect.height > document[0].rect.width


@pytest.mark.parametrize("name,content", [("broken.png", b"not an image"), ("empty.webp", b"")])
def test_corrupt_images_fail_without_replacing_an_existing_output(
    tmp_path: Path, name: str, content: bytes
) -> None:
    source = tmp_path / name
    source.write_bytes(content)
    output = tmp_path / "document.pdf"
    output.write_bytes(b"previous complete result")

    with pytest.raises(DocumentToolError, match="Could not read"):
        images_to_pdf([source], output)

    assert output.read_bytes() == b"previous complete result"


def test_failed_atomic_promotion_closes_and_removes_the_staged_pdf(
    tmp_path: Path, monkeypatch
) -> None:
    source = _image(tmp_path / "source.png", (40, 80), (1, 2, 3))
    output = tmp_path / "document.pdf"
    output.write_bytes(b"previous complete result")

    def fail_replace(_source: Path, _target: Path) -> Path:
        raise OSError("promotion failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="promotion failed"):
        images_to_pdf([source], output)

    assert output.read_bytes() == b"previous complete result"
    assert list(tmp_path.glob(".document-*.pdf")) == []


def test_unsupported_and_oversized_images_are_rejected(tmp_path: Path, monkeypatch) -> None:
    gif = _image(tmp_path / "animation.gif", (20, 20), (1, 2, 3))
    too_large = _image(tmp_path / "large.png", (11, 10), (1, 2, 3))

    with pytest.raises(DocumentToolError, match="not a supported"):
        images_to_pdf([gif], tmp_path / "gif.pdf")

    monkeypatch.setattr(document_tools, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(DocumentToolError, match="pixel limit"):
        images_to_pdf([too_large], tmp_path / "large.pdf")


def _source_pdf(path: Path, pages: int = 5) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=400 + number * 10, height=600 + number * 5)
        page.insert_text((72, 72), f"Page {number}")
        if number == 2:
            page.insert_link(
                {"kind": fitz.LINK_URI, "from": fitz.Rect(70, 80, 170, 100), "uri": "https://example.com"}
            )
            annotation = page.add_text_annot((100, 120), "keep this note")
            annotation.update()
    document.set_metadata({"title": "Range source", "author": "PDF2DOCX tests"})
    toc = [[1, "Before", 1]]
    if pages >= 2:
        toc.append([1, "Start", 2])
    if pages >= 4:
        toc.append([1, "End", 4])
    if pages >= 5:
        toc.append([1, "After", 5])
    document.set_toc(toc)
    document.save(path)
    document.close()
    return path


def test_split_preserves_document_features_and_rebases_bookmarks(tmp_path: Path) -> None:
    source = _source_pdf(tmp_path / "source.pdf")
    output = tmp_path / "range.pdf"

    result = split_pdf(source, output, 2, 4)

    assert result == output
    with fitz.open(source) as original, fitz.open(output) as extracted:
        assert extracted.page_count == 3
        assert extracted.metadata["title"] == "Range source"
        assert extracted.metadata["author"] == "PDF2DOCX tests"
        assert extracted[0].rect == original[1].rect
        assert extracted[2].rect == original[3].rect
        assert extracted[0].get_links()[0]["uri"] == "https://example.com"
        annotations = list(extracted[0].annots() or [])
        assert annotations[0].info["content"] == "keep this note"
        assert extracted.get_toc() == [[1, "Start", 1], [1, "End", 3]]


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(1, 1, 1), (3, 3, 1), (5, 5, 1), (1, 5, 5), (2, 4, 3)],
)
def test_split_ranges_are_inclusive(
    tmp_path: Path, start: int, end: int, expected: int
) -> None:
    source = _source_pdf(tmp_path / "source.pdf")
    output = tmp_path / f"{start}-{end}.pdf"

    split_pdf(source, output, start, end)

    with fitz.open(output) as document:
        assert document.page_count == expected


@pytest.mark.parametrize(("start", "end"), [(0, 1), (2, 1), (1, 6)])
def test_split_rejects_invalid_ranges_without_replacing_output(
    tmp_path: Path, start: int, end: int
) -> None:
    source = _source_pdf(tmp_path / "source.pdf")
    output = tmp_path / "document.pdf"
    output.write_bytes(b"previous complete result")

    with pytest.raises(DocumentToolError, match="page range"):
        split_pdf(source, output, start, end)

    assert output.read_bytes() == b"previous complete result"


def test_split_rejects_unreadable_and_encrypted_pdfs(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"not a pdf")
    with pytest.raises(DocumentToolError, match="Could not read"):
        split_pdf(corrupt, tmp_path / "corrupt-output.pdf", 1, 1)

    plain = _source_pdf(tmp_path / "plain.pdf", pages=1)
    encrypted = tmp_path / "encrypted.pdf"
    with fitz.open(plain) as source:
        source.save(
            encrypted,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="reader",
        )
    with pytest.raises(DocumentToolError, match="encrypted"):
        split_pdf(encrypted, tmp_path / "encrypted-output.pdf", 1, 1)

    empty_password = tmp_path / "empty-password.pdf"
    with fitz.open(plain) as source:
        source.save(
            empty_password,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="owner",
            user_pw="",
        )
    with pytest.raises(DocumentToolError, match="encrypted"):
        split_pdf(empty_password, tmp_path / "empty-password-output.pdf", 1, 1)
