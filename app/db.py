"""Durable store for accounts, sessions, and conversion history.

This replaces the single `history.json` the app kept when it had one implicit
user. The job registry itself still lives in memory; this module is only
responsible for mirroring it to disk — and now for saying who each job belongs
to — so that history and the finished documents it points at survive a restart.

A job's record is kept as the JSON blob `Job.to_record()` already produces
rather than being spread across columns. Only `id`, `user_id` and `created_at`
are promoted, because those are the only fields anything queries on; everything
else the dataclass grows comes along without a schema change.

Every call opens its own connection. Conversions run in Starlette's threadpool,
so a shared handle would need thread affinity this does not want, and SQLite in
WAL mode is happy with a connection per operation at this scale.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import history
from .config import settings

# Set by the tests. `settings` is a frozen dataclass whose fields are read at
# import time, so pointing the database somewhere else needs a seam here.
DATABASE: Path | None = None

_INIT_LOCK = threading.Lock()
_INITIALISED: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT,
  name          TEXT,
  picture       TEXT,
  created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  record     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_owner ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id);
"""


def _migrate(connection: sqlite3.Connection) -> None:
    """Bring a database written by an earlier version up to `SCHEMA`.

    Two changes since accounts were password-only. `name` and `picture` are
    plain additions, which SQLite can do in place. `password_hash` dropping its
    NOT NULL is not: SQLite cannot relax a constraint, so that one needs the
    table rebuilt around the new definition. Both are guarded by what is
    actually in the file, so this is a no-op on every boot after the first.
    """
    columns = {row["name"]: row for row in connection.execute("PRAGMA table_info(users)")}
    if not columns:  # A fresh database; `SCHEMA` has already built it correctly.
        return

    for added in ("name", "picture"):
        if added not in columns:
            connection.execute(f"ALTER TABLE users ADD COLUMN {added} TEXT")

    if not columns["password_hash"]["notnull"]:
        return

    # A Google-only account has no password to store, so the column has to admit
    # NULL. Foreign keys are deferred for the swap because `jobs` and `sessions`
    # reference `users(id)` and the rename would otherwise be seen as orphaning
    # them mid-flight.
    connection.executescript(
        """
        CREATE TABLE users_rebuilt (
          id            TEXT PRIMARY KEY,
          email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT,
          name          TEXT,
          picture       TEXT,
          created_at    TEXT NOT NULL
        );
        INSERT INTO users_rebuilt (id, email, password_hash, name, picture, created_at)
          SELECT id, email, password_hash, NULL, NULL, created_at FROM users;
        DROP TABLE users;
        ALTER TABLE users_rebuilt RENAME TO users;
        """
    )


def path() -> Path:
    """Where the database lives. Resolved per call, the way the old history was."""
    return DATABASE if DATABASE is not None else settings.data_dir / "pdf2docx.db"


