"""faster-whisper's own audio decoding works with the installed PyAV.

The other tests use a fake ASR, so they never reach faster-whisper's decoder.
PyAV 19 dropped an argument that faster-whisper 1.2.1 passes to av.open(), and
every real transcription failed while all the tests passed.
"""

from __future__ import annotations

import pytest
from conftest import FIXTURES


def test_faster_whisper_decodes_a_fixture() -> None:
    audio = pytest.importorskip("faster_whisper.audio")
    samples = audio.decode_audio(str(FIXTURES / "ch01_take1.wav"))
    assert samples.ndim == 1
    assert len(samples) > 16000  # more than a second at Whisper's 16 kHz
