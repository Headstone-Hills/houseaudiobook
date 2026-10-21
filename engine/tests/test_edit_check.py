"""The edit check (D-061): lines read in full that the edit no longer plays."""

from __future__ import annotations

from typing import Any

import test_analysis_api as base
from fastapi.testclient import TestClient

from hillaudio.analysis.edit_check import Removal, Word, check, heard_fraction
from hillaudio.analysis.issues import ReadingInfo, SentenceInfo

client, factory, wait = base.client, base.factory, base.wait

LINE = SentenceInfo(10, 0, "The fox ran away.", "1", 1, 1)


def words(text: str, start: float, step: float = 0.5) -> list[Word]:
    return [Word(t, start + i * step, start + i * step + 0.4) for i, t in enumerate(text.split())]


def reading(start: float, end: float, heard: str = "The fox ran away.", **kw: Any) -> ReadingInfo:
    return ReadingInfo(
        kw.get("ordinal", 0), kw.get("source", 1), start, end, kw.get("coverage", 1.0),
        kw.get("score", 1.0), heard, tuple(kw.get("diffs", ())),
    )  # fmt: skip


def run(
    readings: list[ReadingInfo],
    ws: list[Word],
    heard: list[tuple[float, float]],
    removals: list[Removal] = (),  # type: ignore[assignment]
) -> tuple[int, list[Any]]:
    return check([LINE], readings, {1: ws}, {1: heard}, {1: list(removals)})


def test_a_line_that_plays_whole_is_fine() -> None:
    assert run([reading(10, 12)], words("The fox ran away.", 10), [(0, 100)]) == (1, [])


def test_a_cut_through_a_line_clips_it() -> None:
    cut = Removal(7, "stutter", 10.9, 11.5)  # takes "ran"
    checked, (lost,) = run(
        [reading(10, 12)], words("The fox ran away.", 10), [(0, 10.9), (11.5, 100)], [cut]
    )
    assert checked == 1
    assert (lost.status, lost.missing, lost.unit_id, lost.page_label) == (
        "clipped",
        ["ran"],
        10,
        "1",
    )
    assert lost.removals == [{"id": 7, "kind": "stutter", "start": 10.9, "end": 11.5}]


def test_cut_short_counts_a_quarter_of_a_word_gone() -> None:
    w = Word("away.", 11.5, 11.9)
    assert heard_fraction(w, [(0, 11.85)]) > 0.75  # an eighth gone: still heard
    _, (lost,) = run([reading(10, 11.9)], words("The fox ran away.", 10), [(0, 11.75)])
    assert lost.missing == ["away."]


def test_the_whole_line_gone_is_removed_unless_another_take_plays() -> None:
    take1, take2 = reading(10, 12), reading(20, 22, score=0.9)
    ws = words("The fox ran away.", 10) + words("The fox ran away.", 20)
    # The chosen take plays: fine, though the other one is cut.
    assert run([take1, take2], ws, [(19, 30)])[1] == []
    _, (lost,) = run([take1, take2], ws, [(0, 9), (13, 19)], [Removal(None, "muted", 19, 30)])
    assert (lost.status, lost.start, lost.missing) == (
        "removed",
        10,
        ["The", "fox", "ran", "away."],
    )


def test_a_stutter_cut_may_take_either_copy_of_the_word() -> None:
    # "The fox fox ran away.": the second "fox" is the extra; the cut takes the first.
    ws = words("The fox fox ran away.", 10)
    extra = {
        "kind": "extra",
        "expected": "",
        "heard": "fox",
        "start": 11.0,
        "end": 11.4,
        "repeat": True,
    }
    r = reading(10, 12.5, "The fox fox ran away.", diffs=[extra])
    assert run([r], ws, [(0, 10.5), (10.9, 100)]) == (1, [])


def test_only_the_words_the_reading_heard_count() -> None:
    # "but" starts the next false start, right at the reading's edge, and is cut.
    ws = words("The fox ran away. but", 10)
    assert run([reading(10, 12.0)], ws, [(0, 12.0)]) == (1, [])
    # A word with no duration (the recognizer gives some) plays if its moment does.
    assert heard_fraction(Word("x", 5.0, 5.0), [(4, 6)]) == 1.0
    assert heard_fraction(Word("x", 5.0, 5.0), [(6, 7)]) == 0.0


def test_lines_never_read_in_full_are_not_the_edits_business() -> None:
    false_start = reading(10, 11, "The fox", coverage=0.5)
    assert run([false_start], words("The fox", 10), []) == (0, [])


# -- through the API, on the fixtures ---------------------------------------------------------


def lost(c: TestClient) -> dict[str, Any]:
    r = c.get("/api/project/edit-check")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_before_any_comparison(client: TestClient) -> None:
    assert lost(client) == {"run_id": None, "stale": False, "checked": 0, "lines": []}


def test_the_automatic_edit_loses_nothing_and_a_slip_is_caught(client: TestClient) -> None:
    base.setup_book(client)
    assert wait(client, client.post("/api/project/analysis", json={}))["status"] == "done"
    # Cleanup and take choices on the fixtures keep every line whole.
    report = lost(client)
    assert report["checked"] == 13 and report["lines"] == []

    # A slip on the timeline: a cut through "He did his best to reach them".
    cmp = client.get("/api/project/comparison").json()
    line = next(s for s in cmp["sentences"] if s["text"].startswith("He did his best"))
    r = next(x for x in line["readings"] if x["coverage"] == 1.0)
    mid = (r["start"] + r["end"]) / 2
    cut = {"source_id": r["source_id"], "start": mid - 0.4, "end": mid + 0.4}
    assert client.post("/api/project/edl/cut", json=cut).status_code == 200
    (found,) = lost(client)["lines"]
    assert found["unit_id"] == line["unit_id"] and found["status"] == "clipped"
    assert found["page_label"] == "1" and found["missing"]
    (why,) = found["removals"]
    assert why["kind"] == "cut"

    # Restoring the cut puts the line back.
    client.post("/api/project/edl/regions/status", json={"ids": [why["id"]], "status": "restored"})
    assert lost(client)["lines"] == []
