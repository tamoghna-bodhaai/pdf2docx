"""Durable record of past conversions, kept as a JSON file next to the job folders.

The job registry itself lives in memory; this module is only responsible for
mirroring it to disk so that history — and the finished documents it points at —
survive a server restart.
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
    """Every stored record, oldest first. A missing or corrupt file reads as empty."""
    file = path()
    if not file.exists():
        return []
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # History is a convenience — never let a bad file stop the server booting.
        return []

    records = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict) and record.get("id")]


def save(records: list[dict]) -> None:
    """Replace the stored history. Written atomically so a crash cannot truncate it."""
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"version": _VERSION, "jobs": records}, indent=2)
    temporary = file.with_name(file.name + ".tmp")
    with _WRITE_LOCK:
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, file)


def overflow(records: list[dict], limit: int | None = None) -> list[dict]:
    """The oldest records beyond `limit`, i.e. the ones the caller should evict.

    `records` is expected newest-first. A limit of 0 keeps everything.
    """
    limit = settings.history_limit if limit is None else limit
    if limit <= 0 or len(records) <= limit:
        return []
    return records[limit:]
