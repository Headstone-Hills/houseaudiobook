"""The mixer (M7): clip gain and fades, track gain/pan/mute/solo, overlaps, joins.

An in-memory reader stands in for ffmpeg so every sample can be checked exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from hillaudio.edl.model import (
    Clip,
    FreeClip,
    Region,
    Segment,
    SourceInfo,
    Track,
    layout,
    set_attrs,
)
from hillaudio.edl.render import XFADE_S, Input, render

RATE = 1000
RNG = np.random.default_rng(7)
AUDIO = {
    1: RNG.uniform(-0.5, 0.5, 5000).astype(np.float32),
    2: RNG.uniform(-0.5, 0.5, 3000).astype(np.float32),
}
SOURCES = [SourceInfo(1, RATE, 5000), SourceInfo(2, RATE, 3000)]
INPUTS = {i: Input(Path(f"/x/{i}.wav"), RATE, 1, len(a)) for i, a in AUDIO.items()}


def reader(inp: Input, start: int, n: int, rate: int, channels: int) -> np.ndarray:
    src = AUDIO[int(inp.path.stem)]
    out = np.zeros((n, channels), dtype=np.float32)
    part = src[max(0, start) : max(0, start + n)]
    out[max(0, -start) : max(0, -start) + len(part)] = part[:, None]
    return out


def mix(
    segs: list[Segment] | None = None,
    frees: Sequence[FreeClip] = (),
    regions: Sequence[Region] = (),
    tracks: dict[int, Track] | None = None,
    channels: int = 1,
    t0: int = 0,
    t1: int | None = None,
) -> tuple[np.ndarray, list[Clip]]:
    cl = layout(SOURCES, list(regions), RATE, segs, list(frees))
    end = max(c.timeline_end for c in cl) if t1 is None else t1
    return render(cl, INPUTS, RATE, channels, t0, end, reader, tracks), cl


def test_unedited_is_exact() -> None:
    out, _ = mix()
    np.testing.assert_array_equal(out[:, 0], np.concatenate([AUDIO[1], AUDIO[2]]))


def test_clip_gain_and_linear_fades() -> None:
    segs = set_attrs(
        [Segment(1, 0, 5000), Segment(2, 0, 3000)],
        1,
        0,
        5000,
        gain_db=-6.0,
        fade_in=100,
        fade_out=200,
    )
    out, _ = mix(segs)
    g = 10 ** (-6 / 20)
    env = np.ones(5000, dtype=np.float32)
    env[:100] = np.linspace(0, 1, 100)
    env[-200:] = np.linspace(1, 0, 200)
    np.testing.assert_allclose(out[:5000, 0], AUDIO[1] * env * g, rtol=1e-6, atol=1e-7)
    np.testing.assert_array_equal(out[5000:, 0], AUDIO[2])  # the next clip is untouched


def test_join_crossfade_is_the_m5_blend() -> None:
    cut = Region(1, 1, 2000, 2500, "removed")
    out, _ = mix(regions=[cut])
    h = round(XFADE_S * RATE)
    p = 2000
    ramp = np.linspace(0, 1, 2 * h, dtype=np.float32)
    blend = AUDIO[1][p - h : p + h] * (1 - ramp) + AUDIO[1][2500 - h : 2500 + h] * ramp
    np.testing.assert_allclose(out[p - h : p + h, 0], blend, rtol=1e-6)
    np.testing.assert_array_equal(out[: p - h, 0], AUDIO[1][: p - h])
    np.testing.assert_array_equal(out[p + h : 4500, 0], AUDIO[1][2500 + h :])
    # A partial render equals the same stretch of the full one.
    part, _ = mix(regions=[cut], t0=p - 7, t1=p + 9)
    np.testing.assert_array_equal(part, out[p - 7 : p + 9])


def test_free_clips_sum_with_the_narration() -> None:
    room = FreeClip(9, 2, 2, 0, 1000, 500, gain_db=-20.0)
    tracks = {1: Track(1, "Narration", "narration"), 2: Track(2, "Room tone")}
    out, _ = mix(frees=[room], tracks=tracks)
    want = np.concatenate([AUDIO[1], AUDIO[2]])
    want[500:1500] += AUDIO[2][:1000] * np.float32(10 ** (-20 / 20))
    np.testing.assert_allclose(out[:, 0], want, rtol=1e-6, atol=1e-7)


def test_track_gain_pan_mute_solo() -> None:
    room = FreeClip(9, 2, 2, 0, 1000, 0)
    base = {1: Track(1, "Narration", "narration"), 2: Track(2, "Room tone")}
    # Pan the room tone hard left in a stereo mix: right channel is narration only.
    tr = base | {2: Track(2, "Room tone", pan=-1.0, gain_db=-6.0)}
    out, _ = mix(frees=[room], tracks=tr, channels=2)
    narr = np.concatenate([AUDIO[1], AUDIO[2]])
    np.testing.assert_array_equal(out[:, 1], narr)
    left = narr.copy()
    left[:1000] += AUDIO[2][:1000] * np.float32(10 ** (-6 / 20))
    np.testing.assert_allclose(out[:, 0], left, rtol=1e-6, atol=1e-7)
    # Mute the narration: only the room tone remains.
    out, _ = mix(frees=[room], tracks=base | {1: Track(1, "Narration", "narration", mute=True)})
    np.testing.assert_array_equal(out[:1000, 0], AUDIO[2][:1000])
    assert not out[1000:].any()
    # Solo the narration: the room tone is silent.
    out, _ = mix(frees=[room], tracks=base | {1: Track(1, "Narration", "narration", solo=True)})
    np.testing.assert_array_equal(out[:, 0], narr)


def test_muted_clip_and_faded_join_has_no_blend() -> None:
    segs = set_attrs([Segment(1, 0, 5000), Segment(2, 0, 3000)], 2, 0, 3000, muted=True)
    out, _ = mix(segs)
    np.testing.assert_array_equal(out[:5000, 0], AUDIO[1])
    assert not out[5000:].any()
    # A fade-out at a cut replaces the crossfade there.
    segs = set_attrs([Segment(1, 0, 5000)], 1, 0, 2000, fade_out=50)
    out, _ = mix(segs, regions=[Region(1, 1, 2000, 2500, "removed")], t1=4500)
    np.testing.assert_array_equal(out[2000:2010, 0], AUDIO[1][2500:2510])  # no ramp in


@pytest.mark.parametrize("t0,t1", [(0, 7700), (4690, 4710), (123, 4567)])
def test_partial_renders_match(t0: int, t1: int) -> None:
    segs = set_attrs(
        [Segment(1, 0, 5000), Segment(2, 0, 3000)], 1, 1000, 3000, gain_db=4.0, fade_in=30
    )
    frees = [FreeClip(1, 2, 2, 100, 900, 4800, fade_out=40)]
    tracks = {1: Track(1, "N", "narration"), 2: Track(2, "R", pan=0.5)}
    full, _ = mix(segs, frees, [Region(1, 1, 2000, 2300, "removed")], tracks, 2)
    part, _ = mix(segs, frees, [Region(1, 1, 2000, 2300, "removed")], tracks, 2, t0, t1)
    np.testing.assert_array_equal(part, full[t0:t1])
