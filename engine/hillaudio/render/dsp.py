"""Finalize DSP (pure numpy): measurement, noise reduction, gate, compressor,
limiter. SPEC §6.8; D-050.

Everything works on float32 arrays shaped (frames, channels) at one sample rate.
Dynamics run on 10 ms analysis frames: the gain each frame wants is computed
vectorized, then smoothed with attack/release in a small loop over frames (100
per second of audio, cheap even for a 10 h book), then interpolated to samples.
Processors carry their state between blocks, so long files are processed in
pieces and the result is the same as processing them whole.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Audio = npt.NDArray[np.float32]
F64 = npt.NDArray[np.float64]
FRAME_S = 0.01
SILENCE_DB = -120.0


def db(x: float | F64) -> F64:
    return np.asarray(20.0 * np.log10(np.maximum(np.abs(x), 1e-12)), dtype=np.float64)


def undb(d: float | F64) -> F64:
    return np.asarray(10.0 ** (np.asarray(d, dtype=np.float64) / 20.0))


# -- measurement ------------------------------------------------------------------------


@dataclass(frozen=True)
class Levels:
    peak_db: float  # sample peak, dBFS
    rms_db: float  # whole-file RMS, dBFS (unweighted, all channels)
    noise_floor_db: float  # RMS of the quietest 0.5 s window
    digital_silence: bool  # a 0.5 s window of exact zeros (ACX rejects these)


def _power(x: Audio) -> F64:
    return np.asarray(np.mean(np.square(x, dtype=np.float64), axis=1), dtype=np.float64)


def measure(x: Audio, sr: int, window_s: float = 0.5, hop_s: float = 0.1) -> Levels:
    """Levels the way ACX describes them: peak, RMS and noise floor (the RMS of the
    quietest stretch, normally room tone between phrases)."""
    if len(x) == 0:
        return Levels(SILENCE_DB, SILENCE_DB, SILENCE_DB, False)
    p = _power(x)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(p)))
    w, h = max(1, int(window_s * sr)), max(1, int(hop_s * sr))
    c = np.concatenate([[0.0], np.cumsum(p)])
    starts = np.arange(0, max(1, len(p) - w + 1), h)
    ends = np.minimum(starts + w, len(p))
    win = (c[ends] - c[starts]) / np.maximum(1, ends - starts)
    silent = bool(np.any(win == 0.0))
    nonzero = win[win > 0]
    floor = float(np.sqrt(np.min(nonzero))) if len(nonzero) else 0.0
    return Levels(float(db(peak)), float(db(rms)), float(db(floor)), silent)


def frame_db(x: Audio, hop: int) -> F64:
    """RMS level of each analysis frame (dBFS); a partial last frame counts."""
    p = _power(x)
    n = -(-len(p) // hop)
    pad = np.zeros(n * hop)
    pad[: len(p)] = p
    return db(np.sqrt(pad.reshape(n, hop).mean(axis=1)))


def apply_frame_gain(x: Audio, gains_db: F64, hop: int, prev_db: float) -> Audio:
    """Sample gains interpolated between frame gains (each frame's gain is reached
    at its end, starting from the previous frame's), so steps never click."""
    n = len(x)
    knots = np.concatenate([[prev_db], gains_db])
    pos = np.arange(n) / hop  # 0..frames
    g = np.interp(pos, np.arange(len(knots)), knots)
    return (x * undb(g)[:, None]).astype(np.float32)


def _coef(time_s: float) -> float:
    """One-pole smoothing coefficient per analysis frame for a time constant."""
    return float(np.exp(-FRAME_S / time_s)) if time_s > 0 else 0.0


def smooth(target_db: F64, attack_s: float, release_s: float, state: float) -> tuple[F64, float]:
    """Gain smoothing: falling gain (more reduction) follows with the attack time,
    rising gain with the release time."""
    a, r = _coef(attack_s), _coef(release_s)
    out = np.empty_like(target_db)
    g = state
    for i, t in enumerate(target_db):
        c = a if t < g else r
        g = c * g + (1 - c) * t
        out[i] = g
    return out, g


# -- dynamics -----------------------------------------------------------------------------


@dataclass
class Compressor:
    """Gentle downward compression of loud passages (auto-level, SPEC §6.8.3)."""

    threshold_db: float = -24.0
    ratio: float = 2.0
    knee_db: float = 6.0
    attack_s: float = 0.02
    release_s: float = 0.25
    state: float = 0.0

    def curve(self, level_db: F64) -> F64:
        """Gain (dB, <= 0) for an input level: a soft-knee static curve."""
        over = level_db - self.threshold_db
        w = self.knee_db
        slope = 1.0 / self.ratio - 1.0
        g = np.where(over <= -w / 2, 0.0, slope * over)
        if w > 0:
            knee = np.abs(over) < w / 2
            g = np.where(knee, slope * (over + w / 2) ** 2 / (2 * w), g)
        return np.asarray(g, dtype=np.float64)

    def process(self, x: Audio, sr: int) -> Audio:
        hop = max(1, round(FRAME_S * sr))
        target = self.curve(frame_db(x, hop))
        prev = self.state
        g, self.state = smooth(target, self.attack_s, self.release_s, self.state)
        return apply_frame_gain(x, g, hop, prev)


@dataclass
class Gate:
    """Downward expander for noise between phrases (SPEC §6.8.2): below the
    threshold the level drops `ratio` times faster, by at most `range_db`."""

    threshold_db: float = -50.0
    ratio: float = 2.0
    range_db: float = 12.0
    attack_s: float = 0.005
    hold_s: float = 0.05
    release_s: float = 0.1
    state: float = 0.0
    held: int = 0  # frames of hold left

    def process(self, x: Audio, sr: int) -> Audio:
        hop = max(1, round(FRAME_S * sr))
        level = frame_db(x, hop)
        under = level < self.threshold_db
        target = np.where(
            under, np.maximum(-self.range_db, (level - self.threshold_db) * (self.ratio - 1)), 0.0
        )
        # Hold: after speech, stay open for hold_s before closing.
        hold_frames = round(self.hold_s / FRAME_S)
        out = np.empty_like(target)
        held = self.held
        for i, (u, t) in enumerate(zip(under, target, strict=True)):
            if not u:
                held = hold_frames
                out[i] = t
            elif held > 0:
                held -= 1
                out[i] = 0.0
            else:
                out[i] = t
        self.held = held
        prev = self.state
        # Opening is the attack (fast, so speech onsets aren't eaten); closing the release.
        a, r = _coef(self.attack_s), _coef(self.release_s)
        g = np.empty_like(out)
        s = self.state
        for i, t in enumerate(out):
            c = a if t > s else r
            s = c * s + (1 - c) * t
            g[i] = s
        self.state = s
        return apply_frame_gain(x, g, hop, prev)


def block_quantum(sr: int) -> int:
    """Blocks must start on both the 10 ms dynamics grid and the limiter's 1 ms
    grid for block-by-block processing to equal one pass: a multiple of both."""
    return math.lcm(max(1, round(FRAME_S * sr)), max(1, round(0.001 * sr)))


def limit(
    x: Audio,
    sr: int,
    ceiling_db: float,
    ahead: Audio | None = None,
    state: float = 1.0,
    lookahead_s: float = 0.005,
    release_s: float = 0.05,
) -> tuple[Audio, float]:
    """Look-ahead peak limiter: the gain comes down *before* a peak (lookahead_s)
    and recovers with release_s. `ahead`: the samples after this block (so a block
    edge never surprises it). A final clamp guarantees nothing exceeds the ceiling.
    Returns the block and the gain state for the next block."""
    c = float(undb(ceiling_db))
    hop = max(1, round(0.001 * sr))
    full = np.concatenate([x, ahead]) if ahead is not None and len(ahead) else x
    peak = np.max(np.abs(full), axis=1)
    n = -(-len(peak) // hop)
    pad = np.zeros(n * hop)
    pad[: len(peak)] = peak
    fpk = pad.reshape(n, hop).max(axis=1)
    need = np.minimum(1.0, c / np.maximum(fpk, 1e-12))
    la = max(1, round(lookahead_s / 0.001))
    # Each frame takes the smallest gain needed within the next `la` frames.
    padded = np.concatenate([need, np.ones(la)])
    look = np.min(np.lib.stride_tricks.sliding_window_view(padded, la + 1), axis=1)[:n]
    rel = float(np.exp(-0.001 / release_s))
    g = np.empty(n)
    s = state
    for i, t in enumerate(look):
        s = t if t < s else rel * s + (1 - rel) * t
        g[i] = s
    frames_here = -(-len(x) // hop)
    knots = np.concatenate([[state], g[:frames_here]])
    gs = np.interp(np.arange(len(x)) / hop, np.arange(len(knots)), knots)
    y = np.clip(x * gs[:, None], -c, c).astype(np.float32)
    return y, float(g[frames_here - 1]) if frames_here else state


# -- noise reduction ------------------------------------------------------------------------


def fft_size(sr: int) -> int:
    """About 43 ms, as a power of two: 2048 at 44.1/48 kHz, 512 at 16 kHz."""
    return int(2 ** round(np.log2(sr * 0.043)))


def _stft(x: F64, n: int) -> npt.NDArray[np.complex128]:
    hop = n // 4
    win = np.hanning(n + 1)[:n]
    pad = np.concatenate([np.zeros(n), x, np.zeros(n + hop)])
    frames = np.lib.stride_tricks.sliding_window_view(pad, n)[::hop]
    return np.asarray(np.fft.rfft(frames * win, axis=1))


def _istft(spec: npt.NDArray[np.complex128], n: int, length: int) -> F64:
    hop = n // 4
    win = np.hanning(n + 1)[:n]
    frames = np.fft.irfft(spec, n=n, axis=1) * win
    out = np.zeros(hop * (len(frames) - 1) + n)
    norm = np.zeros_like(out)
    for i, f in enumerate(frames):
        out[i * hop : i * hop + n] += f
        norm[i * hop : i * hop + n] += win**2
    out = out / np.maximum(norm, 1e-8)
    return out[n : n + length]


@dataclass
class NoiseProfile:
    n_fft: int
    magnitude: F64  # mean magnitude per bin of the noise

    @classmethod
    def from_audio(cls, noise: Audio, sr: int) -> NoiseProfile:
        n = fft_size(sr)
        mono = noise.astype(np.float64).mean(axis=1)
        if len(mono) < n:
            mono = np.resize(mono, n)  # loop a very short clip
        spec = np.abs(_stft(mono, n))
        return cls(n, spec[2:-2].mean(axis=0) if len(spec) > 4 else spec.mean(axis=0))


def quietest(x: Audio, sr: int, seconds: float = 1.0) -> tuple[int, int]:
    """The quietest stretch of the given length (not digital silence): a
    stand-in for room tone when the user hasn't picked one."""
    w = max(1, int(seconds * sr))
    p = _power(x)
    if len(p) <= w:
        return 0, len(p)
    c = np.concatenate([[0.0], np.cumsum(p)])
    hop = max(1, int(0.05 * sr))
    starts = np.arange(0, len(p) - w + 1, hop)
    e = c[starts + w] - c[starts]
    e = np.where(e > 0, e, np.inf)
    i = int(starts[int(np.argmin(e))])
    return i, i + w


@dataclass
class NoiseReducer:
    """Spectral gating against a noise profile (SPEC §6.8.1). Each bin keeps
    max(floor, 1 - alpha * noise/level) of its magnitude; the mask is smoothed over
    neighbouring frames and bins to avoid "musical noise"."""

    profile: NoiseProfile
    strength: float = 0.6  # 0..1
    reduction_db: float = 12.0  # at most this much is taken off any bin

    def process(self, x: Audio) -> Audio:
        n = self.profile.n_fft
        alpha = 1.0 + 2.0 * self.strength
        floor = float(undb(-self.reduction_db * max(0.0, min(1.0, self.strength * 1.5))))
        out = np.empty_like(x)
        for ch in range(x.shape[1]):
            spec = _stft(x[:, ch].astype(np.float64), n)
            mag = np.abs(spec)
            gain = np.clip(
                1.0 - alpha * self.profile.magnitude / np.maximum(mag, 1e-12), floor, 1.0
            )
            # 3x3 moving average of the mask (frames x bins).
            k = np.pad(gain, 1, mode="edge")
            rows, cols = gain.shape
            acc = np.zeros_like(gain)
            for i in range(3):
                for j in range(3):
                    acc += k[i : i + rows, j : j + cols]
            gain = acc / 9.0
            out[:, ch] = _istft(spec * gain, n, len(x)).astype(np.float32)
        return out

    @property
    def context(self) -> int:
        """Samples of neighbouring audio a block needs on each side."""
        return 2 * self.profile.n_fft


def dither_16(x: Audio, rng: np.random.Generator) -> npt.NDArray[np.int16]:
    """TPDF dither to 16-bit: the quantization error becomes benign noise
    (about -96 dBFS), well under the -60 dB noise-floor limit."""
    lsb = 1.0 / 32768.0
    noise = (rng.random(x.shape) - rng.random(x.shape)) * lsb
    return np.clip(np.round((x + noise) * 32767.0), -32768, 32767).astype(np.int16)


__all__ = [
    "Compressor",
    "Gate",
    "Levels",
    "NoiseProfile",
    "NoiseReducer",
    "dither_16",
    "limit",
    "measure",
    "quietest",
]
