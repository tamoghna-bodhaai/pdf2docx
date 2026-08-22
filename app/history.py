"""Legacy JSON conversion history reader.

New account-aware history lives in SQLite, but installations that predate
accounts already have a ``history.json`` beside their job directories. The
first account that registers claims those records through ``db.import_legacy_jobs``.
This module remains deliberately read-compatible with both historical payload
shapes and never makes a malformed convenience file block startup.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .config import settings

_WRITE_LOCK = threading.Lock()
_VERSION = 1


def path() -> Path:
    return settings.data_dir / "history.json"


def load() -> list[dict]:
    """Every stored legacy record. A missing or corrupt file reads as empty."""
    file = path()
    if not file.exists():
        return []
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    records = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return []
    return [
        record for record in records
        if isinstance(record, dict) and record.get("id")
    ]


def save(records: list[dict]) -> None:
    """Retained for direct legacy callers; new web jobs use the database."""
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"version": _VERSION, "jobs": records}, indent=2)
    temporary = file.with_name(file.name + ".tmp")
    with _WRITE_LOCK:
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, file)


def overflow(records: list[dict], limit: int | None = None) -> list[dict]:
    """The oldest legacy records beyond the configured history limit."""
    limit = settings.history_limit if limit is None else limit
    if limit <= 0 or len(records) <= limit:
        return []
    return records[limit:]
