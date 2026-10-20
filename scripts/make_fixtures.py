#!/usr/bin/env python3
"""Generate synthetic narration fixtures with known, injected defects (SPEC §11).

Output (default: engine/fixtures/):
  manuscript.txt       the source text, with `[p. N]` page markers
  ch01_take1.wav ...   synthetic narration, 16 kHz mono 16-bit
  ground_truth.json    every read sentence and injected defect, with timestamps

Design: everything except TTS and file writing is a pure function, so the planning
and timestamp math can be tested with a fake synthesizer and no speech engine.
Ground-truth timestamps come from the assembly itself (we place every clip and
gap), never from analysing the audio, so they are exact by construction.

The default TTS is espeak-ng, because it runs the same on macOS (brew) and Linux
(apt), so regenerated fixtures match the committed ones. `--tts say` uses the
macOS voice for more natural speech; its timestamps are still exact, but the
audio differs from what is committed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

Audio = npt.NDArray[np.float32]
Synth = Callable[[str], Audio]

SAMPLE_RATE = 16_000
SEED = 20261020
GROUND_TRUTH_VERSION = 1

# Pacing of a "clean" read. Long-silence defects are chosen well above the
# spec's suggested 2.0 s threshold; normal gaps are well below it.
LEAD_IN_S = 0.5
TAIL_S = 0.5
SENTENCE_GAP_S = 0.45
PARAGRAPH_GAP_S = 0.9
STUTTER_GAP_S = 0.15
RESTART_GAP_S = 0.6
COMMENTARY_GAP_S = 0.7

# Each speech clip is scaled to this peak so levels are predictable.
CLIP_PEAK = 0.5  # about -6 dBFS
TRIM_THRESHOLD = 0.02  # amplitude below which TTS padding is trimmed
TRIM_PAD_S = 0.01

# --------------------------------------------------------------------------
# Manuscript (pure data)
# --------------------------------------------------------------------------

# Adapted from Aesop's Fables (public domain). Kept short so committed audio is small.
MANUSCRIPT_TEXT = """\
[p. 1]
Chapter One. The Fox and the Grapes.

A hungry fox saw some fine bunches of grapes hanging from a vine. \
The vine was trained along a high trellis. \
He did his best to reach them by jumping as high as he could.

[p. 2]
But it was all in vain, for they were just out of reach. \
So he gave up trying, and walked away with an air of dignity. \
I thought those grapes were ripe, he said, but I see now they are quite sour.

[p. 3]
Chapter Two. The Tortoise and the Hare.

A hare once made fun of a tortoise for being so slow. \
The tortoise laughed and said that he would win a race against him.

