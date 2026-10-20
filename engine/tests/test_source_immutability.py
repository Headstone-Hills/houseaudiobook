"""Non-negotiable (CLAUDE.md): imported source audio is never modified, moved or deleted.

Runs every M1 workflow that touches sources, through the API, then proves each
original's bytes, size, permissions and modification time are unchanged. Later
milestones must add their workflows here.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES, MakeAudio
from fastapi.testclient import TestClient

from hillaudio.api.app import create_app
from hillaudio.api.guard import DEFAULT_HOSTS


def fingerprint(path: Path) -> tuple[str, int, int, int]:
    st = os.stat(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, st.st_size, st.st_mode, st.st_mtime_ns


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    with TestClient(create_app(hosts=DEFAULT_HOSTS | {"testserver"})) as c:
        yield c


def wait(client: TestClient, r: Any) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = client.app.state.engine.jobs.wait(r.json()["job_id"])  # type: ignore[attr-defined]
    assert job.status == "done", job.error
    data: dict[str, Any] = job.result
    return data


def test_full_m1_workflow_leaves_sources_untouched(
    client: TestClient, tmp_path: Path, make_audio: MakeAudio
) -> None:
    user_dir = tmp_path / "user recordings"
    user_dir.mkdir()
    originals: list[Path] = [
        make_audio("hi-res.wav", seconds=2.0, sample_rate=96_000, channels=2, subtype="PCM_24"),
        make_audio("narration.mp3", seconds=2.0),
        Path(shutil.copy(FIXTURES / "ch02_take1.wav", user_dir / "ch02.wav")),
        FIXTURES / "ch01_take1.wav",  # imported straight from the committed fixtures
        FIXTURES / "ch01_take2.wav",
    ]
    before = {p: fingerprint(p) for p in originals}

    loc = tmp_path / "projects"
    loc.mkdir()
    proj = client.post("/api/projects", json={"location": str(loc), "name": "Immutable"}).json()

    # Import every way there is: copy, reference, re-import of a duplicate, upload.
    wait(
        client,
        client.post(
            "/api/project/sources/import",
            json={"paths": [str(p) for p in originals[:3]], "mode": "copy"},
        ),
    )
    wait(
        client,
        client.post(
            "/api/project/sources/import", json={"paths": [str(originals[3])], "mode": "reference"}
        ),
    )
    wait(
        client,
        client.post(
            "/api/project/sources/import", json={"paths": [str(originals[0])], "mode": "reference"}
        ),
    )
    wait(
        client,
        client.put(
            "/api/project/sources/upload",
            params={"filename": "up.wav"},
            content=originals[4].read_bytes(),
        ),
    )
    sources = client.get("/api/project").json()["sources"]
    assert len(sources) == 5

    # Peaks, then peaks regenerated after the cache is cleared.
    for s in sources:
        assert client.get(f"/api/project/sources/{s['id']}/peaks").status_code == 200
    shutil.rmtree(Path(proj["path"]) / "cache" / "peaks")
    for s in sources:
        assert client.get(f"/api/project/sources/{s['id']}/peaks").status_code == 200

    # Close, reopen (verifies every hash), relink a reference source to itself.
    client.post("/api/project/close")
    client.post("/api/projects/open", json={"path": proj["path"]})
    for job in client.get("/api/jobs").json():
        client.app.state.engine.jobs.wait(job["id"])  # type: ignore[attr-defined]
    assert {s["status"] for s in client.get("/api/project").json()["sources"]} == {"ok"}
    ref = next(s for s in sources if s["mode"] == "reference")
    assert (
        client.post(
            f"/api/project/sources/{ref['id']}/relink", json={"path": ref["path"]}
        ).status_code
        == 200
    )
    client.post("/api/project/close")

    after = {p: fingerprint(p) for p in originals}
    assert after == before

    # And every copy inside the project is byte-identical to its original.
    by_hash = {before[p][0] for p in originals}
    for s in sources:
        stored = Path(proj["path"]) / s["path"] if s["mode"] == "copy" else Path(s["path"])
        assert hashlib.sha256(stored.read_bytes()).hexdigest() == s["sha256"]
        assert s["sha256"] in by_hash
