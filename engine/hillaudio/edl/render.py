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

from hillaudio.edl.model import NARRATION, Clip, Track, audible

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
    # Mono into a stereo mix: read mono and copy it to both sides (ffmpeg's own
    # upmix lowers each side by 3 dB).
    ac = 1 if inp.channels == 1 else channels
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(ac), "-ar", str(rate)]
    # Read a little past what's needed, then cut exactly.
    cmd += ["-t", f"{(skip + n) / rate + 0.05:.6f}", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()[:200]
        raise RuntimeError(f"ffmpeg failed to decode {inp.path.name}: {err}")
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    data = data[: len(data) - len(data) % ac].reshape(-1, ac)[skip : skip + n]
    out[: len(data)] = data  # (n, 1) broadcasts to every channel
    return out


def _joins(prev: Clip, nxt: Clip, inp: Input, rate: int) -> int:
    """The crossfade half-length at a narration join (0: none). Only a cut inside
    one recording blends; a new recording starts as recorded, and a faded edge
    has its own shape."""
    if prev.source_id != nxt.source_id or prev.track_id != nxt.track_id:
        return 0
    if prev.source_out == nxt.source_in:
        return 0  # a split: the audio simply continues, so there's nothing to blend
    if inp.sample_rate != rate or prev.fade_out or nxt.fade_in or prev.muted or nxt.muted:
        return 0  # resampled sources join without a blend (D-041)
    # Room on both sides of both edges, inside the recording and the clips.
    return max(
        0,
        min(
            round(XFADE_S * rate),
            prev.source_out - prev.source_in,
            nxt.source_out - nxt.source_in,
            inp.num_frames - prev.source_out,
            nxt.source_in,
        ),
    )


def _envelope(
    a: int, b: int, shapes: Sequence[tuple[npt.NDArray[np.float32], int]]
) -> npt.NDArray[np.float32] | None:
    """The product of gain shapes over timeline [a, b); each starts at its own
    timeline sample. None when there is nothing to shape."""
    if not shapes:
        return None
    env = np.ones(b - a, dtype=np.float32)
    for seg, start in shapes:
        x, y = max(a, start), min(b, start + len(seg))
        if x < y:
            env[x - a : y - a] *= seg[x - start : y - start]
    return env


def _gain(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def render(
    clips: Sequence[Clip],
    inputs: dict[int, Input],
    rate: int,
    channels: int,
    t0: int,
    t1: int,
    reader: Callable[[Input, int, int, int, int], Audio] = read,
    tracks: dict[int, Track] | None = None,
) -> Audio:
    """Timeline samples [t0, t1) at `rate`, `channels` wide: every audible clip,
    with clip gain and fades, track gain and pan, summed (D-047).

    Each clip is overlap-added: a narration clip that joins the next part of the
    same recording reaches h samples past its edge (its handle) with a falling
    ramp, and the next one starts h early with a rising ramp, which together are
    the 10 ms crossfade. Unity gain and centre pan touch no samples, so an
    unedited timeline renders the sources exactly.
    """
    tracks = tracks or {}
    out = np.zeros((max(0, t1 - t0), channels), dtype=np.float32)
    playing = (
        [c for c in clips if audible(c, tracks)] if tracks else [c for c in clips if not c.muted]
    )
    before = {id(c): 0 for c in playing}
    after = {id(c): 0 for c in playing}
    narration = [c for c in playing if c.track_id == NARRATION]
    for prev, nxt in pairwise(narration):
        if prev.timeline_end != nxt.timeline_start:
            continue
        h = _joins(prev, nxt, inputs[prev.source_id], rate)
        after[id(prev)] = before[id(nxt)] = h

    for c in playing:
        hb, ha = before[id(c)], after[id(c)]
        lo, hi = c.timeline_start - hb, c.timeline_end + ha
        a, b = max(t0, lo), min(t1, hi)
        if a >= b:
            continue
        inp = inputs[c.source_id]
        if inp.sample_rate == rate:
            src_start = c.source_in - hb + (a - lo)
        else:
            src_start = c.source_in + int(Fraction(a - c.timeline_start, rate) * inp.sample_rate)
        data = reader(inp, src_start, b - a, rate, channels)

        shapes: list[tuple[npt.NDArray[np.float32], int]] = []
        if hb:
            shapes.append((np.linspace(0.0, 1.0, 2 * hb, dtype=np.float32), c.timeline_start - hb))
        if ha:
            shapes.append(
                (1 - np.linspace(0.0, 1.0, 2 * ha, dtype=np.float32), c.timeline_end - ha)
            )
        if c.fade_in:
            shapes.append((np.linspace(0.0, 1.0, c.fade_in, dtype=np.float32), c.timeline_start))
        if c.fade_out:
            shapes.append(
                (np.linspace(1.0, 0.0, c.fade_out, dtype=np.float32), c.timeline_end - c.fade_out)
            )
        env = _envelope(a, b, shapes)
        if env is not None:
            data = data * env[:, None]
        tr = tracks.get(c.track_id)
        g = c.gain_db + (tr.gain_db if tr else 0.0)
        if g:
            data = data * np.float32(_gain(g))
        if tr is not None and tr.pan and channels == 2:
            # Balance law: unity at centre; one side fades out as the other stays.
            data = data * np.array(
                [min(1.0, 1.0 - tr.pan), min(1.0, 1.0 + tr.pan)], dtype=np.float32
            )
        out[a - t0 : b - t0] += data
    return out


def wav_bytes(audio: Audio, rate: int) -> bytes:
    """16-bit WAV for listening in the browser (a preview, not an export)."""
    import io

    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1.0, 1.0), rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()
