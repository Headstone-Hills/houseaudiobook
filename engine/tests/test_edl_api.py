"""Cleanup and the EDL through the API (M5): auto-edits, restores, rolling edits,
undo/redo, previews, and the round trip back to the original audio."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import test_analysis_api as base
from conftest import FIXTURES
from fakeasr import TRUTH
from fastapi.testclient import TestClient

from hillaudio.edl import store
from hillaudio.edl.model import duration
from hillaudio.edl.render import Input, render
from hillaudio.ingest.decode import iter_blocks
from hillaudio.store.project import Project

client, factory, setup_book, wait = base.client, base.factory, base.setup_book, base.wait
LABELS = [n[:-4] for n in base.AUDIO]


def project(c: TestClient) -> Project:
    p: Project = c.app.state.engine.require_project()  # type: ignore[attr-defined]
    return p


# M5 behaviour on its own: everything but take comparison (M6 has test_takes_api).
M5_STAGES = ["transcribe", "compare_text", "cleanup", "flubs", "silences"]


def run(c: TestClient, stages: list[str] | None = None) -> dict[str, Any]:
    job = wait(c, c.post("/api/project/analysis", json={"stages": stages or M5_STAGES}))
    assert job["status"] == "done", job
    return job


def edl(c: TestClient) -> dict[str, Any]:
    r = c.get("/api/project/edl")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def full_render(p: Project) -> np.ndarray:
    tl = store.timeline(p)
    inputs = {
        s.id: Input(p.resolve(s), s.sample_rate, s.channels, s.num_frames) for s in p.sources()
    }
    return render(tl.clips, inputs, tl.rate, 1, 0, duration(tl.clips))


ORIGINAL = np.concatenate([np.concatenate(list(iter_blocks(FIXTURES / n, 1))) for n in base.AUDIO])


def truth_cuts() -> list[tuple[str, str, float, float]]:
    return sorted(
        (f["name"][:-4], e["type"], e["start"], e["end"])
        for f in TRUTH["files"]
        for e in f["events"]
        if e["type"] in ("stutter", "false_start", "commentary")
    )


def test_auto_cleanup_removes_exactly_the_injected_material(client: TestClient) -> None:
    setup_book(client)
    job = run(client)
    assert job["result"]["cleanup"] == {"removed": 4, "suggested": 0, "kept": 0}
    d = edl(client)
    got = sorted((r["source_label"], r["kind"], r["start"], r["end"]) for r in d["regions"])
    want = truth_cuts()
    assert [(g[0], g[1]) for g in got] == [(w[0], w[1]) for w in want]
    for g, w in zip(got, want, strict=True):
        assert g[2] == pytest.approx(w[2], abs=0.06) and g[3] == pytest.approx(w[3], abs=0.06)
    assert all(r["status"] == "removed" and r["origin"] == "auto" for r in d["regions"])
    cut = sum(r["seconds"] for r in d["regions"])
    assert d["duration"] == pytest.approx(d["original_duration"] - cut, abs=1e-3)
    assert d["undo"].startswith("Cleanup: 4 removed")

    log = [o for o in project(client).operations() if o["op_type"] == "cleanup_apply"]
    assert len(log) == 1 and log[0]["origin"] == "auto"
    # The flub and pause checks still find their issues; nothing they need was cut.
    kinds = sorted(i["kind"] for i in client.get("/api/project/issues").json()["issues"])
    assert kinds == ["flub", "silence", "silence"]


def test_round_trip_undo_all_renders_the_original(client: TestClient) -> None:
    """SPEC §11: import -> auto-edit -> undo all -> render equals the original."""
    setup_book(client)
    run(client)
    p = project(client)
    edited = full_render(p)
    assert len(edited) < len(ORIGINAL)
    while client.post("/api/project/edl/undo").status_code == 200:
        pass
    assert edl(client)["regions"] == []
    np.testing.assert_array_equal(full_render(p), ORIGINAL)
    # And redo brings the edit back exactly.
    assert client.post("/api/project/edl/redo").status_code == 200
    np.testing.assert_array_equal(full_render(p), edited)


def test_restore_and_reapply_one_region(client: TestClient) -> None:
    setup_book(client)
    run(client)
    d = edl(client)
    (com,) = [r for r in d["regions"] if r["kind"] == "commentary"]
    r = client.post(
        "/api/project/edl/regions/status", json={"ids": [com["id"]], "status": "restored"}
    )
    after = r.json()
    assert after["duration"] == pytest.approx(d["duration"] + com["seconds"], abs=1e-3)
    assert next(x for x in after["regions"] if x["id"] == com["id"])["decided_by"] == "user"
    assert after["undo"] == "Restore commentary"

    # Re-running cleanup keeps the user's restore (stable key) and adds nothing.
    job = run(client, ["cleanup"])
    assert job["result"]["cleanup"] == {"removed": 0, "suggested": 0, "kept": 4}
    again = next(x for x in edl(client)["regions"] if x["id"] == com["id"])
    assert again["status"] == "restored"

    client.post("/api/project/edl/regions/status", json={"ids": [com["id"]], "status": "removed"})
    assert edl(client)["duration"] == pytest.approx(d["duration"], abs=1e-3)


def test_rolling_edit_restores_cut_material(client: TestClient) -> None:
    """SPEC §5: dragging a clip edge outward re-exposes removed audio; a rolling
    edit moves the shared boundary without changing the total length."""
    setup_book(client)
    run(client)
    p = project(client)
    d = edl(client)
    (st,) = [
        r for r in d["regions"] if r["kind"] == "stutter" and r["source_label"] == "ch01_take1"
    ]

    # Roll: the boundary moves 0.1 s later; the length doesn't change.
    rolled = client.post(f"/api/project/edl/regions/{st['id']}/roll", json={"delta": 0.1}).json()
    assert rolled["duration"] == pytest.approx(d["duration"], abs=1e-6)
    moved = next(x for x in rolled["regions"] if x["id"] == st["id"])
    assert moved["start"] == pytest.approx(st["start"] + 0.1, abs=1e-4)
    client.post("/api/project/edl/undo")

    # Drag the outgoing clip's end outward, halfway into the cut, then all the way.
    half = st["start"] + st["seconds"] / 2
    r = client.patch(f"/api/project/edl/regions/{st['id']}", json={"start": half}).json()
    assert r["duration"] == pytest.approx(d["duration"] + st["seconds"] / 2, abs=1e-3)
    client.patch(f"/api/project/edl/regions/{st['id']}", json={"start": st["end"]})

    # Restored by the drag: the audio there is the original, sample for sample.
    tl = store.timeline(p)
    src = next(s for s in p.sources() if s.label == "ch01_take1")
    a, b = round(st["start"] * 16000), round(st["end"] * 16000)
    inputs = {s.id: Input(p.resolve(s), s.sample_rate, 1, s.num_frames) for s in p.sources()}
    # On the timeline, ch01_take1 comes first; the false start (later) is still cut.
    audio = render(tl.clips, inputs, tl.rate, 1, a - 2000, b + 2000)
    original = np.concatenate(list(iter_blocks(p.resolve(src), 1)))
    np.testing.assert_array_equal(audio, original[a - 2000 : b + 2000])

    bad = client.patch(f"/api/project/edl/regions/{st['id']}", json={"start": 9.0, "end": 8.0})
    assert bad.status_code == 400


def test_undo_redo_persist_across_reopen(client: TestClient, tmp_path: Path) -> None:
    setup_book(client)
    run(client)
    d = edl(client)
    ids = [r["id"] for r in d["regions"]]
    client.post("/api/project/edl/regions/status", json={"ids": ids[:2], "status": "restored"})
    client.post("/api/project/edl/undo")
    path = project(client).root
    client.post("/api/project/close")
    assert client.post("/api/projects/open", json={"path": str(path)}).status_code == 200
    h = client.get("/api/project/edl/history").json()
    assert h["redo"] == "Restore 2" and h["undo"].startswith("Cleanup")
    assert [e["state"] for e in h["entries"]] == ["done", "undone"]
    client.post("/api/project/edl/redo")
    assert sum(r["status"] == "restored" for r in edl(client)["regions"]) == 2

    # A new edit after an undo drops the redo stack.
    client.post("/api/project/edl/undo")
    client.post("/api/project/edl/cut", json={"source_id": 2, "start": 0.0, "end": 0.4})
    h = client.get("/api/project/edl/history").json()
    assert h["redo"] is None and [e["state"] for e in h["entries"]] == ["done", "dropped", "done"]
    assert client.post("/api/project/edl/redo").status_code == 409


def test_shorten_a_long_pause_resolves_its_issue(client: TestClient) -> None:
    setup_book(client)
    run(client)
    issues = client.get("/api/project/issues").json()["issues"]
    pause = next(i for i in issues if i["detail"].get("boundary") == "paragraph")
    r = client.post(f"/api/project/issues/{pause['id']}/shorten", json={"keep": 1.2})
    assert r.status_code == 200
    reg = next(x for x in r.json()["regions"] if x["kind"] == "pause")
    assert reg["seconds"] == pytest.approx(pause["detail"]["seconds"] - 1.2, abs=0.02)
    job = run(client, ["silences"])
    assert job["result"]["issues"]["auto_resolved"] == 1
    got = {i["id"]: i for i in client.get("/api/project/issues").json()["issues"]}
    assert got[pause["id"]]["status"] == "resolved"

    # Undo the shortening and the pause is back: the issue regresses.
    client.post("/api/project/edl/undo")
    job = run(client, ["silences"])
    assert job["result"]["issues"]["regressed"] == 1

    flub = next(i for i in issues if i["kind"] == "flub")
    assert client.post(f"/api/project/issues/{flub['id']}/shorten", json={}).status_code == 400


def test_checks_see_the_edited_timeline(client: TestClient) -> None:
    """Cut the clean pickup of the misread line and the misread is a flub again."""
    setup_book(client)
    run(client)
    take2 = next(s for s in project(client).sources() if s.label == "ch01_take2")
    client.post(
        "/api/project/edl/cut", json={"source_id": take2.id, "start": 0.0, "end": take2.duration}
    )
    job = run(client, ["flubs"])
    assert job["result"]["issues"]["new"] == 1
    flubs = [i for i in client.get("/api/project/issues").json()["issues"] if i["kind"] == "flub"]
    assert sorted(i["page_label"] for i in flubs) == ["2", "2"]


def test_suggested_below_the_threshold(client: TestClient) -> None:
    setup_book(client)
    r = client.put("/api/project/cleanup-settings", json={"auto_apply": 0.95})
    assert r.status_code == 200 and r.json()["auto_apply"] == 0.95
    job = run(client)
    assert job["result"]["cleanup"] == {"removed": 0, "suggested": 4, "kept": 0}
    d = edl(client)
    assert d["duration"] == pytest.approx(d["original_duration"])  # nothing applied
    ids = [x["id"] for x in d["regions"]]
    after = client.post("/api/project/edl/regions/status", json={"ids": ids, "status": "removed"})
    assert after.json()["duration"] < d["duration"]
    assert client.put("/api/project/cleanup-settings", json={"auto_apply": 2}).status_code == 400


def test_preview_render(client: TestClient) -> None:
    setup_book(client)
    run(client)
    d = edl(client)
    st = next(r for r in d["regions"] if r["kind"] == "stutter")
    at = st["timeline_at"]
    r = client.get("/api/project/edl/render", params={"start": at - 3, "end": at + 3})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    info = sf.info(io.BytesIO(r.content))
    assert info.duration == pytest.approx(6.0, abs=0.01) and info.samplerate == 16000
    assert client.get("/api/project/edl/render", params={"start": 2, "end": 1}).status_code == 400
    far = client.get("/api/project/edl/render", params={"start": 500, "end": 501})
    assert far.status_code == 400


def test_bad_requests(client: TestClient) -> None:
    assert client.post("/api/project/edl/undo").status_code == 409
    assert client.post("/api/project/edl/redo").status_code == 409
    assert edl(client) == edl(client) and edl(client)["regions"] == []
    st = {"ids": [99], "status": "removed"}
    assert client.post("/api/project/edl/regions/status", json=st).status_code == 404
    st = {"ids": [1], "status": "gone"}
    assert client.post("/api/project/edl/regions/status", json=st).status_code == 400
    assert client.patch("/api/project/edl/regions/5", json={"start": 1}).status_code == 404
    cut = {"source_id": 9, "start": 0, "end": 1}
    assert client.post("/api/project/edl/cut", json=cut).status_code == 404
    assert client.post("/api/project/issues/5/shorten", json={}).status_code == 404
