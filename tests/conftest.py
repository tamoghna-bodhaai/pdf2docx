"""Synthetic PDFs to crop from, and the accounts the API is reached through.

The defects these tests cover are about resolution, so the fixtures are built
around it: a page that is nothing but a coarse scan, and a page that is drawn.
Both are made here rather than checked in, so a fixture can never drift away
from the resolution its test claims for it.

The rest is authentication. Every `/api` route is behind a session now, so a
bare `TestClient` gets 401 and nothing else; `client` below is signed in, and
`other_client` is a second account for the tests that check one user cannot
reach another's documents.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

# Before anything imports `app`. `Settings` reads the environment once, at class
# definition time, and `app.main` restores its job registry at import — so
# pointing the data directory somewhere disposable has to happen first.
_DATA_DIR = tempfile.mkdtemp(prefix="pdf2docx-tests-")
os.environ["PDF2DOCX_DATA_DIR"] = _DATA_DIR
os.environ["PDF2DOCX_INVITE_CODES"] = "test-invite,second-invite"
os.environ["PDF2DOCX_COOKIE_SECURE"] = "off"
# Emptied rather than left alone, for the storage guard: `load_dotenv` does not
# override what is already set. `PDF2DOCX_DATA_DIR` above already wins over the
# volume, but a suite run *on* Railway would otherwise inherit its environment
# and decide the fatal check applies to it. No test may depend on the host it
# happens to be running on.
os.environ["RAILWAY_ENVIRONMENT"] = ""
os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = ""
os.environ["RAILWAY_VOLUME_NAME"] = ""

import fitz
import pytest
from fastapi.testclient import TestClient

from app import auth, db, main

A4 = (595.0, 842.0)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """A database per test, and an empty job registry to go with it."""
    monkeypatch.setattr(db, "DATABASE", tmp_path / "pdf2docx.db")
    monkeypatch.setattr(main, "JOBS", {})
    auth.SIGN_IN.reset()
    auth.INVITE.reset()
    yield


def _account(email: str) -> TestClient:
    """A registered account, on a client already carrying its session cookie.

    Signed up through the real endpoint rather than by writing rows: the cookie
    the tests then ride on is the same one a browser would be given.
    """
    client = TestClient(main.app)
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "a good password", "invite_code": "test-invite"},
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def client(database):
    """Signed in. This is the client every API test should reach for."""
    return _account("first@example.com")


@pytest.fixture
def user(client):
    """The account `client` is signed in as."""
    return auth.User(**client.get("/api/auth/me").json())


@pytest.fixture
def other_client(database):
    """A second account, for proving one user cannot reach another's jobs."""
    return _account("second@example.com")


@pytest.fixture
def anonymous(database):
    """Signed out, for the tests about what an unauthenticated caller sees."""
    return TestClient(main.app)


def _speckled(width: int, height: int) -> fitz.Pixmap:
    """An image with content in it, so nothing downstream optimises it away."""
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    pixmap.clear_with(255)
    for step in range(0, min(width, height), 4):
        pixmap.set_rect(fitz.IRect(step, step, step + 2, height), (0, 0, 0))
    return pixmap


@pytest.fixture
def scan(tmp_path: Path):
    """A page whose only content is one image, at a chosen resolution.

    This is what a photographed or photocopied book page looks like to the
    extractor: no text layer, no drawings, one raster covering the page.
    """

    def build(dpi: float = 90.0, name: str = "scan.pdf") -> Path:
        path = tmp_path / name
        document = fitz.open()
        page = document.new_page(width=A4[0], height=A4[1])
        pixels = _speckled(round(A4[0] / 72 * dpi), round(A4[1] / 72 * dpi))
        page.insert_image(page.rect, pixmap=pixels)
        document.save(path)
        document.close()
        return path

    return build


@pytest.fixture
def drawn(tmp_path: Path) -> Path:
    """A page of vector artwork and text — no raster, so no resolution ceiling."""
    path = tmp_path / "drawn.pdf"
    document = fitz.open()
    page = document.new_page(width=A4[0], height=A4[1])
    page.draw_rect(fitz.Rect(100, 100, 300, 300), color=(0, 0, 0), width=1.5)
    page.draw_line(fitz.Point(100, 100), fitz.Point(300, 300), color=(0, 0, 0))
    page.insert_text(fitz.Point(100, 400), "a caption under the figure", fontsize=11)
    document.save(path)
    document.close()
    return path
