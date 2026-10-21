"""Finalize DSP (M8) on synthetic signals: exact where the maths is exact."""

from __future__ import annotations

import numpy as np
import pytest

from hillaudio.render.dsp import (
    Compressor,
    Gate,
    NoiseProfile,
    NoiseReducer,
    block_quantum,
    dither_16,
    fft_size,
    limit,
    measure,
    quietest,
    undb,
)

SR = 16000
RNG = np.random.default_rng(11)


def tone(db_level: float, seconds: float, f: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    a = float(undb(db_level)) * np.sqrt(2)  # sine RMS = amplitude / sqrt(2)
    out: np.ndarray = (a * np.sin(2 * np.pi * f * t)).astype(np.float32)[:, None]
    return out


def noise(db_level: float, seconds: float) -> np.ndarray:
    return (RNG.standard_normal((int(seconds * SR), 1)) * float(undb(db_level))).astype(np.float32)


def test_measure_matches_definitions() -> None:
    x = np.concatenate([tone(-20, 2.0), noise(-70, 1.0), tone(-20, 2.0)])
    lv = measure(x, SR)
    assert lv.peak_db == pytest.approx(-20 + 3.0103, abs=0.01)  # a sine's peak is +3 dB over RMS
    p = np.mean(x.astype(np.float64) ** 2)
    assert lv.rms_db == pytest.approx(10 * np.log10(p), abs=1e-6)
    assert lv.noise_floor_db == pytest.approx(-70, abs=0.5)
    assert not lv.digital_silence
    assert measure(np.concatenate([x, np.zeros((SR, 1), np.float32)]), SR).digital_silence


def test_compressor_static_curve_and_gain() -> None:
    c = Compressor(threshold_db=-24, ratio=2, knee_db=0)
    assert c.curve(np.array([-30.0, -24.0, -14.0])).tolist() == [0.0, 0.0, -5.0]
    soft = Compressor(threshold_db=-24, ratio=4, knee_db=6)
    g = soft.curve(np.array([-27.0, -24.0, -21.0, -10.0]))
    assert g[0] == 0 and g[1] == pytest.approx(-0.75 * 9 / 12) and g[3] == pytest.approx(-0.75 * 14)
    # Steady loud tone: settles at the curve's level.
    out = Compressor(threshold_db=-24, ratio=2, knee_db=0, attack_s=0.005, release_s=0.05).process(
        tone(-14, 3.0), SR
    )
    assert measure(out[-SR:], SR).rms_db == pytest.approx(-19.0, abs=0.1)


def test_gate_attenuates_noise_between_phrases_but_not_speech() -> None:
    x = np.concatenate([tone(-20, 1.0), noise(-65, 1.0), tone(-20, 1.0)])
    y = Gate(threshold_db=-50, ratio=2, range_db=12).process(x, SR)
    assert measure(y[: SR - 800], SR).rms_db == pytest.approx(-20, abs=0.05)  # speech untouched
    gap = measure(y[SR + 4000 : 2 * SR - 1000], SR).rms_db
    assert gap == pytest.approx(-77, abs=0.5)  # 12 dB down (the range limit)


def test_limiter_never_exceeds_the_ceiling_and_leaves_quiet_audio() -> None:
    x = np.concatenate([tone(-30, 1.0), tone(-1, 0.5), tone(-30, 1.0)])
    x[int(1.2 * SR)] = 0.99  # an isolated spike
    y, _ = limit(x, SR, -3.5)
    assert float(np.max(np.abs(y))) <= float(undb(-3.5)) + 1e-7
    np.testing.assert_allclose(y[: SR // 2], x[: SR // 2], atol=1e-7)  # well before: untouched
    # In blocks with look-ahead, the same as in one go.
    cut = block_quantum(SR) * 100  # blocks start on the frame grids
    a, b = x[:cut], x[cut:]
    ya, st = limit(a, SR, -3.5, ahead=b[: SR // 100])
    yb, _ = limit(b, SR, -3.5, state=st)
    np.testing.assert_allclose(np.concatenate([ya, yb]), y, atol=1e-6)


def test_noise_reduction_lowers_the_floor_and_keeps_speech() -> None:
    hiss = noise(-50, 6.0)
    x = hiss.copy()
    x[SR : 3 * SR] += tone(-18, 2.0, 300)
    x[4 * SR : 5 * SR] += tone(-18, 1.0, 1200)
    prof = NoiseProfile.from_audio(hiss[5 * SR :], SR)
    assert prof.n_fft == fft_size(SR) == 512
    y = NoiseReducer(prof, strength=0.7, reduction_db=15).process(x)
    before, after = measure(x, SR), measure(y, SR)
    assert after.noise_floor_db < before.noise_floor_db - 8
    speech = slice(int(1.2 * SR), int(2.8 * SR))
    assert measure(y[speech], SR).rms_db == pytest.approx(measure(x[speech], SR).rms_db, abs=1.0)
    # Blocks with context equal one pass, away from the edges.
    nr = NoiseReducer(prof, strength=0.7, reduction_db=15)
    ctx = nr.context
    a, b = 2 * SR, 4 * SR
    part = nr.process(x[a - ctx : b + ctx])[ctx:-ctx]
    np.testing.assert_allclose(part, y[a:b], atol=1e-5)


def test_quietest_and_dither() -> None:
    x = np.concatenate([tone(-20, 2.0), noise(-60, 1.2), tone(-20, 2.0)])
    a, b = quietest(x, SR, 1.0)
    assert a >= 2 * SR and b <= int(3.2 * SR) + 1
    q = dither_16(tone(-20, 1.0), np.random.default_rng(0))
    assert q.dtype == np.int16
    err = q.astype(np.float64) / 32767 - tone(-20, 1.0)
    assert 20 * np.log10(np.sqrt(np.mean(err**2))) < -90
