"""Accounts: password hashing, sessions, and the dependency every route hangs off.

Nothing here needs a third-party library. Passwords are hashed with
`hashlib.scrypt` and sessions are `secrets` tokens looked up in the database, so
the whole of authentication is stdlib — the same way the rest of this codebase
prefers to work.

A session is an opaque random token whose SHA-256 is what gets stored. That is
deliberately not a signed cookie: a signed cookie stays valid until it expires,
whereas a row can be deleted, which is what "sign out" has to mean.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

from . import db
from .config import settings

COOKIE_NAME = "pdf2docx_session"

# scrypt work factors. 128 * N * r bytes of memory, so ~16 MB per hash at these
# values. `maxmem` has to be passed explicitly: OpenSSL's default budget rejects
# N this large on some builds.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SCRYPT_DKLEN = 32

MIN_PASSWORD_LENGTH = 8

class Throttle:
    """Counts recent failures per key and refuses once there are too many.

    Brute-force cover for a URL that is public even though the app is not. Held
    in memory rather than the database: it protects against a burst, and a
    restart clearing it is not worth a write per failed attempt.

    `capacity` bounds the table itself. Whoever is guessing controls the keys —
    an address that does not exist, an address the request invented — so without
    a ceiling a flood would grow it without bound.
    """

    def __init__(self, limit: int, window: float, capacity: int = 2048) -> None:
        self.limit = limit
        self.window = window
        self.capacity = capacity
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        """True when this key has failed too often too recently to try again."""
        cutoff = time.monotonic() - self.window
        with self._lock:
            recent = [stamp for stamp in self._failures.get(key, []) if stamp > cutoff]
            if recent:
                self._failures[key] = recent
            else:
                self._failures.pop(key, None)
            return len(recent) >= self.limit

    def record(self, key: str) -> None:
        cutoff = time.monotonic() - self.window
        with self._lock:
            if len(self._failures) >= self.capacity and key not in self._failures:
                # Sweep what has aged out before admitting another key. If that
                # frees nothing the table is genuinely saturated, and dropping
                # this record only forgoes a throttle — it never grants access.
                for stale in [
                    name for name, stamps in self._failures.items()
                    if not any(stamp > cutoff for stamp in stamps)
                ]:
                    del self._failures[stale]
                if len(self._failures) >= self.capacity:
                    return
            self._failures.setdefault(key, []).append(time.monotonic())

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()

    def __len__(self) -> int:
        return len(self._failures)


# Sign-ins, keyed by address. The limit is a ceiling on work rather than a
# tripwire: `authenticate` checks the password before consulting it, so a
# correct one always gets in and clears the count. An earlier version refused
# first, which meant five typos locked the right password out for a quarter of
# an hour — the single most common way this sign-in form "failed" for someone
# holding valid credentials. What the throttle still has to do is bound the
# scrypt work an attacker can force (~16 MB and real CPU per verification) and
# hold online guessing to a rate that a password of the required length
# survives, which a limit at this height does.
SIGN_IN = Throttle(limit=20, window=15 * 60)

# Wrong invite codes, keyed by client address. An invite code is short enough to
# be worth guessing — five digits is a hundred thousand of them — so without
# this the sign-up form is a keypad someone can walk end to end in an afternoon.
# Keyed by caller rather than by code, because the code is what is being varied.
INVITE = Throttle(limit=10, window=60 * 60)


@dataclass(frozen=True)
class User:
    id: str
    email: str
    created_at: str

    @classmethod
    def from_row(cls, row: dict) -> User:
        return cls(id=row["id"], email=row["email"], created_at=row["created_at"])

    def as_dict(self) -> dict:
        return {"id": self.id, "email": self.email, "created_at": self.created_at}


# ----------------------------------------------------------------- passwords --

def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$hash`, carrying its own parameters so they can change."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check. A stored hash this cannot parse simply fails."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Unknown addresses must take the same one scrypt verification as known ones.
# Generating this once also avoids doubling attacker-controlled CPU work on each
# miss (a fresh dummy hash plus its verification).
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


# ------------------------------------------------------------------ throttle --

def _throttle_key(email: str) -> str:
    return email.strip().lower()


def caller(request: Request) -> str:
    """Who is asking, for throttling. Uvicorn runs with `--proxy-headers`, so
    behind Railway this is the real client rather than the proxy."""
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------ sessions --

def token_hash(token: str) -> str:
    """What gets stored for an opaque token. Never the token itself.

    The session cookie is a random token held by the browser; only its hash is
    stored, so a database read is not enough to mint one.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_token_hash = token_hash


