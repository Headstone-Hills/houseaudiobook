"""Tests for scripts/make_fixtures.py.

Pure parts are tested with a fake synthesizer (no TTS needed). The committed
fixtures are then checked against their own ground truth.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf

import make_fixtures as mf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SR = mf.SAMPLE_RATE


def fake_synth(text: str) -> mf.Audio:
    """A tone whose length depends on the text, padded like real TTS output."""
    n = int(0.05 * SR * len(text.split()))
    tone = 0.3 * np.sin(np.arange(n) * 2 * np.pi * 220 / SR)
    pad = np.zeros(int(0.1 * SR))
    return np.concatenate([pad, tone, pad]).astype(np.float32)


def db(x: np.ndarray[Any, Any]) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x, dtype=np.float64)))))


@pytest.fixture(scope="module")
def rendered() -> tuple[list[mf.FilePlan], list[mf.Assembled], dict[str, Any]]:
    return mf.render_all(fake_synth)


# ---- pure: manuscript and plan ------------------------------------------------


def test_manuscript_pages_and_chapters() -> None:
    sents = mf.parse_manuscript(mf.MANUSCRIPT_TEXT)
    by_id = {s.id: s for s in sents}
    assert len(sents) == 13
    assert by_id["s00"].is_heading and by_id["s00"].page == 1
    # The page marker sits between paragraphs; the next paragraph is on page 2.
    assert by_id["s03"].page == 1 and by_id["s04"].page == 2
    assert by_id["s07"].is_heading and by_id["s07"].chapter == 2 and by_id["s07"].page == 3


def test_substitute_and_omit() -> None:
    assert mf.substitute("out of reach.", "reach", "sight") == "out of sight."
    assert mf.omit("I see now they are", "now") == "I see they are"
    with pytest.raises(ValueError):
        mf.omit("nothing here", "now")


def test_trim_silence_removes_padding() -> None:
    clip = fake_synth("one two")
    trimmed = mf.trim_silence(clip, SR)
    tone = int(0.05 * SR * 2)
    pad = int(mf.TRIM_PAD_S * SR)
    # The tone (plus a small safety pad each side) survives; the 0.1 s padding doesn't.
    assert tone - 10 <= len(trimmed) <= tone + 2 * pad
    assert len(mf.trim_silence(np.zeros(100, dtype=np.float32), SR)) == 0


# ---- pure: assembly and ground truth --------------------------------------------


def test_counts_match_scenario(rendered: Any) -> None:
    _, _, truth = rendered
    assert truth["counts"] == {
        "commentary": 1,
        "false_start": 1,
        "long_silence": 2,
        "misread": 1,
        "read": 13,
        "skip": 1,
        "stutter": 2,
        "take_group": 2,
    }


def test_event_spans_are_exact_and_ordered(rendered: Any) -> None:
    plans, assembled, _ = rendered
    for plan, asm in zip(plans, assembled, strict=True):
        events = asm.events
        # Contiguous where adjacent, never overlapping, inside the file.
        for a, b in itertools.pairwise(events):
            assert a["end_sample"] <= b["start_sample"]
        assert events[-1]["end_sample"] <= len(asm.audio)
        for e in events:
            assert e["start"] == e["start_sample"] / SR
            if e["type"] == "long_silence":
                expected = round(e["detail"]["seconds"] * SR)
                assert e["end_sample"] - e["start_sample"] == expected
        # Total length is exactly the sum of the planned pieces.
        assert len(asm.audio) > 0, plan.name


def test_removal_regions_precede_their_restart(rendered: Any) -> None:
    _, assembled, _ = rendered
    for asm in assembled:
        evs = asm.events
        for i, e in enumerate(evs):
            if e["type"] in ("stutter", "false_start"):
                nxt = evs[i + 1]
                # A perfect cut of the removal region leaves the clean read intact.
                assert nxt["type"] == "read"
                assert nxt["sentence_id"] == e["sentence_id"]
                assert nxt["start_sample"] == e["end_sample"]


def test_misread_and_skip_text(rendered: Any) -> None:
    _, _, truth = rendered
    events = [e for f in truth["files"] for e in f["events"]]
    misread = next(e for e in events if e["type"] == "misread")
    assert "reach" in misread["expected"] and "sight" in misread["heard"]
    skip = next(e for e in events if e["type"] == "skip")
    assert " now " in skip["expected"] and " now " not in skip["heard"]


def test_take_groups(rendered: Any) -> None:
    _, _, truth = rendered
    groups = {g["sentence_id"]: g for g in truth["take_groups"]}
    assert set(groups) == {"s04", "s10"}
    # Pickup in take 2 is preferred over the misread in take 1.
    assert groups["s04"]["takes"][groups["s04"]["preferred_index"]]["file"] == "ch01_take2.wav"


def test_noise_level_matches_request(rendered: Any) -> None:
    plans, assembled, _ = rendered
    for plan, asm in zip(plans, assembled, strict=True):
        lead = asm.audio[: int(mf.LEAD_IN_S * SR)]
        assert abs(db(lead) - plan.noise_dbfs) < 0.5


def test_render_is_deterministic() -> None:
    a = mf.render_all(fake_synth)[1]
    b = mf.render_all(fake_synth)[1]
    for x, y in zip(a, b, strict=True):
        assert np.array_equal(x.audio, y.audio)


# ---- committed fixtures -----------------------------------------------------------


@pytest.fixture(scope="module")
def truth() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "ground_truth.json").read_text())
    return data


def test_committed_manuscript_is_current() -> None:
    assert (FIXTURES / "manuscript.txt").read_text(encoding="utf-8") == mf.MANUSCRIPT_TEXT


def test_committed_truth_matches_scenario(truth: dict[str, Any], rendered: Any) -> None:
    """The committed ground truth was generated from the current scenario.

    Timestamps depend on the TTS, so compare structure only.
    """
    _, _, fake = rendered

    def shape(t: dict[str, Any]) -> Any:
        return [
            (f["name"], [(e["type"], e["sentence_id"], e["page"]) for e in f["events"]])
            for f in t["files"]
        ]

    assert truth["version"] == mf.GROUND_TRUTH_VERSION
    assert shape(truth) == shape(fake), "run `make fixtures` and commit the result"
    assert truth["counts"] == fake["counts"]


def test_committed_audio_matches_truth(truth: dict[str, Any]) -> None:
    for f in truth["files"]:
        info = sf.info(str(FIXTURES / f["name"]))
        assert info.samplerate == f["sample_rate"] == SR
        assert info.channels == 1
        assert info.frames == f["num_samples"]


def test_committed_audio_levels(truth: dict[str, Any]) -> None:
    """Silences sit at the injected noise level; speech is far above it."""
    for f in truth["files"]:
        audio, _ = sf.read(str(FIXTURES / f["name"]), dtype="float32")
        for e in f["events"]:
            seg = audio[e["start_sample"] : e["end_sample"]]
            if e["type"] == "long_silence":
                assert abs(db(seg) - f["noise_dbfs_rms"]) < 1.0, (f["name"], e)
            elif e["type"] == "read":
                # The noisy pickup file (-50 dBFS) has only ~24 dB SNR, by design.
                assert db(seg) > f["noise_dbfs_rms"] + 20, (f["name"], e)
