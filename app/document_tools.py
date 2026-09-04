"""Local document processing behind a deliberately small public interface.

Both operations write to a sibling temporary file and replace ``output_path``
only after PyMuPDF has successfully reopened the completed document. Callers
therefore never observe a partial result.
"""

from __future__ import annotations

import io
import os
import tempfile
import shutil
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Iterable

import fitz
from PIL import Image, ImageOps, UnidentifiedImageError

A4_PORTRAIT = (fitz.paper_rect("a4").width, fitz.paper_rect("a4").height)
A4_LANDSCAPE = (A4_PORTRAIT[1], A4_PORTRAIT[0])
PAGE_MARGIN = 36.0
try:
    MAX_IMAGE_PIXELS = int(os.environ.get("PDF2DOCX_IMAGE_MAX_PIXELS", "40000000"))
except ValueError:
    MAX_IMAGE_PIXELS = 40_000_000
SUPPORTED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
PAGE_ORIENTATIONS = frozenset({"auto", "portrait", "landscape"})


class DocumentToolError(ValueError):
    """An input cannot be processed safely as the requested document."""


def _temporary_output(output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{output_path.stem}-", suffix=".pdf", dir=output_path.parent
    )
    os.close(descriptor)
    return Path(raw_path)


def _fit_rect(width: float, height: float, page_width: float, page_height: float) -> fitz.Rect:
    available_width = page_width - 2 * PAGE_MARGIN
    available_height = page_height - 2 * PAGE_MARGIN
    scale = min(available_width / width, available_height / height)
    drawn_width, drawn_height = width * scale, height * scale
    left = (page_width - drawn_width) / 2
    top = (page_height - drawn_height) / 2
    return fitz.Rect(left, top, left + drawn_width, top + drawn_height)


def _normalised_image(path: Path) -> tuple[bytes, int, int]:
    try:
        with Image.open(path) as opened:
            if opened.format not in SUPPORTED_IMAGE_FORMATS:
                raise DocumentToolError(
                    f"{path.name} is not a supported JPEG, PNG, or WebP image."
                )
            if opened.width * opened.height > MAX_IMAGE_PIXELS:
                raise DocumentToolError(
                    f"{path.name} exceeds the {MAX_IMAGE_PIXELS:,}-pixel limit."
                )
            image = ImageOps.exif_transpose(opened)
            image.load()
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                image = flattened
            else:
                image = image.convert("RGB")
            width, height = image.size
            stream = io.BytesIO()
            image.save(stream, format="PNG", optimize=True)
            return stream.getvalue(), width, height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DocumentToolError(f"Could not read {path.name} as an image.") from exc


def images_to_pdf(
    images: Iterable[str | Path],
    output_path: str | Path,
    orientation: str = "auto",
) -> Path:
    """Compose ordered JPEG, PNG, or WebP images into orientation-matched A4 pages."""
    sources = [Path(image) for image in images]
    if not sources:
        raise DocumentToolError("Attach at least one image.")
    if orientation not in PAGE_ORIENTATIONS:
        raise DocumentToolError("Page orientation must be auto, portrait, or landscape.")

    target = Path(output_path)
    staged = _temporary_output(target)
    document = fitz.open()
    try:
        for source in sources:
            data, width, height = _normalised_image(source)
            use_landscape = orientation == "landscape" or (
                orientation == "auto" and width > height
            )
            page_width, page_height = A4_LANDSCAPE if use_landscape else A4_PORTRAIT
            page = document.new_page(width=page_width, height=page_height)
            page.insert_image(
                _fit_rect(width, height, page_width, page_height),
                stream=data,
                keep_proportion=True,
            )
        document.save(staged, garbage=4, deflate=True)
        document.close()
        with fitz.open(staged) as completed:
            if completed.page_count != len(sources):
                raise DocumentToolError("The composed PDF is incomplete.")
        staged.replace(target)
        return target
    finally:
        if not document.is_closed:
            document.close()
        # After a successful atomic promotion this path no longer exists. On
        # every failure it is the incomplete sibling that must be discarded.
        staged.unlink(missing_ok=True)


