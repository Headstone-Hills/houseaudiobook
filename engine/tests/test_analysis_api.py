"""Transcribe + Compare to Text through the API, with the fake transcriber (M3)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES
from fakeasr import TRUTH, FakeTranscriber
from fastapi.testclient import TestClient

from hillaudio.api.app import create_app
from hillaudio.api.guard import DEFAULT_HOSTS
from hillaudio.asr.base import AsrSettings

AUDIO = ["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"]


class Factory:
    """Hands out FakeTranscribers and remembers them, so tests can count calls."""

    def __init__(self) -> None:
        self.made: list[FakeTranscriber] = []
        self.block: threading.Event | None = None

    def __call__(self, settings: AsrSettings) -> FakeTranscriber:
        t = FakeTranscriber(settings, self.block)
        self.made.append(t)
        return t

    @property
    def calls(self) -> list[str]:
        return [c for t in self.made for c in t.calls]


@pytest.fixture
def factory() -> Factory:
    return Factory()


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: Factory
) -> Iterator[TestClient]:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    app = create_app(hosts=DEFAULT_HOSTS | {"testserver"}, transcriber_factory=factory)
    with TestClient(app) as c:
        loc = tmp_path / "projects"
        loc.mkdir()
        assert (
            c.post("/api/projects", json={"location": str(loc), "name": "Book"}).status_code == 200
        )
        yield c


def wait(client: TestClient, r: Any) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = client.app.state.engine.jobs.wait(r.json()["job_id"])  # type: ignore[attr-defined]
    data: dict[str, Any] = job.to_json()
    return data


def setup_book(client: TestClient) -> None:
    j = wait(
        client,
        client.post(
            "/api/project/sources/import", json={"paths": [str(FIXTURES / n) for n in AUDIO]}
        ),
    )
    assert j["status"] == "done"
    j = wait(
        client,
        client.post(
            "/api/project/manuscript", json={"source": {"path": str(FIXTURES / "manuscript.txt")}}
        ),
    )
    assert j["status"] == "done"


def by_ordinal(cmp: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {s["ordinal"]: s for s in cmp["sentences"]}


def test_full_run_matches_ground_truth(client: TestClient, factory: Factory) -> None:
    setup_book(client)
    job = wait(client, client.post("/api/project/analysis", json={}))
    assert job["status"] == "done", job
    assert job["label"] == (
        "Transcribe + Compare to Text + Clean up + Compare takes + Flag flubs + Flag silences"
    )
    assert sorted(factory.calls) == sorted(AUDIO)

    cmp = client.get("/api/project/comparison").json()
    s = by_ordinal(cmp)
    assert len(s) == 13 and all(x["readings"] for x in s.values())
    # The misread: one take with a substitution, and the clean pickup in take 2.
    takes = sorted(s[4]["readings"], key=lambda r: r["score"])
    assert [(r["source_label"], [d["kind"] for d in r["diffs"]]) for r in takes] == [
        ("ch01_take1", ["substitution"]),
        ("ch01_take2", []),
    ]
    assert takes[0]["diffs"][0]["expected"] == "reach"
    assert s[4]["best_score"] == 1.0
    assert [d["kind"] for d in s[6]["readings"][0]["diffs"]] == ["missing"]  # skipped "now"
    assert sorted(r["coverage"] for r in s[3]["readings"]) == [pytest.approx(4 / 14), 1.0]
    assert [u["text"] for u in cmp["unaligned"]] == ["Hold on, let me get some water."]
    # Times come from the transcript, snapped to the audio: within 50 ms of the truth.
    truth = {
        (f["name"][:-4], e["sentence_id"]): e
        for f in TRUTH["files"]
        for e in f["events"]
        if e["type"] == "read"
    }
    r = next(r for r in s[1]["readings"])
    t = truth[("ch01_take1", "s01")]
    assert abs(r["start"] - t["start"]) < 0.05 and abs(r["end"] - t["end"]) < 0.05
    assert s[4]["page_label"] == "2"

    words = client.get(f"/api/project/sources/{r['source_id']}/transcript").json()["words"]
    assert words[0]["text"] == "Chapter"


def test_transcripts_are_cached_by_hash_and_settings(client: TestClient, factory: Factory) -> None:
    setup_book(client)
    wait(client, client.post("/api/project/analysis", json={}))
    assert len(factory.calls) == 3
    wait(client, client.post("/api/project/analysis", json={}))
    assert len(factory.calls) == 3  # second run: all from cache
    wait(
        client,
        client.post(
            "/api/project/analysis", json={"stages": ["transcribe"], "asr": {"model": "tiny.en"}}
        ),
    )
    assert len(factory.calls) == 6  # other settings: transcribed again


def test_compare_only_needs_transcripts(client: TestClient) -> None:
    setup_book(client)
    job = wait(client, client.post("/api/project/analysis", json={"stages": ["compare_text"]}))
    assert job["status"] == "failed" and "transcribe first" in job["error"]


def test_compare_needs_a_manuscript(client: TestClient) -> None:
    wait(
        client,
        client.post("/api/project/sources/import", json={"paths": [str(FIXTURES / AUDIO[1])]}),
    )
    job = wait(client, client.post("/api/project/analysis", json={}))
    assert job["status"] == "failed" and "needs a manuscript" in job["error"]


def test_bad_requests(client: TestClient) -> None:
    assert client.post("/api/project/analysis", json={"stages": ["dance"]}).status_code == 400
    r = client.post("/api/project/analysis", json={"asr": {"model": "huge-v9"}})
    assert r.status_code == 400 and "unknown model" in r.json()["detail"]
    assert client.get("/api/project/comparison").json() is None  # nothing run yet
    assert client.get("/api/project/sources/99/transcript").status_code == 404
    info = client.get("/api/asr").json()
    assert info["default"]["model"] == "small.en"
    assert {"name": "small.en", "size_mb": 464} in info["models"]


def test_stages_follow_the_project_functions(client: TestClient, tmp_path: Path) -> None:
    r = client.post(
        "/api/projects",
        json={
            "location": str(tmp_path / "projects"),
            "name": "Organize only",
            "functions": ["organize"],  # not an analysis stage
        },
    )
    assert r.status_code == 200
    r = client.post("/api/project/analysis", json={})
    assert r.status_code == 400 and "no analysis stages" in r.json()["detail"]


def test_remapping_pages_clears_stale_comparison(client: TestClient, factory: Factory) -> None:
    setup_book(client)
    wait(client, client.post("/api/project/analysis", json={}))
    wait(
        client,
        client.put("/api/project/manuscript/settings", json={"settings": {"mode": "estimate"}}),
    )
    cmp = client.get("/api/project/comparison").json()
    assert cmp["stale"] is True
    assert all(s["readings"] == [] for s in cmp["sentences"]) and cmp["unaligned"] == []
    wait(client, client.post("/api/project/analysis", json={}))
    cmp = client.get("/api/project/comparison").json()
    assert cmp["stale"] is False
    assert by_ordinal(cmp)[4]["page_label"] == "approx. 1"
    assert all(s["readings"] for s in cmp["sentences"])
    assert len(factory.calls) == 3  # transcripts reused


def test_cancel_marks_the_run_cancelled(client: TestClient, factory: Factory) -> None:
    setup_book(client)
    factory.block = threading.Event()  # transcription waits until cancelled
    r = client.post("/api/project/analysis", json={})
    job_id = r.json()["job_id"]
    jobs = client.app.state.engine.jobs  # type: ignore[attr-defined]
    while not factory.calls:
        threading.Event().wait(0.01)
    client.post(f"/api/jobs/{job_id}/cancel")
    assert jobs.wait(job_id).status == "cancelled"
    project = client.app.state.engine.project  # type: ignore[attr-defined]
    with project.db() as conn:
        row = conn.execute("SELECT status FROM analysis_run ORDER BY id DESC").fetchone()
    assert row["status"] == "cancelled"
    assert client.get("/api/project/comparison").json() is None
