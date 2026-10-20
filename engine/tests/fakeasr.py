"""A fake speech recognizer built from the fixture ground truth (no model needed).

Each event's heard words are spread evenly over its true time span, so tests can
check alignment and timing exactly. Optional seeded noise imitates Whisper:
random wrong words, and stutters dropped (as unprompted Whisper does).
"""

from __future__ import annotations

import json
import random
import threading
from pathlib import Path
from typing import Any

from conftest import FIXTURES

import make_fixtures as mf
from hillaudio.asr.base import AsrSettings, TranscriptWord
from hillaudio.progress import CheckCancel, Progress

TRUTH: dict[str, Any] = json.loads((FIXTURES / "ground_truth.json").read_text())
GAP_AFTER = {
    "stutter": mf.STUTTER_GAP_S,
    "false_start": mf.RESTART_GAP_S,
    "commentary": mf.COMMENTARY_GAP_S,
}
NOISE_WORDS = ["apple", "river", "quickly", "blue", "seven", "window", "under", "garden"]


def truth_file(name: str) -> dict[str, Any]:
    return next(f for f in TRUTH["files"] if f["name"] == name)


def fake_words(
    file: dict[str, Any], noise: float = 0.0, drop_stutters: bool = False, seed: int = 0
) -> list[TranscriptWord]:
    rng = random.Random(seed)
    words: list[TranscriptWord] = []
    for e in file["events"]:
        if not e["heard"] or (drop_stutters and e["type"] == "stutter"):
            continue
        heard = e["heard"].split()
        end = e["end"] - GAP_AFTER.get(e["type"], 0.0)
        step = (end - e["start"]) / len(heard)
        protected = {(e.get("detail") or {}).get("heard_word")}
        for i, w in enumerate(heard):
            if (
                noise
                and e["type"] == "read"
                and w.strip(".,") not in protected
                and rng.random() < noise
            ):
                w = rng.choice(NOISE_WORDS)
            words.append(
                TranscriptWord(w, e["start"] + i * step, e["start"] + (i + 1) * step, 0.95)
            )
    return words


class FakeTranscriber:
    """Recognizes the fixture files by name; counts calls (to test caching)."""

    calls: list[str]

    def __init__(self, settings: AsrSettings, block: threading.Event | None = None) -> None:
        self.settings = settings
        self.calls = []
        self.block = block  # if set, wait (checking for cancel) until it's set

    def transcribe(
        self, path: Path, duration: float, progress: Progress, check_cancel: CheckCancel
    ) -> list[TranscriptWord]:
        # Copies in sources/ are named "<hash>_<original name>".
        name = path.name.split("_", 1)[1] if path.parent.name == "sources" else path.name
        self.calls.append(name)
        while self.block is not None and not self.block.wait(0.01):
            check_cancel()
        progress(1.0, "transcribed")
        return fake_words(truth_file(name))