def split_pdf(
    source_path: str | Path,
    output_path: str | Path,
    start_page: int,
    end_page: int,
) -> Path:
    """Extract an inclusive range while retaining the selected pages' PDF features."""
    source_file = Path(source_path)
    target = Path(output_path)
    try:
        source = fitz.open(source_file)
    except Exception as exc:
        raise DocumentToolError(f"Could not read {source_file.name} as a PDF.") from exc

    staged: Path | None = None
    extracted = fitz.open()
    try:
        if source.needs_pass or source.metadata.get("encryption"):
            raise DocumentToolError("The source PDF is encrypted and cannot be split.")
        count = source.page_count
        if not 1 <= start_page <= end_page <= count:
            raise DocumentToolError(
                f"Choose a page range from 1 to {count}, with the start before the end."
            )

        extracted.insert_pdf(
            source,
            from_page=start_page - 1,
            to_page=end_page - 1,
            links=True,
            annots=True,
        )
        metadata_keys = {
            "title", "author", "subject", "keywords", "creator", "producer",
            "creationDate", "modDate", "trapped",
        }
        extracted.set_metadata(
            {key: value for key, value in source.metadata.items() if key in metadata_keys and value}
        )

        bookmarks: list[list] = []
        previous_level = 0
        for level, title, page, destination in source.get_toc(simple=False):
            if not start_page <= page <= end_page:
                continue
            new_level = max(1, min(int(level), previous_level + 1))
            previous_level = new_level
            new_page = page - start_page + 1
            rebased = deepcopy(destination)
            rebased.pop("xref", None)
            if isinstance(rebased.get("page"), int):
                rebased["page"] = new_page - 1
            bookmarks.append([new_level, title, new_page, rebased])
        if bookmarks:
            extracted.set_toc(bookmarks)

        staged = _temporary_output(target)
        extracted.save(staged, garbage=4, deflate=True)
        extracted.close()
        with fitz.open(staged) as completed:
            if completed.page_count != end_page - start_page + 1:
                raise DocumentToolError("The extracted PDF is incomplete.")
        staged.replace(target)
        return target
    except DocumentToolError:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise DocumentToolError(f"Could not extract the requested page range: {exc}") from exc
    finally:
        if not extracted.is_closed:
            extracted.close()
        if not source.is_closed:
            source.close()


def _merged_ranges(
    source_path: Path, output_path: Path, ranges: list[tuple[int, int]]
) -> None:
    """Write ranges into one PDF in caller-provided order."""
    target = fitz.open()
    with fitz.open(source_path) as source:
        for start, end in ranges:
            target.insert_pdf(
                source, from_page=start - 1, to_page=end - 1, links=True, annots=True
            )
        metadata_keys = {
            "title", "author", "subject", "keywords", "creator", "producer",
            "creationDate", "modDate", "trapped",
        }
        target.set_metadata(
            {key: value for key, value in source.metadata.items() if key in metadata_keys and value}
        )
    try:
        target.save(output_path, garbage=4, deflate=True)
    finally:
        target.close()


def split_pdf_ranges(
    source_path: str | Path,
    output_dir: str | Path,
    ranges: Iterable[tuple[int, int]],
    *,
    merge: bool,
    stem: str,
) -> list[dict]:
    """Build and atomically promote the complete configured split artifact set."""
    source_file = Path(source_path)
    selected = [(int(start), int(end)) for start, end in ranges]
    if not selected:
        raise DocumentToolError("Add at least one page range.")
    try:
        with fitz.open(source_file) as source:
            if source.needs_pass or source.metadata.get("encryption"):
                raise DocumentToolError("The source PDF is encrypted and cannot be split.")
            count = source.page_count
    except DocumentToolError:
        raise
    except Exception as exc:
        raise DocumentToolError(f"Could not read {source_file.name} as a PDF.") from exc
    if any(not 1 <= start <= end <= count for start, end in selected):
        raise DocumentToolError(
            f"Choose every page range from 1 to {count}, with the start before the end."
        )

    destination = Path(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".split-artifacts-", dir=destination.parent))
    artifacts: list[dict] = []
    try:
        if merge:
            filename = f"{stem}-selected-pages.pdf"
            _merged_ranges(source_file, staged / "merged.pdf", selected)
            artifacts.append({
                "key": "merged", "filename": filename,
                "media_type": "application/pdf",
                "pages": sum(end - start + 1 for start, end in selected),
            })
        else:
            occurrences: dict[tuple[int, int], int] = {}
            packaged: list[tuple[Path, str]] = []
            for ordinal, (start, end) in enumerate(selected, start=1):
                pair = (start, end)
                occurrences[pair] = occurrences.get(pair, 0) + 1
                repeat = occurrences[pair]
                suffix = f"-{repeat}" if repeat > 1 else ""
                filename = f"{stem}-pages-{start}-{end}{suffix}.pdf"
                path = staged / f"range-{ordinal}.pdf"
                split_pdf(source_file, path, start, end)
                artifacts.append({
                    "key": f"range-{ordinal}", "filename": filename,
                    "media_type": "application/pdf", "pages": end - start + 1,
                })
                packaged.append((path, filename))
            package_name = f"{stem}-ranges.zip"
            with zipfile.ZipFile(staged / "package.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                for path, filename in packaged:
                    archive.write(path, filename)
            artifacts.append({
                "key": "package", "filename": package_name,
                "media_type": "application/zip", "pages": None,
            })

        backup = destination.with_name(f".{destination.name}-old-{uuid.uuid4().hex}")
        if destination.exists():
            destination.replace(backup)
        try:
            staged.replace(destination)
        except Exception:
            if backup.exists():
                backup.replace(destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return artifacts
    except DocumentToolError:
        raise
    except Exception as exc:
        raise DocumentToolError(f"Could not create the requested PDF artifacts: {exc}") from exc
    finally:
        shutil.rmtree(staged, ignore_errors=True)
