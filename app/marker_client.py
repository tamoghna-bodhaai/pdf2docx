"""Validated client for the marker-pdf sidecar, and the little that is done to its output.

This module is the boundary that validates the sidecar's untrusted HTTP
responses, and it is deliberately thin. The sidecar returns a finished document
rather than detector output needing interpretation, and the whole point of the
`marker` mode is to see what marker produced rather than what this codebase made
of it.

So exactly two things happen to marker's text here, and both are recorded on the
`Applied` record so a defect can be attributed to the right party:

  * image references are given a directory prefix, because the writers resolve
    them relative to the job's working directory and marker names its images as
    if they sat beside the Markdown;
  * the document is split on marker's own page separator, because every other
    mode gives each source page its own Word page.

Nothing is reordered, rewritten, stripped or repaired. What marker returned is
also written to disk verbatim before any of this runs.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .config import settings

# Formats the sidecar's renderers can return. `markdown` is the only one the
# .docx can be built from; the rest are carried for inspection.
FORMATS = ("markdown", "html", "json", "chunks")
SUFFIXES = {"markdown": ".md", "html": ".html", "json": ".json", "chunks": ".json"}

# Where a job keeps marker's own output, untouched.
RAW_DIR = "marker"
RAW_IMAGE_DIR = "images"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGES = 2000
MAX_IMAGE_BYTES = 64 * 1024 * 1024

# Marker writes `\n\n{N}` followed by a rule of dashes when `paginate_output` is
# on. The exact width of that rule is upstream's business and has changed
# before, so match a run of them rather than a count, and tolerate the page
# number appearing with or without its braces.
PAGE_BREAK_RE = re.compile(r"^[ \t]*\{?\d+\}?-{20,}[ \t]*$", re.M)

# An inline or reference image whose target has no scheme and no directory —
# which is how marker refers to the images it extracted alongside the text.
IMAGE_REF_RE = re.compile(r"(?P<prefix>!\[[^\]]*\]\()(?P<target>[^)\s]+)(?P<suffix>\))")


class MarkerError(RuntimeError):
    """The marker sidecar is unavailable or returned something unusable."""


@dataclass(frozen=True)
class MarkerDocument:
    """One conversion, exactly as the sidecar returned it."""

    format: str
    content: str
    images: dict[str, bytes] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)

    @property
    def suffix(self) -> str:
        return SUFFIXES.get(self.format, ".txt")


@dataclass(frozen=True)
class Applied:
    """What was done to marker's text on the way to the .docx, and nothing else."""

    images_prefixed: int = 0
    images_unresolved: int = 0
    pages: int = 1
    paginated: bool = False

    def as_dict(self) -> dict:
        return {
            "images_prefixed": self.images_prefixed,
            "images_unresolved": self.images_unresolved,
            "pages": self.pages,
            "paginated": self.paginated,
        }


def _safe_image_name(name: Any, index: int) -> str:
    """Accept marker's own filename, or refuse it — never rewrite it.

    The name decides where bytes are written inside the job directory and what
    the saved Markdown points at, so it is checked here rather than trusted and
    caught later by `docx_builder.resolve_picture`. Marker's names are plain
    (`_page_0_Figure_1.jpeg`); anything with a directory in it, an absolute
    path, or an unknown suffix is not a name this sidecar has any reason to
    produce.
    """
    if not isinstance(name, str) or not name.strip():
        raise MarkerError(f"images[{index}] must be a non-empty name")
    candidate = name.strip()
    if candidate != Path(candidate).name or candidate in {".", ".."}:
        raise MarkerError(f"image name is not a plain filename: {candidate!r}")
    if Path(candidate).suffix.lower() not in IMAGE_SUFFIXES:
        raise MarkerError(f"image name has an unsupported suffix: {candidate!r}")
    return candidate


def _detail(response: httpx.Response) -> str:
    """What the sidecar said went wrong, rather than just the status code.

    marker's own failures are the useful ones — a missing inference server, an
    unreadable PDF, an option it rejected — and they arrive as FastAPI's
    `detail`. Reporting only "500 Internal Server Error" sends whoever is
    reading the job to the sidecar's log to learn something the response already
    told us.
    """
    try:
        payload = response.json()
    except ValueError:
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if detail is None:
        return f"HTTP {response.status_code}"
    text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
    return f"HTTP {response.status_code}: {' '.join(text.split())[:600]}"


