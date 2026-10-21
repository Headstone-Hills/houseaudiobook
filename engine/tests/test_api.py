"""Integration tests through the HTTP/WebSocket API (SPEC §10)."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES, MakeAudio
from fastapi.testclient import TestClient

from hillaudio import __version__
from hillaudio.analysis.peaks import BASE, LEVELS
from hillaudio.api.app import create_app
from hillaudio.api.guard import DEFAULT_HOSTS, DEFAULT_ORIGINS

UI = "http://127.0.0.1:5173"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    app = create_app(hosts=DEFAULT_HOSTS | {"testserver"}, origins=DEFAULT_ORIGINS)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def location(tmp_path: Path) -> Path:
    loc = tmp_path / "projects"
    loc.mkdir()
    return loc


def wait_job(client: TestClient, job_id: str) -> dict[str, Any]:
    job = client.app.state.engine.jobs.wait(job_id)  # type: ignore[attr-defined]
    data: dict[str, Any] = job.to_json()
    return data


def create(client: TestClient, location: Path, name: str = "Book") -> dict[str, Any]:
    r = client.post("/api/projects", json={"location": str(location), "name": name})
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def import_paths(client: TestClient, paths: list[Path], mode: str = "copy") -> dict[str, Any]:
    r = client.post(
        "/api/project/sources/import", json={"paths": [str(p) for p in paths], "mode": mode}
    )
    assert r.status_code == 200, r.text
    return wait_job(client, r.json()["job_id"])


def test_health(client: TestClient) -> None:
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["version"] == __version__ and "ffmpeg" in h["ffmpeg"]


def test_functions_endpoint(client: TestClient) -> None:
    fns = {f["key"]: f for f in client.get("/api/functions").json()}
    assert fns["flubs"]["requires"] == ["compare_text"]
    assert fns["compare_text"]["needs_text"] is True


# ---- local-only guard ------------------------------------------------------------------


def test_guard_refuses_foreign_host_and_origin(client: TestClient) -> None:
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/health", headers={"Origin": UI}).status_code == 200


def test_guard_refuses_foreign_websocket(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/jobs/ws", headers={"Origin": "https://evil.example"}) as ws,
    ):
        ws.receive_json()


def test_real_app_refuses_testserver_host() -> None:
    with TestClient(create_app()) as c:  # default hosts: loopback names only
        assert c.get("/api/health").status_code == 403


# ---- projects ----------------------------------------------------------------------------


def test_create_open_and_recent(client: TestClient, location: Path) -> None:
    proj = create(client, location)
    assert proj["name"] == "Book" and proj["sources"] == []
    assert (
        client.post("/api/projects", json={"location": str(location), "name": "Book"}).status_code
        == 400
    )
    s = client.get("/api/settings").json()
    assert s["peaks"] == {"base": BASE, "factor": 4, "levels": LEVELS}
    assert s["page_defaults"]["mode"] == "auto"
    assert s["page_defaults"]["words_per_page"] == 250
    recent = s["recent"]
    assert [r["name"] for r in recent] == ["Book"]

    assert client.post("/api/project/close").json() == {"closed": True}
    assert client.get("/api/project").json() is None
    assert client.post("/api/project/sources/import", json={"paths": ["/x.wav"]}).status_code == 409
    assert client.post("/api/projects/open", json={"path": proj["path"]}).json()["name"] == "Book"
    assert client.post("/api/projects/open", json={"path": str(location)}).status_code == 400


def test_function_selection_closes_prerequisites(client: TestClient, location: Path) -> None:
    r = client.post(
        "/api/projects", json={"location": str(location), "name": "B", "functions": ["takes"]}
    )
    assert r.json()["functions"] == ["transcribe", "compare_text", "takes"]


# ---- import --------------------------------------------------------------------------------


def test_import_paths_with_partial_failure(
    client: TestClient, location: Path, make_audio: MakeAudio, tmp_path: Path
) -> None:
    create(client, location)
    bogus = tmp_path / "bogus.wav"
    bogus.write_text("nope")
    job = import_paths(client, [FIXTURES / "ch01_take2.wav", make_audio("b.mp3"), bogus])
    assert job["status"] == "done"
    files = job["result"]["files"]
    assert [("error" in f) for f in files] == [False, False, True]

    proj = client.get("/api/project").json()
    assert [(s["format"], s["status"]) for s in proj["sources"]] == [("wav", "ok"), ("mp3", "ok")]
    assert any("mixed sample rates" in w for w in proj["warnings"])


def test_upload_streams_into_project(client: TestClient, location: Path) -> None:
    proj = create(client, location)
    data = (FIXTURES / "ch01_take2.wav").read_bytes()
    r = client.put(
        "/api/project/sources/upload", params={"filename": "../../Pickup 3.wav"}, content=data
    )
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    (src,) = client.get("/api/project").json()["sources"]
    assert src["original_name"] == "Pickup 3.wav" and src["label"] == "Pickup 3"
    assert list((Path(proj["path"]) / "cache" / "uploads").iterdir()) == []  # temp removed
    assert (
        client.put(
            "/api/project/sources/upload", params={"filename": "x.flac"}, content=b"x"
        ).status_code
        == 400
    )


def test_peaks_paging_and_regeneration(client: TestClient, location: Path) -> None:
    proj = create(client, location)
    import_paths(client, [FIXTURES / "ch01_take2.wav"])
    sid = client.get("/api/project").json()["sources"][0]["id"]

    full = client.get(f"/api/project/sources/{sid}/peaks").json()
    assert full["frames_per_bucket"] == BASE and full["sample_rate"] == 16000
    assert full["total_buckets"] == len(full["max"]) == -(-full["num_frames"] // BASE)
    page = client.get(f"/api/project/sources/{sid}/peaks", params={"start": 10, "count": 5}).json()
    assert page["max"] == full["max"][10:15] and page["start"] == 10
    coarse = client.get(f"/api/project/sources/{sid}/peaks", params={"level": LEVELS - 1}).json()
    assert len(coarse["max"]) < len(full["max"])

    assert (
        client.get(f"/api/project/sources/{sid}/peaks", params={"level": LEVELS}).status_code == 422
    )
    assert client.get("/api/project/sources/999/peaks").status_code == 404

    for f in (Path(proj["path"]) / "cache" / "peaks").iterdir():
        f.unlink()
    assert client.get(f"/api/project/sources/{sid}/peaks").json()["max"] == full["max"]


# ---- verify on open, relink ----------------------------------------------------------------


def test_open_verifies_and_relink_fixes(
    client: TestClient, location: Path, fixture_copy: Callable[[str], Path], tmp_path: Path
) -> None:
    proj = create(client, location)
    ref = fixture_copy("ch01_take2.wav")
    import_paths(client, [ref], mode="reference")
    client.post("/api/project/close")
    moved = tmp_path / "moved.wav"
    os.rename(ref, moved)

    opened = client.post("/api/projects/open", json={"path": proj["path"]}).json()
    # Verification runs in the background; a missing file can be detected before
    # this response is built, so either state is correct here.
    assert opened["sources"][0]["status"] in ("checking", "missing")
    (verify_job,) = client.get("/api/jobs").json()
    wait_job(client, verify_job["id"])
    src = client.get("/api/project").json()["sources"][0]
    assert src["status"] == "missing"

    bad = client.post(
        f"/api/project/sources/{src['id']}/relink", json={"path": str(FIXTURES / "ch02_take1.wav")}
    )
    assert bad.status_code == 400 and "not the same audio" in bad.json()["detail"]
    good = client.post(f"/api/project/sources/{src['id']}/relink", json={"path": str(moved)})
    assert good.json()["sources"][0]["status"] == "ok"


# ---- jobs over WebSocket ---------------------------------------------------------------------


def test_jobs_websocket_reports_import(client: TestClient, location: Path) -> None:
    create(client, location)
    with client.websocket_connect("/api/jobs/ws", headers={"Origin": UI}) as ws:
        assert ws.receive_json() == {"type": "jobs", "jobs": []}
        r = client.post(
            "/api/project/sources/import", json={"paths": [str(FIXTURES / "ch01_take2.wav")]}
        )
        job_id = r.json()["job_id"]
        statuses: list[str] = []
        while not statuses or statuses[-1] not in ("done", "failed"):
            msg = ws.receive_json()
            (job,) = [j for j in msg["jobs"] if j["id"] == job_id]
            statuses.append(job["status"])
        assert statuses[-1] == "done"


def test_cannot_switch_project_while_job_runs(client: TestClient, location: Path) -> None:
    import threading

    create(client, location)
    release = threading.Event()
    jobs = client.app.state.engine.jobs  # type: ignore[attr-defined]
    job = jobs.submit("test", "blocker", lambda ctx: release.wait(5))
    try:
        r = client.post("/api/projects", json={"location": str(location), "name": "Other"})
        assert r.status_code == 409
    finally:
        release.set()
        jobs.wait(job.id)
