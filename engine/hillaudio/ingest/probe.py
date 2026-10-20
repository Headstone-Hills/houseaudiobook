"""Probe audio files with ffprobe.

`parse_ffprobe` is pure so the format rules can be tested on canned output;
`probe` is the thin I/O wrapper.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# v1 supports only these (SPEC §2). Keyed by ffprobe's format_name, with the
# file extensions we accept for each, so a renamed file can't slip through.
SUPPORTED_FORMATS: dict[str, tuple[str, ...]] = {
    "wav": (".wav", ".wave"),
    "mp3": (".mp3",),
}
LOSSLESS_CODEC_PREFIXES = ("pcm_",)


class UnsupportedAudio(ValueError):
    """The file is not a v1-supported audio file."""


@dataclass(frozen=True)
class ProbeInfo:
    format: str  # "wav" | "mp3"
    codec: str  # e.g. "pcm_s16le", "mp3"
    sample_rate: int
    channels: int
    duration: float  # container estimate; exact length comes from decoding
    bit_depth: int | None  # PCM only
    bit_rate: int | None

    @property
    def lossless(self) -> bool:
        # Drives the "never transcode lossy -> lossy" rule at export (M8).
        return self.codec.startswith(LOSSLESS_CODEC_PREFIXES)


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_ffprobe(data: dict[str, Any], suffix: str) -> ProbeInfo:
    fmt = data.get("format", {})
    names = str(fmt.get("format_name", "")).split(",")
    fmt_name = next((n for n in names if n in SUPPORTED_FORMATS), None)
    if fmt_name is None:
        raise UnsupportedAudio(f"unsupported format {fmt.get('format_name')!r}; use .wav or .mp3")
    if suffix.lower() not in SUPPORTED_FORMATS[fmt_name]:
        raise UnsupportedAudio(f"file extension {suffix!r} does not match its {fmt_name} content")

    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if len(audio) != 1:
        raise UnsupportedAudio(f"expected exactly one audio stream, found {len(audio)}")
    st = audio[0]

    sample_rate = _int(st.get("sample_rate"))
    channels = _int(st.get("channels"))
    if not sample_rate or not channels:
        raise UnsupportedAudio("missing sample rate or channel count")
    duration = float(st.get("duration") or fmt.get("duration") or 0.0)
    codec = str(st.get("codec_name", ""))
    bits = _int(st.get("bits_per_sample")) or _int(st.get("bits_per_raw_sample"))
    return ProbeInfo(
        format=fmt_name,
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        duration=duration,
        bit_depth=bits if codec.startswith(LOSSLESS_CODEC_PREFIXES) and bits else None,
        bit_rate=_int(st.get("bit_rate")) or _int(fmt.get("bit_rate")),
    )


def probe(path: Path) -> ProbeInfo:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise UnsupportedAudio(f"not a readable audio file: {proc.stderr.strip()[:200]}")
    return parse_ffprobe(json.loads(proc.stdout), path.suffix)
