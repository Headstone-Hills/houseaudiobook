"""The EDL model and the renderer (M5): derived clips, positions, sample-exact reads."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from conftest import FIXTURES

from hillaudio.edl.model import (
    Region,
    Segment,
    SourceInfo,
    clips,
    default_segments,
    duration,
    kept_seconds,
    merged,
    normalize,
    place,
    to_source,
    to_timeline,
)
from hillaudio.edl.render import XFADE_S, Input, read, render
from hillaudio.ingest.decode import iter_blocks

A = SourceInfo(1, 16000, 160_000)  # 10 s
B = SourceInfo(2, 16000, 80_000)  # 5 s


def region(i: int, src: int, a: int, b: int, status: str = "removed") -> Region:
    return Region(i, src, a, b, status)  # type: ignore[arg-type]


def test_clips_ripple_around_removed_regions() -> None:
    rs = [region(1, 1, 16000, 32000), region(2, 1, 30000, 40000), region(3, 2, 0, 8000)]
    rs.append(region(4, 1, 100_000, 120_000, "restored"))  # inactive
    assert merged(rs, 1) == [(16000, 40000)]
    cl = clips([A, B], rs, 16000)
    assert [(c.source_id, c.source_in, c.source_out, c.timeline_start) for c in cl] == [
        (1, 0, 16000, 0),
        (1, 40000, 160_000, 16000),
        (2, 8000, 80_000, 136_000),
    ]
    assert duration(cl) == 160_000 + 80_000 - 24000 - 8000
    src = {1: A, 2: B}
    assert to_timeline(cl, src, 16000, 1, 20000) is None  # cut
    assert to_timeline(cl, src, 16000, 1, 40000) == 16000
    assert to_source(cl, src, 16000, 16000) == (1, 40000)
    assert kept_seconds(rs, 1, 16000, 0.5, 3.0) == pytest.approx(1.0)


def test_mixed_rates_have_no_drift() -> None:
    a = SourceInfo(1, 44100, 44100 * 3600)  # an hour at 44.1 kHz
    b = SourceInfo(2, 48000, 48000)
    rs = [region(i, 1, i * 44100 * 10, i * 44100 * 10 + 4410) for i in range(1, 300)]
    cl = clips([a, b], rs, 48000)
    kept = 44100 * 3600 - 299 * 4410
    assert cl[-1].timeline_start == round(kept * 48000 / 44100)  # exact, not summed rounding
    assert duration(cl) == cl[-1].timeline_start + 48000


def test_empty_regions_are_inactive() -> None:
    assert clips([A], [region(1, 1, 500, 500)], 16000)[0].source_out == 160_000


# -- render ------------------------------------------------------------------------------


def fixture(name: str) -> tuple[Input, np.ndarray]:
    path = FIXTURES / name
    info = sf.info(str(path))
    full = np.concatenate(list(iter_blocks(path, info.channels)))
    return Input(path, info.samplerate, info.channels, len(full)), full


@pytest.mark.parametrize("start", [0, 1, 15999, 131033, 205810, 400_000])
def test_read_is_sample_exact(start: int) -> None:
    inp, full = fixture("ch01_take1.wav")
    got = read(inp, start, 1234, inp.sample_rate, inp.channels)
    want = full[start : start + 1234]
    np.testing.assert_array_equal(got[: len(want)], want)


def test_read_mp3_matches_a_full_decode(tmp_path: Path) -> None:
    mp3 = tmp_path / "t.mp3"  # a test-only lossy copy; sources are never transcoded
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(FIXTURES / "ch01_take2.wav"), "-b:a", "64k", str(mp3)],
        check=True,
    )
    full = np.concatenate(list(iter_blocks(mp3, 1)))
    inp = Input(mp3, 16000, 1, len(full))
    for start in (0, 17000, 40001):
        got = read(inp, start, 800, 16000, 1)
        np.testing.assert_allclose(got, full[start : start + 800], atol=1e-6)


def test_render_without_edits_is_the_sources_back_to_back() -> None:
    names = ["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"]
    loaded = [fixture(n) for n in names]
    inputs = {i: inp for i, (inp, _) in enumerate(loaded)}
    srcs = [SourceInfo(i, inp.sample_rate, inp.num_frames) for i, inp in inputs.items()]
    cl = clips(srcs, [], 16000)
    out = render(cl, inputs, 16000, 1, 0, duration(cl))
    np.testing.assert_array_equal(out, np.concatenate([f for _, f in loaded]))


def test_render_with_a_cut_blends_only_at_the_join() -> None:
    inp, full = fixture("ch01_take1.wav")
    src = SourceInfo(0, 16000, inp.num_frames)
    a, b = 131033, 137898  # the stutter
    cl = clips([src], [region(1, 0, a, b)], 16000)
    out = render(cl, {0: inp}, 16000, 1, 0, duration(cl))
    want = np.concatenate([full[:a], full[b:]])
    assert len(out) == len(want)
    h = round(XFADE_S * 16000)
    np.testing.assert_array_equal(out[: a - h], want[: a - h])
    np.testing.assert_array_equal(out[a + h :], want[a + h :])
    # Across the join: a blend of the audio on either side, ending and starting exactly.
    assert out[a - h] == pytest.approx(full[a - h, 0])
    # A partial render equals the same stretch of the full one.
    part = render(cl, {0: inp}, 16000, 1, a - 1000, a + 1000)
    np.testing.assert_array_equal(part, out[a - 1000 : a + 1000])


# -- segments (M6): moving a pickup into place ------------------------------------------------


C = SourceInfo(3, 16000, 48_000)  # a 3 s pickup file


def test_place_a_pickup_before_the_line_it_replaces_and_back() -> None:
    base = default_segments([A, B, C])
    moved = place(base, 3, 8000, 40_000, before=(1, 64_000))
    assert moved == [
        Segment(1, 0, 64_000),
        Segment(3, 8000, 40_000),
        Segment(1, 64_000, 160_000),
        Segment(2, 0, 80_000),
        Segment(3, 0, 8000),
        Segment(3, 40_000, 48_000),
    ]
    assert place(moved, 3, 8000, 40_000) == base  # home again: merged back exactly
    cl = clips([A, B, C], [region(1, 1, 64_000, 70_000)], 16000, moved)
    assert [(c.source_id, c.source_in, c.source_out) for c in cl[:3]] == [
        (1, 0, 64_000),
        (3, 8000, 40_000),
        (1, 70_000, 160_000),  # the replaced line, cut by its region
    ]
    assert duration(cl) == 160_000 + 80_000 + 48_000 - 6000


def test_place_within_one_recording_and_edges() -> None:
    base = default_segments([A])
    later = place(base, 1, 100_000, 120_000, before=(1, 16_000))
    assert later == [
        Segment(1, 0, 16_000),
        Segment(1, 100_000, 120_000),
        Segment(1, 16_000, 100_000),
        Segment(1, 120_000, 160_000),
    ]
    assert place(later, 1, 100_000, 120_000) == base
    # "Before" the very start, or a sample that isn't on the timeline: still valid.
    assert place(base, 1, 0, 1000, before=(1, 0)) == base
    assert normalize([Segment(1, 0, 5), Segment(1, 5, 5), Segment(1, 5, 9)]) == [Segment(1, 0, 9)]
