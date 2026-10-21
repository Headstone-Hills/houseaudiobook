"""Speech-model downloads (M9) against a fake Hugging Face: pinned paths,
progress, resume, checksum failures, cancel, and the API."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fakehf import FakeHub, fake_model
from fastapi.testclient import TestClient

from hillaudio.api.app import create_app
from hillaudio.api.guard import DEFAULT_HOSTS
from hillaudio.asr import models
from hillaudio.progress import Cancelled

WEIGHTS = bytes(range(256)) * 9000  # 2.3 MB: several 1 MB chunks
FAKE, FILES = fake_model("fake.en", WEIGHTS, repo="Org/fake-model")


@pytest.fixture
def hf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeHub]:
    s = FakeHub()
    s.add(FAKE, FILES)
    monkeypatch.setenv("HILLAUDIO_MODEL_ENDPOINT", s.url)
    monkeypatch.setenv("HILLAUDIO_MODELS", str(tmp_path / "models"))
    monkeypatch.setitem(models.CATALOG, FAKE.name, FAKE)
    yield s
    s.httpd.shutdown()


def test_download_verifies_and_installs(hf: FakeHub, tmp_path: Path) -> None:
    seen: list[float] = []
    path = models.download("fake.en", lambda f, _m: seen.append(f))
    assert path == tmp_path / "models" / "fake.en"
    assert (path / "model.bin").read_bytes() == WEIGHTS and (path / models.MARKER).is_file()
    assert seen[-1] == 1.0 and seen == sorted(seen) and len(seen) > 2
    # Pinned: fetched at the recorded revision, never "main".
    assert all(p.startswith("/Org/fake-model/resolve/abc123/") for p, _ in hf.requests)
    assert models.local_dir(FAKE) == path
    hf.requests.clear()
    assert models.download("fake.en") == path and hf.requests == []  # already there


def test_an_interrupted_download_resumes(hf: FakeHub, tmp_path: Path) -> None:
    hf.fail_after = 1_500_000
    with pytest.raises(models.ModelError, match="resumes"):
        models.download("fake.en")
    assert models.local_dir(FAKE) is None  # nothing half-installed
    part = tmp_path / "models" / "fake.en" / "model.bin.part"
    assert part.stat().st_size >= 1_000_000
    hf.fail_after = None
    hf.requests.clear()
    models.download("fake.en")
    ranges = [r for p, r in hf.requests if p.endswith("model.bin")]
    assert ranges and ranges[0] is not None and ranges[0].startswith("bytes=")
    assert models.local_dir(FAKE) is not None


def test_a_bad_checksum_is_discarded(hf: FakeHub, tmp_path: Path) -> None:
    key = next(k for k in hf.files if k.endswith("model.bin"))
    hf.files[key] = WEIGHTS[:-1] + b"X"  # same size, different content
    with pytest.raises(models.ModelError, match="checksum"):
        models.download("fake.en")
    d = tmp_path / "models" / "fake.en"
    assert not (d / "model.bin").exists() and not (d / "model.bin.part").exists()
    assert models.local_dir(FAKE) is None


def test_cancel_keeps_the_partial_file_for_later(hf: FakeHub, tmp_path: Path) -> None:
    calls = {"n": 0}

    def cancel_after_one_chunk() -> None:
        calls["n"] += 1
        if calls["n"] > 2:
            raise Cancelled()

    with pytest.raises(Cancelled):
        models.download("fake.en", check_cancel=cancel_after_one_chunk)
    assert (tmp_path / "models" / "fake.en" / "model.bin.part").is_file()
    assert models.local_dir(FAKE) is None


def test_remove_and_unknown(hf: FakeHub) -> None:
    models.download("fake.en")
    assert models.remove("fake.en") and models.local_dir(FAKE) is None
    with pytest.raises(models.ModelError):
        models.get("huge-v9")


def test_api(hf: FakeHub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    with TestClient(create_app(hosts=DEFAULT_HOSTS | {"testserver"})) as c:
        v = c.get("/api/models").json()
        fake = next(m for m in v["models"] if m["name"] == "fake.en")
        assert not fake["installed"] and fake["revision"] == "abc123" and v["source"] == hf.url
        small = next(m for m in v["models"] if m["name"] == "small.en")
        assert small["size_mb"] == 486 and small["license"] == "MIT"
        r = c.post("/api/models/fake.en/download")
        job = c.app.state.engine.jobs.wait(r.json()["job_id"], 30)  # type: ignore[attr-defined]
        assert job.status == "done", job.error
        assert next(m for m in c.get("/api/models").json()["models"] if m["name"] == "fake.en")[
            "installed"
        ]
        assert c.post("/api/models/nope/download").status_code == 404
        r = c.delete("/api/models/fake.en")
        assert r.json()["removed"] is True
        # The ASR info lists only verifiable models, with their download state.
        info = c.get("/api/asr").json()
        assert {m["name"] for m in info["models"]} == {
            "tiny.en",
            "base.en",
            "small.en",
            "medium.en",
        }
        assert all(m["downloaded"] is False for m in info["models"])


def test_transcription_needs_a_downloaded_model(hf: FakeHub) -> None:
    pytest.importorskip("faster_whisper")
    from hillaudio.asr.base import AsrSettings, AsrUnavailable
    from hillaudio.asr.whisper import _model

    with pytest.raises(AsrUnavailable, match=r"isn't downloaded yet \(about 486 MB\)"):
        _model(AsrSettings(model="small.en"))
