"""Validated client for the Mathpix Files API, and the little that is done to its output.

This module is the boundary that validates Mathpix's untrusted HTTP responses, and
it is deliberately thin, for the same reason `marker_client` is: Mathpix returns a
finished document rather than detector output needing interpretation, and the whole
point of the `mathpix` mode is to see what Mathpix produced rather than what this
codebase made of it.

So exactly two things happen to the local preview copy of Mathpix's text here,
and both are recorded on the `Applied` record so a defect can be attributed to
the right party:

  * image references are downloaded and repointed at local files, because Mathpix
    hosts its crops on its own CDN and the browser serves preview assets from the
    job's working directory;
  * the document is split on Mathpix's own page separator, because every other mode
    keeps the source page and rendered Markdown navigation aligned.

Nothing is reordered, rewritten, stripped or repaired. In particular the maths is
not touched: `math_inline_delimiters` is set at request time so Mathpix emits the
`$…$` / `$$…$$` this codebase already reads, rather than being translated here after
the fact. What Mathpix returned is also written to disk verbatim before any of this
runs. When DOCX is requested, `document.docx` is Mathpix's own file copied byte
for byte.

Endpoint shapes follow Mathpix's official `mpxpy` client, which is the only complete
statement of the multipart upload contract — `POST /files/v1` takes the file part
plus an `options_json` form field, and authenticates with `app_id`/`app_key` headers.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import unquote, urlsplit

import httpx

from .config import settings

# Where a job keeps Mathpix's own output, untouched.
RAW_DIR = "mathpix"
RAW_IMAGE_DIR = "images"

# The statuses a file or a single format can be in. `split` is Mathpix reporting
# that the document has been divided into pages and is being worked through.
STATUSES = ("pending", "split", "processing", "completed", "error")
TERMINAL = ("completed", "error")


@dataclass(frozen=True)
class Format:
    """One thing Mathpix can hand back, and everything needed to serve it.

    `key` is what goes in `conversion_formats` at request time; an empty `key`
    marks a format Mathpix produces automatically, which is asked for by
    downloading it and never by requesting it. `status_key` is what the status
    response's `formats` map calls it, which is not always the extension —
    Mathpix reports the LaTeX archive as `latex` while serving it as `tex.zip`.
    """

    ext: str
    key: str
    media_type: str
    status_key: str = ""
    note: str = ""

    @property
    def requested(self) -> bool:
        return bool(self.key)

    @property
    def state_name(self) -> str:
        return self.status_key or self.key or self.ext


_OFFICE = "application/vnd.openxmlformats-officedocument"

# The one table the download routes, the config endpoint and the browser's button
# row are all generated from. A format Mathpix ships later should mean a row here
# and nothing else.
FORMATS: tuple[Format, ...] = (
    Format("docx", "docx", f"{_OFFICE}.wordprocessingml.document", note="primary; self-contained"),
    Format("md", "md", "text/markdown"),
    Format("mmd", "", "text/markdown", note="always produced"),
    Format("tex.zip", "tex.zip", "application/zip", status_key="latex", note="self-contained"),
    Format("html", "html", "text/html"),
    Format("html.zip", "html.zip", "application/zip", note="self-contained"),
    Format("md.zip", "md.zip", "application/zip", note="self-contained"),
    Format("mmd.zip", "mmd.zip", "application/zip", note="self-contained"),
    Format("pdf", "pdf", "application/pdf", note="re-rendered, HTML pipeline"),
    Format("latex.pdf", "latex.pdf", "application/pdf", note="re-rendered, LaTeX pipeline"),
    Format("pptx", "pptx", f"{_OFFICE}.presentationml.presentation", note="self-contained"),
    Format("xlsx", "xlsx", f"{_OFFICE}.spreadsheetml.sheet", note="tables only"),
    Format("lines.json", "", "application/json", note="always produced; raw geometry export"),
    Format("lines.mmd.json", "", "application/json", note="always produced"),
)

BY_EXT: dict[str, Format] = {entry.ext: entry for entry in FORMATS}

# Formats produced without being asked for. Fetched, never requested.
ALWAYS = tuple(entry.ext for entry in FORMATS if not entry.requested)

# The page-aligned viewer cannot fulfil its contract without Mathpix Markdown.
PREVIEW_REQUIRED = "mmd"
REQUIRED_RESULTS = (PREVIEW_REQUIRED,)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGES = 2000
MAX_IMAGE_BYTES = 64 * 1024 * 1024

# Mathpix writes `\pagebreak` on a line of its own when `include_page_breaks` is
# set. Tolerate surrounding whitespace and the LaTeX spelling with a trailing
# brace group, which some versions emit.
PAGE_BREAK_RE = re.compile(r"^[ \t]*\\pagebreak\s*(?:\{\})?[ \t]*$", re.M)

# An inline image reference. Mathpix's targets are absolute CDN URLs carrying a
# query string, so unlike marker's this has to match a full URL.
IMAGE_REF_RE = re.compile(r"(?P<prefix>!\[[^\]]*\]\()(?P<target>[^)\s]+)(?P<suffix>\))")


class MathpixError(RuntimeError):
    """Mathpix is unavailable, refused the document, or returned something unusable."""


class MathpixNotReady(MathpixError):
    """The format exists but Mathpix is still converting it."""


class MathpixUnsupported(MathpixError):
    """The format was never requested, or Mathpix does not offer it for this document."""


@dataclass(frozen=True)
class MathpixStatus:
    """One reading of `GET /files/v1/{file_id}`, validated."""

    file_id: str
    status: str
    percent_done: float = 0.0
    num_pages: int = 0
    num_pages_completed: int = 0
    formats: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL

    @property
    def failed(self) -> bool:
        return self.status == "error"

    def format_state(self, entry: Format) -> str:
        """What the status map says about one format, under either of its names."""
        for name in (entry.state_name, entry.ext, entry.key):
            if name and name in self.formats:
                return self.formats[name]
        return ""


@dataclass(frozen=True)
class Applied:
    """What was done to Mathpix's text for the local preview, and nothing else."""

    images_downloaded: int = 0
    images_failed: int = 0
    images_unresolved: int = 0
    pages: int = 1
    paginated: bool = False

    def as_dict(self) -> dict:
        return {
            "images_downloaded": self.images_downloaded,
            "images_failed": self.images_failed,
            "images_unresolved": self.images_unresolved,
            "pages": self.pages,
            "paginated": self.paginated,
        }


