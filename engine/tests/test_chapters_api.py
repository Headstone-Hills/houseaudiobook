"""Chapter workspaces (D-059) and audio that covers only part of the manuscript."""

from __future__ import annotations

from typing import Any

import test_analysis_api as base
from conftest import FIXTURES
from fastapi.testclient import TestClient

client, factory, wait = base.client, base.factory, base.wait

CH1 = "Chapter One. The Fox and the Grapes."
CH2 = "Chapter Two. The Tortoise and the Hare."


def book(c: TestClient, audio: list[str]) -> None:
    j = wait(
        c,
        c.post("/api/project/sources/import", json={"paths": [str(FIXTURES / n) for n in audio]}),
    )
    assert j["status"] == "done"
    j = wait(
        c,
        c.post(
            "/api/project/manuscript", json={"source": {"path": str(FIXTURES / "manuscript.txt")}}
        ),
    )
    assert j["status"] == "done"


def chapters(c: TestClient) -> list[dict[str, Any]]:
    r = c.get("/api/project/chapters")
    assert r.status_code == 200, r.text
    out: list[dict[str, Any]] = r.json()
    return out


def test_no_manuscript_no_chapters(client: TestClient) -> None:
    assert chapters(client) == []


def test_chapters_before_comparing(client: TestClient) -> None:
    book(client, ["ch01_take1.wav"])
    ch = chapters(client)
    assert [(c["number"], c["title"], c["status"]) for c in ch] == [
        (1, CH1, "not_compared"),
        (2, CH2, "not_compared"),
    ]
    assert [c["sentences"] for c in ch] == [7, 6]
    assert [c["page_label"] for c in ch] == ["1", "3"]
    assert ch[0]["spans"] == [] and ch[0]["timeline"] is None


def test_audio_for_one_chapter_only(client: TestClient) -> None:
    """Only chapter one recorded: chapter two is "not recorded", not 6 missing lines."""
    book(client, ["ch01_take1.wav", "ch01_take2.wav"])
    assert wait(client, client.post("/api/project/analysis", json={}))["status"] == "done"

    one, two = chapters(client)
    assert (one["status"], one["read"], one["not_recorded"]) == ("recorded", 7, 0)
    assert (two["status"], two["read"], two["not_recorded"]) == ("not_recorded", 0, 6)
    assert two["spans"] == [] and two["timeline"] is None
    # Chapter one is in both takes; take 2 is the pickup of one line.
    assert [s["source_id"] for s in one["spans"]] == [1, 2]
    assert one["spans"][0]["start"] < 1.0 < 30 < one["spans"][0]["end"]
    assert one["timeline"] is not None and one["timeline"]["start"] < 1.0

    issues = client.get("/api/project/issues").json()["issues"]
    assert not [i for i in issues if i["kind"] == "missing_line"]
    assert one["open_issues"] == len([i for i in issues if i["status"] == "open"]) > 0
    assert two["open_issues"] == 0

    cmp = client.get("/api/project/comparison").json()
    recorded = {s["ordinal"]: s["recorded"] for s in cmp["sentences"]}
    assert [o for o, r in recorded.items() if not r] == [7, 8, 9, 10, 11, 12]


def test_whole_book_and_one_chapter_export(client: TestClient) -> None:
    book(client, base.AUDIO)
    assert wait(client, client.post("/api/project/analysis", json={}))["status"] == "done"
    one, two = chapters(client)
    assert [c["status"] for c in (one, two)] == ["recorded", "recorded"]
    assert set(one["sentence_ids"]).isdisjoint(two["sentence_ids"])
    assert two["timeline"]["start"] > one["timeline"]["start"]

    pf = client.get(
        "/api/project/export/preflight", params={"format": "wav24", "chapter": two["id"]}
    ).json()
    assert pf["problems"] == []
    assert [o["title"] for o in pf["outputs"]] == [CH2]
    # Only chapter two's issues count against chapter two's export.
    assert sum(pf["open_issues"].values()) == two["open_issues"]

    j = wait(
        client,
        client.post("/api/project/export", json={"format": "wav24", "chapter": two["id"]}),
    )
    assert j["status"] == "done", j
    assert j["label"] == "Export WAV24 of one chapter"
    # Numbered as in the whole book, so it sorts with the other chapter files.
    assert [f["file"].rsplit("/", 1)[-1] for f in j["result"]["files"]] == [
        "02_Chapter_Two_The_Tortoise_and_the_Hare.wav"
    ]


def test_chapter_export_needs_the_chapter_on_the_timeline(client: TestClient) -> None:
    book(client, ["ch01_take1.wav"])
    assert wait(client, client.post("/api/project/analysis", json={}))["status"] == "done"
    two = chapters(client)[1]
    pf = client.get(
        "/api/project/export/preflight", params={"format": "wav24", "chapter": two["id"]}
    ).json()
    assert pf["outputs"] == []
    assert pf["problems"] == [
        "this chapter isn't on the timeline yet (record it, then Compare to Text)"
    ]


def test_guide_for_one_chapter(client: TestClient) -> None:
    book(client, ["ch01_take1.wav", "ch02_take1.wav"])
    assert wait(client, client.post("/api/project/analysis", json={}))["status"] == "done"
    one, two = chapters(client)

    def rows(**q: Any) -> list[dict[str, Any]]:
        r = client.get("/api/project/guide/rows", params={"include_silences": True, **q})
        out: list[dict[str, Any]] = r.json()["rows"]
        return out

    every, mine, theirs = rows(), rows(chapter=one["id"]), rows(chapter=two["id"])
    assert len(mine) == one["open_issues"] > 0
    assert len(mine) + len(theirs) == len(every)
    r = client.get(
        "/api/project/guide",
        params={"format": "csv", "chapter": two["id"], "include_silences": True},
    )
    assert r.status_code == 200
    data = [line for line in r.text.strip().splitlines() if not line.startswith("#")]
    assert len(data) == len(theirs) == 1
