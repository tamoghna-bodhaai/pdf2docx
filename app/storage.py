"""Whether the data directory will still be there after a redeploy.

This module exists because of one failure. The container filesystem is wiped on
every deploy, `pdf2docx.db` and every converted document live under
`PDF2DOCX_DATA_DIR`, and if no volume is mounted there they go with it —
silently. What that looks like from the sign-in page is an account that worked
yesterday being told its password is wrong, which reads as authentication being
unreliable rather than as storage having been thrown away. It is the most
confusing way this application can fail.

Detection used to be a guess: whether the directory sat on a different device
from `/`. It no longer has to be. Railway injects `RAILWAY_VOLUME_MOUNT_PATH`
into the environment if and only if a volume is actually attached, so on Railway
the question has a direct answer. The device heuristic is kept for everywhere
else, where there is nothing better.

The consequence changed too, and that is the point of the module now. Warning
about this at boot was useless — the log line went to a place nobody was looking
while the deploy that emptied the disk went ahead anyway. On Railway a missing
volume is now fatal: the container exits, the healthcheck never answers, the
deploy is marked failed, and the previous deployment stays up. Refusing to start
is the only response that actually protects the data, because by the time anyone
reads a warning the accounts are already gone.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import settings


def on_railway() -> bool:
    """Whether this process is running on Railway.

    `RAILWAY_ENVIRONMENT` is set for every deployment there. It gates the fatal
    check, because refusing to boot is right for a deployment that is one push
    away from discarding real accounts and wrong for a laptop.
    """
    return bool(os.environ.get("RAILWAY_ENVIRONMENT", "").strip())


def volume_mount() -> Path | None:
    """The attached volume's mount point, or None if there is no volume."""
    raw = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    return Path(raw) if raw else None


def volume_name() -> str | None:
    raw = os.environ.get("RAILWAY_VOLUME_NAME", "").strip()
    return raw or None


def _under(directory: Path, parent: Path) -> bool:
    """Whether `directory` is `parent` or sits inside it. Never raises."""
    try:
        return directory.resolve().is_relative_to(parent.resolve())
    except (OSError, ValueError):
        return False


def _free_bytes(directory: Path) -> int | None:
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None


def report(directory: Path | None = None) -> dict:
    """What can be said about where this instance keeps its data.

    `ephemeral` is the one that matters, and it is deliberately three-valued:
    True means the data is known to be at risk, False means it is known to be on
    durable storage, and None means the question was not asked. It is not asked
    for a directory under the home directory, which is the ordinary
    local-development case — nobody running this on their laptop is redeploying a
    container, and a warning there would be noise that teaches people to ignore
    the real one.
    """
    directory = directory if directory is not None else settings.data_dir
    mount = volume_mount()

    state: dict = {
        "data_dir": str(directory),
        "volume": volume_name(),
        "mount_path": str(mount) if mount else None,
        "free_bytes": _free_bytes(directory),
    }

    if on_railway():
        # A volume that exists but is mounted somewhere else is worth catching
        # separately: it is attached, so it looks configured, and it is not where
        # anything is being written.
        state["ephemeral"] = not (mount is not None and _under(directory, mount))
        state["mount"] = mount is not None
        return state

    home = Path.home()
    checked = True
    try:
        checked = not directory.resolve().is_relative_to(home.resolve())
    except (OSError, ValueError):
        pass

    if not checked:
        return {**state, "ephemeral": None, "mount": None}

    try:
        on_root_device = directory.stat().st_dev == Path("/").stat().st_dev
    except OSError:
        # The directory does not exist yet, or cannot be read. Nothing useful to
        # say, and this must never be the thing that stops the app booting.
        return {**state, "ephemeral": None, "mount": None}

    return {**state, "ephemeral": on_root_device, "mount": os.path.ismount(directory)}


WARNING = (
    "%s is on the same device as / — nothing written there survives a redeploy, "
    "including the accounts and history in pdf2docx.db and every converted "
    "document. Attach a volume mounted at that path, or point PDF2DOCX_DATA_DIR "
    "at one."
)

# Where to tell someone to mount it. Deliberately not the current `data_dir`:
# with no volume that is the `~/.pdf2docx` fallback, and advising a mount at
# /root/.pdf2docx inside a container would be actively wrong.
SUGGESTED_MOUNT = "/data"

NO_VOLUME = (
    "Refusing to start: this is a Railway deployment with no volume attached, so "
    "{data_dir} is ordinary container storage and everything written there — "
    "every account, every session, and every converted document — is destroyed "
    "by the next deploy.\n"
    "\n"
    "Attach a volume to this service, mounted at {suggested}:\n"
    "    railway volume add -m {suggested}\n"
    "\n"
    "Nothing else needs configuring. The mount path is read back from "
    "RAILWAY_VOLUME_MOUNT_PATH, so PDF2DOCX_DATA_DIR can stay unset and the two "
    "cannot disagree."
)

MISPLACED_VOLUME = (
    "Refusing to start: a volume is attached at {mount}, but this instance is "
    "writing to {data_dir}, which is not on it. Everything written there is "
    "destroyed by the next deploy.\n"
    "\n"
    "Unset PDF2DOCX_DATA_DIR so the mount path is used, or point it inside "
    "{mount}."
)


class EphemeralStorage(RuntimeError):
    """Durable storage was required and is not present."""


def warn_if_ephemeral() -> str | None:
    """The warning this instance deserves at boot, or None. Never raises."""
    try:
        state = report()
    except Exception:
        return None
    return WARNING % state["data_dir"] if state.get("ephemeral") else None


def require_durable_storage() -> None:
    """Refuse to run a Railway deployment that would silently discard its data.

    Deliberately limited to Railway. Everywhere else a directory on the root
    device is normal and none of this applies, so the check must never be the
    reason a laptop or a test run fails to start.
    """
    if not on_railway():
        return

    state = report()
    if not state.get("ephemeral"):
        return

    mount = state.get("mount_path")
    message = (
        MISPLACED_VOLUME.format(mount=mount, data_dir=state["data_dir"])
        if mount
        else NO_VOLUME.format(data_dir=state["data_dir"], suggested=SUGGESTED_MOUNT)
    )
    raise EphemeralStorage(message)
