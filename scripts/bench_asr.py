#!/usr/bin/env python3
"""Benchmark Whisper models on the synthetic fixtures (spike 01 exit criteria).

For each model, transcribes every fixture file with word timestamps and reports:
- WER against the *heard* (verbatim) text, including stutters, false starts and
  commentary, since the app needs those transcribed, not tidied away;
- survival: for each injected event, the share of its heard words that appear
  in the transcript inside the event's time span;
- boundary error: |transcript - truth| at sentence starts and ends, in ms;
- real-time factor: processing seconds per audio second.

Model weights download to $HILLAUDIO_HOME/models (default ~/.hillaudiobook/models).

    engine/.venv/bin/python scripts/bench_asr.py --models small.en medium.en
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hillaudio.analysis.boundaries import envelope, snap_end, snap_start, speech_mask
from hillaudio.text.normalize import normalize, tokens_equal

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "engine" / "fixtures"
SPAN_SLACK_S = 0.25  # words may straddle an event edge by this much


@dataclass
class Word:
    text: str
    start: float
    end: float
    prob: float


@dataclass
class FileResult:
    name: str
    words: list[Word]
    seconds: float  # processing time
    duration: float


@dataclass
class ModelResult:
    model: str
    files: list[FileResult] = field(default_factory=list)


def edit_distance(ref: Sequence[str], hyp: Sequence[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (not tokens_equal(r, h)))
        prev = cur
    return prev[-1]


def words_in(words: list[Word], start: float, end: float) -> list[Word]:
    return [
        w
        for w in words
        if w.end > start - SPAN_SLACK_S
        and w.start < end + SPAN_SLACK_S
        and (w.start + w.end) / 2 >= start - SPAN_SLACK_S
        and (w.start + w.end) / 2 <= end + SPAN_SLACK_S
    ]


def sentence_words(window: list[Word], expected: Sequence[str]) -> list[Word]:
    """The window's words that belong to the sentence.

    Words with no letters or digits are dropped (Whisper can emit "-" as a word).
    The start is the last place where the next two words match the sentence's
    first two; the end is the first place where the previous two match its last
    two. A stutter "The" just before "The vine" is excluded that way.
    """
    ws = [w for w in window if normalize(w.text)]
    toks = [normalize(w.text)[0] for w in ws]

    def match(i: int, want: Sequence[str]) -> bool:
        got = toks[i : i + len(want)]
        return len(got) == len(want) and all(
            tokens_equal(a, b) for a, b in zip(got, want, strict=True)
        )

    head, tail = list(expected[:2]), list(expected[-2:])
    starts = [i for i in range(len(ws)) if match(i, head)]
    ends = [i + len(tail) for i in range(len(ws)) if match(i, tail)]
    a = starts[-1] if starts else 0
    later_ends = [e for e in ends if e > a]
    b = later_ends[0] if later_ends else len(ws)
    return ws[a:b] or ws


def _take(pool: list[str], wanted: Sequence[str]) -> int:
    """Remove each wanted token from pool once (multiset); return how many were found."""
    hits = 0
    for tok in wanted:
        idx = next((i for i, g in enumerate(pool) if tokens_equal(tok, g)), None)
        if idx is not None:
            hits += 1
            pool.pop(idx)
    return hits


def survival(event: dict[str, Any], following: dict[str, Any] | None, words: list[Word]) -> float:
    """Share of a removal event's heard tokens present in the transcript.

    The event and the sentence after it are scored together, and the sentence's own
    words are taken out first: a stutter "The" before "The vine..." only survives if
    the transcript has *two* "the"s there.
    """
    heard = normalize(event["heard"] or "")
    if not heard:
        return 1.0
    end = following["end"] if following else event["end"]
    pool = normalize(" ".join(w.text for w in words_in(words, event["start"], end)))
    if following:
        _take(pool, normalize(following["heard"] or ""))
    return _take(pool, heard) / len(heard)


def detected(event: dict[str, Any], words: list[Word]) -> float:
    """1.0 if the transcript shows the injected text error, else 0.0.

    misread: the substituted word is there and the expected one isn't.
    skip: the missing word is absent.
    """
    got = normalize(" ".join(w.text for w in words_in(words, event["start"], event["end"])))

    def has(word: str) -> bool:
        return any(tokens_equal(word, g) for g in got)

    d = event.get("detail", {})
    if event["type"] == "misread":
        return float(has(d["heard_word"]) and not has(d["expected_word"]))
    return float(not has(d["missing_word"]))


def transcribe(model_name: str, prompt: str | None) -> ModelResult:
    from faster_whisper import WhisperModel

    home = Path(os.environ.get("HILLAUDIO_HOME", "~/.hillaudiobook")).expanduser()
    model = WhisperModel(
        model_name, device="cpu", compute_type="int8", download_root=str(home / "models")
    )
    result = ModelResult(model_name + (" +prompt" if prompt else ""))
    truth = json.loads((FIXTURES / "ground_truth.json").read_text())
    for f in truth["files"]:
        t0 = time.monotonic()
        segments, info = model.transcribe(
            str(FIXTURES / f["name"]),
            language="en",
            word_timestamps=True,
            initial_prompt=prompt,
            beam_size=5,
        )
        words = [
            Word(w.word.strip(), w.start, w.end, w.probability)
            for s in segments
            for w in (s.words or [])
        ]
        result.files.append(FileResult(f["name"], words, time.monotonic() - t0, info.duration))
    return result


def report(results: list[ModelResult]) -> str:
    truth = json.loads((FIXTURES / "ground_truth.json").read_text())
    kinds = ["stutter", "false_start", "commentary", "misread", "skip"]
    titles = ["stutter kept", "false start kept", "commentary kept", "misread seen", "skip seen"]
    lines = [
        "| Model | WER (verbatim) | "
        + " | ".join(titles)
        + " | start err median / p95 (ms) | end err median / p95 (ms)"
        + " | snapped start / end p95 (ms) | RTF |",
        "|---" * (len(kinds) + 6) + "|",
    ]
    masks = {}
    for ft in truth["files"]:
        import soundfile as sf

        audio, sr = sf.read(str(FIXTURES / ft["name"]), dtype="float32")
        env = envelope(audio, sr)
        masks[ft["name"]] = (speech_mask(env), env.fps)
    for r in results:
        errors = ref_len = 0
        surv: dict[str, list[float]] = {k: [] for k in kinds}
        start_err: list[float] = []
        end_err: list[float] = []
        snap_s: list[float] = []
        snap_e: list[float] = []
        for fr, ft in zip(r.files, truth["files"], strict=True):
            ref = [t for e in ft["events"] for t in normalize(e["heard"] or "")]
            hyp = normalize(" ".join(w.text for w in fr.words))
            errors += edit_distance(ref, hyp)
            ref_len += len(ref)
            events = ft["events"]
            for i, e in enumerate(events):
                if e["type"] in ("misread", "skip"):
                    surv[e["type"]].append(detected(e, fr.words))
                elif e["type"] in surv:
                    nxt = events[i + 1] if i + 1 < len(events) else None
                    following = nxt if nxt and nxt["type"] == "read" else None
                    surv[e["type"]].append(survival(e, following, fr.words))
                if e["type"] == "read":
                    ws = sentence_words(
                        words_in(fr.words, e["start"], e["end"]), normalize(e["heard"])
                    )
                    if ws:
                        start_err.append(abs(ws[0].start - e["start"]) * 1000)
                        end_err.append(abs(ws[-1].end - e["end"]) * 1000)
                        mask, fps = masks[ft["name"]]
                        # Neighbouring words bound the snap (see analysis/boundaries.py).
                        i0, i1 = fr.words.index(ws[0]), fr.words.index(ws[-1])
                        lo = fr.words[i0 - 1].start if i0 > 0 else None
                        hi = fr.words[i1 + 1].end if i1 + 1 < len(fr.words) else None
                        s = snap_start(mask, fps, ws[0].start, lower=lo, upper=ws[0].end)
                        en = snap_end(mask, fps, ws[-1].end, lower=ws[-1].start, upper=hi)
                        snap_s.append(abs(s - e["start"]) * 1000)
                        snap_e.append(abs(en - e["end"]) * 1000)
        rtf = sum(f.seconds for f in r.files) / sum(f.duration for f in r.files)

        def stat(xs: list[float]) -> str:
            if not xs:
                return "n/a"
            xs = sorted(xs)
            return f"{statistics.median(xs):.0f} / {xs[min(len(xs) - 1, int(0.95 * len(xs)))]:.0f}"

        cells = [f"{100 * errors / ref_len:.1f}%"]
        cells += [f"{100 * statistics.mean(surv[k]):.0f}%" if surv[k] else "n/a" for k in kinds]

        def p95(xs: list[float]) -> float:
            return sorted(xs)[min(len(xs) - 1, int(0.95 * len(xs)))] if xs else 0.0

        cells += [
            stat(start_err),
            stat(end_err),
            f"{p95(snap_s):.0f} / {p95(snap_e):.0f}",
            f"{rtf:.2f}",
        ]
        lines.append(f"| {r.model} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", nargs="+", default=["small.en", "medium.en", "large-v3-turbo"])
    ap.add_argument(
        "--prompt",
        default=None,
        help="also run each model with this initial_prompt (disfluency priming)",
    )
    ap.add_argument("--dump", type=Path, help="write raw transcripts as JSON here")
    ap.add_argument("--from-dump", type=Path, help="re-score a previous --dump; no models run")
    args = ap.parse_args(argv)

    if args.from_dump:
        raw = json.loads(args.from_dump.read_text())
        loaded = [
            ModelResult(
                m, [FileResult(n, [Word(**w) for w in ws], 0.0, 1.0) for n, ws in files.items()]
            )
            for m, files in raw.items()
        ]
        print(report(loaded))
        return 0

    results: list[ModelResult] = []
    for name in args.models:
        print(f"transcribing with {name}...", file=sys.stderr, flush=True)
        results.append(transcribe(name, None))
        if args.prompt:
            results.append(transcribe(name, args.prompt))
    print(report(results))
    if args.dump:
        args.dump.write_text(
            json.dumps(
                {
                    r.model: {f.name: [w.__dict__ for w in f.words] for f in r.files}
                    for r in results
                },
                indent=1,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
