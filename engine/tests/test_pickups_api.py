"""Pickups (D-062): re-recorded lines go over the flubs, and nothing else of a
pickup file plays."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import test_analysis_api as base
from conftest import FIXTURES
from fastapi.testclient import TestClient

client, factory, wait = base.client, base.factory, base.wait


def add(c: TestClient, names: list[str], role: str = "narration") -> None:
    paths = [str(FIXTURES / n) for n in names]
    r = c.post("/api/project/sources/import", json={"paths": paths, "role": role})
    assert wait(c, r)["status"] == "done"


def analyze(c: TestClient) -> None:
    assert wait(c, c.post("/api/project/analysis", json={}))["status"] == "done"


def manuscript(c: TestClient) -> None:
    body = {"source": {"path": str(FIXTURES / "manuscript.txt")}}
    assert wait(c, c.post("/api/project/manuscript", json=body))["status"] == "done"


def sources(c: TestClient) -> dict[str, dict[str, Any]]:
    return {s["label"]: s for s in c.get("/api/project").json()["sources"]}


def clips(c: TestClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = c.get("/api/project/timeline").json()["clips"]
    return [x for x in out if x["track_id"] == 1]


def issues(c: TestClient) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = c.get("/api/project/issues").json()["issues"]
    return out


def test_a_pickup_goes_over_the_flub(client: TestClient) -> None:
    add(client, ["ch01_take1.wav", "ch02_take1.wav"])
    manuscript(client)
    analyze(client)
    reach = "But it was all in vain, for they were just out of reach."
    flub = next(i for i in issues(client) if i["kind"] == "flub" and i["expected"] == reach)
    assert flub["status"] == "open"
    before = client.get("/api/project/timeline").json()["duration"]

    # The narrator re-records the line; it comes in as a pickup.
    add(client, ["ch01_take2.wav"], role="pickup")
    src = sources(client)
    assert src["ch01_take2"]["role"] == "pickup" and src["ch01_take1"]["role"] == "narration"
    # Until it's analyzed, a pickup isn't on the timeline at all.
    assert all(x["source_id"] != src["ch01_take2"]["id"] for x in clips(client))
    analyze(client)

    group = next(
        g for g in client.get("/api/project/takes").json()["groups"] if g["sentence_text"] == reach
    )
    chosen = next(t for t in group["takes"] if t["status"] == "preferred")
    assert chosen["source_label"] == "ch01_take2" and group["confident"]
    # It plays where the line was: right after the line before it, once.
    pickup_clips = [x for x in clips(client) if x["source_id"] == src["ch01_take2"]["id"]]
    assert len(pickup_clips) == 1
    order = [x["source_label"] for x in clips(client)]
    at = order.index("ch01_take2")
    assert order[at - 1] == "ch01_take1" and order[at + 1] == "ch01_take1"
    # The flubbed reading is cut and the pickup's span is in: about the same length.
    after = client.get("/api/project/timeline").json()["duration"]
    assert abs(after - before) < 1.5
    # The flub is gone (fixed by the pickup); nothing is lost in the edit.
    again = next(i for i in issues(client) if i["stable_key"] == flub["stable_key"])
    assert (again["status"], again["decided_by"]) == ("resolved", "auto")
    assert client.get("/api/project/edit-check").json()["lines"] == []


def test_lines_only_in_pickups_go_where_they_belong(client: TestClient) -> None:
    # Chapter one was recorded as pickups only: every line goes in its place,
    # before chapter two, and the rest of the file (an aside) stays off.
    add(client, ["ch02_take1.wav"])
    add(client, ["ch01_take1.wav"], role="pickup")
    manuscript(client)
    analyze(client)
    src = sources(client)
    tl = client.get("/api/project/timeline").json()
    narr = [x for x in tl["clips"] if x["track_id"] == 1]
    labels = [x["source_label"] for x in narr]
    first_ch2 = labels.index("ch02_take1")
    assert set(labels[:first_ch2]) == {"ch01_take1"} and "ch01_take1" not in labels[first_ch2:]
    # In manuscript order: each placed piece starts later in the pickup file.
    starts = [x["source_in"] for x in narr[:first_ch2]]
    assert starts == sorted(starts)
    # Not the whole pickup file: the aside "Hold on, let me get some water" is out.
    heard = sum(
        x["source_out"] - x["source_in"] for x in narr if x["source_id"] == src["ch01_take1"]["id"]
    )
    assert heard < src["ch01_take1"]["duration"] - 2.0
    # Chapter one counts as recorded: no line is missing.
    assert not [i for i in issues(client) if i["kind"] == "missing_line"]
    assert client.get("/api/project/edit-check").json()["lines"] == []
    one, two = client.get("/api/project/chapters").json()
    assert one["status"] == "recorded" and one["timeline"]["end"] <= two["timeline"]["start"] + 0.5


def test_a_new_pickup_for_a_line_you_chose_asks_again(client: TestClient, tmp_path: Path) -> None:
    add(client, ["ch01_take1.wav", "ch02_take1.wav"])
    manuscript(client)
    add(client, ["ch01_take2.wav"], role="pickup")
    analyze(client)
    reach = "But it was all in vain, for they were just out of reach."

    def group() -> dict[str, Any]:
        groups = client.get("/api/project/takes").json()["groups"]
        out: dict[str, Any] = next(g for g in groups if g["sentence_text"] == reach)
        return out

    # The user keeps the original reading after all.
    g = group()
    original = next(t for t in g["takes"] if t["source_label"] == "ch01_take1")
    client.post(f"/api/project/takes/{g['id']}/choose", json={"take_id": original["id"]})
    assert group()["state"] == "confirmed"

    # Another pickup of the same line arrives (the same words, another file).
    audio, rate = sf.read(FIXTURES / "ch01_take2.wav")
    second = tmp_path / "ch01_take2.wav"
    sf.write(second, np.concatenate([audio, np.zeros(rate // 10)]), rate, subtype="PCM_16")
    r = client.post("/api/project/sources/import", json={"paths": [str(second)], "role": "pickup"})
    assert wait(client, r)["status"] == "done"
    analyze(client)
    # The user's choice stands, but the line is back for them to listen and choose.
    g = group()
    assert len(g["takes"]) == 3
    assert next(t for t in g["takes"] if t["status"] == "preferred")["id"] == original["id"]
    assert (g["state"], g["confident"]) == ("suggested", False)


def test_the_pickups_report(client: TestClient) -> None:
    add(client, ["ch01_take1.wav", "ch02_take1.wav"])
    manuscript(client)
    analyze(client)
    add(client, ["ch01_take2.wav"], role="pickup")
    (before,) = client.get("/api/project/pickups").json()
    assert before["label"] == "ch01_take2" and not before["checked"] and before["lines"] == []
    analyze(client)
    (rep,) = client.get("/api/project/pickups").json()
    assert rep["checked"] and rep["unmatched"] == []
    (line,) = rep["lines"]
    assert (line["status"], line["replaces"], line["page_label"]) == ("placed", "flub", "2")
    assert line["text"].endswith("out of reach.")


def test_a_pickup_leaves_other_issues_alone(client: TestClient) -> None:
    """Regression: the pause before a line replaced by a pickup came back as a new
    issue (its key used the neighbouring line as heard), and the old one was
    marked fixed."""
    add(client, ["ch01_take1.wav", "ch02_take1.wav"])
    manuscript(client)
    analyze(client)
    before = {i["stable_key"]: i["status"] for i in issues(client) if i["kind"] == "silence"}
    add(client, ["ch01_take2.wav"], role="pickup")
    analyze(client)
    after = {i["stable_key"]: i["status"] for i in issues(client) if i["kind"] == "silence"}
    assert after == before
