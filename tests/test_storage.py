"""Whether this instance will still have its data after the next deploy.

These tests guard one bug, and it is the worst one this application has had: the
container filesystem is wiped on every deploy, and with no volume attached at
`PDF2DOCX_DATA_DIR` every account, session and converted document went with it.
It presented as passwords mysteriously not working.

Two things made it silent, and there is a test here for each. The data directory
was hardcoded, so a deployment with no volume looked exactly like one with a
volume; and the check that noticed only wrote to a log, so the deploy that
emptied the disk went ahead regardless. Now the path is read back from the
volume, and a Railway deployment without one refuses to start.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from app import config, storage


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """No test here may inherit the host's answer to any of these."""
    for name in (
        "PDF2DOCX_DATA_DIR",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_VOLUME_MOUNT_PATH",
        "RAILWAY_VOLUME_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def _at(monkeypatch, directory: Path):
    """Point `storage` at a directory, the way the app would be pointed at one."""
    replaced = dataclasses.replace(storage.settings, data_dir=directory)
    monkeypatch.setattr(storage, "settings", replaced)


# -- where the data directory comes from ------------------------------------------ #


def test_an_explicit_setting_wins(monkeypatch, tmp_path) -> None:
    """The tests and local development both rely on this, so it comes first."""
    monkeypatch.setenv("PDF2DOCX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    assert config._data_dir() == tmp_path


def test_the_volume_is_used_when_nothing_is_set(monkeypatch) -> None:
    """The mount path is read back rather than duplicated, so it cannot drift."""
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    assert config._data_dir() == Path("/data")


def test_without_either_it_falls_back_to_the_home_directory(monkeypatch) -> None:
    assert config._data_dir() == Path.home() / ".pdf2docx"


def test_a_blank_mount_path_is_not_a_mount_path(monkeypatch) -> None:
    """Railway sets the variable only with a volume; empty must not count as one."""
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "   ")
    assert config._data_dir() == Path.home() / ".pdf2docx"


def test_the_settings_object_picks_it_up(monkeypatch) -> None:
    """`data_dir` is a default_factory, so a fresh Settings re-reads the volume."""
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    assert config.Settings().data_dir == Path("/data")


# -- refusing to start ------------------------------------------------------------ #


def test_railway_without_a_volume_refuses_to_start(monkeypatch, tmp_path) -> None:
    """The whole point. A failed deploy loses nothing; a successful one loses everything."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    _at(monkeypatch, tmp_path)
    with pytest.raises(storage.EphemeralStorage) as raised:
        storage.require_durable_storage()
    assert "no volume attached" in str(raised.value)


def test_railway_with_a_volume_starts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    _at(monkeypatch, tmp_path)
    storage.require_durable_storage()


def test_a_directory_inside_the_volume_starts(monkeypatch, tmp_path) -> None:
    """Being on the volume is what matters, not being its root."""
    inside = tmp_path / "pdf2docx"
    inside.mkdir()
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    _at(monkeypatch, inside)
    storage.require_durable_storage()


def test_a_volume_mounted_somewhere_else_refuses_to_start(monkeypatch, tmp_path) -> None:
    """Attached but unused is the most convincing way to look configured."""
    volume = tmp_path / "volume"
    elsewhere = tmp_path / "elsewhere"
    volume.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(volume))
    _at(monkeypatch, elsewhere)
    with pytest.raises(storage.EphemeralStorage) as raised:
        storage.require_durable_storage()
    assert str(volume) in str(raised.value)


def test_off_railway_never_refuses(monkeypatch, tmp_path) -> None:
    """A laptop has no volume and does not need one; this must never block it."""
    _at(monkeypatch, tmp_path)
    storage.require_durable_storage()


# -- what the health endpoint can say --------------------------------------------- #


def test_a_home_directory_is_not_judged(monkeypatch) -> None:
    """Ordinary local development. A warning here teaches people to ignore them."""
    _at(monkeypatch, Path.home() / ".pdf2docx")
    assert storage.report()["ephemeral"] is None


def test_the_report_names_the_volume(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    monkeypatch.setenv("RAILWAY_VOLUME_NAME", "pdf2docx-data")
    _at(monkeypatch, tmp_path)

    state = storage.report()
    assert state["volume"] == "pdf2docx-data"
    assert state["mount_path"] == str(tmp_path)
    assert state["ephemeral"] is False
    assert state["free_bytes"] > 0


def test_free_space_is_reported_for_an_unreadable_directory(monkeypatch, tmp_path) -> None:
    """Never the reason a health check fails, whatever it finds."""
    _at(monkeypatch, tmp_path / "does-not-exist")
    assert storage.report()["free_bytes"] is None


def test_the_warning_is_advisory_and_never_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    _at(monkeypatch, tmp_path)
    assert storage.WARNING.split("%s")[1][:20] in (storage.warn_if_ephemeral() or "")
