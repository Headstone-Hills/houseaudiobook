"""Issues (M4): flubs, unread lines, overlong silences, stable keys, reconciliation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest
import soundfile as sf
from conftest import FIXTURES
from fakeasr import TRUTH, fake_words

from hillaudio.analysis import compare as cmp
from hillaudio.analysis.boundaries import envelope, speech_mask
from hillaudio.analysis.issues import (
    Existing,
    IssueDraft,
    IssueSettings,
    ReadingInfo,
    SentenceInfo,
    likely_misheard,
    reconcile,
    silence_issues,
    text_issues,
)
from hillaudio.text.extract_txt import extract_text
from hillaudio.text.pages import PageSettings, build_page_map
from hillaudio.text.units import build_units

SETTINGS = IssueSettings().validate()


def book() -> list[SentenceInfo]:
    s = PageSettings()
    doc = extract_text((FIXTURES / "manuscript.txt").read_bytes(), s)
    units = build_units(doc, build_page_map(doc, s))
    out = []
    for u in units:
        if u.kind != "sentence":
            continue
        para = units[u.parent] if u.parent is not None else None
        chapter = para.parent if para is not None and para.parent is not None else -1
        out.append(
            SentenceInfo(
                u.ordinal,
                u.ordinal,
                u.text,
                u.page.label,
                u.parent if u.parent is not None else -1,
                chapter,
            )
        )
    return out


SENTENCES = book()
FILES = {f["name"]: i for i, f in enumerate(TRUTH["files"])}


def readings_for(names: list[str], noise: float = 0.0, seed: int = 0) -> list[ReadingInfo]:
    out = []
    for name in names:
        f = TRUTH["files"][FILES[name]]
        words = [cmp.Word(w.text, w.start, w.end, w.prob) for w in fake_words(f, noise, seed=seed)]
        c = cmp.compare([cmp.Sentence(s.ordinal, s.text) for s in SENTENCES], words)
        out += [
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
    return out


ALL = list(FILES)


def keyed(drafts: list[IssueDraft]) -> set[tuple[str, int | None]]:
    return {(d.kind, d.unit_id) for d in drafts}


# ---- flubs and unread lines -----------------------------------------------------------


def test_flubs_with_perfect_asr_precision_and_recall() -> None:
    """The skip (s06) has no clean take: a flub. The misread (s04) has a clean pickup
    in take 2: no issue. Stutters are repeats (cleanup), not flubs."""
    issues = text_issues(SENTENCES, readings_for(ALL), SETTINGS)
    assert keyed(issues) == {("flub", 6)}
    (skip,) = issues
    assert (skip.severity, skip.page_label, skip.source_id) == ("high", "2", 0)
    assert [d["kind"] for d in skip.detail["diffs"]] == ["missing"]
    assert skip.start == pytest.approx(29.67, abs=0.05)


def test_without_the_pickup_the_misread_is_a_flub() -> None:
    issues = text_issues(SENTENCES, readings_for(["ch01_take1.wav", "ch02_take1.wav"]), SETTINGS)
    assert keyed(issues) == {("flub", 4), ("flub", 6)}
    misread = next(i for i in issues if i.unit_id == 4)
    assert misread.detail["diffs"][0]["expected"] == "reach"
    assert misread.severity == "high"  # "reach" -> "sight" isn't a mishearing


def test_unread_lines() -> None:
    issues = text_issues(SENTENCES, readings_for(["ch01_take1.wav"]), SETTINGS)
    missing = sorted(i.unit_id or 0 for i in issues if i.kind == "missing_line")
    assert missing == [7, 8, 9, 10, 11, 12]  # chapter two was never read
    assert all(i.page_label == "3" for i in issues if i.kind == "missing_line")


def test_false_start_only_is_a_flub() -> None:
    rs = [
        r
        for r in readings_for(["ch01_take1.wav"])
        if not (r.sentence_ordinal == 3 and r.coverage == 1.0)
    ]
    (i,) = [i for i in text_issues(SENTENCES, rs, SETTINGS) if i.unit_id == 3]
    assert i.kind == "flub" and not i.detail["complete"]


def test_likely_mishearing_is_low_severity() -> None:
    assert likely_misheard(
        {"kind": "substitution", "expected": "trellis", "heard": "trelleys.", "confidence": 0.9},
        SETTINGS,
    )
    assert not likely_misheard(
        {"kind": "substitution", "expected": "reach", "heard": "sight.", "confidence": 0.9},
        SETTINGS,
    )
    assert likely_misheard(
        {"kind": "substitution", "expected": "reach", "heard": "sight.", "confidence": 0.3},
        SETTINGS,
    )
    assert not likely_misheard({"kind": "missing", "expected": "now", "heard": ""}, SETTINGS)
    # Sounds alike though spelled differently (regression: medium.en heard "left").
    assert likely_misheard(
        {"kind": "substitution", "expected": "laughed", "heard": "left", "confidence": 0.82},
        SETTINGS,
    )


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("laughed", "left", True),
        ("trellis", "trelleys", True),
        ("air", "error", True),
        ("phone", "fone", True),
        ("night", "nite", True),
        ("reach", "sight", False),
        ("grapes", "braids", False),
        ("was", "went", False),
    ],
)
def test_sound_key(a: str, b: str, same: bool) -> None:
    from hillaudio.analysis.issues import sound_key

    assert (sound_key(a) == sound_key(b)) is same


@pytest.mark.parametrize("noise", [0.05, 0.10])
def test_flub_recall_under_asr_noise(noise: float) -> None:
    """Injected text errors are always found; noise words cause extra flags (a
    real recognizer's errors are mostly similar-sounding, so those rank low)."""
    for seed in range(10):
        found = keyed(text_issues(SENTENCES, readings_for(ALL, noise, seed), SETTINGS))
        assert ("flub", 6) in found


# ---- silences ------------------------------------------------------------------------------


def masks() -> dict[str, tuple[Any, float]]:
    out = {}
    for name in ALL:
        audio, sr = sf.read(str(FIXTURES / name), dtype="float32")
        env = envelope(audio, sr)
        out[name] = (speech_mask(env), env.fps)
    return out


MASKS = masks()


def test_silences_precision_and_recall() -> None:
    """Ground truth: 3.5 s at a paragraph break (ch01), 2.6 s mid-paragraph (ch02)."""
    rs = readings_for(ALL)
    found: list[IssueDraft] = []
    for name in ALL:
        m, fps = MASKS[name]
        found += silence_issues(FILES[name], name, m, fps, SENTENCES, rs, SETTINGS)
    truth = [
        (FILES[f["name"]], e)
        for f in TRUTH["files"]
        for e in f["events"]
        if e["type"] == "long_silence"
    ]
    assert len(found) == len(truth) == 2  # precision and recall 1.0
    for (src, e), d in zip(
        sorted(truth, key=lambda x: x[0]),
        sorted(found, key=lambda d: d.source_id or 0),
        strict=True,
    ):
        assert d.source_id == src
        assert d.start == pytest.approx(e["start"], abs=0.05)
        assert d.end == pytest.approx(e["end"], abs=0.05)
    by_src = {d.source_id: d for d in found}
    assert by_src[0].detail["boundary"] == "paragraph" and by_src[0].page_label == "1"
    assert by_src[2].detail["boundary"] == "sentence" and by_src[2].page_label == "3"


def test_silence_thresholds_depend_on_the_boundary() -> None:
    rs = readings_for(ALL)
    m, fps = MASKS["ch01_take1.wav"]
    strict = IssueSettings(silence_sentence_s=2.0, silence_paragraph_s=4.0, silence_chapter_s=6.0)
    assert silence_issues(0, "a", m, fps, SENTENCES, rs, strict) == []  # 3.5 s < 4.0 s
    loose = IssueSettings(silence_sentence_s=0.5, silence_paragraph_s=0.5, silence_chapter_s=0.5)
    assert len(silence_issues(0, "a", m, fps, SENTENCES, rs, loose)) > 3
    with pytest.raises(ValueError):
        IssueSettings(silence_sentence_s=3, silence_paragraph_s=2).validate()


def test_silences_without_alignment_use_the_strictest_threshold() -> None:
    m, fps = MASKS["ch02_take1.wav"]
    (d,) = silence_issues(2, "b", m, fps, [], [], SETTINGS)
    assert d.detail["boundary"] == "unknown" and d.page_label is None


# ---- stable keys and reconciliation -------------------------------------------------------


def test_keys_are_stable_across_runs_and_page_remaps() -> None:
    a = text_issues(SENTENCES, readings_for(ALL), SETTINGS)
    b = text_issues(SENTENCES, readings_for(ALL, 0.0, seed=7), SETTINGS)
    assert [i.stable_key for i in a] == [i.stable_key for i in b]
    remapped = [
        SentenceInfo(
            s.unit_id + 100, s.ordinal, s.text, f"approx. {s.ordinal}", s.paragraph, s.chapter
        )
        for s in SENTENCES
    ]
    c = text_issues(remapped, readings_for(ALL), SETTINGS)
    assert [i.stable_key for i in a] == [i.stable_key for i in c]


def draft(key: str, kind: str = "flub") -> IssueDraft:
    return IssueDraft(kind, "high", key, 0, 1.0, 2.0, 1, "1", "s", "s", "h")  # type: ignore[arg-type]


def test_reconcile_states() -> None:
    existing = [
        Existing("open-still", "flub", "open", None),
        Existing("open-gone", "flub", "open", None),
        Existing("dismissed", "flub", "dismissed", "user"),
        Existing("resolved-back", "flub", "resolved", "user"),
        Existing("auto-back", "flub", "resolved", "auto"),
        Existing("silence-open", "silence", "open", None),
        Existing("resolved-gone", "flub", "resolved", "user"),
    ]
    drafts = [draft(k) for k in ("open-still", "dismissed", "resolved-back", "auto-back", "fresh")]
    updates = {u.stable_key: u for u in reconcile(existing, drafts, ["flub", "missing_line"])}
    assert {k: (u.status, u.change) for k, u in updates.items()} == {
        "open-still": ("open", "still_open"),
        "dismissed": ("dismissed", "still_open"),  # the user's decision is kept
        "resolved-back": ("open", "regressed"),
        "auto-back": ("open", "regressed"),
        "fresh": ("open", "new"),
        "open-gone": ("resolved", "auto_resolved"),
    }
    assert updates["open-gone"].decided_by == "auto"
    # Silences weren't checked this run, and a resolved issue that's gone stays as it is.
    assert "silence-open" not in updates and "resolved-gone" not in updates
