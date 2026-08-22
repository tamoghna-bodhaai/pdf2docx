"""Whether the data directory will still be there after a redeploy.

This module exists because of one failure. The container filesystem is wiped on
every deploy, `pdf2docx.db` lives under `PDF2DOCX_DATA_DIR`, and if no volume is
mounted there the database goes with it — silently. What that looks like from
the sign-in page is an account that worked yesterday being told its password is
wrong, which reads as authentication being unreliable rather than as storage
having been thrown away. It is the most confusing way this application can fail,
so it is worth a check at boot and a line in the health endpoint.

The test is whether the directory sits on a different device from `/`. A mounted
volume does; a plain directory baked into the image does not.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import settings


def report(directory: Path | None = None) -> dict:
    """What can be said about where this instance keeps its data.

    `ephemeral` is the one that matters, and it is deliberately three-valued:
    True means the data is known to be at risk, False means it is on its own
    device, and None means the question was not asked. It is not asked for a
    directory under the home directory, which is the ordinary local-development
    case — nobody running this on their laptop is redeploying a container, and a
    warning there would be noise that teaches people to ignore the real one.
    """
    directory = directory if directory is not None else settings.data_dir
    home = Path.home()

    checked = True
    try:
        checked = not directory.resolve().is_relative_to(home.resolve())
    except (OSError, ValueError):
        pass

    if not checked:
        return {"data_dir": str(directory), "ephemeral": None, "mount": None}

    try:
        on_root_device = directory.stat().st_dev == Path("/").stat().st_dev
    except OSError:
        # The directory does not exist yet, or cannot be read. Nothing useful to
        # say, and this must never be the thing that stops the app booting.
        return {"data_dir": str(directory), "ephemeral": None, "mount": None}

    return {
        "data_dir": str(directory),
        "ephemeral": on_root_device,
        "mount": os.path.ismount(directory),
    }


WARNING = (
    "%s is on the same device as / — nothing written there survives a redeploy, "
    "including the accounts and history in pdf2docx.db. Attach a volume mounted "
    "at that path, or point PDF2DOCX_DATA_DIR at one."
)


def warn_if_ephemeral() -> str | None:
    """The warning this instance deserves at boot, or None. Never raises."""
    try:
        state = report()
    except Exception:
        return None
    return WARNING % state["data_dir"] if state.get("ephemeral") else None
