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
    """Where conversion history and finished documents are kept between runs."""
    raw = os.environ.get("PDF2DOCX_DATA_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".pdf2docx"


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

    # OpenRouter (OpenAI-compatible) endpoint
    api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    base_url: str = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    app_url: str = os.environ.get("OPENROUTER_APP_URL", "http://localhost:8000")
    app_title: str = os.environ.get("OPENROUTER_APP_TITLE", "PDF to DOCX")

    # Model
    model: str = os.environ.get("PDF2DOCX_MODEL", "anthropic/claude-sonnet-5")
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
    figure_model: str = os.environ.get("PDF2DOCX_FIGURE_MODEL", "").strip()
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

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def headers(self) -> dict[str, str]:
        """Attribution headers OpenRouter uses to identify the calling app."""
        return {"HTTP-Referer": self.app_url, "X-Title": self.app_title}


settings = Settings()
