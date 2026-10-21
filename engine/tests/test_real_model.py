"""Opt-in: the real small.en model through the API pipeline on the fixtures.

Downloads ~460 MB on first use. Run with:

    HILLAUDIO_MODEL_TESTS=1 HILLAUDIO_MODELS=~/.hillaudiobook/models \\
        engine/.venv/bin/python -m pytest engine/tests/test_real_model.py

M3 acceptance: the report on fixtures matches the injected errors (D-028 targets:
every read sentence found, the injected misread and skip flagged, no false flags).
M4 acceptance: the Issues list is exactly the skip and the two long pauses.
M5 acceptance: cleanup removes exactly the stutters, false start and commentary,
and no line that must stay.
M6 acceptance: the two lines read twice are grouped; the clean pickup is chosen
over the misread take, confidently.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from hillaudio.api.app import create_app
from hillaudio.api.guard import DEFAULT_HOSTS

pytestmark = pytest.mark.skipif(
    os.environ.get("HILLAUDIO_MODEL_TESTS") != "1",
    reason="set HILLAUDIO_MODEL_TESTS=1 to run the real speech model",
)
TEXT_ERRORS = {"missing", "substitution", "transposition", "reordered"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HILLAUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "HILLAUDIO_MODELS",
        os.environ.get("HILLAUDIO_MODELS", str(Path("~/.hillaudiobook/models").expanduser())),
    )
    with TestClient(create_app(hosts=DEFAULT_HOSTS | {"testserver"})) as c:
        yield c


def wait(client: TestClient, r: Any, timeout: float = 900) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    job = client.app.state.engine.jobs.wait(r.json()["job_id"], timeout)  # type: ignore[attr-defined]
    assert job.status == "done", job.error
    data: dict[str, Any] = job.to_json()
    return data


def test_small_en_finds_exactly_the_injected_errors(client: TestClient, tmp_path: Path) -> None:
    loc = tmp_path / "projects"
    loc.mkdir()
    client.post("/api/projects", json={"location": str(loc), "name": "Real"})
    names = ["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"]
    wait(
        client,
        client.post(
            "/api/project/sources/import", json={"paths": [str(FIXTURES / n) for n in names]}
        ),
    )
    wait(
        client,
        client.post(
            "/api/project/manuscript", json={"source": {"path": str(FIXTURES / "manuscript.txt")}}
        ),
    )
    wait(client, client.post("/api/project/analysis", json={}))

    cmp = client.get("/api/project/comparison").json()
    flagged = {
        (r["source_label"], s["ordinal"])
        for s in cmp["sentences"]
        for r in s["readings"]
        if r["coverage"] >= 0.999 and any(d["kind"] in TEXT_ERRORS for d in r["diffs"])
    }
    assert flagged == {("ch01_take1", 4), ("ch01_take1", 6)}  # the misread and the skip
    # Every other sentence has a take without text errors. (Its score can be under
    # 1.0: a stutter is an "extra" difference, which cleanup removes in M5.)
    for s in cmp["sentences"]:
        clean = [
            r
            for r in s["readings"]
            if r["coverage"] >= 0.999 and not any(d["kind"] in TEXT_ERRORS for d in r["diffs"])
        ]
        assert clean or s["ordinal"] == 6, s
    extras = {
        s["ordinal"]: [
            d["heard"] for r in s["readings"] for d in r["diffs"] if d["kind"] == "extra"
        ]
        for s in cmp["sentences"]
    }
    assert extras[2] == ["The"] and extras[9] == ["The"]  # the two stutters
    assert any("water" in u["text"] for u in cmp["unaligned"])  # the commentary

    # M4: the misread has a clean pickup, so only the skip is a flub; plus two pauses.
    issues = client.get("/api/project/issues").json()["issues"]
    got = sorted((i["kind"], i["severity"], i["source_label"], i["page_label"]) for i in issues)
    assert got == [
        ("flub", "high", "ch01_take1", "2"),
        ("silence", "medium", "ch01_take1", "1"),
        ("silence", "medium", "ch02_take1", "3"),
    ]

    # M5: what cleanup removed, against the ground truth.
    from fakeasr import TRUTH

    regions = [r for r in client.get("/api/project/edl").json()["regions"] if r["kind"] != "take"]
    print("real cleanup:", [(r["source_label"], r["kind"], r["start"], r["end"]) for r in regions])
    truth = sorted(
        (f["name"][:-4], e["type"], e["start"], e["end"])
        for f in TRUTH["files"]
        for e in f["events"]
        if e["type"] in ("stutter", "false_start", "commentary")
    )
    got = sorted((r["source_label"], r["kind"], r["start"], r["end"]) for r in regions)
    assert [(g[0], g[1]) for g in got] == [(w[0], w[1]) for w in truth]
    assert all(r["status"] == "removed" for r in regions)
    for g, w in zip(got, truth, strict=True):
        assert abs(g[2] - w[2]) < 0.1 and abs(g[3] - w[3]) < 0.1, (g, w)

    # M6: take groups.
    groups = client.get("/api/project/takes").json()["groups"]
    by_text = {g["sentence_text"][:12]: g for g in groups}
    assert set(by_text) == {"But it was a", "The hare soo"}
    s04 = by_text["But it was a"]
    pref = next(t for t in s04["takes"] if t["status"] == "preferred")
    assert pref["source_label"] == "ch01_take2" and s04["confident"]