def _initialise(file: Path) -> None:
    """Create the schema and run migrations, once per database file per process.

    On its own connection, and deliberately one that never turns foreign keys
    on: rebuilding `users` drops and recreates a table that `jobs` and
    `sessions` both reference, which enforcement would refuse mid-flight. Every
    connection handed out by `connect` enforces them as before.
    """
    key = str(file)
    if key in _INITIALISED:
        return
    with _INIT_LOCK:
        if key in _INITIALISED:
            return
        file.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(file, timeout=10.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            _migrate(connection)
            connection.commit()
        finally:
            connection.close()
        _INITIALISED.add(key)


@contextlib.contextmanager
def connect():
    """An initialised connection, committed and closed when the block ends.

    `sqlite3.Connection` is its own context manager, but that one commits
    without closing — which would leak a handle per call at the rate this module
    is used. Hence the wrapper.
    """
    file = path()
    _initialise(file)
    connection = sqlite3.connect(file, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------- users --

def create_user(
    user_id: str,
    email: str,
    password_hash: str | None,
    name: str | None = None,
    picture: str | None = None,
) -> dict:
    """Register an account. Raises `sqlite3.IntegrityError` if the email is taken.

    `password_hash` is None for an account that signs in through Google and has
    no password of its own to check.
    """
    record = {"id": user_id, "email": email, "password_hash": password_hash,
              "name": name, "picture": picture, "created_at": now()}
    with connect() as connection:
        connection.execute(
            "INSERT INTO users (id, email, password_hash, name, picture, created_at)"
            " VALUES (:id, :email, :password_hash, :name, :picture, :created_at)",
            record,
        )
    return record


def user_by_email(email: str) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
    return dict(row) if row else None


def user_by_id(user_id: str) -> dict | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def user_count() -> int:
    with connect() as connection:
        count = connection.execute("SELECT count(*) FROM users").fetchone()[0]
    return int(count)


# ------------------------------------------------------------------ sessions --

def create_session(token_hash: str, user_id: str, expires_at: str) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO sessions (token_hash, user_id, created_at, expires_at)"
            " VALUES (?, ?, ?, ?)",
            (token_hash, user_id, now(), expires_at),
        )


def session_user(token_hash: str) -> dict | None:
    """The account a live session belongs to, or None if it is unknown or expired."""
    with connect() as connection:
        row = connection.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id"
            " WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
            (token_hash, now()),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def purge_expired_sessions() -> None:
    with connect() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now(),))


# ---------------------------------------------------------------------- jobs --

def save_job(user_id: str, job_id: str, created_at: str, record: dict) -> None:
    """Insert or replace one job's record. Ownership never changes after the first write."""
    with connect() as connection:
        connection.execute(
            "INSERT INTO jobs (id, user_id, created_at, record) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET record = excluded.record",
            (job_id, user_id, created_at, json.dumps(record)),
        )


def load_jobs() -> list[dict]:
    """Every stored job record, newest first, each carrying its `user_id`.

    History is a convenience — a missing or corrupt database reads as empty
    rather than stopping the server booting.
    """
    try:
        with connect() as connection:
            rows = connection.execute(
                "SELECT user_id, record FROM jobs ORDER BY created_at DESC"
            ).fetchall()
    except sqlite3.Error:
        return []

    records = []
    for row in rows:
        try:
            record = json.loads(row["record"])
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id"):
            record["user_id"] = row["user_id"]
            records.append(record)
    return records


def import_legacy_jobs(user_id: str) -> list[dict]:
    """Claim unowned ``history.json`` records for the first registering account.

    ``INSERT OR IGNORE`` makes this safe to call after every registration: the
    first account to see a legacy id owns it, and a later registration cannot
    move that job. The source JSON remains untouched as a recovery copy.
    """
    claimed: list[dict] = []
    records = history.load()
    if not records:
        return claimed

    with connect() as connection:
        for record in records:
            directory = record.get("directory")
            if not directory or not Path(directory).exists():
                continue
            job_id = str(record["id"])
            created_at = str(record.get("created_at") or now())
            connection.execute(
                "INSERT OR IGNORE INTO jobs (id, user_id, created_at, record)"
                " VALUES (?, ?, ?, ?)",
                (job_id, user_id, created_at, json.dumps(record)),
            )
            owner = connection.execute(
                "SELECT user_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if owner and owner["user_id"] == user_id:
                claimed.append({**record, "user_id": user_id})
    return claimed


def delete_job(job_id: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def delete_jobs_for(user_id: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM jobs WHERE user_id = ?", (user_id,))


def overflow(user_id: str, limit: int | None = None) -> list[dict]:
    """One user's oldest records beyond `limit`, i.e. the ones to evict.

    The limit is per account rather than global: one teammate's busy week should
    not push another's finished documents off the end. A limit of 0 keeps
    everything.
    """
    limit = settings.history_limit if limit is None else limit
    if limit <= 0:
        return []
    with connect() as connection:
        rows = connection.execute(
            "SELECT record FROM jobs WHERE user_id = ?"
            " ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (user_id, limit),
        ).fetchall()

    stale = []
    for row in rows:
        try:
            record = json.loads(row["record"])
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id"):
            stale.append(record)
    return stale
