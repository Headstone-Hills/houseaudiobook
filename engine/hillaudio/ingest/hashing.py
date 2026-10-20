"""Content hashing for source files."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

CHUNK = 1 << 20  # 1 MiB: large enough to be fast, small enough for 2 GB files


def sha256_file(path: Path, on_bytes: Callable[[int], None] | None = None) -> str:
    """Hash a file by streaming it; opens read-only and never writes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
    return h.hexdigest()
