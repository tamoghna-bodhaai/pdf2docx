"""Runtime configuration, read once from the environment / .env file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

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

    Deliberately not an allowlist. The Files API accepts every OCR and conversion option `POST /v3/pdf` does, so
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

    Blank means every export. Mathpix converts the document once and renders
    each format from that same job, so there is no per-format cost and no reason
    to withhold any.

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
    # Mathpix Files API — the conversion backend. It is a paid remote service
    # and the document leaves the machine, so retention is off by default and
    # the job deletes what it uploaded once it has the results.
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
    # Let Mathpix retain the document to improve their models. Off unless asked
    # for: this is someone else's PDF, and the default should not give it away.
    mathpix_improve: bool = os.environ.get("PDF2DOCX_MATHPIX_IMPROVE", "off").strip().lower() == "on"
    # Delete the uploaded document from Mathpix once the results are downloaded.
    mathpix_delete: bool = os.environ.get("PDF2DOCX_MATHPIX_DELETE", "on").strip().lower() != "off"
    # What Mathpix charges per page, used only to estimate a job's cost. Mathpix
    # bills per page rather than per token, so the figure this reports is an
    # estimate from the page count and is flagged as one.
    mathpix_page_rate: float = _float("PDF2DOCX_MATHPIX_PAGE_RATE", 0.0015)

    # Source-page rendering, for the side-by-side viewer. `dpi` is capped by
    # `max_edge` so a poster-sized page cannot ask for an enormous PNG.
    dpi: int = _int("PDF2DOCX_DPI", 180)
    max_edge: int = _int("PDF2DOCX_MAX_EDGE", 2000)
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


settings = Settings()
