"""Take groups (M6): detection, ranking and suggestions on the fixtures."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

import pytest
import soundfile as sf
from conftest import FIXTURES
from fakeasr import TRUTH, fake_words
from test_issues import FILES, SENTENCES

from hillaudio.analysis import compare as cmp
from hillaudio.analysis.boundaries import envelope, speech_mask
from hillaudio.analysis.issues import ReadingInfo
from hillaudio.analysis.takes import Level, TakeGroup, groups

NAMES = [f["name"] for f in TRUTH["files"]]
LABEL = [n[:-4] for n in NAMES]  # "ch01_take1"


def load() -> tuple[dict[int, Level], dict[int, float]]:
    levels, durations = {}, {}
    for name in NAMES:
        audio, sr = sf.read(str(FIXTURES / name), dtype="float32")
        env = envelope(audio, sr)
        levels[FILES[name]] = Level(env.db, speech_mask(env), env.fps)
        durations[FILES[name]] = len(audio) / sr
    return levels, durations


LEVELS, DURATIONS = load()


def detect(noise: float = 0.0, seed: int = 0, **over: Any) -> list[TakeGroup]:
    readings: list[ReadingInfo] = []
    onsets: dict[int, list[float]] = {}
    for name in NAMES:
        sid = FILES[name]
        tw = fake_words(TRUTH["files"][sid], noise, False, seed)
        onsets[sid] = [w.start for w in tw]
        lv = LEVELS[sid]
        words = [cmp.Word(w.text, w.start, w.end, w.prob) for w in tw]
        c = cmp.compare(
            [cmp.Sentence(s.ordinal, s.text) for s in SENTENCES], words, lv.speech, lv.fps
        )
        readings += [
            ReadingInfo(
                r.sentence_key,
                sid,
                r.start,
                r.end,
                r.coverage,
                r.score,
                r.heard,
                tuple(asdict(d) for d in r.diffs),
            )
            for r in c.readings
        ]
    args: dict[str, Any] = {
        "source_keys": {FILES[n]: n for n in NAMES},
        "source_order": {FILES[n]: FILES[n] for n in NAMES},
        "durations": DURATIONS,
        "onsets": onsets,
        "cut": {},
        "levels": LEVELS,
    } | over
    return groups(SENTENCES, readings, **args)


def truth_groups() -> dict[int, list[tuple[str, float]]]:
    """Sentences read more than once in full (read/misread/skip), by ordinal."""
    out: dict[int, list[tuple[str, float]]] = {}
    for f in TRUTH["files"]:
        for e in f["events"]:
            if e["type"] in ("read", "misread", "skip"):
                out.setdefault(int(e["sentence_id"][1:]), []).append((f["name"], e["start"]))
    return {k: v for k, v in out.items() if len(v) > 1}


def test_groups_precision_recall_and_suggestions() -> None:
    gs = detect()
    truth = truth_groups()
    found = {g.ordinal: g for g in gs}
    print("take groups found:", sorted(found), "truth:", sorted(truth))
    assert set(found) == set(truth) == {4, 10}  # precision and recall 1.0
    for o, g in found.items():
        assert sorted((NAMES[t.source_id], round(t.start, 1)) for t in g.takes) == sorted(
            (n, round(s, 1)) for n, s in truth[o]
        )
    # The misread take loses to the clean pickup, confidently.
    s04 = found[4]
    assert LABEL[s04.takes[0].source_id] == "ch01_take2" and s04.confident
    assert s04.reasons == ["fewer text errors"]
    assert s04.takes[1].hard_errors == 1
    # Two clean reads: the later one, but the user should listen.
    s10 = found[10]
    assert s10.takes[0].start > s10.takes[1].start and not s10.confident
    assert s10.reasons == ["the later take"]


def test_take_spans_run_to_the_next_speech() -> None:
    found = {g.ordinal: g for g in detect()}
    misread = next(t for t in found[4].takes if LABEL[t.source_id] == "ch01_take1")
    assert misread.span_end == pytest.approx(22.96, abs=0.06)  # the commentary's onset
    pickup = next(t for t in found[4].takes if LABEL[t.source_id] == "ch01_take2")
    assert pickup.span_end == pytest.approx(DURATIONS[1])  # nothing follows: the file's end
    first = min(found[10].takes, key=lambda t: t.start)
    assert first.span_end == pytest.approx(16.05, abs=0.06)  # the retake's onset


def test_false_starts_and_cut_readings_are_not_takes() -> None:
    assert 3 not in {g.ordinal for g in detect()}  # s03's false start is a fragment
    # If cleanup had cut the second s10 read, s10 has one take left: no group.
    s10 = next(g for g in detect() if g.ordinal == 10)
    second = max(s10.takes, key=lambda t: t.start)
    gs = detect(cut={second.source_id: [(second.start, second.span_end)]})
    assert 10 not in {g.ordinal for g in gs}


def test_loudness_breaks_ties() -> None:
    s10 = next(g for g in detect() if g.ordinal == 10)
    later = max(s10.takes, key=lambda t: t.start)
    quiet = {**LEVELS}
    lv = LEVELS[later.source_id]
    db = lv.db.copy()
    a, b = int(later.start * lv.fps), int(later.end * lv.fps) + 1
    db[a:b] -= 12.0  # the later take recorded 12 dB quieter
    quiet[later.source_id] = replace(lv, db=db)
    g = next(g for g in detect(levels=quiet) if g.ordinal == 10)
    assert g.takes[0].start < later.start and g.reasons == ["more even loudness"]


@pytest.mark.parametrize("noise", [0.05, 0.10])
def test_noisy_asr(noise: float) -> None:
    """Noise never invents groups, and both real groups are still found. The
    ranking follows the evidence: whenever the pickup has fewer real errors than
    the misread take, it wins. (Fake noise words are random, so they count as
    real errors; a real recognizer's errors are mostly sound-alikes, which rank
    as soft. The win rate is reported, not asserted.)"""
    wins = found_both = 0
    for seed in range(10):
        found = {g.ordinal: g for g in detect(noise, seed)}
        assert set(found) <= {4, 10}, (seed, set(found))
        found_both += set(found) == {4, 10}
        if 4 in found:
            pickup = next(t for t in found[4].takes if LABEL[t.source_id] == "ch01_take2")
            misread = next(t for t in found[4].takes if LABEL[t.source_id] == "ch01_take1")
            if pickup.hard_errors < misread.hard_errors:
                assert found[4].takes[0] is pickup, seed
            wins += found[4].takes[0] is pickup
    print(f"noise {noise}: both groups in {found_both}/10, pickup suggested in {wins}/10")
    assert found_both >= 9


def test_stable_keys() -> None:
    a, b = detect(), detect(0.0, seed=5)
    assert [g.stable_key for g in a] == [g.stable_key for g in b]
    assert [[t.stable_key for t in g.takes] for g in a] == [
        [t.stable_key for t in g.takes] for g in b
    ]
