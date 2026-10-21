"""Where the external tools are: ffmpeg and ffprobe (decode, encode, probe).

In order: `$HILLAUDIO_FFMPEG_DIR` (a folder holding both), the copies bundled
next to the engine in the packaged app (D-056), then PATH. The packaged app
always uses its own copies, so it doesn't depend on what the user installed.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import cache
from pathlib import Path


def bundled_dir() -> Path | None:
    """The `bin/` folder inside a frozen (PyInstaller) engine, if any."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    d = Path(base) / "bin"
    return d if d.is_dir() else None


@cache
def _find(name: str) -> str:
    override = os.environ.get("HILLAUDIO_FFMPEG_DIR")
    for d in (Path(override).expanduser() if override else None, bundled_dir()):
        if d is not None and (d / name).is_file():
            return str(d / name)
    return shutil.which(name) or name  # a missing tool fails with a clear error on use


def ffmpeg() -> str:
    return _find("ffmpeg")


def ffprobe() -> str:
    return _find("ffprobe")
