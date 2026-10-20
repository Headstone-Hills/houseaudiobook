"""Transcript cache under cache/transcripts/, keyed by file hash + ASR settings."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from hillaudio.asr.base import AsrSettings, TranscriptWord


def path(cache_dir: Path, sha256: str, settings: AsrSettings) -> Path:
    return cache_dir / "transcripts" / f"{sha256}.{settings.key()}.json"


def load(cache_dir: Path, sha256: str, settings: AsrSettings) -> list[TranscriptWord] | None:
    p = path(cache_dir, sha256, settings)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return [TranscriptWord(**w) for w in data["words"]]


def save(cache_dir: Path, sha256: str, settings: AsrSettings, words: list[TranscriptWord]) -> None:
    p = path(cache_dir, sha256, settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(
        json.dumps({"settings": settings.to_json(), "words": [asdict(w) for w in words]}),
        encoding="utf-8",
    )
    os.replace(tmp, p)  # never leave a half-written cache entry
