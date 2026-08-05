"""Runtime configuration, read once from the environment / .env file."""

from __future__ import annotations

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


def _data_dir() -> Path:
    """Where conversion history and finished documents are kept between runs."""
    raw = os.environ.get("PDF2DOCX_DATA_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".pdf2docx"


@dataclass(frozen=True)
class Settings:
    # OpenRouter (OpenAI-compatible) endpoint
    api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    base_url: str = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    app_url: str = os.environ.get("OPENROUTER_APP_URL", "http://localhost:8000")
    app_title: str = os.environ.get("OPENROUTER_APP_TITLE", "PDF to DOCX")

    # Model
    model: str = os.environ.get("PDF2DOCX_MODEL", "anthropic/claude-sonnet-5")
    reasoning_effort: str = os.environ.get("PDF2DOCX_REASONING_EFFORT", "").strip()
    max_tokens: int = _int("PDF2DOCX_MAX_TOKENS", 16000)

    # Output layout. "structured" rebuilds the PDF's own content as an editable
    # document — flowing paragraphs, native equations, real tables and pictures;
    # "replica" reproduces the page exactly with positioned frames, at the cost of
    # being editable; "flow" has the model retype each page as Markdown.
    layout: str = os.environ.get("PDF2DOCX_LAYOUT", "structured").strip().lower()
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
