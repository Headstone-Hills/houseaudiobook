from __future__ import annotations

import numpy as np
import pytest

from hillaudio.analysis.peaks import BASE, FACTOR, LEVELS, PeakBuilder, compute_peaks


def signal(n: int, channels: int = 2) -> np.ndarray:
    rng = np.random.default_rng(1)
    return (rng.uniform(-0.9, 0.9, (n, channels))).astype(np.float32)


@pytest.mark.parametrize("block", [1, 7, 511, 512, 513, 4096, 100_000])
def test_block_size_does_not_change_peaks(block: int) -> None:
    x = signal(BASE * 37 + 123)
    ref = compute_peaks(x)
    b = PeakBuilder()
    for i in range(0, len(x), block):
        b.add(x[i : i + block])
    got = b.finish()
    assert got.num_frames == ref.num_frames == len(x)
    for a, r in zip(got.levels, ref.levels, strict=True):
        assert np.array_equal(a.min, r.min) and np.array_equal(a.max, r.max)


def test_levels_and_envelope() -> None:
    x = signal(BASE * FACTOR**3 * 2 + 5)
    p = compute_peaks(x)
    assert [lvl.frames_per_bucket for lvl in p.levels] == [BASE * FACTOR**i for i in range(LEVELS)]
    lvl0 = p.levels[0]
    # Partial final bucket is kept.
    assert len(lvl0.max) == -(-len(x) // BASE)
    # Bucket 0 covers frames 0..BASE across all channels.
    assert lvl0.max[0] == round(float(x[:BASE].max()) * 32767)
    assert lvl0.min[0] == round(float(x[:BASE].min()) * 32767)
    # Coarser levels are the envelope of finer ones.
    for fine, coarse in zip(p.levels, p.levels[1:], strict=False):
        assert coarse.max.max() == fine.max.max()
        assert coarse.min.min() == fine.min.min()
        assert len(coarse.max) == -(-len(fine.max) // FACTOR)


def test_empty_and_mono() -> None:
    assert compute_peaks(np.zeros((0, 1), dtype=np.float32)).levels[0].max.size == 0
    mono = np.ones(BASE, dtype=np.float32) * 0.5
    assert compute_peaks(mono).levels[0].max.tolist() == [round(0.5 * 32767)]
