"""Cleanup candidates (M5): stutters, false starts and commentary on the fixtures.

Ground truth: each cleanup event's span runs from the unwanted speech's onset to
the next speech's onset, which is exactly the cut that should be made.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest
import soundfile as sf
from conftest import FIXTURES
from fakeasr import TRUTH, fake_words
from test_issues import FILES, SENTENCES

from hillaudio.analysis import compare as cmp
from hillaudio.analysis.boundaries import envelope, speech_mask
from hillaudio.analysis.cleanup import (
    CleanupSettings,
    Cut,
    Gap,
    TWord,
    cuts,
    hint_score,
    repeat_cuts,
)
from hillaudio.analysis.issues import ReadingInfo

KINDS = ("stutter", "false_start", "commentary")
SETTINGS = CleanupSettings()


def masks() -> dict[str, tuple[Any, float]]:
    out = {}
    for f in TRUTH["files"]:
        audio, sr = sf.read(str(FIXTURES / f["name"]), dtype="float32")
        env = envelope(audio, sr)
        out[f["name"]] = (speech_mask(env), env.fps)
    return out


MASKS = masks()


def detect(name: str, noise: float = 0.0, seed: int = 0, drop: bool = False) -> list[Cut]:
    f = TRUTH["files"][FILES[name]]
    tw = fake_words(f, noise, drop, seed)
    words = [cmp.Word(w.text, w.start, w.end, w.prob) for w in tw]
    m, fps = MASKS[name]
    c = cmp.compare([cmp.Sentence(s.ordinal, s.text) for s in SENTENCES], words, m, fps)
    readings = [
        ReadingInfo(
            r.sentence_key,
            FILES[name],
            r.start,
            r.end,
            r.coverage,
            r.score,
            r.heard,
            tuple(asdict(d) for d in r.diffs),
        )
        for r in c.readings
    ]
    gaps = [Gap(u.start, u.end, u.text) for u in c.unaligned]
    return cuts(
        FILES[name],
        name,
        SENTENCES,
        readings,
        gaps,
        [TWord(w.text, w.start, w.end) for w in tw],
        m,
        fps,
        SETTINGS,
    )


def truth(kind: str) -> list[tuple[str, dict[str, Any]]]:
    return [(f["name"], e) for f in TRUTH["files"] for e in f["events"] if e["type"] == kind]


def score(found: dict[str, list[Cut]], kind: str, tol: float = 0.06) -> tuple[float, float]:
    want = truth(kind)
    got = [(n, c) for n, cs in found.items() for c in cs if c.kind == kind]
    hits = sum(
        any(
            n == wn and abs(c.start - e["start"]) < tol and abs(c.end - e["end"]) < tol
            for n, c in got
        )
        for wn, e in want
    )
    precision = hits / len(got) if got else 1.0
    recall = hits / len(want) if want else 1.0
    return precision, recall


def speech_cut(found: dict[str, list[Cut]]) -> list[tuple[str, Cut]]:
    """Cuts that take any part of a line that must stay (read/misread/skip)."""
    bad = []
    for f in TRUTH["files"]:
        keep = [e for e in f["events"] if e["type"] in ("read", "misread", "skip")]
        for c in found.get(f["name"], []):
            if any(c.start < e["end"] - 0.06 and c.end > e["start"] + 0.06 for e in keep):
                bad.append((f["name"], c))
    return bad


def test_perfect_asr_precision_and_recall_per_kind() -> None:
    found = {f["name"]: detect(f["name"]) for f in TRUTH["files"]}
    report = {k: score(found, k) for k in KINDS}
    print("cleanup P/R:", report)
    assert report == {k: (1.0, 1.0) for k in KINDS}
    assert speech_cut(found) == []
    by_kind = {c.kind: c for cs in found.values() for c in cs}
    assert by_kind["commentary"].confidence >= SETTINGS.auto_apply  # "hold on", "let me"
    assert by_kind["false_start"].text == "He did his best"


@pytest.mark.parametrize("noise", [0.05, 0.10])
def test_noisy_asr_never_cuts_read_lines(noise: float) -> None:
    """Noise words can make extra candidates, but never at the cost of a line that
    must stay; the injected events are still found in most seeds."""
    recall = {k: 0.0 for k in KINDS}
    seeds = 10
    for seed in range(seeds):
        found = {f["name"]: detect(f["name"], noise, seed) for f in TRUTH["files"]}
        assert speech_cut(found) == [], seed
        for k in KINDS:
            recall[k] += score(found, k)[1] / seeds
    print(f"noise {noise} recall:", recall)
    # A stutter is lost when the recognizer mishears the word right after it: then
    # there is no repeat in the text (or the alignment ties the other way). That
    # happens in 2 of 10 seeds here; the cost is a stutter left in, never a line cut.
    assert recall["stutter"] >= 0.75, recall
    assert recall["false_start"] >= 0.9 and recall["commentary"] >= 0.9, recall


def test_settings_turn_kinds_off() -> None:
    f = "ch01_take1.wav"
    off = CleanupSettings(stutters=False, commentary=False)
    tw = fake_words(TRUTH["files"][FILES[f]])
    assert all(c.kind == "false_start" for c in _with(f, off, tw))
    with pytest.raises(ValueError):
        CleanupSettings(auto_apply=1.5).validate()


def _with(name: str, s: CleanupSettings, tw: Any) -> list[Cut]:
    words = [cmp.Word(w.text, w.start, w.end, w.prob) for w in tw]
    m, fps = MASKS[name]
    c = cmp.compare([cmp.Sentence(x.ordinal, x.text) for x in SENTENCES], words, m, fps)
    rs = [
        ReadingInfo(
            r.sentence_key,
            0,
            r.start,
            r.end,
            r.coverage,
            r.score,
            r.heard,
            tuple(asdict(d) for d in r.diffs),
        )
        for r in c.readings
    ]
    gaps = [Gap(u.start, u.end, u.text) for u in c.unaligned]
    return cuts(
        0, name, SENTENCES, rs, gaps, [TWord(w.text, w.start, w.end) for w in tw], m, fps, s
    )


def test_stable_keys() -> None:
    a = detect("ch01_take1.wav")
    b = detect("ch01_take1.wav", 0.0, seed=3)
    assert [c.stable_key for c in a] == [c.stable_key for c in b]
    assert len({c.stable_key for c in a}) == len(a)


def test_hints() -> None:
    assert hint_score("Hold on, let me get some water.") == 2
    assert hint_score("Sorry, from the top.") == 2
    assert hint_score("The fox ran home.") == 0


def test_transcript_only_repeats() -> None:
    def w(text: str) -> list[TWord]:
        return [TWord(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(text.split())]

    (c,) = repeat_cuts(0, "k", w("so the the vine was high"), None, 100.0)
    assert (c.kind, c.text, c.start, c.end) == ("stutter", "the", 0.5, 1.0)
    (c,) = repeat_cuts(0, "k", w("he went in the- in the house"), None, 100.0)
    assert (c.text, c.start, c.end) == ("in the-", 1.0, 2.0)
    assert repeat_cuts(0, "k", w("that that is fine"), None, 100.0)[0].confidence < 0.9
    assert repeat_cuts(0, "k", w("no repeats at all here"), None, 100.0) == []
    # The fixture stutters, without a manuscript.
    f = TRUTH["files"][FILES["ch01_take1.wav"]]
    m, fps = MASKS["ch01_take1.wav"]
    words = [TWord(x.text, x.start, x.end) for x in fake_words(f)]
    (s,) = repeat_cuts(0, "k", words, m, fps)
    e = truth("stutter")[0][1]
    assert abs(s.start - e["start"]) < 0.06 and abs(s.end - e["end"]) < 0.06


def test_an_aside_with_no_duration_is_not_cut() -> None:
    """Regression: the recognizer gave an aside's 8 words one timestamp, right where
    the next line starts. Snapping the cut's start to the speech onset carried it
    past its end, and storing the backwards cut failed the whole analysis
    ("CHECK constraint failed: end_sample >= start_sample")."""
    import numpy as np

    from hillaudio.analysis.issues import SentenceInfo

    fps = 100.0
    mask = np.zeros(400, dtype=bool)
    mask[160:300] = True  # silence until 1.6 s, then the line
    line = SentenceInfo(0, 0, "A large man with a beard.", "1", 1, 1)
    reading = ReadingInfo(0, 0, 1.0, 3.0, 1.0, 1.0, "a large man with a beard", ())
    aside = Gap(1.0, 1.0, "Well, I'm gonna just do this in")
    words = [TWord(t, 1.0, 1.0) for t in aside.text.split()]
    said = ["a", "large", "man", "with", "a", "beard"]
    words += [TWord(t, 1.6 + 0.2 * i, 1.75 + 0.2 * i) for i, t in enumerate(said)]
    found = cuts(0, "k", [line], [reading], [aside], words, mask, fps, SETTINGS)
    assert [c for c in found if c.kind == "commentary"] == []
    assert all(c.end > c.start for c in found)
