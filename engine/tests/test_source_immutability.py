"""Non-negotiable (CLAUDE.md): imported source audio and manuscripts are never
modified, moved or deleted.

Runs every workflow that touches sources, through the API, then proves each
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


def test_m2_manuscript_workflows_leave_originals_untouched(
    client: TestClient, tmp_path: Path
) -> None:
    user_dir = tmp_path / "manuscripts"
    user_dir.mkdir()
    originals = [
        Path(shutil.copy(FIXTURES / "manuscript.docx", user_dir / "book.docx")),
        FIXTURES / "manuscript.pdf",  # straight from the committed fixtures
        FIXTURES / "manuscript.txt",
    ]
    before = {p: fingerprint(p) for p in originals}

    loc = tmp_path / "projects"
    loc.mkdir()
    proj = client.post("/api/projects", json={"location": str(loc), "name": "Text"}).json()

    for path in originals:
        src = {"path": str(path)}
        assert (
            client.post("/api/project/manuscript/preview", json={"source": src}).status_code == 200
        )
        wait(client, client.post("/api/project/manuscript", json={"source": src}))
        wait(
            client,
            client.put("/api/project/manuscript/settings", json={"settings": {"mode": "estimate"}}),
        )
    up = client.put(
        "/api/project/manuscript/upload",
        params={"filename": "up.docx"},
        content=originals[0].read_bytes(),
    ).json()
    wait(
        client,
        client.post("/api/project/manuscript", json={"source": {"upload_id": up["upload_id"]}}),
    )
    client.post("/api/project/close")
    client.post("/api/projects/open", json={"path": proj["path"]})
    for job in client.get("/api/jobs").json():
        client.app.state.engine.jobs.wait(job["id"])  # type: ignore[attr-defined]
    assert client.get("/api/project/manuscript").json()["status"] == "ok"
    client.post("/api/project/close")

    assert {p: fingerprint(p) for p in originals} == before


def test_m3_m4_analysis_leaves_sources_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transcribe, Compare to Text, cleanup, the issue checks, decisions, edits,
    undo/redo, excerpts and renders only read the sources (one referenced in place)."""
    from fakeasr import FakeTranscriber

    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    names = ["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"]
    originals = [FIXTURES / n for n in names] + [FIXTURES / "manuscript.txt"]
    before = {p: fingerprint(p) for p in originals}
    app = create_app(hosts=DEFAULT_HOSTS | {"testserver"}, transcriber_factory=FakeTranscriber)
    with TestClient(app) as c:
        loc = tmp_path / "projects"
        loc.mkdir()
        c.post("/api/projects", json={"location": str(loc), "name": "Analysis"})
        wait(
            c,
            c.post(
                "/api/project/sources/import",
                json={"paths": [str(FIXTURES / names[0])], "mode": "reference"},
            ),
        )
        wait(
            c,
            c.post(
                "/api/project/sources/import",
                json={"paths": [str(FIXTURES / n) for n in names[1:]]},
            ),
        )
        wait(
            c,
            c.post(
                "/api/project/manuscript",
                json={"source": {"path": str(FIXTURES / "manuscript.txt")}},
            ),
        )
        wait(c, c.post("/api/project/analysis", json={}))
        wait(c, c.post("/api/project/analysis", json={"stages": ["compare_text"]}))
        assert c.get("/api/project/comparison").json()["sentences"]
        found = c.get("/api/project/issues").json()["issues"]
        assert found
        ids = [i["id"] for i in found]
        c.patch("/api/project/issues", json={"ids": ids, "status": "resolved"})
        wait(c, c.post("/api/project/analysis", json={"stages": ["flubs", "silences"]}))
        for i in found:
            if i["source_file_id"] is not None:
                r = c.get(
                    f"/api/project/sources/{i['source_file_id']}/excerpt",
                    params={"start": i["start"], "end": i["end"]},
                )
                assert r.status_code == 200
        # M5: cleanup cuts, restores, rolling edits, undo/redo and preview renders.
        regions = c.get("/api/project/edl").json()["regions"]
        assert regions
        rid = regions[0]["id"]
        c.post("/api/project/edl/regions/status", json={"ids": [rid], "status": "restored"})
        c.post(f"/api/project/edl/regions/{rid}/roll", json={"delta": 0.05})
        c.post("/api/project/edl/cut", json={"source_id": 1, "start": 0.0, "end": 0.3})
        c.post("/api/project/edl/undo")
        c.post("/api/project/edl/redo")
        r = c.get("/api/project/edl/render", params={"start": 0, "end": 20})
        assert r.status_code == 200
    assert {p: fingerprint(p) for p in originals} == before
