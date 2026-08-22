"""Runtime configuration, read once from the environment / .env file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .model_policy import DEFAULT_MODEL, is_model_allowed

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _model_setting(name: str, default: str) -> str:
    """Read a model id, replacing legacy disallowed values with a safe default."""
    raw = os.environ.get(name, "").strip()
    return raw if raw and is_model_allowed(raw) else default


def _data_dir() -> Path:
    """Where conversion history and finished documents are kept between runs.

    The volume comes before the default, and deliberately so. Railway injects
    `RAILWAY_VOLUME_MOUNT_PATH` only when a volume is actually attached, which
    makes it the one trustworthy answer to "is there durable storage here" — a
    hardcoded `/data` says nothing, because the directory exists either way.
    Reading the mount path rather than being told it also removes the failure
    where a volume is attached at one path while this points at another; there
    is no second place for the two to disagree.

    An explicit `PDF2DOCX_DATA_DIR` still wins, because the tests set it to a
    temporary directory before importing `app` and a developer may reasonably
    want to override it on a machine that has a volume.
    """
    raw = os.environ.get("PDF2DOCX_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if mount:
        return Path(mount)
    return Path.home() / ".pdf2docx"


def _marker_options() -> dict:
    """Whatever the user wants passed straight through to marker's own config.

    Deliberately not an allowlist of the options this codebase happens to know
    about. Marker's `ConfigParser` is the same object its CLI builds its flags
    into, so anything the CLI accepts — `use_llm`, `force_ocr`, `mode`,
    `format_lines`, or whatever upstream adds next — is reachable from here
    without a change to this file. A malformed value is dropped rather than
    raised, the way `_int` and `_float` above treat theirs.
    """
    raw = os.environ.get("PDF2DOCX_MARKER_OPTIONS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _marker_extra_formats() -> tuple[str, ...]:
    """Extra renderers to run purely so their output can be read.

    Each one is a second conversion of the same PDF, so this is off by default.
    """
    raw = os.environ.get("PDF2DOCX_MARKER_EXTRA_FORMATS", "")
    wanted = (value.strip().lower() for value in raw.split(","))
    return tuple(value for value in wanted if value in {"markdown", "html", "json", "chunks"})


def _invite_codes() -> tuple[str, ...]:
    """The codes that may be used to create an account, comma-separated.

    More than one so each teammate can be given their own. Removing one retires
    that invitation without affecting other codes; it does not disable an
    account that has already registered. Empty closes sign-ups.
    """
    raw = os.environ.get("PDF2DOCX_INVITE_CODES", "")
    return tuple(code.strip() for code in raw.split(",") if code.strip())


def _mathpix_options() -> dict:
    """Whatever the user wants passed straight through to Mathpix's own options.

    Deliberately not an allowlist, for the same reason `_marker_options` is not.
    The Files API accepts every OCR and conversion option `POST /v3/pdf` does, so
    `rm_spaces`, `idiomatic_eqn_arrays`, `include_equation_tags`,
    `enable_tables_fallback`, `alphabets_allowed`, `conversion_options` or
    whatever Mathpix adds next has to reach it intact for the mode to be worth
    having. A malformed value is dropped rather than raised.
    """
    raw = os.environ.get("PDF2DOCX_MATHPIX_OPTIONS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _mathpix_formats() -> tuple[str, ...]:
    """Default Mathpix exports for clients that omit a per-job selection.

    This inverts `_marker_extra_formats` above, and the difference between the
    two backends is the reason. Every extra marker renderer is another full
    conversion of the PDF, so marker's list is off by default; Mathpix converts
    the document once and renders each format from that same job, so there is no
    reason to withhold any.

    Only the parsing happens here. Which names are real and what an empty
    configured selection resolves to are `mathpix_client.requested_formats`'s
    to decide, because that is where the format catalog lives — and importing it
    here would be circular. A browser job's explicit selection bypasses this
    legacy default, including when it deliberately omits DOCX.
    """
    raw = os.environ.get("PDF2DOCX_MATHPIX_FORMATS", "").strip()
    wanted = (value.strip().lower() for value in raw.split(","))
    return tuple(value for value in wanted if value)


@dataclass(frozen=True)
class Settings:
    # marker-pdf sidecar, used by the `marker` layout mode. Its timeout covers a
    # whole document rather than one page, so it is generous by comparison with
    # anything measured per request.
    marker_url: str = os.environ.get("PDF2DOCX_MARKER_URL", "http://127.0.0.1:8011").strip().rstrip("/")
    marker_connect_timeout: float = _float("PDF2DOCX_MARKER_CONNECT_TIMEOUT", 2.0)
    marker_request_timeout: float = _float("PDF2DOCX_MARKER_REQUEST_TIMEOUT", 900.0)
    marker_options: dict = field(default_factory=_marker_options)
    marker_extra_formats: tuple[str, ...] = field(default_factory=_marker_extra_formats)
    # Ask marker for its JSON renderer as well, so the page viewer can draw the
    # blocks marker found over the page. That is a second conversion of the same
    # PDF — marker renders the document it just built, and the sidecar builds it
    # again per request — so it is a real cost, and turning this off leaves a
    # marker job converting exactly as it did before, with a plain page image in
    # place of the overlay. What marker returns is still written verbatim; this
    # only changes what it is asked for.
    marker_detection: bool = os.environ.get("PDF2DOCX_MARKER_DETECTION", "on").strip().lower() != "off"

    # Mathpix Files API, used by the `mathpix` layout mode. Unlike every other
    # backend here this one is a paid remote service and the document leaves the
    # machine, so retention is off by default and the job deletes what it
    # uploaded once it has the results.
    mathpix_url: str = os.environ.get("MATHPIX_URL", "https://api.mathpix.com").strip().rstrip("/")
    mathpix_app_id: str = os.environ.get("MATHPIX_APP_ID", "").strip()
    mathpix_app_key: str = os.environ.get("MATHPIX_APP_KEY", "").strip()
    # Per HTTP call, not per document — a Mathpix conversion is many short
    # requests and one long wait, and the wait is `mathpix_poll_timeout`.
    mathpix_connect_timeout: float = _float("PDF2DOCX_MATHPIX_CONNECT_TIMEOUT", 10.0)
    mathpix_request_timeout: float = _float("PDF2DOCX_MATHPIX_REQUEST_TIMEOUT", 120.0)
    mathpix_poll_interval: float = _float("PDF2DOCX_MATHPIX_POLL_INTERVAL", 2.0)
    mathpix_poll_timeout: float = _float("PDF2DOCX_MATHPIX_POLL_TIMEOUT", 1800.0)
    mathpix_options: dict = field(default_factory=_mathpix_options)
    mathpix_formats: tuple[str, ...] = field(default_factory=_mathpix_formats)
    # Legacy setting retained so old `.env` files remain harmless. Mathpix line
    # geometry is now kept only as a raw export and never drawn in the viewer.
    mathpix_detection: bool = os.environ.get("PDF2DOCX_MATHPIX_DETECTION", "on").strip().lower() != "off"
    # Let Mathpix retain the document to improve their models. Off unless asked
    # for: this is someone else's PDF, and the default should not give it away.
    mathpix_improve: bool = os.environ.get("PDF2DOCX_MATHPIX_IMPROVE", "off").strip().lower() == "on"
    # Delete the uploaded document from Mathpix once the results are downloaded.
    mathpix_delete: bool = os.environ.get("PDF2DOCX_MATHPIX_DELETE", "on").strip().lower() != "off"
    # What Mathpix charges per page, used only to estimate a job's cost. Mathpix
    # bills per page rather than per token, so the figure this reports is an
    # estimate from the page count and is flagged as one.
    mathpix_page_rate: float = _float("PDF2DOCX_MATHPIX_PAGE_RATE", 0.0015)

    # OpenRouter (OpenAI-compatible) endpoint
    api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    base_url: str = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    app_url: str = os.environ.get("OPENROUTER_APP_URL", "http://localhost:8000")
    app_title: str = os.environ.get("OPENROUTER_APP_TITLE", "PDF to DOCX")

    # Model
    model: str = field(
        default_factory=lambda: _model_setting("PDF2DOCX_MODEL", DEFAULT_MODEL)
    )
    # Ask a second time where a scanned page's figures are, reading their boxes
    # off a coordinate grid ruled over the page. The boxes a transcription
    # reports alongside the text are its least reliable output, and a crop taken
    # from a wrong one is the most visible way this tool gets a page wrong.
    # Costs one extra request per page that has figures. Turn off to trust the
    # transcription's own boxes.
    locate_figures: bool = os.environ.get("PDF2DOCX_LOCATE", "on").strip().lower() != "off"
    # Which model does that locating. Reading a box off a ruled line is a
    # different skill from transcribing, and a cheap model can be poor at it
    # while transcribing perfectly well — so it can be pointed somewhere else.
    # Empty means use the transcription model.
    figure_model: str = field(
        default_factory=lambda: _model_setting("PDF2DOCX_FIGURE_MODEL", "")
    )
    reasoning_effort: str = os.environ.get("PDF2DOCX_REASONING_EFFORT", "").strip()
    max_tokens: int = _int("PDF2DOCX_MAX_TOKENS", 16000)

    # Output layout. "structured" rebuilds the PDF's own content as an editable
    # document — flowing paragraphs, native equations, real tables and pictures;
    # "replica" reproduces the page exactly with positioned frames, at the cost of
    # being editable; "flow" has the model retype each page as Markdown;
    # "marker" hands the whole PDF to marker-pdf and writes what it returns.
    layout: str = os.environ.get("PDF2DOCX_LAYOUT", "structured").strip().lower()

    # Column layout of the flowing modes' output, and only the default: the
    # browser offers the same choice per conversion. "auto" (the browser calls
    # it "multi") reads each source page and sets the Word section to match, so
    # a two-column book page comes back as two columns; "off" — "natural" in the
    # browser, since one flowing column is what a linear transcription is —
    # keeps every page in one column whatever the source did; a number forces
    # that many columns throughout. The replica modes ignore this: they
    # reproduce the page's geometry already.
    columns: str = os.environ.get("PDF2DOCX_COLUMNS", "auto").strip().lower()
    # Equations in replica mode: "auto" reads them back as native Word equations,
    # "off" leaves them as exact images.
    math_mode: str = os.environ.get("PDF2DOCX_MATH", "auto").strip().lower()
    # Map PDF fonts onto universally available equivalents (Times/Arial/Courier).
    # Turn off if the reader has the document's original fonts installed.
    map_fonts: bool = os.environ.get("PDF2DOCX_FONT_MAP", "on").strip().lower() != "off"

    # Rendering & throughput
    dpi: int = _int("PDF2DOCX_DPI", 180)
    max_edge: int = _int("PDF2DOCX_MAX_EDGE", 2000)
    diagram_dpi: int = _int("PDF2DOCX_DIAGRAM_DPI", 300)
    math_dpi: int = _int("PDF2DOCX_MATH_DPI", 320)
    # Cap a figure crop at the resolution the page can actually supply, so a
    # region cut out of a scan is not interpolated up into its own noise.
    # Regions that are drawn rather than scanned have no such ceiling and are
    # always cut at `diagram_dpi`. Turn off to cut everything at `diagram_dpi`.
    crop_native_dpi: bool = os.environ.get("PDF2DOCX_CROP_NATIVE", "on").strip().lower() != "off"
    concurrency: int = _int("PDF2DOCX_CONCURRENCY", 4)
    max_pages: int = _int("PDF2DOCX_MAX_PAGES", 0)

    # Local conversion history
    data_dir: Path = field(default_factory=_data_dir)
    history_limit: int = _int("PDF2DOCX_HISTORY_LIMIT", 100)

    # Accounts. Conversions are billed per page by Mathpix, so a public URL with
    # open signup is an open wallet: an account can only be created by someone
    # holding one of the invite codes, and configuring none closes signup
    # entirely rather than opening it. Sessions are opaque tokens looked up in
    # the database, so there is no signing secret to configure.
    invite_codes: tuple[str, ...] = field(default_factory=_invite_codes)
    session_days: int = _int("PDF2DOCX_SESSION_DAYS", 30)
    # `auto` reads the scheme off the request, which is the only setting that is
    # right in both places this runs: `on` silently drops the cookie when the
    # app is reached over plain HTTP (a LAN address during development), and
    # `off` would ship a session cookie without the flag in production. Uvicorn
    # runs with `--proxy-headers`, so behind Railway's TLS termination the
    # request still reports `https`. `on`/`off` remain as overrides.
    cookie_secure: str = os.environ.get("PDF2DOCX_COOKIE_SECURE", "auto").strip().lower()

    # Refuse an upload larger than this before it is written, so one oversized
    # PDF cannot fill the volume the whole history lives on. 0 means no limit.
    max_upload_mb: int = _int("PDF2DOCX_MAX_UPLOAD_MB", 50)

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def headers(self) -> dict[str, str]:
        """Attribution headers OpenRouter uses to identify the calling app."""
        return {"HTTP-Referer": self.app_url, "X-Title": self.app_title}


settings = Settings()