# Everything Mathpix will render on request, which is what an unset selection means.
EVERYTHING = tuple(entry.ext for entry in FORMATS if entry.requested)


def requestable_formats(wanted: Iterable[str]) -> tuple[str, ...]:
    """Known requestable formats in caller order, without adding defaults.

    This is the exact-selection half of the catalog interface. It intentionally
    preserves an empty selection: the browser may request only Mathpix's
    always-produced preview outputs.
    """
    seen: set[str] = set()
    return tuple(
        name
        for name in wanted
        if name in BY_EXT
        and BY_EXT[name].requested
        and not (name in seen or seen.add(name))
    )


def requested_formats(wanted: Iterable[str] | None = None) -> tuple[str, ...]:
    """Resolve the configured default used by clients that omit a selection.

    An empty selection means all of them rather than none: Mathpix renders every
    requested format from the one conversion, so withholding any buys nothing.
    Unknown names are dropped rather than raised, the way the marker settings
    treat theirs — a stale entry in an `.env` file should not stop a conversion.
    """
    chosen = list(requestable_formats(wanted or ()))
    if not chosen:
        chosen = list(EVERYTHING)
    # Older clients expected DOCX even when a configured subset omitted it.
    # Keep that default contract; an explicit start-form selection uses
    # ``requestable_formats`` and can deliberately leave DOCX out.
    if "docx" not in chosen:
        chosen.insert(0, "docx")
    return tuple(chosen)


def conversion_formats(wanted: Iterable[str] | None = None) -> dict[str, bool]:
    """`conversion_formats` as Mathpix wants it, from a list of extensions."""
    chosen = requested_formats() if wanted is None else requestable_formats(wanted)
    return {BY_EXT[name].key: True for name in chosen}


