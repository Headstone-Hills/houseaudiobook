from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES, MakeAudio

from hillaudio.ingest.decode import iter_blocks
from hillaudio.ingest.probe import UnsupportedAudio, parse_ffprobe, probe


def ffprobe_json(fmt: str, codec: str, **stream: Any) -> dict[str, Any]:
    return {
        "format": {"format_name": fmt, "duration": "1.5", "bit_rate": "128000"},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": codec,
                "sample_rate": "48000",
                "channels": 2,
                **stream,
            }
        ],
    }


def test_parse_wav_and_mp3() -> None:
    wav = parse_ffprobe(ffprobe_json("wav", "pcm_s24le", bits_per_sample=24), ".WAV")
    assert (wav.format, wav.sample_rate, wav.channels, wav.bit_depth, wav.lossless) == (
        "wav",
        48000,
        2,
        24,
        True,
    )
    mp3 = parse_ffprobe(ffprobe_json("mp3", "mp3", bits_per_sample=0), ".mp3")
    assert (mp3.format, mp3.bit_depth, mp3.lossless, mp3.bit_rate) == ("mp3", None, False, 128000)


@pytest.mark.parametrize(
    ("data", "suffix", "msg"),
    [
        (ffprobe_json("flac", "flac"), ".flac", "unsupported format"),
        (ffprobe_json("mp3", "mp3"), ".wav", "does not match"),  # renamed file
        ({"format": {"format_name": "wav"}, "streams": []}, ".wav", "exactly one audio stream"),
        (ffprobe_json("wav", "pcm_s16le", sample_rate="0"), ".wav", "sample rate"),
    ],
)
def test_parse_rejects(data: dict[str, Any], suffix: str, msg: str) -> None:
    with pytest.raises(UnsupportedAudio, match=msg):
        parse_ffprobe(data, suffix)


def test_probe_real_files(make_audio: MakeAudio) -> None:
    info = probe(FIXTURES / "ch01_take2.wav")
    assert (info.format, info.sample_rate, info.channels, info.bit_depth) == ("wav", 16000, 1, 16)
    mp3 = probe(make_audio("t.mp3", seconds=2.0, sample_rate=44100))
    assert (mp3.format, mp3.codec, mp3.channels, mp3.lossless) == ("mp3", "mp3", 2, False)


def test_probe_rejects_non_audio(tmp_path: Path) -> None:
    bogus = tmp_path / "notes.wav"
    bogus.write_text("not audio")
    with pytest.raises(UnsupportedAudio):
        probe(bogus)


def test_decode_exact_frames(make_audio: MakeAudio) -> None:
    path = make_audio("d.wav", seconds=1.0, sample_rate=44100, channels=2)
    blocks = list(iter_blocks(path, channels=2, block_frames=1000))
    assert sum(len(b) for b in blocks) == 44100
    assert all(b.shape[1] == 2 for b in blocks)


def test_decode_stopped_early_does_not_hang(make_audio: MakeAudio) -> None:
    path = make_audio("long.wav", seconds=5.0)
    gen = iter_blocks(path, channels=2, block_frames=1000)
    next(gen)
    gen.close()  # must kill ffmpeg and return promptly