def cookie_secure(request: Request) -> bool:
    """Whether to mark cookies `Secure`, for the scheme this request arrived on.

    `auto` is the default and the only answer that is right in both places this
    runs. Marking a cookie `Secure` over plain HTTP does not downgrade it — the
    browser discards it outright, and silently: the sign-in returns 200, the
    redirect follows, and the app sends the caller straight back to the sign-in
    page with nothing to explain why. Hard-coding it off would instead ship a
    session cookie without the flag in production.

    Uvicorn runs with `--proxy-headers`, so behind a TLS-terminating proxy the
    request still reports the scheme the browser actually used.
    """
    if settings.cookie_secure == "on":
        return True
    if settings.cookie_secure == "off":
        return False
    return request.url.scheme == "https"


def open_session(request: Request, response: Response, user_id: str) -> str:
    """Mint a session, store its hash, and set the cookie. Returns the raw token."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_days)
    db.create_session(_token_hash(token), user_id, expires.isoformat(timespec="seconds"))
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_days * 24 * 60 * 60,
        httponly=True,
        # Every request the browser makes is same-origin, and `lax` keeps plain
        # `<img src="/api/jobs/.../page/1.png">` authenticated without any JS.
        samesite="lax",
        secure=cookie_secure(request),
        path="/",
    )
    return token


def close_session(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.delete_session(_token_hash(token))
    response.delete_cookie(COOKIE_NAME, path="/")


def session_user(request: Request) -> User | None:
    """Whoever is signed in, or None. Never raises — used by the page routes."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        row = db.session_user(_token_hash(token))
    except Exception:
        return None
    return User.from_row(row) if row else None


def current_user(request: Request) -> User:
    """FastAPI dependency: the signed-in account, or 401. Guards every API route."""
    user = session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user


# -------------------------------------------------------------------- signup --

def signup_open() -> bool:
    return bool(settings.invite_codes)


def check_invite(code: str, request: Request) -> None:
    """Raise unless `code` is one of the configured invite codes.

    No codes closes signup rather than opening it: the failure mode of getting
    this backwards is strangers spending someone else's Mathpix credit.

    Wrong codes are counted against the caller. Codes are short — five digits is
    a hundred thousand of them, and a handful are valid at once — so the guess
    rate is the only thing that makes them safe on a public URL.
    """
    if not signup_open():
        raise HTTPException(
            status_code=403,
            detail="Sign-ups are closed. Set PDF2DOCX_INVITE_CODES to open them.",
        )

    key = caller(request)
    if INVITE.blocked(key):
        raise HTTPException(
            status_code=429,
            detail="Too many invalid invite codes. Try again later.",
        )

    offered = code.strip()
    # Every configured code is compared, and each comparison is constant time:
    # stopping at the first match would leak which one matched through timing.
    matched = False
    for valid in settings.invite_codes:
        if secrets.compare_digest(offered, valid):
            matched = True

    if not matched:
        INVITE.record(key)
        raise HTTPException(status_code=403, detail="That invite code is not valid.")


def normalise_email(email: str) -> str:
    address = email.strip()
    if "@" not in address or address.startswith("@") or address.endswith("@") or " " in address:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return address


def check_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.",
        )
    return password


def register(email: str, password: str) -> User:
    """Create an account, refusing an address that already has one."""
    address = normalise_email(email)
    check_password(password)
    if db.user_by_email(address) is not None:
        raise HTTPException(status_code=409, detail="That email already has an account.")
    try:
        row = db.create_user(uuid.uuid4().hex, address, hash_password(password))
    except sqlite3.IntegrityError:
        # The preflight produces the useful common-case response; the unique
        # index remains authoritative when two registrations race.
        raise HTTPException(
            status_code=409, detail="That email already has an account."
        ) from None
    return User.from_row(row)


def authenticate(email: str, password: str) -> User:
    """Check a sign-in, counting failures against the throttle.

    The password is verified before the throttle is consulted, so a correct one
    is never refused for the sake of earlier typos — it succeeds and wipes the
    count. The throttle only ever refuses once the ceiling is reached, which
    caps the scrypt work a stranger can force without punishing the account
    holder for mistyping.
    """
    address = email.strip()
    row = db.user_by_email(address)
    # Hash regardless of whether the account exists, so the response time does
    # not say which addresses are registered. A row with no password hash —
    # left by an account created before sign-in required one — takes the dummy
    # for the same reason.
    stored = (row["password_hash"] if row else None) or _DUMMY_PASSWORD_HASH
    if row is not None and row["password_hash"] and verify_password(password, stored):
        SIGN_IN.clear(_throttle_key(address))
        return User.from_row(row)

    if SIGN_IN.blocked(_throttle_key(address)):
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-ins. Try again in a few minutes.",
        )
    SIGN_IN.record(_throttle_key(address))
    raise HTTPException(status_code=401, detail="Wrong email or password.")

