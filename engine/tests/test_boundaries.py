from __future__ import annotations

import numpy as np
import pytest

from hillaudio.analysis.boundaries import (
    EnvelopeBuilder,
    envelope,
    snap_end,
    snap_span,
    snap_start,
    speech_mask,
)

SR = 16_000


def signal(spec: list[tuple[float, bool]], noise_db: float = -65.0) -> np.ndarray:
    """Concatenate (seconds, is_speech) pieces: 440 Hz tone at -12 dBFS or noise."""
    rng = np.random.default_rng(0)
    parts = []
    for seconds, speech in spec:
        n = round(seconds * SR)
        noise = rng.standard_normal(n) * 10 ** (noise_db / 20)
        tone = 0.35 * np.sin(2 * np.pi * 440 * np.arange(n) / SR) if speech else 0
        parts.append(noise + tone)
    return np.concatenate(parts).astype(np.float32)


# 0.5 s silence, 1.0 s speech, 0.03 s dip (a stop consonant), 0.5 s speech,
# 0.4 s pause, 0.6 s speech, 0.5 s silence.
AUDIO = signal(
    [(0.5, False), (1.0, True), (0.03, False), (0.5, True), (0.4, False), (0.6, True), (0.5, False)]
)


@pytest.fixture(scope="module")
def mask() -> np.ndarray:
    return speech_mask(envelope(AUDIO, SR))


def test_envelope_block_size_invariant() -> None:
    ref = envelope(AUDIO, SR)
    b = EnvelopeBuilder(SR)
    for i in range(0, len(AUDIO), 777):
        b.add(AUDIO[i : i + 777])
    assert np.allclose(b.finish().db, ref.db)
    assert ref.fps == 100.0


def test_mask_fills_short_dips(mask: np.ndarray) -> None:
    # The 30 ms dip at 1.5 s is inside speech; the 0.4 s pause at 2.03 s is not.
    assert mask[150:153].all()
    assert not mask[210:240].any()
    assert not mask[:45].any()


def test_start_in_leading_silence_moves_forward(mask: np.ndarray) -> None:
    # Whisper's classic error: the first word at 0.00 s.
    assert snap_start(mask, 100, 0.0) == pytest.approx(0.5, abs=0.02)


def test_start_inside_speech_moves_back_to_pause_edge(mask: np.ndarray) -> None:
    assert snap_start(mask, 100, 2.6) == pytest.approx(2.43, abs=0.02)  # 0.17 s back


def test_start_inside_long_speech_without_close_pause_stays(mask: np.ndarray) -> None:
    assert snap_start(mask, 100, 1.2) == pytest.approx(1.2)  # pause is 0.7 s back


def test_end_in_silence_moves_back_and_in_speech_moves_forward(mask: np.ndarray) -> None:
    assert snap_end(mask, 100, 2.3) == pytest.approx(2.03, abs=0.02)
    assert snap_end(mask, 100, 2.9) == pytest.approx(3.03, abs=0.02)


def test_span_never_inverts(mask: np.ndarray) -> None:
    # A span entirely in silence would snap past itself: keep the original.
    assert snap_span(mask, 100, 2.1, 2.2) == (2.1, 2.2)


def test_empty_audio() -> None:
    m = speech_mask(envelope(np.zeros(0, dtype=np.float32), SR))
    assert snap_start(m, 100, 1.0) == 1.0 and snap_end(m, 100, 1.0) == 1.0


def test_start_placed_inside_a_stutter_moves_forward_past_the_pause() -> None:
    """Regression (bench): Whisper put the sentence's first word inside the stutter
    before it; snapping back landed on the stutter, 430 ms early."""
    # stutter "The" 0.5-0.8, pause 0.15 s, sentence 0.95-2.0
    audio = signal([(0.5, False), (0.3, True), (0.15, False), (1.05, True), (0.5, False)])
    m = speech_mask(envelope(audio, SR))
    assert snap_start(m, 100, 0.78) == pytest.approx(0.95, abs=0.02)  # nearer: forward
    assert snap_start(m, 100, 0.55) == pytest.approx(0.5, abs=0.02)  # nearer: back


def test_start_never_moves_into_the_previous_word() -> None:
    """Regression (bench): the sentence's "The" was placed inside a stutter "The"
    that the transcript also has; snapping back landed on the stutter."""
    audio = signal([(0.5, False), (0.3, True), (0.15, False), (1.05, True), (0.5, False)])
    m = speech_mask(envelope(audio, SR))
    # Back edge (0.5) is nearer, but the previous word starts at 0.5 itself.
    assert snap_start(m, 100, 0.62, lower=0.5) == pytest.approx(0.95, abs=0.02)
    assert snap_end(m, 100, 1.9, upper=1.95) == pytest.approx(1.9)  # next word's end bounds it


def test_quiet_word_ending_is_kept() -> None:
    """Regression (bench): a soft final consonant below the speech threshold was cut."""
    rng = np.random.default_rng(1)
    loud = 0.35 * np.sin(2 * np.pi * 440 * np.arange(8000) / SR)
    soft = 0.004 * rng.standard_normal(2400)  # ~-48 dBFS "ch", 150 ms
    quiet = rng.standard_normal(8000) * 10 ** (-65 / 20)
    audio = np.concatenate([quiet, loud, soft, quiet]).astype(np.float32)
    m = speech_mask(envelope(audio, SR))
    assert snap_end(m, 100, 1.0) == pytest.approx(1.15, abs=0.02)