The hare soon ran far ahead, and lay down to take a nap. \
The tortoise plodded on and never stopped. \
When the hare woke up, the tortoise was already at the finish line.
"""

PAGE_MARKER = re.compile(r"^\[p\. (\d+)\]$")


@dataclass(frozen=True)
class Sentence:
    id: str  # e.g. "s07"; stable across regenerations
    text: str
    page: int
    chapter: int
    paragraph: int  # global paragraph index
    is_heading: bool


def parse_manuscript(text: str) -> list[Sentence]:
    """Split the fixture manuscript into sentences with pages.

    This parser only needs to understand our own fixture format; the real
    manuscript importer (M2) is separate and must handle far more.
    """
    sentences: list[Sentence] = []
    page = 0
    chapter = 0
    paragraph = -1
    for block in re.split(r"\n\s*\n", text):
        lines = []
        for line in block.strip().splitlines():
            m = PAGE_MARKER.match(line.strip())
            if m:
                page = int(m.group(1))
            elif line.strip():
                lines.append(line.strip())
        if not lines:
            continue
        body = " ".join(lines)
        paragraph += 1
        heading = body.startswith("Chapter ")
        if heading:
            chapter += 1
            parts = [body]
        else:
            parts = [p for p in re.split(r"(?<=[.!?])\s+", body) if p]
        for part in parts:
            sentences.append(
                Sentence(
                    id=f"s{len(sentences):02d}",
                    text=part,
                    page=page,
                    chapter=chapter,
                    paragraph=paragraph,
                    is_heading=heading,
                )
            )
    return sentences


# --------------------------------------------------------------------------
# Plan: what gets "said", in order (pure)
# --------------------------------------------------------------------------

EventType = Literal[
    "read",  # a manuscript sentence read correctly
    "stutter",  # repeated word before a sentence; span = the region to remove
    "false_start",  # partial phrase + pause before a restart; span = region to remove
    "commentary",  # off-script speech + pause; span = region to remove
    "misread",  # sentence read with one word substituted
    "skip",  # sentence read with one word missing
    "long_silence",  # gap above the overlong-silence threshold
]


@dataclass(frozen=True)
class Speech:
    text: str
    event: int | None = None  # index into FilePlan.events


@dataclass(frozen=True)
class Silence:
    seconds: float
    event: int | None = None


Piece = Speech | Silence


@dataclass
class EventSpec:
    type: EventType
    sentence_id: str | None
    page: int
    expected: str | None = None
    heard: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class FilePlan:
    name: str
    noise_dbfs: float  # RMS of added white noise, dB re full scale (1.0)
    pieces: list[Piece] = field(default_factory=list)
    events: list[EventSpec] = field(default_factory=list)

    def event(self, spec: EventSpec) -> int:
        self.events.append(spec)
        return len(self.events) - 1

    def gap(self, seconds: float) -> None:
        self.pieces.append(Silence(seconds))

    def read(self, s: Sentence, heard: str | None = None, ev: EventSpec | None = None) -> None:
        """Read a sentence. With `ev`, the read itself is the defect (misread/skip)."""
        spec = ev or EventSpec("read", s.id, s.page, expected=s.text, heard=s.text)
        spec.heard = heard if heard is not None else s.text
        self.pieces.append(Speech(spec.heard, self.event(spec)))

    def stutter(self, s: Sentence) -> None:
        word = s.text.split()[0]
        ev = self.event(EventSpec("stutter", s.id, s.page, heard=word, detail={"word": word}))
        self.pieces += [Speech(word, ev), Silence(STUTTER_GAP_S, ev)]
        self.read(s)

    def false_start(self, s: Sentence, n_words: int) -> None:
        partial = " ".join(s.text.split()[:n_words])
        ev = self.event(EventSpec("false_start", s.id, s.page, heard=partial))
        self.pieces += [Speech(partial, ev), Silence(RESTART_GAP_S, ev)]
        self.read(s)

    def commentary(self, text: str, page: int) -> None:
        ev = self.event(EventSpec("commentary", None, page, heard=text))
        self.pieces += [Speech(text, ev), Silence(COMMENTARY_GAP_S, ev)]

    def long_silence(self, seconds: float, page: int, after: Sentence) -> None:
        ev = self.event(
            EventSpec("long_silence", None, page, detail={"seconds": seconds, "after": after.id})
        )
        self.pieces.append(Silence(seconds, ev))


def substitute(text: str, old: str, new: str) -> str:
    out, n = re.subn(rf"\b{re.escape(old)}\b", new, text, count=1)
    if n != 1:
        raise ValueError(f"{old!r} not found in {text!r}")
    return out


def omit(text: str, word: str) -> str:
    out, n = re.subn(rf"\s*\b{re.escape(word)}\b", "", text, count=1)
    if n != 1:
        raise ValueError(f"{word!r} not found in {text!r}")
    return out


def gap_between(prev: Sentence, nxt: Sentence) -> float:
    return PARAGRAPH_GAP_S if prev.paragraph != nxt.paragraph else SENTENCE_GAP_S


def build_plans(sentences: Sequence[Sentence]) -> list[FilePlan]:
    """The fixed defect scenario. Changing it changes the ground truth."""
    s = {x.id: x for x in sentences}
    ch1 = [x for x in sentences if x.chapter == 1]
    ch2 = [x for x in sentences if x.chapter == 2]

    # Chapter 1, take 1: most cleanup defects, low noise.
    p1 = FilePlan("ch01_take1.wav", noise_dbfs=-65.0)
    p1.gap(LEAD_IN_S)
    for i, x in enumerate(ch1):
        if i:
            p1.gap(gap_between(ch1[i - 1], x))
        if x.id == "s02":
            p1.stutter(x)
        elif x.id == "s03":
            p1.false_start(x, n_words=4)
        elif x.id == "s04":
            # Overlong pause at the paragraph break replaces the normal gap.
            p1.pieces.pop()
            p1.long_silence(3.5, page=s["s03"].page, after=s["s03"])
            heard = substitute(x.text, "reach", "sight")
            p1.read(
                x,
                heard,
                EventSpec(
                    "misread",
                    x.id,
                    x.page,
                    expected=x.text,
                    detail={"expected_word": "reach", "heard_word": "sight"},
                ),
            )
        elif x.id == "s05":
            p1.commentary("Hold on, let me get some water.", page=x.page)
            p1.read(x)
        elif x.id == "s06":
            heard = omit(x.text, "now")
            p1.read(
                x,
                heard,
                EventSpec("skip", x.id, x.page, expected=x.text, detail={"missing_word": "now"}),
            )
        else:
            p1.read(x)
    p1.gap(TAIL_S)

    # Chapter 1, take 2: a pickup of the misread sentence, recorded noisier.
    p2 = FilePlan("ch01_take2.wav", noise_dbfs=-50.0)
    p2.gap(LEAD_IN_S)
    p2.read(s["s04"])
    p2.gap(TAIL_S)

    # Chapter 2: clean read, but one sentence is read twice in a row (an
    # in-file duplicate take) and there is one overlong mid-paragraph pause.
    p3 = FilePlan("ch02_take1.wav", noise_dbfs=-65.0)
    p3.gap(LEAD_IN_S)
    for i, x in enumerate(ch2):
        if i:
            p3.gap(gap_between(ch2[i - 1], x))
        if x.id == "s09":
            p3.stutter(x)
        elif x.id == "s10":
            p3.read(x)
            p3.gap(PARAGRAPH_GAP_S)
            p3.read(x)
        elif x.id == "s11":
            p3.pieces.pop()
            p3.long_silence(2.6, page=s["s10"].page, after=s["s10"])
            p3.read(x)
        else:
            p3.read(x)
    p3.gap(TAIL_S)

    return [p1, p2, p3]


# --------------------------------------------------------------------------
# Assembly (pure): place clips, compute exact timestamps, add noise
# --------------------------------------------------------------------------


def trim_silence(clip: Audio, sr: int, threshold: float = TRIM_THRESHOLD) -> Audio:
    """Remove TTS padding so our gaps, not the engine's, define the pauses."""
    loud = np.flatnonzero(np.abs(clip) > threshold)
    if loud.size == 0:
        return clip[:0]
    pad = int(TRIM_PAD_S * sr)
    return clip[max(0, loud[0] - pad) : loud[-1] + 1 + pad]