def parse_document_response(payload: Any) -> MarkerDocument:
    """Validate the JSON response from ``POST /convert-document``."""
    if not isinstance(payload, dict):
        raise MarkerError("convert-document response must be an object")

    fmt = str(payload.get("format") or "markdown").strip().lower()
    if fmt not in FORMATS:
        raise MarkerError(f"unsupported output format: {fmt!r}")

    content = payload.get("content")
    if not isinstance(content, str):
        raise MarkerError("content must be a string")

    raw_images = payload.get("images") or {}
    if not isinstance(raw_images, dict):
        raise MarkerError("images must be an object")
    if len(raw_images) > MAX_IMAGES:
        raise MarkerError(f"response carries more than {MAX_IMAGES} images")

    images: dict[str, bytes] = {}
    total = 0
    for index, (name, encoded) in enumerate(raw_images.items()):
        key = _safe_image_name(name, index)
        if not isinstance(encoded, str):
            raise MarkerError(f"images[{key}] must be base64 text")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MarkerError(f"images[{key}] is not valid base64") from exc
        total += len(data)
        if total > MAX_IMAGE_BYTES:
            raise MarkerError("response images exceed the size limit")
        images[key] = data

    metadata = payload.get("metadata")
    config = payload.get("config")
    return MarkerDocument(
        format=fmt,
        content=content,
        images=images,
        metadata=metadata if isinstance(metadata, dict) else {},
        config=config if isinstance(config, dict) else {},
    )


def write_raw(document: MarkerDocument, work_dir: Path) -> Path:
    """Write marker's output under the job directory, byte for byte.

    This runs before anything reads the content, so the record of what marker
    produced survives even if the .docx build then fails.
    """
    out_dir = work_dir / RAW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"document{document.suffix}"
    # Written as bytes, not text: text mode rewrites line endings on the way out,
    # and a file whose whole purpose is to be the unedited copy cannot be allowed
    # to differ from what arrived, on any platform.
    path.write_bytes(document.content.encode("utf-8"))

    if document.images:
        image_dir = out_dir / RAW_IMAGE_DIR
        image_dir.mkdir(parents=True, exist_ok=True)
        for name, data in document.images.items():
            (image_dir / name).write_bytes(data)
    return path


def prefix_image_refs(markdown: str, known: set[str]) -> tuple[str, int, int]:
    """Point marker's image references at where its images were actually written.

    Only names marker returned are rewritten. A reference to anything else is
    left exactly as it stands: it is marker's output, and silently deleting or
    redirecting it would misrepresent what marker did. The writers refuse to
    embed a reference they cannot resolve, so such a figure degrades to its
    caption rather than to a broken document.
    """
    prefixed = unresolved = 0

    def replace(match: re.Match) -> str:
        nonlocal prefixed, unresolved
        target = match.group("target")
        if target in known:
            prefixed += 1
            return f"{match.group('prefix')}{RAW_DIR}/{RAW_IMAGE_DIR}/{target}{match.group('suffix')}"
        unresolved += 1
        return match.group(0)

    return IMAGE_REF_RE.sub(replace, markdown), prefixed, unresolved


def split_pages(markdown: str) -> list[str]:
    """Split on marker's page separator, or return the document as one page.

    Pagination is requested of the sidecar, but a marker version that does not
    honour it must still convert — one long page is a worse document than five,
    not a failed one.

    marker writes its separator *above* each page, so whatever precedes the first
    one is a preamble rather than a page and is dropped. Blank pages after that
    are kept. A page marker read as nothing is still a page: dropping it would
    shift every page after it out of step with the PDF, and would hide exactly
    the failure worth knowing about — when the inference backend degrades, marker
    returns empty pages rather than an error.
    """
    if not PAGE_BREAK_RE.search(markdown):
        return [markdown]
    pages = [page.strip("\n") for page in PAGE_BREAK_RE.split(markdown)]
    if pages and not pages[0].strip():
        pages = pages[1:]
    return pages or [markdown]


def is_empty(page: str) -> bool:
    """True when marker returned nothing usable for this page.

    A page holding only a figure is not empty — the figure is the content. What
    this catches is the page that came back with no words and no pictures, which
    is what a failed layout or OCR pass produces. marker reports that as a
    successful conversion, so the only way it becomes visible is if something
    looks.
    """
    if IMAGE_REF_RE.search(page):
        return False
    return not re.search(r"\w", page)


@dataclass
class MarkerClient:
    base_url: str = field(default_factory=lambda: settings.marker_url)
    connect_timeout: float = field(default_factory=lambda: settings.marker_connect_timeout)
    request_timeout: float = field(default_factory=lambda: settings.marker_request_timeout)

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.request_timeout, connect=self.connect_timeout)

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MarkerError(f"marker health check failed: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("status") not in {"ok", "ready"}:
            raise MarkerError("marker is not ready")
        return payload

    def convert_document(self, pdf_path: Path, config: dict | None = None) -> MarkerDocument:
        """Convert a whole PDF, passing `config` through to marker untouched."""
        try:
            with pdf_path.open("rb") as handle:
                response = httpx.post(
                    f"{self.base_url}/convert-document",
                    files={"file": (pdf_path.name, handle, "application/pdf")},
                    data={"config": json.dumps(config or {})},
                    timeout=self.timeout,
                )
            if response.is_error:
                raise MarkerError(f"marker conversion failed: {_detail(response)}")
            return parse_document_response(response.json())
        except MarkerError:
            raise
        except OSError as exc:
            raise MarkerError(f"could not read {pdf_path.name}: {exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MarkerError(f"marker conversion failed: {exc}") from exc
