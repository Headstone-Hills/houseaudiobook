"""Compare to Text (M3): alignment of transcripts to the manuscript.

Ground-truth tests use a *fake transcriber* built from the fixture ground truth:
each event's heard words spread evenly over its true time span. With perfect ASR
every injected event must come out exactly right; with seeded ASR noise the
alignment must still find every sentence and every injected text error.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from conftest import FIXTURES

import make_fixtures as mf
from hillaudio.analysis.compare import Comparison, Sentence, SentenceReading, Word, compare
from hillaudio.text.extract_txt import extract_text
from hillaudio.text.pages import PageSettings, build_page_map
from hillaudio.text.units import build_units

TRUTH: dict[str, Any] = json.loads((FIXTURES / "ground_truth.json").read_text())
GAP_AFTER = {
    "stutter": mf.STUTTER_GAP_S,
    "false_start": mf.RESTART_GAP_S,
    "commentary": mf.COMMENTARY_GAP_S,
}
NOISE_WORDS = ["apple", "river", "quickly", "blue", "seven", "window", "under", "garden", "silver"]


def manuscript() -> list[Sentence]:
    s = PageSettings()
    doc = extract_text((FIXTURES / "manuscript.txt").read_bytes(), s)
    units = build_units(doc, build_page_map(doc, s))
    return [Sentence(u.ordinal, u.text) for u in units if u.kind == "sentence"]


SENTENCES = manuscript()
SID = {f"s{i:02d}": i for i in range(len(SENTENCES))}  # ground-truth id -> key


def fake_words(
    file: dict[str, Any], noise: float = 0.0, drop_stutters: bool = False, seed: int = 0
) -> list[Word]:
    rng = random.Random(seed)
    words: list[Word] = []
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
                w = rng.choice(NOISE_WORDS)  # an ASR error
            words.append(Word(w, e["start"] + i * step, e["start"] + (i + 1) * step, 0.95))
    return words


def run(name: str, **kw: Any) -> Comparison:
    file = next(f for f in TRUTH["files"] if f["name"] == name)
    return compare(SENTENCES, fake_words(file, **kw))


def of(c: Comparison, sid: str) -> list[SentenceReading]:
    return [r for r in c.readings if r.sentence_key == SID[sid]]


# ---- perfect ASR: every injected event exactly -------------------------------------


def test_clean_reads_are_perfect_and_timed() -> None:
    for f in TRUTH["files"]:
        c = compare(SENTENCES, fake_words(f))
        for e in f["events"]:
            if e["type"] != "read":
                continue
            complete = [
                r
                for r in of(c, e["sentence_id"])
                if r.complete and abs(r.start - e["start"]) < 0.05
            ]
            assert len(complete) == 1, (f["name"], e)
            r = complete[0]
            assert abs(r.end - e["end"]) < 0.05
            assert [d.kind for d in r.diffs if d.kind != "extra"] == []


def test_misread_is_a_substitution_with_text() -> None:
    (r,) = of(run("ch01_take1.wav"), "s04")
    assert [(d.kind, d.expected, d.heard) for d in r.diffs] == [("substitution", "reach", "sight.")]
    assert r.score == pytest.approx(12 / 13)


def test_skipped_word_is_missing() -> None:
    (r,) = of(run("ch01_take1.wav"), "s06")
    assert [(d.kind, d.expected, d.heard) for d in r.diffs] == [("missing", "now", "")]


def test_false_start_is_a_partial_reading_before_the_full_one() -> None:
    rs = sorted(of(run("ch01_take1.wav"), "s03"), key=lambda r: r.start)
    assert len(rs) == 2
    partial, full = rs
    assert partial.coverage == pytest.approx(4 / 14) and partial.heard == "He did his best"
    assert full.complete and full.score == 1.0
    assert partial.end <= full.start


def test_stutter_is_an_extra_word_on_the_next_sentence() -> None:
    for name, sid in (("ch01_take1.wav", "s02"), ("ch02_take1.wav", "s09")):
        (r,) = of(run(name), sid)
        assert [(d.kind, d.expected, d.heard) for d in r.diffs] == [("extra", "", "The")]
        assert r.complete


def test_commentary_is_unaligned_and_not_blamed_on_a_sentence() -> None:
    c = run("ch01_take1.wav")
    assert [u.text for u in c.unaligned] == ["Hold on, let me get some water."]
    (s04,) = of(c, "s04")
    (s05,) = of(c, "s05")
    assert all(d.kind != "extra" for d in s04.diffs + s05.diffs)


def test_takes_across_and_within_files() -> None:
    s04 = of(run("ch01_take1.wav"), "s04") + of(run("ch01_take2.wav"), "s04")
    assert sorted(r.score for r in s04) == [pytest.approx(12 / 13), 1.0]
    s10 = of(run("ch02_take1.wav"), "s10")
    assert len(s10) == 2 and all(r.complete and r.score == 1.0 for r in s10)


def test_flub_detection_precision_recall_perfect_asr() -> None:
    """Report: sentences whose reading has a text error vs the injected errors."""
    injected = {
        (f["name"], e["sentence_id"])
        for f in TRUTH["files"]
        for e in f["events"]
        if e["type"] in ("misread", "skip")
    }
    flagged = set()
    for f in TRUTH["files"]:
        c = compare(SENTENCES, fake_words(f))
        for r in c.readings:
            if r.complete and any(
                d.kind in ("missing", "substitution", "transposition", "reordered") for d in r.diffs
            ):
                sid = next(k for k, v in SID.items() if v == r.sentence_key)
                flagged.add((f["name"], sid))
    assert flagged == injected  # precision 1.0, recall 1.0


# ---- noisy ASR: robustness --------------------------------------------------------------


@pytest.mark.parametrize("noise", [0.05, 0.10, 0.20])
def test_alignment_survives_asr_noise(noise: float) -> None:
    """Every read sentence is still found at the right time, and every injected text
    error still shows up, with Whisper-like noise: random wrong words, and stutters
    dropped (as unprompted Whisper does)."""
    found = expected = errors_found = 0
    for seed in range(20):
        for f in TRUTH["files"]:
            c = compare(SENTENCES, fake_words(f, noise, drop_stutters=True, seed=seed))
            for e in f["events"]:
                if e["type"] in ("read", "misread", "skip"):
                    expected += 1
                    hits = [
                        r
                        for r in of(c, e["sentence_id"])
                        if r.coverage >= 0.8 and abs(r.start - e["start"]) < 0.6
                    ]
                    found += bool(hits)
                    if e["type"] != "read" and hits:
                        errors_found += any(
                            d.kind in ("missing", "substitution") for d in hits[0].diffs
                        )
    assert found / expected >= (0.99 if noise <= 0.10 else 0.95)
    # Recall of injected text errors: the misread and the skip, in all 20 seeds.
    assert errors_found == 2 * 20


# ---- unit cases ---------------------------------------------------------------------------


def words(text: str, probs: dict[int, float] | None = None) -> list[Word]:
    return [
        Word(w, i * 0.4, i * 0.4 + 0.3, (probs or {}).get(i, 0.95))
        for i, w in enumerate(text.split())
    ]


BOOK = [
    Sentence(0, "The quick brown fox jumps over the lazy dog."),
    Sentence(1, "It had twelve apples in the old wooden barrel."),
    Sentence(2, "Go now."),
    Sentence(3, "Then the long winter finally came to the valley."),
]


def test_transposition_and_numerals() -> None:
    c = compare(
        BOOK,
        words(
            "The quick brown fox jumps over the lazy dog. "
            "It had 12 apples in the wooden old barrel."
        ),
    )
    (r,) = [r for r in c.readings if r.sentence_key == 1]
    assert [(d.kind, d.expected, d.heard) for d in r.diffs] == [
        ("transposition", "old wooden", "wooden old")
    ]


def test_reordered_phrase() -> None:
    c = compare(
        BOOK,
        words(
            "The quick brown fox jumps over the lazy dog. "
            "It had twelve apples in the old wooden barrel. Go now. "
            "Then finally the long winter came to the valley."
        ),
    )
    (r,) = [r for r in c.readings if r.sentence_key == 3]
    assert [(d.kind, d.expected, d.heard) for d in r.diffs] == [
        ("reordered", "", "finally"),
        ("reordered", "finally", ""),
    ]


def test_skipped_short_sentence_is_missing_not_paired_with_its_neighbour() -> None:
    """Regression: a tie in the DP paired "now" (skipped line) with "then" (next line)."""
    c = compare(
        BOOK,
        words(
            "It had twelve apples in the old wooden barrel. "
            "Then the long winter finally came to the valley."
        ),
    )
    by_key = {r.sentence_key: r for r in c.readings}
    assert by_key[3].score == 1.0  # untouched by the skip before it
    if 2 in by_key:  # if reported at all, as every word missing
        assert [d.kind for d in by_key[2].diffs] == ["missing"]


def test_low_confidence_match_is_a_possible_mispronunciation() -> None:
    c = compare(BOOK, words("The quick brown fox jumps over the lazy dog.", {2: 0.2}))
    (r,) = c.readings
    assert [(d.kind, d.expected) for d in r.diffs] == [("mispronunciation", "brown")]
    assert r.score == 1.0  # a suspicion, not an error


def test_short_pickup_found_by_fallback() -> None:
    """A two-word line read on its own can't form a 3-token anchor."""
    c = compare(BOOK, words("Go now."))
    (r,) = c.readings
    assert r.sentence_key == 2 and r.complete and r.score == 1.0


def test_short_aside_between_sentences_is_unaligned() -> None:
    c = compare(
        BOOK,
        words(
            "The quick brown fox jumps over the lazy dog. Sorry. "
            "It had twelve apples in the old wooden barrel."
        ),
    )
    assert [u.text for u in c.unaligned] == ["Sorry."]
    assert all(r.score == 1.0 for r in c.readings)


def test_nothing_matches() -> None:
    c = compare(BOOK, words("completely unrelated chatter about the weather today"))
    assert c.readings == [] and len(c.unaligned) == 1