def normalize_peak(clip: Audio, peak: float = CLIP_PEAK) -> Audio:
    m = float(np.max(np.abs(clip))) if clip.size else 0.0
    return clip if m == 0.0 else (clip * (peak / m)).astype(np.float32)


def white_noise(n: int, dbfs: float, rng: np.random.Generator) -> Audio:
    rms = 10.0 ** (dbfs / 20.0)
    noise: Audio = (rng.standard_normal(n) * rms).astype(np.float32)
    return noise


@dataclass
class Assembled:
    audio: Audio
    events: list[dict[str, Any]]


def assemble(plan: FilePlan, clips: dict[str, Audio], sr: int, seed: int) -> Assembled:
    """Lay out a plan's pieces on a timeline. `clips` maps text -> trimmed audio."""
    parts: list[Audio] = []
    spans: dict[int, list[int]] = {}  # event index -> [start_sample, end_sample]
    cursor = 0
    for piece in plan.pieces:
        if isinstance(piece, Speech):
            chunk = clips[piece.text]
        else:
            # Round once here; ground truth is derived from the same integers.
            chunk = np.zeros(round(piece.seconds * sr), dtype=np.float32)
        if piece.event is not None:
            span = spans.setdefault(piece.event, [cursor, cursor])
            span[1] = cursor + len(chunk)
        parts.append(chunk)
        cursor += len(chunk)

    audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    # The seed varies by file so noise is independent but reproducible.
    rng = np.random.default_rng([seed, *plan.name.encode()])
    audio = audio + white_noise(len(audio), plan.noise_dbfs, rng)
    audio = np.clip(audio, -1.0, 1.0).astype(np.float32)

    events = []
    for idx, spec in enumerate(plan.events):
        start, end = spans[idx]
        events.append(
            {
                "type": spec.type,
                "start": start / sr,
                "end": end / sr,
                "start_sample": start,
                "end_sample": end,
                "sentence_id": spec.sentence_id,
                "page": spec.page,
                "expected": spec.expected,
                "heard": spec.heard,
                **({"detail": spec.detail} if spec.detail else {}),
            }
        )
    events.sort(key=lambda e: (e["start_sample"], e["end_sample"]))
    return Assembled(audio, events)


