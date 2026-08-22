"""Accounts: who can create one, who can sign in, and whose documents they see.

The last of those is the reason this module exists. Before accounts, a job id
was the only thing protecting a document, and job ids are twelve hex characters.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from app import auth, db, history, main


def _settings(monkeypatch, **overrides):
    """`Settings` is frozen, so a test swaps the whole object rather than a field."""
    replaced = dataclasses.replace(auth.settings, **overrides)
    monkeypatch.setattr(auth, "settings", replaced)
    monkeypatch.setattr(main, "settings", replaced)


def _signup(client, email="new@example.com", password="a good password", code="test-invite"):
    return client.post(
        "/api/auth/signup",
        json={"email": email, "password": password, "invite_code": code},
    )


# -- passwords -------------------------------------------------------------------- #


def test_a_password_verifies_against_its_own_hash() -> None:
    encoded = auth.hash_password("a good password")
    assert auth.verify_password("a good password", encoded)
    assert not auth.verify_password("a good passwore", encoded)


def test_the_same_password_hashes_differently_every_time() -> None:
    """Distinct salts, so two accounts sharing a password do not look alike."""
    assert auth.hash_password("same") != auth.hash_password("same")


def test_a_hash_this_cannot_parse_fails_rather_than_raises() -> None:
    for stored in ("", "not-a-hash", "scrypt$only$four$parts", "bcrypt$1$2$3$ab$cd"):
        assert not auth.verify_password("anything", stored)


# -- signup ----------------------------------------------------------------------- #


def test_signup_needs_the_invite_code(anonymous) -> None:
    assert _signup(anonymous, code="wrong").status_code == 403
    assert _signup(anonymous, code="").status_code == 403
    assert db.user_count() == 0


def test_signup_with_the_right_code_creates_the_account(anonymous) -> None:
    reply = _signup(anonymous)
    assert reply.status_code == 200
    assert reply.json()["email"] == "new@example.com"
    assert auth.COOKIE_NAME in reply.cookies
    assert anonymous.get("/api/auth/me").json()["email"] == "new@example.com"


def test_any_configured_code_creates_an_account(anonymous) -> None:
    """Each teammate gets their own, so all of them have to work."""
    assert _signup(anonymous, email="a@team.io", code="test-invite").status_code == 200
    assert _signup(anonymous, email="b@team.io", code="second-invite").status_code == 200
    assert db.user_count() == 2


def test_withdrawing_one_code_leaves_the_others_working(anonymous, monkeypatch) -> None:
    _settings(monkeypatch, invite_codes=("second-invite",))
    assert _signup(anonymous, email="a@team.io", code="test-invite").status_code == 403
    assert _signup(anonymous, email="b@team.io", code="second-invite").status_code == 200


def test_guessing_invite_codes_is_rate_limited(anonymous) -> None:
    """Five digits is a hundred thousand codes; the guess rate is the defence."""
    for index in range(auth.INVITE.limit):
        reply = _signup(anonymous, email=f"guess{index}@team.io", code=str(10000 + index))
        assert reply.status_code == 403

    assert _signup(anonymous, email="more@team.io", code="99999").status_code == 429
    # Blocked even when the code is right, so guessing cannot be resumed.
    assert _signup(anonymous, email="real@team.io", code="test-invite").status_code == 429
    assert db.user_count() == 0


def test_signup_is_closed_when_no_code_is_configured(anonymous, monkeypatch) -> None:
    """An unset code closes sign-ups. Getting this backwards opens the wallet."""
    _settings(monkeypatch, invite_codes=())
    assert not auth.signup_open()
    assert anonymous.get("/api/auth/config").json()["signup_open"] is False
    assert _signup(anonymous, code="").status_code == 403
    assert _signup(anonymous, code="test-invite").status_code == 403
    # Closed comes before the throttle: a shut door is not a wrong guess.
    assert len(auth.INVITE) == 0


def test_an_email_can_only_have_one_account(anonymous) -> None:
    assert _signup(anonymous).status_code == 200
    assert _signup(anonymous).status_code == 409
    # Case is not a second account.
    assert _signup(anonymous, email="NEW@example.com").status_code == 409


def test_signup_refuses_a_short_password_or_a_bad_address(anonymous) -> None:
    assert _signup(anonymous, password="short").status_code == 400
    assert _signup(anonymous, email="not-an-address").status_code == 400
    assert db.user_count() == 0


# -- sign in ---------------------------------------------------------------------- #


def test_the_wrong_password_is_refused(anonymous) -> None:
    _signup(anonymous)
    reply = anonymous.post(
        "/api/auth/login", json={"email": "new@example.com", "password": "wrong"}
    )
    assert reply.status_code == 401


def test_an_unknown_address_uses_the_prebuilt_dummy_hash(
    anonymous, monkeypatch
) -> None:
    """A miss performs one verification, never a fresh hash plus verification."""
    monkeypatch.setattr(
        auth,
        "hash_password",
        lambda _password: pytest.fail("authentication built a new dummy hash"),
    )

    reply = anonymous.post(
        "/api/auth/login",
        json={"email": "missing@example.com", "password": "wrong"},
    )

    assert reply.status_code == 401


def test_signing_in_is_case_insensitive_about_the_address(anonymous) -> None:
    _signup(anonymous)
    reply = anonymous.post(
        "/api/auth/login",
        json={"email": "NEW@Example.com", "password": "a good password"},
    )
    assert reply.status_code == 200


def test_repeated_failures_are_throttled(anonymous) -> None:
    _signup(anonymous)
    body = {"email": "new@example.com", "password": "wrong"}
    for _ in range(auth.SIGN_IN.limit):
        assert anonymous.post("/api/auth/login", json=body).status_code == 401

    assert anonymous.post("/api/auth/login", json=body).status_code == 429


def test_the_right_password_is_never_refused_for_earlier_typos(anonymous) -> None:
    """The throttle caps work; it does not lock out whoever knows the password.

    This is the regression the sign-in form was actually failing on. Refusing
    before checking meant a handful of typos made the correct password useless
    for a quarter of an hour, which is indistinguishable — from the outside —
    from authentication being broken.
    """
    _signup(anonymous)
    wrong = {"email": "new@example.com", "password": "wrong"}
    for _ in range(auth.SIGN_IN.limit * 2):
        anonymous.post("/api/auth/login", json=wrong)
    assert auth.SIGN_IN.blocked("new@example.com")

    right = {"email": "new@example.com", "password": "a good password"}
    assert anonymous.post("/api/auth/login", json=right).status_code == 200
    # And getting in wipes the count, so the next typo starts from zero.
    assert not auth.SIGN_IN.blocked("new@example.com")


def test_a_successful_sign_in_clears_the_failures(anonymous) -> None:
    _signup(anonymous)
    for _ in range(auth.SIGN_IN.limit - 1):
        anonymous.post("/api/auth/login", json={"email": "new@example.com", "password": "no"})
    good = {"email": "new@example.com", "password": "a good password"}
    assert anonymous.post("/api/auth/login", json=good).status_code == 200
    assert not auth.SIGN_IN.blocked("new@example.com")


def test_a_registration_race_still_reports_duplicate_email(
    anonymous, monkeypatch
) -> None:
    monkeypatch.setattr(db, "user_by_email", lambda _email: None)

    def conflict(*_args, **_kwargs):
        raise sqlite3.IntegrityError("users.email")

    monkeypatch.setattr(db, "create_user", conflict)

    assert _signup(anonymous).status_code == 409


# -- sessions --------------------------------------------------------------------- #


def test_a_session_carries_across_requests(client) -> None:
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/history").status_code == 200


def test_signing_out_revokes_the_session(client) -> None:
    stolen = client.cookies.get(auth.COOKIE_NAME)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    # The token itself is dead, not merely dropped by this client.
    client.cookies.set(auth.COOKIE_NAME, stolen)
    assert client.get("/api/auth/me").status_code == 401


def test_an_unknown_or_expired_token_is_not_a_session(anonymous, client) -> None:
    anonymous.cookies.set(auth.COOKIE_NAME, "not-a-real-token")
    assert anonymous.get("/api/auth/me").status_code == 401

    # A real account, so this is the expiry being enforced and nothing else.
    owner = auth.User(**client.get("/api/auth/me").json())
    db.create_session(auth._token_hash("stale"), owner.id, "2000-01-01T00:00:00+00:00")
    anonymous.cookies.set(auth.COOKIE_NAME, "stale")
    assert anonymous.get("/api/auth/me").status_code == 401


# -- what a signed-out caller sees ------------------------------------------------ #


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/config"),
        ("get", "/api/history"),
        ("get", "/api/auth/me"),
        ("post", "/api/convert"),
        ("post", "/api/jobs/anything/start"),
        ("get", "/api/jobs/anything"),
        ("get", "/api/jobs/anything/markdown"),
        ("get", "/api/jobs/anything/detection"),
        ("get", "/api/jobs/anything/page/1.png"),
        ("get", "/api/jobs/anything/asset/figures/a.png"),
        ("get", "/api/jobs/anything/download"),
        ("delete", "/api/jobs/anything"),
        ("delete", "/api/history"),
    ],
)
def test_every_api_route_needs_a_session(anonymous, method, path) -> None:
    assert getattr(anonymous, method)(path).status_code == 401


def test_the_workspace_redirects_to_sign_in(anonymous, client) -> None:
    reply = anonymous.get("/", follow_redirects=False)
    assert reply.status_code == 302
    # Relative, so a TLS-terminating proxy cannot downgrade it to http.
    assert reply.headers["location"] == "/login"

    assert client.get("/", follow_redirects=False).status_code == 200


def test_the_sign_in_page_redirects_when_already_signed_in(anonymous, client) -> None:
    assert anonymous.get("/login").status_code == 200
    reply = client.get("/login", follow_redirects=False)
    assert reply.status_code == 302
    assert reply.headers["location"] == "/"


def test_the_health_check_needs_nothing(anonymous) -> None:
    body = anonymous.get("/healthz").json()
    assert body["ok"] is True
    # It also reports on storage, which is how a deployment missing its volume
    # can be spotted without signing in to an instance that just lost the
    # account you would sign in as.
    assert "data_dir" in body["storage"]


def test_the_first_account_claims_legacy_json_history(
    anonymous, monkeypatch, tmp_path
) -> None:
    directory = tmp_path / "jobs" / "legacyjob"
    directory.mkdir(parents=True)
    (directory / "source.pdf").write_bytes(b"%PDF legacy")
    legacy_file = tmp_path / "history.json"
    legacy_file.write_text(json.dumps({
        "version": 1,
        "jobs": [{
            "id": "legacyjob",
            "filename": "legacy.pdf",
            "pages": 2,
            "status": "ready",
            "created_at": "2025-01-01T00:00:00+00:00",
            "directory": str(directory),
        }, {
            "id": "missinglegacy",
            "filename": "missing.pdf",
            "pages": 1,
            "status": "done",
            "created_at": "2024-01-01T00:00:00+00:00",
            "directory": str(tmp_path / "jobs" / "missinglegacy"),
        }],
    }))
    monkeypatch.setattr(history, "path", lambda: legacy_file)

    reply = _signup(anonymous)

    assert reply.status_code == 200
    owner = reply.json()["id"]
    assert main.JOBS["legacyjob"].user_id == owner
    assert anonymous.get("/api/history").json()["jobs"][0]["id"] == "legacyjob"
    assert [record["id"] for record in db.load_jobs()] == ["legacyjob"]
    assert legacy_file.exists()

    second = _signup(
        anonymous,
        email="second-new@example.com",
        code="second-invite",
    )
    assert second.status_code == 200
    assert main.JOBS["legacyjob"].user_id == owner


# -- isolation: the point of the whole change ------------------------------------- #


@pytest.fixture
def owned(tmp_path, monkeypatch, user):
    """A finished job belonging to `user`, with something to download."""
    directory = tmp_path / "owned"
    directory.mkdir()
    (directory / "document.docx").write_bytes(b"private document")
    (directory / "document.md").write_text("# private")
    (directory / "detection.json").write_text('{"mode":"mathpix","pages":[]}')
    job = main.Job(
        id="ownedjob", user_id=user.id, filename="private.pdf", pages=1,
        directory=directory, status="done", layout="mathpix",
    )
    monkeypatch.setitem(main.JOBS, job.id, job)
    return job


@pytest.mark.parametrize(
    "method, suffix",
    [
        ("get", ""),
        ("get", "/markdown"),
        ("get", "/detection"),
        ("get", "/download"),
        ("get", "/download?format=mathpix-docx"),
        ("delete", ""),
    ],
)
def test_another_account_cannot_reach_a_job(other_client, owned, method, suffix) -> None:
    """404, not 403 — a 403 would confirm the id exists, and ids are guessable."""
    reply = getattr(other_client, method)(f"/api/jobs/{owned.id}{suffix}")
    assert reply.status_code == 404, reply.text
    assert (owned.directory / "document.docx").exists()


def test_another_account_cannot_start_a_job_with_format_parameters(
    other_client, owned
) -> None:
    reply = other_client.post(
        f"/api/jobs/{owned.id}/start", data={"formats": "docx,html"}
    )
    assert reply.status_code == 404
    assert owned.status == "done"


def test_the_owner_can_reach_what_the_stranger_cannot(client, owned) -> None:
    assert client.get(f"/api/jobs/{owned.id}").status_code == 200
    assert client.get(f"/api/jobs/{owned.id}/download").content == b"private document"


def test_history_shows_only_your_own_jobs(client, other_client, owned) -> None:
    mine = client.get("/api/history").json()
    assert [job["id"] for job in mine["jobs"]] == [owned.id]
    assert other_client.get("/api/history").json() == {
        "jobs": [], "total_cost": 0, "count": 0
    }


def test_clearing_history_leaves_other_accounts_alone(other_client, owned) -> None:
    assert other_client.delete("/api/history").json() == {"deleted": 0}
    assert (owned.directory / "document.docx").exists()
    assert owned.id in main.JOBS


def test_a_job_record_remembers_its_owner(owned) -> None:
    """Ownership survives the round trip through the database."""
    main._persist(owned)
    [restored] = db.load_jobs()
    assert restored["user_id"] == owned.user_id
    assert main.Job.from_record(restored).user_id == owned.user_id


def test_ownership_is_never_reported_to_the_browser(client, owned) -> None:
    assert "user_id" not in client.get(f"/api/jobs/{owned.id}").json()
    assert "user_id" not in client.get("/api/history").json()["jobs"][0]


def test_expired_sessions_are_swept_up(client) -> None:
    """They are refused either way; boot is where they stop accumulating."""
    owner = auth.User(**client.get("/api/auth/me").json())
    db.create_session(auth._token_hash("old"), owner.id, "2000-01-01T00:00:00+00:00")

    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2

    db.purge_expired_sessions()

    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    # The live one is untouched.
    assert client.get("/api/auth/me").status_code == 200


def test_the_throttle_table_cannot_grow_without_bound() -> None:
    """A flood of attempts for keys that do not exist must not fill memory."""
    throttle = auth.Throttle(limit=5, window=15 * 60, capacity=64)
    for index in range(throttle.capacity + 500):
        throttle.record(f"flood-{index}@example.com")
    assert len(throttle) <= throttle.capacity
