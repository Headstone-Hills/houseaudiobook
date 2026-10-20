"""Transcriber interface and settings (D-007, D-028)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from hillaudio.progress import CheckCancel, Progress

# Primes Whisper to keep disfluencies: without it every model tested dropped every
# injected stutter; with it small.en kept them all (docs/bench/asr-2026-10-20.md).
DISFLUENCY_PROMPT = "Um, uh, the- the, I mean, sorry, let me start again."
MODELS = ("tiny.en", "base.en", "small.en", "medium.en", "large-v3-turbo")


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start: float  # seconds from source start
    end: float
    prob: float


@dataclass(frozen=True)
class AsrSettings:
    model: str = "small.en"
    compute_type: str = "int8"
    beam_size: int = 5
    language: str = "en"
    prompt: str = DISFLUENCY_PROMPT

    def key(self) -> str:
        """Cache key: transcripts are reused only for identical settings."""
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AsrSettings:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        s = cls(**known)
        if s.model not in MODELS:
            raise ValueError(f"unknown model {s.model!r}; choose one of {', '.join(MODELS)}")
        return s


class Transcriber(Protocol):
    settings: AsrSettings

    def transcribe(
        self, path: Path, duration: float, progress: Progress, check_cancel: CheckCancel
    ) -> list[TranscriptWord]: ...


class AsrUnavailable(RuntimeError):
    """The speech engine or its model can't be used (not installed, no download)."""