def _error_id(response: httpx.Response) -> str:
    """Mathpix's own name for what went wrong, if the body carries one."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("error_id", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    info = payload.get("error_info")
    if isinstance(info, dict):
        value = info.get("id")
        if isinstance(value, str):
            return value
    return ""


def _detail(response: httpx.Response) -> str:
    """What Mathpix said went wrong, rather than just the status code."""
    name = _error_id(response)
    if not name:
        return f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}: {name}"


def parse_status_response(payload: Any, file_id: str) -> MathpixStatus:
    """Validate the JSON response from ``GET /files/v1/{file_id}``."""
    if not isinstance(payload, dict):
        raise MathpixError("file status response must be an object")

    status = str(payload.get("status") or "").strip().lower()
    if status not in STATUSES:
        raise MathpixError(f"unknown file status: {status!r}")

    try:
        percent = float(payload.get("percent_done") or 0.0)
    except (TypeError, ValueError):
        percent = 0.0
    percent = min(100.0, max(0.0, percent))

    def count(key: str) -> int:
        try:
            return max(0, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    raw_formats = payload.get("formats") or {}
    if not isinstance(raw_formats, dict):
        raise MathpixError("formats must be an object")
    formats = {
        str(name): str(state).strip().lower()
        for name, state in raw_formats.items()
        if isinstance(name, str)
    }

    error = payload.get("error")
    return MathpixStatus(
        file_id=file_id,
        status=status,
        percent_done=percent,
        num_pages=count("num_pages"),
        num_pages_completed=count("num_pages_completed"),
        formats=formats,
        error=error if isinstance(error, str) and error else None,
    )


def parse_lines_json(raw: bytes | str) -> dict:
    """Validate `lines.json` far enough to hand it to the detection reader."""
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MathpixError(f"lines.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise MathpixError("lines.json must be an object")
    return payload


def _safe_image_name(target: str, index: int) -> str:
    """Derive a plain filename from a Mathpix image URL, or refuse it.

    The name decides where bytes are written inside the job directory and what
    the saved Markdown points at, so it is checked here rather than trusted and
    caught later by `docx_builder.resolve_picture`. Mathpix's crops are CDN URLs
    with a query string (`.../cropped/abc123.jpg?height=200&width=400`), so the
    query is dropped and only the last path segment is considered — and that
    segment still has to be a plain filename with a known suffix.
    """
    path = unquote(urlsplit(target).path)
    candidate = Path(path).name.strip()
    if not candidate or candidate in {".", ".."}:
        raise MathpixError(f"images[{index}] has no usable filename: {target!r}")
    if candidate != Path(candidate).name:
        raise MathpixError(f"image name is not a plain filename: {candidate!r}")
    if Path(candidate).suffix.lower() not in IMAGE_SUFFIXES:
        raise MathpixError(f"image name has an unsupported suffix: {candidate!r}")
    return candidate


def write_raw(ext: str, data: bytes, work_dir: Path) -> Path:
    """Write one of Mathpix's outputs under the job directory, byte for byte.

    This runs before anything reads the content, so the record of what Mathpix
    produced survives even if the .docx build then fails.
    """
    out_dir = work_dir / RAW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"document.{ext}"
    path.write_bytes(data)
    return path


def split_pages(markdown: str) -> list[str]:
    """Split on Mathpix's page separator, or return the document as one page.

    Page breaks are requested, but a Mathpix response that does not carry them
    must still convert — one long page is a worse document than five, not a
    failed one.

    Unlike marker, Mathpix writes its separator *between* pages rather than above
    each one, so what precedes the first is the first page and is kept. A page
    read as nothing is still a page: dropping it would shift every page after it
    out of step with the PDF, and would hide exactly the failure worth knowing
    about.
    """
    if not PAGE_BREAK_RE.search(markdown):
        return [markdown]
    pages = [page.strip("\n") for page in PAGE_BREAK_RE.split(markdown)]
    return pages or [markdown]


def is_empty(page: str) -> bool:
    """True when Mathpix returned nothing usable for this page.

    A page holding only a figure is not empty — the figure is the content. What
    this catches is the page that came back with no words and no pictures.
    """
    if IMAGE_REF_RE.search(page):
        return False
    return not re.search(r"\w", page)


@dataclass
class MathpixClient:
    base_url: str = field(default_factory=lambda: settings.mathpix_url)
    app_id: str = field(default_factory=lambda: settings.mathpix_app_id)
    app_key: str = field(default_factory=lambda: settings.mathpix_app_key)
    connect_timeout: float = field(default_factory=lambda: settings.mathpix_connect_timeout)
    request_timeout: float = field(default_factory=lambda: settings.mathpix_request_timeout)
    poll_interval: float = field(default_factory=lambda: settings.mathpix_poll_interval)
    poll_timeout: float = field(default_factory=lambda: settings.mathpix_poll_timeout)

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.request_timeout, connect=self.connect_timeout)

    @property
    def headers(self) -> dict[str, str]:
        """Mathpix authenticates with both identifiers, but tolerates the key alone."""
        headers = {"app_key": self.app_key}
        if self.app_id:
            headers["app_id"] = self.app_id
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/files/v1{path}"

    def submit(self, pdf_path: Path, options: dict | None = None) -> str:
        """Upload a local PDF and return the `file_id` Mathpix tracks it under.

        The `Idempotency-Key` is derived from the file's own bytes and the options
        sent with it, so a submission retried after a network failure resolves to
        the job already running rather than to a second billed conversion.
        """
        payload = json.dumps(options or {}, sort_keys=True)
        try:
            digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise MathpixError(f"could not read {pdf_path.name}: {exc}") from exc
        key = hashlib.sha256(f"{digest}:{payload}".encode()).hexdigest()

        try:
            with pdf_path.open("rb") as handle:
                response = httpx.post(
                    self._url(""),
                    files={"file": (pdf_path.name, handle, "application/pdf")},
                    data={"options_json": payload},
                    headers={**self.headers, "Idempotency-Key": key},
                    timeout=self.timeout,
                )
            if response.is_error:
                raise MathpixError(f"mathpix upload failed: {_detail(response)}")
            body = response.json()
        except MathpixError:
            raise
        except OSError as exc:
            raise MathpixError(f"could not read {pdf_path.name}: {exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MathpixError(f"mathpix upload failed: {exc}") from exc

        file_id = body.get("file_id") if isinstance(body, dict) else None
        if not isinstance(file_id, str) or not file_id.strip():
            raise MathpixError("mathpix did not return a file_id")
        return file_id.strip()

    def status(self, file_id: str) -> MathpixStatus:
        try:
            response = httpx.get(self._url(f"/{file_id}"), headers=self.headers, timeout=self.timeout)
            if response.is_error:
                raise MathpixError(f"mathpix status failed: {_detail(response)}")
            return parse_status_response(response.json(), file_id)
        except MathpixError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise MathpixError(f"mathpix status failed: {exc}") from exc

    def poll(
        self,
        file_id: str,
        on_status: Callable[[MathpixStatus], None] | None = None,
        deadline: float | None = None,
    ) -> MathpixStatus:
        """Poll until the document reaches a terminal state, reporting progress."""
        limit = deadline if deadline is not None else time.monotonic() + self.poll_timeout
        while True:
            state = self.status(file_id)
            if on_status is not None:
                on_status(state)
            if state.failed:
                raise MathpixError(f"mathpix could not process the document: {state.error or 'error'}")
            if state.done:
                return state
            if time.monotonic() >= limit:
                raise MathpixError(
                    f"mathpix did not finish within {self.poll_timeout:.0f}s "
                    f"(status {state.status}, {state.percent_done:.0f}% done)"
                )
            time.sleep(self.poll_interval)

    def fetch(self, file_id: str, ext: str) -> bytes:
        """Download one result. Raises `MathpixNotReady` when it is still converting."""
        try:
            response = httpx.get(
                self._url(f"/{file_id}.{ext}"), headers=self.headers, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            raise MathpixError(f"mathpix download of .{ext} failed: {exc}") from exc

        if not response.is_error:
            return response.content

        name = _error_id(response)
        if response.status_code == 415 or name == "unsupported_format":
            raise MathpixUnsupported(f".{ext} was not requested for this document")
        if response.status_code == 409 or name == "format_not_ready":
            raise MathpixNotReady(f".{ext} is still converting")
        raise MathpixError(f"mathpix download of .{ext} failed: {_detail(response)}")

    def fetch_all(
        self,
        file_id: str,
        wanted: Iterable[str],
        on_ready: Callable[[str, bytes], None],
        deadline: float | None = None,
    ) -> dict[str, str]:
        """Download every requested result, returning the reason each missing one is missing.

        Formats complete independently, so this retries a not-ready format rather
        than giving up on it. An explicitly unsupported optional format is simply
        absent — a document with no tables has no `.xlsx`. Operational HTTP errors
        still fail collection instead of masquerading as unsupported output.
        MMD is required for the page-aligned preview; every selected conversion
        format, including DOCX, is optional because Mathpix may not produce it
        for a particular document.
        """
        limit = deadline if deadline is not None else time.monotonic() + self.poll_timeout
        pending = list(dict.fromkeys(wanted))
        missing: dict[str, str] = {}

        while pending:
            waiting: list[str] = []
            for ext in pending:
                try:
                    on_ready(ext, self.fetch(file_id, ext))
                except MathpixNotReady:
                    waiting.append(ext)
                except MathpixUnsupported as exc:
                    missing[ext] = str(exc)
            pending = waiting
            if not pending:
                break
            if time.monotonic() >= limit:
                for ext in pending:
                    missing[ext] = "still converting when the job gave up waiting"
                break
            time.sleep(self.poll_interval)

        absent_required = [ext for ext in REQUIRED_RESULTS if ext in missing]
        if absent_required:
            ext = absent_required[0]
            raise MathpixError(f"mathpix produced no .{ext}: {missing[ext]}")
        return missing

    def download_images(self, markdown: str, work_dir: Path) -> tuple[str, Applied]:
        """Fetch Mathpix's hosted crops into the job and repoint the references at them.

        Mathpix keeps its crops on its own CDN, so a reference that is left alone
        is a reference the preview cannot show offline. Only references that
        download cleanly are rewritten; anything else is left exactly as it
        stands, because silently deleting or redirecting it would misrepresent
        what Mathpix did. Raw Mathpix exports are never passed through this
        function.
        """
        image_dir = work_dir / RAW_DIR / RAW_IMAGE_DIR
        downloaded: dict[str, str] = {}
        failed: set[str] = set()
        unresolved = 0
        total_bytes = 0

        def replace(match: re.Match) -> str:
            nonlocal unresolved, total_bytes
            target = match.group("target")
            if target in downloaded:
                return f"{match.group('prefix')}{downloaded[target]}{match.group('suffix')}"
            if target in failed:
                return match.group(0)
            if not target.lower().startswith(("http://", "https://")):
                # A relative reference is already local as far as this module is
                # concerned; leaving it alone is the honest answer.
                unresolved += 1
                return match.group(0)
            if len(downloaded) >= MAX_IMAGES or total_bytes > MAX_IMAGE_BYTES:
                failed.add(target)
                unresolved += 1
                return match.group(0)
            try:
                name = _safe_image_name(target, len(downloaded))
                response = httpx.get(target, timeout=self.timeout, follow_redirects=True)
                response.raise_for_status()
                data = response.content
            except (MathpixError, httpx.HTTPError):
                # Recorded on the `Applied` count, never raised: one crop that
                # will not download is a figure that degrades to its caption,
                # not a document that failed to convert.
                failed.add(target)
                unresolved += 1
                return match.group(0)
            total_bytes += len(data)
            image_dir.mkdir(parents=True, exist_ok=True)
            (image_dir / name).write_bytes(data)
            reference = f"{RAW_DIR}/{RAW_IMAGE_DIR}/{name}"
            downloaded[target] = reference
            return f"{match.group('prefix')}{reference}{match.group('suffix')}"

        rewritten = IMAGE_REF_RE.sub(replace, markdown)
        applied = Applied(
            images_downloaded=len(downloaded),
            images_failed=len(failed),
            images_unresolved=unresolved,
        )
        return rewritten, applied

    def delete(self, file_id: str) -> None:
        """Remove the document from Mathpix's storage. Best effort by design.

        The job already has everything it needs by the time this runs, so a
        failure here is worth neither an exception nor a retry — but leaving a
        user's document on a third party's storage when nothing needs it there
        is worth one request to avoid.
        """
        try:
            httpx.delete(self._url(f"/{file_id}"), headers=self.headers, timeout=self.timeout)
        except httpx.HTTPError:
            return