def take_groups(files: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group every reading of the same sentence that occurs more than once.

    A take is any read/misread/skip of the sentence. The suggested preferred take
    is the latest correct read, matching how narrators record pickups.
    """
    takes: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        for e in f["events"]:
            if e["type"] in ("read", "misread", "skip"):
                takes.setdefault(e["sentence_id"], []).append(
                    {"file": f["name"], "start": e["start"], "end": e["end"], "type": e["type"]}
                )
    groups = []
    for sid, ts in sorted(takes.items()):
        if len(ts) < 2:
            continue
        correct = [i for i, t in enumerate(ts) if t["type"] == "read"]
        groups.append(
            {"sentence_id": sid, "takes": ts, "preferred_index": correct[-1] if correct else None}
        )
    return groups


def build_ground_truth(
    sentences: Sequence[Sentence],
    plans: Sequence[FilePlan],
    assembled: Sequence[Assembled],
    sr: int,
    tts: str,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = [
        {
            "name": p.name,
            "sample_rate": sr,
            "channels": 1,
            "duration": len(a.audio) / sr,
            "num_samples": len(a.audio),
            "noise_dbfs_rms": p.noise_dbfs,
            "events": a.events,
        }
        for p, a in zip(plans, assembled, strict=True)
    ]
    counts: dict[str, int] = {}
    for f in files:
        for e in f["events"]:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
    groups = take_groups(files)
    counts["take_group"] = len(groups)
    return {
        "version": GROUND_TRUTH_VERSION,
        "tts": tts,
        "notes": (
            "Times are float seconds from file start; *_sample fields are exact. "
            "Removal-type events (stutter, false_start, commentary) span the region "
            "a perfect cleanup would cut. Noise is white, RMS re full scale 1.0."
        ),
        "manuscript": "manuscript.txt",
        "sentences": [s.__dict__ for s in sentences],
        "files": files,
        "take_groups": groups,
        "counts": dict(sorted(counts.items())),
    }


def render_all(
    synth: Synth, sr: int = SAMPLE_RATE, seed: int = SEED, tts: str = "custom"
) -> tuple[list[FilePlan], list[Assembled], dict[str, Any]]:
    sentences = parse_manuscript(MANUSCRIPT_TEXT)
    plans = build_plans(sentences)
    texts = sorted({p.text for plan in plans for p in plan.pieces if isinstance(p, Speech)})
    clips = {t: normalize_peak(trim_silence(synth(t), sr)) for t in texts}
    assembled = [assemble(p, clips, sr, seed) for p in plans]
    return plans, assembled, build_ground_truth(sentences, plans, assembled, sr, tts)


# --------------------------------------------------------------------------
# I/O: TTS backends and writing files
# --------------------------------------------------------------------------


def _decode(path: Path, sr: int) -> Audio:
    """Decode any audio file to mono float32 at `sr` via ffmpeg."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        check=True,
        capture_output=True,
    ).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def espeak_synth(sr: int) -> Synth:
    def synth(text: str) -> Audio:
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "s.wav"
            # Fixed voice and rate so output is reproducible across machines.
            subprocess.run(
                ["espeak-ng", "-v", "en-us", "-s", "170", "-w", str(wav), text], check=True
            )
            return _decode(wav, sr)

    return synth


def say_synth(sr: int) -> Synth:
    def synth(text: str) -> Audio:
        with tempfile.TemporaryDirectory() as d:
            aiff = Path(d) / "s.aiff"
            subprocess.run(["say", "-o", str(aiff), text], check=True)
            return _decode(aiff, sr)

    return synth


def write_wav(path: Path, audio: Audio, sr: int) -> None:
    import soundfile as sf  # local import: pure parts must not need it

    sf.write(str(path), audio, sr, subtype="PCM_16")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[1] / "engine" / "fixtures"
    )
    ap.add_argument("--tts", choices=["espeak-ng", "say"], default="espeak-ng")
    args = ap.parse_args(argv)

    for tool in ("ffmpeg", args.tts):
        if shutil.which(tool) is None:
            print(f"error: {tool} not found on PATH", file=sys.stderr)
            return 1
    synth = espeak_synth(SAMPLE_RATE) if args.tts == "espeak-ng" else say_synth(SAMPLE_RATE)
    plans, assembled, truth = render_all(synth, tts=args.tts)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manuscript.txt").write_text(MANUSCRIPT_TEXT, encoding="utf-8")
    for p, a in zip(plans, assembled, strict=True):
        write_wav(args.out / p.name, a.audio, SAMPLE_RATE)
    (args.out / "ground_truth.json").write_text(
        json.dumps(truth, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(f["duration"] for f in truth["files"])
    print(f"wrote {len(plans)} files ({total:.1f} s) to {args.out}; counts: {truth['counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
