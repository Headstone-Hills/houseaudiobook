"""Multi-resolution waveform peaks (pure; no I/O).

Level 0 holds the min and max of every BASE frames (all channels combined into
one envelope). Each further level merges FACTOR buckets of the level below, so
the UI can draw any zoom from a level with roughly one bucket per pixel.

Peaks are stored as int16 (value * 32767). Display needs nowhere near float
precision, and it halves the cache: a 10-hour 44.1 kHz file is about 6 MB at
level 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

BASE = 512
FACTOR = 4
LEVELS = 4  # 512, 2048, 8192, 32768 frames per bucket
SETTINGS_KEY = f"peaks-v1-b{BASE}-f{FACTOR}-l{LEVELS}"

I16 = npt.NDArray[np.int16]


@dataclass(frozen=True)
class PeakLevel:
    frames_per_bucket: int
    min: I16
    max: I16


@dataclass(frozen=True)
class Peaks:
    num_frames: int
    levels: list[PeakLevel]


def _quantize(x: npt.NDArray[np.float32]) -> I16:
    return np.round(np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)


class PeakBuilder:
    """Accumulate peaks from blocks of any size; results don't depend on block size."""

    def __init__(self, base: int = BASE, factor: int = FACTOR, levels: int = LEVELS) -> None:
        self.base, self.factor, self.n_levels = base, factor, levels
        self._carry = np.zeros(0, dtype=np.float32)  # mono envelope frames not yet bucketed
        self._carry_min = np.zeros(0, dtype=np.float32)
        self._mins: list[npt.NDArray[np.float32]] = []
        self._maxs: list[npt.NDArray[np.float32]] = []
        self.num_frames = 0

    def add(self, block: npt.NDArray[np.float32]) -> None:
        """Add (frames, channels) or (frames,) samples."""
        if block.ndim == 1:
            block = block[:, None]
        if block.shape[0] == 0:
            return
        self.num_frames += block.shape[0]
        hi = np.concatenate([self._carry, block.max(axis=1)])
        lo = np.concatenate([self._carry_min, block.min(axis=1)])
        full = (len(hi) // self.base) * self.base
        if full:
            self._maxs.append(hi[:full].reshape(-1, self.base).max(axis=1))
            self._mins.append(lo[:full].reshape(-1, self.base).min(axis=1))
        self._carry, self._carry_min = hi[full:], lo[full:]

    def finish(self) -> Peaks:
        mins, maxs = list(self._mins), list(self._maxs)
        if len(self._carry):
            # A partial final bucket still shows the audio's tail.
            maxs.append(np.array([self._carry.max()], dtype=np.float32))
            mins.append(np.array([self._carry_min.min()], dtype=np.float32))
        lo = np.concatenate(mins) if mins else np.zeros(0, dtype=np.float32)
        hi = np.concatenate(maxs) if maxs else np.zeros(0, dtype=np.float32)

        levels = [PeakLevel(self.base, _quantize(lo), _quantize(hi))]
        for i in range(1, self.n_levels):
            lo, hi = _merge(lo, np.minimum, self.factor), _merge(hi, np.maximum, self.factor)
            levels.append(PeakLevel(self.base * self.factor**i, _quantize(lo), _quantize(hi)))
        return Peaks(self.num_frames, levels)


def _merge(x: npt.NDArray[np.float32], op: np.ufunc, factor: int) -> npt.NDArray[np.float32]:
    if len(x) == 0:
        return x
    pad = (-len(x)) % factor
    if pad:
        # Pad with the last value so padding never changes a bucket's min/max.
        x = np.concatenate([x, np.full(pad, x[-1], dtype=x.dtype)])
    out: npt.NDArray[np.float32] = op.reduce(x.reshape(-1, factor), axis=1)
    return out


def compute_peaks(samples: npt.NDArray[np.float32]) -> Peaks:
    b = PeakBuilder()
    b.add(samples)
    return b.finish()
