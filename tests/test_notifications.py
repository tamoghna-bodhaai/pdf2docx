"""Browser notification transitions are deduplicated across both pollers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("render_notifications.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def test_notification_state_and_browser_fallbacks() -> None:
    subprocess.run(["node", str(SCRIPT)], check=True, capture_output=True, text=True)
