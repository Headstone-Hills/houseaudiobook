"""Issues through the API (M4): flagging, decisions, re-runs, excerpts."""

from __future__ import annotations

import io
from typing import Any

import pytest
import soundfile as sf
import test_analysis_api as base
from conftest import FIXTURES
from fastapi.testclient import TestClient

from hillaudio.store.project import Project

# The same engine with a fake transcriber as the analysis API tests.
client, factory, setup_book, wait = base.client, base.factory, base.setup_book, base.wait


def issues(client: TestClient) -> list[dict[str, Any]]:
    r = client.get("/api/project/issues")
    assert r.status_code == 200, r.text
    out: list[dict[str, Any]] = r.json()["issues"]
    return out


def run(client: TestClient, stages: list[str] | None = None) -> dict[str, Any]:
    body = {} if stages is None else {"stages": stages}
    job = wait(client, client.post("/api/project/analysis", json=body))
    assert job["status"] == "done", job
    return job


def project(client: TestClient) -> Project:
    p: Project = client.app.state.engine.require_project()  # type: ignore[attr-defined]
    return p


def by_kind(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for i in items:
        out.setdefault(i["kind"], []).append(i)
    return out


def test_a_full_run_flags_the_ground_truth(client: TestClient) -> None:
    setup_book(client)
    job = run(client)
    assert job["result"]["issues"] == {
        "new": 3,
        "still_open": 0,
        "auto_resolved": 0,
        "regressed": 0,
    }
    k = by_kind(issues(client))
    assert set(k) == {"flub", "silence"}
    (flub,) = k["flub"]
    assert (flub["severity"], flub["page_label"], flub["source_label"]) == (
        "high",
        "2",
        "ch01_take1",
    )
    assert flub["expected"].startswith("Not now") or "now" in flub["expected"]
    assert [d["kind"] for d in flub["detail"]["diffs"]] == ["missing"]
    assert flub["status"] == "open" and flub["change"] == "new"
    silences = sorted(k["silence"], key=lambda i: i["source_label"])
    assert [(s["source_label"], s["detail"]["boundary"]) for s in silences] == [
        ("ch01_take1", "paragraph"),
        ("ch02_take1", "sentence"),
    ]
    assert silences[0]["detail"]["seconds"] == pytest.approx(3.5, abs=0.06)


def test_decisions_survive_reruns_and_are_logged(client: TestClient) -> None:
    setup_book(client)
    run(client)
    k = by_kind(issues(client))
    flub, s1, s2 = k["flub"][0], *sorted(k["silence"], key=lambda i: i["source_label"])
    r = client.patch(
        "/api/project/issues",
        json={"ids": [flub["id"]], "status": "dismissed", "note": "intended"},
    )
    assert r.status_code == 200 and r.json()["issues"][0]["decided_by"] == "user"
    assert (
        client.patch(
            "/api/project/issues", json={"ids": [s1["id"]], "status": "resolved"}
        ).status_code
        == 200
    )

    job = run(client, ["flubs", "silences"])
    assert job["result"]["issues"]["still_open"] == 3
    after = {i["id"]: i for i in issues(client)}
    assert len(after) == 3  # the same issues, by stable key
    assert (after[flub["id"]]["status"], after[flub["id"]]["note"]) == ("dismissed", "intended")
    assert after[s1["id"]]["status"] == "resolved"
    assert after[s2["id"]]["status"] == "open"
    assert all(i["last_run_id"] == job["result"]["run_id"] for i in after.values())

    ops = [o for o in project(client).operations() if o["op_type"] == "issue_decision"]
    assert [(o["origin"], o["payload"]["to"]) for o in ops] == [
        ("user", "dismissed"),
        ("user", "resolved"),
    ]
    assert ops[0]["payload"]["note_to"] == "intended"


def test_fixed_issues_auto_resolve_and_can_regress(client: TestClient) -> None:
    """Loosening a threshold stands in for an edit that fixes the pause (M5)."""
    setup_book(client)
    run(client)
    (s2,) = [i for i in issues(client) if i["detail"].get("boundary") == "sentence"]
    r = client.put("/api/project/issue-settings", json={"silence_sentence_s": 3.0})
    assert r.status_code == 200 and r.json()["silence_sentence_s"] == 3.0

    job = run(client, ["silences"])
    assert job["result"]["issues"]["auto_resolved"] == 1
    got = {i["id"]: i for i in issues(client)}
    assert (got[s2["id"]]["status"], got[s2["id"]]["change"]) == ("resolved", "auto_resolved")
    assert got[s2["id"]]["decided_by"] == "auto"
    flub = next(i for i in got.values() if i["kind"] == "flub")
    assert flub["change"] == "new" and flub["last_run_id"] != job["result"]["run_id"]

    auto = [o for o in project(client).operations() if o["op_type"] == "issue_status"]
    assert [(o["origin"], o["payload"]["from"], o["payload"]["to"]) for o in auto] == [
        ("auto", "open", "resolved")
    ]

    client.put("/api/project/issue-settings", json={"silence_sentence_s": 2.0})
    job = run(client, ["silences"])
    assert job["result"]["issues"]["regressed"] == 1
    again = {i["id"]: i for i in issues(client)}[s2["id"]]
    assert (again["status"], again["change"]) == ("open", "regressed")


def test_bulk_and_reopen(client: TestClient) -> None:
    setup_book(client)
    run(client)
    ids = [i["id"] for i in issues(client)]
    r = client.patch("/api/project/issues", json={"ids": ids, "status": "resolved"})
    assert [i["status"] for i in r.json()["issues"]] == ["resolved"] * 3
    r = client.patch("/api/project/issues", json={"ids": ids[:1], "status": "open"})
    assert r.json()["issues"][0]["status"] == "open"


def test_remapping_keeps_issue_keys(client: TestClient) -> None:
    setup_book(client)
    run(client)
    (flub,) = [i for i in issues(client) if i["kind"] == "flub"]
    client.patch("/api/project/issues", json={"ids": [flub["id"]], "status": "dismissed"})
    # Importing the manuscript again replaces every unit (new ids, same text).
    wait(
        client,
        client.post(
            "/api/project/manuscript", json={"source": {"path": str(FIXTURES / "manuscript.txt")}}
        ),
    )
    data = client.get("/api/project/issues").json()
    assert data["stale"] is True
    gone = {i["id"]: i for i in data["issues"]}[flub["id"]]
    assert gone["manuscript_unit_id"] is None  # the unit was replaced; the issue stays

    job = wait(client, client.post("/api/project/analysis", json={"stages": ["flubs"]}))
    assert job["status"] == "failed" and "Compare to Text" in job["error"]

    run(client, ["compare_text", "flubs"])
    data = client.get("/api/project/issues").json()
    assert data["stale"] is False
    back = {i["id"]: i for i in data["issues"]}[flub["id"]]
    assert back["status"] == "dismissed" and back["manuscript_unit_id"] is not None
    assert back["page_label"] == "2"


def test_excerpt_has_context(client: TestClient) -> None:
    setup_book(client)
    run(client)
    (flub,) = [i for i in issues(client) if i["kind"] == "flub"]
    r = client.get(
        f"/api/project/sources/{flub['source_file_id']}/excerpt",
        params={"start": flub["start"], "end": flub["end"]},
    )
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    a, b = float(r.headers["x-excerpt-start"]), float(r.headers["x-excerpt-end"])
    assert a == pytest.approx(flub["start"] - 3.0)
    duration = project(client).source(flub["source_file_id"]).duration
    assert b == pytest.approx(min(flub["end"] + 3.0, duration))
    info = sf.info(io.BytesIO(r.content))
    assert info.duration == pytest.approx(b - a, abs=0.01)

    r = client.get("/api/project/sources/1/excerpt", params={"start": 0.5, "end": 1.0})
    assert float(r.headers["x-excerpt-start"]) == 0.0  # clamped to the file


def test_bad_requests(client: TestClient) -> None:
    assert client.get("/api/project/issues").json()["issues"] == []
    assert client.patch("/api/project/issues", json={"ids": [1]}).status_code == 400
    assert (
        client.patch("/api/project/issues", json={"ids": [1], "status": "open"}).status_code == 404
    )
    setup_book(client)
    run(client)
    i = issues(client)[0]["id"]
    r = client.patch("/api/project/issues", json={"ids": [i], "status": "gone"})
    assert r.status_code == 400
    r = client.put("/api/project/issue-settings", json={"silence_sentence_s": 9.0})
    assert r.status_code == 400  # sentence > paragraph
    assert client.put("/api/project/issue-settings", json={"bogus": 1}).status_code == 400
    assert (
        client.get("/api/project/sources/99/excerpt", params={"start": 0, "end": 1}).status_code
        == 404
    )
    r = client.get("/api/project/sources/1/excerpt", params={"start": 2, "end": 1})
    assert r.status_code == 400
