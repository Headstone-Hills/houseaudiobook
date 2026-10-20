"""Peak cache under cache/peaks/, keyed by file hash + peak settings (CLAUDE.md)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from hillaudio.analysis.peaks import SETTINGS_KEY, PeakLevel, Peaks


def cache_path(cache_dir: Path, sha256: str) -> Path:
    return cache_dir / "peaks" / f"{sha256}.{SETTINGS_KEY}.npz"


def save(cache_dir: Path, sha256: str, peaks: Peaks) -> Path:
    path = cache_path(cache_dir, sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.generic]]] = {
        "num_frames": np.array(peaks.num_frames, dtype=np.int64)
    }
    for i, lvl in enumerate(peaks.levels):
        arrays[f"fpb{i}"] = np.array(lvl.frames_per_bucket, dtype=np.int64)
        arrays[f"min{i}"] = lvl.min
        arrays[f"max{i}"] = lvl.max
    # Write then rename, so a crash never leaves a truncated cache file behind.
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        np.savez(f, **arrays)  # type: ignore[arg-type]
    os.replace(tmp, path)
    return path


def load(cache_dir: Path, sha256: str) -> Peaks | None:
    path = cache_path(cache_dir, sha256)
    if not path.is_file():
        return None
    with np.load(path) as z:
        levels = []
        i = 0
        while f"min{i}" in z:
            levels.append(PeakLevel(int(z[f"fpb{i}"]), z[f"min{i}"], z[f"max{i}"]))
            i += 1
        return Peaks(int(z["num_frames"]), levels)
