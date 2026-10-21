"""Render the timeline (or part of it) from the original sources, sample-exact.

Used for previews in M5 and by export later (SPEC §5: "Offline render happens
only on export or Render preview, never in place"). Sources are only read, by
ffmpeg, which also decoded them for analysis, so positions agree.

Joins: where a cut joins two parts of the *same* recording, the render
crossfades over a few milliseconds using the audio on either side of the join
(the "handles" every clip edge keeps), so a cut never clicks. Where one
recording ends and the next begins nothing is blended: that is how they were
recorded. With no cuts there are no joins, so an undone edit renders exactly
the original samples (the round-trip test).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import numpy as np
import numpy.typing as npt

from hillaudio.edl.model import Clip

XFADE_S = 0.005  # each side of a join: 10 ms in all
Audio = npt.NDArray[np.float32]


@dataclass(frozen=True)
class Input:
    path: Path
    sample_rate: int
    channels: int
    num_frames: int


def read(inp: Input, start: int, n: int, rate: int, channels: int) -> Audio:
    """n frames at `rate` from source sample `start` (at the source rate).

    ffmpeg seeks by time in microseconds, which can't name every sample, so seek
    to a whole second before the start (exact at any rate), then drop the exact
    number of frames up to it.
    """
    out = np.zeros((n, channels), dtype=np.float32)
    if n <= 0 or start >= inp.num_frames:
        return out
    sr = inp.sample_rate
    coarse = max(0, start // sr - 1)  # whole seconds
    skip = start - coarse * sr
    if rate != sr:
        skip = round(Fraction(skip * rate, sr))
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if coarse:
        # (Not "-ss 0": for MP3 that changes how the encoder delay is skipped.)
        cmd += ["-ss", str(coarse)]
    cmd += ["-i", str(inp.path)]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(channels), "-ar", str(rate)]
    # Read a little past what's needed, then cut exactly.
    cmd += ["-t", f"{(skip + n) / rate + 0.05:.6f}", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()[:200]
        raise RuntimeError(f"ffmpeg failed to decode {inp.path.name}: {err}")
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    data = data[: len(data) - len(data) % channels].reshape(-1, channels)[skip : skip + n]
    out[: len(data)] = data
    return out


def render(
    clips: Sequence[Clip],
    inputs: dict[int, Input],
    rate: int,
    channels: int,
    t0: int,
    t1: int,
    reader: Callable[[Input, int, int, int, int], Audio] = read,
) -> Audio:
    """Timeline samples [t0, t1) at `rate`, `channels` wide."""
    out = np.zeros((max(0, t1 - t0), channels), dtype=np.float32)
    for c in clips:
        a, b = max(t0, c.timeline_start), min(t1, c.timeline_end)
        if a >= b:
            continue
        inp = inputs[c.source_id]
        src_start = c.source_in + int(Fraction(a - c.timeline_start, rate) * inp.sample_rate)
        out[a - t0 : b - t0] = reader(inp, src_start, b - a, rate, channels)

    for prev, nxt in pairwise(clips):
        if prev.source_id != nxt.source_id:
            continue  # a new recording starts: no blend
        inp = inputs[prev.source_id]
        if inp.sample_rate != rate:
            continue  # resampled sources join without a blend (D-039)
        h = round(XFADE_S * rate)
        # Room on both sides of both edges, inside the recording and the clips.
        h = min(
            h,
            prev.source_out - prev.source_in,
            nxt.source_out - nxt.source_in,
            inp.num_frames - prev.source_out,
            nxt.source_in,
        )
        p = nxt.timeline_start
        if h <= 0 or p + h <= t0 or p - h >= t1:
            continue
        out_tail = reader(inp, prev.source_out - h, 2 * h, rate, channels)
        in_head = reader(inp, nxt.source_in - h, 2 * h, rate, channels)
        ramp = np.linspace(0.0, 1.0, 2 * h, dtype=np.float32)[:, None]
        blend = out_tail * (1 - ramp) + in_head * ramp
        a, b = max(t0, p - h), min(t1, p + h)
        out[a - t0 : b - t0] = blend[a - (p - h) : b - (p - h)]
    return out


def wav_bytes(audio: Audio, rate: int) -> bytes:
    """16-bit WAV for listening in the browser (a preview, not an export)."""
    import io

    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1.0, 1.0), rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()
