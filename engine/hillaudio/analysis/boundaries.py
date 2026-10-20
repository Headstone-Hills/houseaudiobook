"""Snap ASR word/sentence boundaries to the audio (pure). D-008.

Whisper's timestamps are off by 100-500 ms (measured in docs/bench): every file's
first word starts at 0.00 s, and words after a pause start early. Cuts belong in
pauses, and pauses are easy to find acoustically, so each boundary is moved to
the nearest speech edge:

- a start inside silence moves *forward* to where speech begins (up to 0.8 s);
- a start inside speech moves *back* to the nearest pause edge (up to 0.3 s), or
  stays put if there is no pause that close;
- ends are the mirror image.

"Speech" uses two thresholds (hysteresis): a stretch counts as speech if some
frame in it is clearly loud (noise floor + `THRESHOLD_ABOVE_FLOOR_DB`), and its
edges then extend through anything a little above the floor (+ `EDGE_ABOVE_FLOOR_DB`),
so quiet word endings like the "ch" in "reach" aren't clipped. Silences shorter
than `MIN_GAP_S` are filled in, so stop consonants and tiny inter-word dips don't
count as pauses.

Callers snapping a sequence of words pass each word's neighbours as bounds: a
boundary never moves into audio the transcript gives to another word.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

HOP_S = 0.01  # 10 ms frames
MIN_GAP_S = 0.06
THRESHOLD_ABOVE_FLOOR_DB = 15.0
EDGE_ABOVE_FLOOR_DB = 6.0
MIN_THRESHOLD_DB = -55.0  # never call quieter than this speech, even in clean audio
START_BACK_S, START_FORWARD_S = 0.3, 0.8
END_BACK_S, END_FORWARD_S = 0.8, 0.3

F32 = npt.NDArray[np.float32]
Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class Envelope:
    db: F32  # RMS level per frame, dBFS
    fps: float  # frames per second

    @property
    def duration(self) -> float:
        return len(self.db) / self.fps


class EnvelopeBuilder:
    """Frame-RMS envelope from blocks of any size (same result as one big block)."""

    def __init__(self, sample_rate: int, hop_s: float = HOP_S) -> None:
        self.hop = max(1, round(sample_rate * hop_s))
        self.fps = sample_rate / self.hop
        self._carry = np.zeros(0, dtype=np.float64)
        self._frames: list[npt.NDArray[np.float64]] = []

    def add(self, block: npt.NDArray[np.float32]) -> None:
        mono = block.mean(axis=1) if block.ndim == 2 else block
        x = np.concatenate([self._carry, mono.astype(np.float64)])
        full = len(x) // self.hop * self.hop
        if full:
            self._frames.append(np.mean(np.square(x[:full]).reshape(-1, self.hop), axis=1))
        self._carry = x[full:]

    def finish(self) -> Envelope:
        parts = list(self._frames)
        if len(self._carry):
            parts.append(np.array([np.mean(np.square(self._carry))]))
        power = np.concatenate(parts) if parts else np.zeros(0)
        db = 10 * np.log10(np.maximum(power, 1e-12))
        return Envelope(db.astype(np.float32), self.fps)


def envelope(samples: npt.NDArray[np.float32], sample_rate: int) -> Envelope:
    b = EnvelopeBuilder(sample_rate)
    b.add(samples)
    return b.finish()


def speech_mask(
    env: Envelope, threshold_db: float | None = None, min_gap_s: float = MIN_GAP_S
) -> Mask:
    if len(env.db) == 0:
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(env.db, 10))
    if threshold_db is None:
        threshold_db = max(floor + THRESHOLD_ABOVE_FLOOR_DB, MIN_THRESHOLD_DB)
    loud = env.db > threshold_db
    edge = env.db > min(threshold_db, floor + EDGE_ABOVE_FLOOR_DB)
    mask = _fill_gaps(edge, max(1, round(min_gap_s * env.fps)))
    # Keep only stretches that contain at least one loud frame (hysteresis).
    labels = np.cumsum(np.diff(np.concatenate([[0], mask.astype(np.int8)])) == 1) * mask
    keep = np.unique(labels[loud & mask])
    return np.isin(labels, keep[keep > 0])


def _fill_gaps(mask: Mask, min_gap: int) -> Mask:
    """Fill silences shorter than min_gap frames between speech frames."""
    mask = mask.copy()
    idx = np.flatnonzero(mask)
    if len(idx) > 1:
        for g in np.flatnonzero(np.diff(idx) > 1):
            a, b = idx[g] + 1, idx[g + 1]
            if b - a < min_gap:
                mask[a:b] = True
    return mask


def _frame(t: float, fps: float, n: int) -> int:
    return min(max(round(t * fps), 0), n - 1)


def _stretch(mask: Mask, i: int) -> tuple[int, int]:
    """[a, b) of the run of equal mask values containing frame i."""
    v = mask[i]
    change = np.flatnonzero(mask != v)
    before = change[change < i]
    after = change[change > i]
    return (int(before[-1]) + 1 if len(before) else 0, int(after[0]) if len(after) else len(mask))


def snap_start(
    mask: Mask,
    fps: float,
    t: float,
    back_s: float = START_BACK_S,
    forward_s: float = START_FORWARD_S,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    """Snap a start time. `lower`/`upper`: never move before/after these (the
    previous word's start and this word's end, when snapping a sequence)."""
    s = _snap_start(mask, fps, t, back_s, forward_s, lower)
    if (lower is not None and s < lower) or (upper is not None and s >= upper):
        return t
    return s


def _snap_start(
    mask: Mask, fps: float, t: float, back_s: float, forward_s: float, lower: float | None
) -> float:
    n = len(mask)
    if n == 0:
        return t
    i = _frame(t, fps, n)
    a, b = _stretch(mask, i)
    if not mask[i]:
        # In silence: the word starts where this silence ends.
        return b / fps if b < n and (b - i) <= forward_s * fps else t
    # In speech: the nearer of this stretch's onset (back) and the next onset after
    # the following pause (forward). Forward matters when Whisper placed a word
    # inside the previous one, e.g. a stutter: "The. | The vine".
    candidates = []
    if (i - a) <= back_s * fps and (lower is None or a / fps > lower):
        candidates.append(a)
    if b < n:
        _, pause_end = _stretch(mask, b)
        if pause_end < n and (pause_end - i) <= forward_s * fps:
            candidates.append(pause_end)
    if not candidates:
        return t
    return min(candidates, key=lambda c: abs(c - i)) / fps


def snap_end(
    mask: Mask,
    fps: float,
    t: float,
    back_s: float = END_BACK_S,
    forward_s: float = END_FORWARD_S,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    """Snap an end time. `lower`/`upper`: never move before/after these (this
    word's start and the next word's end, when snapping a sequence)."""
    e = _snap_end(mask, fps, t, back_s, forward_s, upper)
    if (upper is not None and e > upper) or (lower is not None and e <= lower):
        return t
    return e


def _snap_end(
    mask: Mask, fps: float, t: float, back_s: float, forward_s: float, upper: float | None
) -> float:
    n = len(mask)
    if n == 0:
        return t
    i = max(_frame(t, fps, n + 1) - 1, 0)  # the last frame before t
    a, b = _stretch(mask, i)
    if not mask[i]:
        # In silence: the word ended where this silence began.
        return a / fps if a > 0 and (i - a) <= back_s * fps else t
    # In speech: the nearer of this stretch's end (forward) and the end of the
    # speech before the previous pause (back).
    candidates = []
    if (b - i) <= forward_s * fps and (upper is None or b / fps < upper):
        candidates.append(b)
    if a > 0:
        pause_start, _ = _stretch(mask, a - 1)
        if pause_start > 0 and (i - pause_start) <= back_s * fps:
            candidates.append(pause_start)
    if not candidates:
        return t
    return min(candidates, key=lambda c: abs(c - i)) / fps


def snap_span(mask: Mask, fps: float, start: float, end: float) -> tuple[float, float]:
    """Snap both edges; never returns an empty or inverted span."""
    s, e = snap_start(mask, fps, start), snap_end(mask, fps, end)
    if e <= s:
        return start, end
    return s, e
