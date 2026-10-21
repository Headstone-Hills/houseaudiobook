"""Takes through the API (M6): groups, suggestions applied to the EDL, choosing,
going back, re-runs keeping choices, and the round trip."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import test_analysis_api as base
import test_edl_api as edl_api
from fastapi.testclient import TestClient

from hillaudio.edl import store
from hillaudio.edl.render import Input, render
from hillaudio.ingest.decode import iter_blocks
from hillaudio.store import schema

client, factory, setup_book, wait = base.client, base.factory, base.setup_book, base.wait
project, full_render, ORIGINAL = edl_api.project, edl_api.full_render, edl_api.ORIGINAL


def run(c: TestClient, stages: list[str] | None = None) -> dict[str, Any]:
    job = wait(
        c, c.post("/api/project/analysis", json={} if stages is None else {"stages": stages})
    )
    assert job["status"] == "done", job
    return job


def groups(c: TestClient) -> dict[int, dict[str, Any]]:
    """By sentence ordinal (the fixture's s04 and s10)."""
    r = c.get("/api/project/takes")
    assert r.status_code == 200, r.text
    units = {
        s["unit_id"]: s["ordinal"] for s in c.get("/api/project/comparison").json()["sentences"]
    }
    return {units[g["manuscript_unit_id"]]: g for g in r.json()["groups"]}


def preferred(g: dict[str, Any]) -> dict[str, Any]:
    return next(t for t in g["takes"] if t["status"] == "preferred")


def order(c: TestClient) -> list[tuple[str, float]]:
    labels = {s.id: s.label for s in project(c).sources()}
    return [
        (labels[x["source_id"]], round(x["source_in"], 2))
        for x in c.get("/api/project/edl").json()["clips"]
    ]


def pickup_at(o: list[tuple[str, float]]) -> int | None:
    """Where take 2's reading (not its leading room tone) plays, if moved."""
    return next((i for i, (lab, t) in enumerate(o) if lab == "ch01_take2" and t > 0.4), None)


def flubs(c: TestClient) -> list[str]:
    issues = c.get("/api/project/issues").json()["issues"]
    return sorted(
        i["sentence_text"][:20] for i in issues if i["kind"] == "flub" and i["status"] == "open"
    )


def test_detection_applies_the_suggestions(client: TestClient) -> None:
    setup_book(client)
    job = run(client)
    assert job["result"]["takes"] == {"groups": 2, "kept": 0, "takes_added": 0}
    g = groups(client)
    assert set(g) == {4, 10}
    s04, s10 = g[4], g[10]
    assert preferred(s04)["source_label"] == "ch01_take2" and s04["confident"]
    assert s04["state"] == "suggested" and s04["reason"] == "fewer text errors"
    later = max(s10["takes"], key=lambda t: t["start"])
    assert preferred(s10)["id"] == later["id"] and not s10["confident"]

    # The pickup plays where the misread line was: right after ch01_take1's s03
    # and the pause after it, before s05.
    o = order(client)
    i = pickup_at(o)
    assert i is not None and o[i - 1][0] == "ch01_take1" and o[i + 1][0] == "ch01_take1"
    # ...and the misread take, the false start, the commentary are all off the timeline.
    kinds = sorted(
        (r["kind"], r["source_label"], r["status"])
        for r in client.get("/api/project/edl").json()["regions"]
        if r["kind"] == "take"
    )
    assert kinds == [
        ("take", "ch01_take1", "removed"),
        ("take", "ch01_take2", "restored"),
        ("take", "ch02_take1", "removed"),
        ("take", "ch02_take1", "restored"),
    ]
    # Flubs are judged on the preferred take: only the skip remains.
    assert flubs(client) == ["I thought those grap"]
    assert job["result"]["issues"]["new"] == 3  # the skip and two long pauses


def test_the_pickup_audio_plays_in_place(client: TestClient) -> None:
    setup_book(client)
    run(client)
    p = project(client)
    tl = store.timeline(p)
    take2 = next(s for s in p.sources() if s.label == "ch01_take2")
    clip = next(c for c in tl.clips if c.source_id == take2.id and c.source_in > 0)
    inputs = {s.id: Input(p.resolve(s), s.sample_rate, 1, s.num_frames) for s in p.sources()}
    # Away from the crossfades: the middle of the clip is take 2's audio exactly.
    mid = (clip.timeline_start + 2000, clip.timeline_end - 2000)
    audio = render(tl.clips, inputs, tl.rate, 1, *mid)
    src = np.concatenate(list(iter_blocks(p.resolve(take2), 1)))
    a = clip.source_in + 2000
    np.testing.assert_array_equal(audio, src[a : a + len(audio)])


def test_choose_back_and_undo(client: TestClient) -> None:
    setup_book(client)
    run(client)
    s04 = groups(client)[4]
    misread = next(t for t in s04["takes"] if t["source_label"] == "ch01_take1")
    r = client.post(f"/api/project/takes/{s04['id']}/choose", json={"take_id": misread["id"]})
    assert r.status_code == 200
    g = next(x for x in r.json()["groups"] if x["id"] == s04["id"])
    assert preferred(g)["id"] == misread["id"] and g["state"] == "confirmed"
    assert g["previous_take_id"] == preferred(s04)["id"]
    assert r.json()["undo"].startswith("Take 2 for “But it was all in vain")
    # The pickup went home: ch01_take2 is in its own place again, and is cut.
    assert pickup_at(order(client)) is None
    # Re-checking: the chosen take has the misread, so it's a flub now.
    run(client, ["flubs"])
    assert flubs(client) == ["But it was all in va", "I thought those grap"]

    # One click back to the previous choice.
    r = client.post(f"/api/project/takes/{s04['id']}/back")
    assert (
        preferred(next(x for x in r.json()["groups"] if x["id"] == s04["id"]))["source_label"]
        == "ch01_take2"
    )
    assert pickup_at(order(client)) is not None

    # Undo both choices: the suggestion again, still unconfirmed.
    client.post("/api/project/edl/undo")
    client.post("/api/project/edl/undo")
    g = groups(client)[4]
    assert preferred(g)["source_label"] == "ch01_take2" and g["state"] == "suggested"


def test_round_trip_with_takes(client: TestClient) -> None:
    setup_book(client)
    run(client)
    s04 = groups(client)[4]
    other = next(t for t in s04["takes"] if t["status"] != "preferred")
    client.post(f"/api/project/takes/{s04['id']}/choose", json={"take_id": other["id"]})
    p = project(client)
    while client.post("/api/project/edl/undo").status_code == 200:
        pass
    with p.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM edl_segment").fetchone()[0] == 0
    assert client.get("/api/project/takes").json()["groups"] == []
    np.testing.assert_array_equal(full_render(p), ORIGINAL)


def test_rerun_keeps_choices(client: TestClient) -> None:
    setup_book(client)
    run(client)
    s10 = groups(client)[10]
    first = min(s10["takes"], key=lambda t: t["start"])
    client.post(f"/api/project/takes/{s10['id']}/choose", json={"take_id": first["id"]})
    job = run(client, ["takes", "flubs"])
    assert job["result"]["takes"] == {"groups": 0, "kept": 2, "takes_added": 0}
    g = groups(client)
    assert preferred(g[10])["id"] == first["id"] and g[10]["state"] == "confirmed"
    assert len(client.get("/api/project/takes").json()["groups"]) == 2


def test_confirm_mark_and_bad_requests(client: TestClient) -> None:
    setup_book(client)
    run(client)
    g = groups(client)
    r = client.post("/api/project/takes/confirm", json={"ids": [g[4]["id"], g[10]["id"]]})
    assert all(x["state"] == "confirmed" for x in r.json()["groups"])
    assert r.json()["undo"] == "Confirm 2 take choices"
    alt = next(t for t in g[10]["takes"] if t["status"] != "preferred")
    r = client.post(f"/api/project/takes/take/{alt['id']}/mark", json={"status": "rejected"})
    assert r.status_code == 200
    pref = preferred(g[10])
    r = client.post(f"/api/project/takes/take/{pref['id']}/mark", json={"status": "rejected"})
    assert r.status_code == 400 and "preferred" in r.json()["detail"]
    r = client.post(f"/api/project/takes/take/{alt['id']}/mark", json={"status": "preferred"})
    assert r.status_code == 400
    assert client.post("/api/project/takes/99/choose", json={"take_id": 1}).status_code == 404
    r = client.post(f"/api/project/takes/{g[4]['id']}/choose", json={"take_id": 999})
    assert r.status_code == 404
    r = client.post(f"/api/project/takes/{g[4]['id']}/back")
    assert r.status_code == 400  # no previous choice yet


def test_takes_need_a_current_comparison(client: TestClient) -> None:
    setup_book(client)
    job = wait(client, client.post("/api/project/analysis", json={"stages": ["takes"]}))
    assert job["status"] == "failed" and "Compare to Text" in job["error"]


def test_migration_keeps_m5_regions(tmp_path: Path) -> None:
    """v5 -> v6 rebuilds edit_region (new kind 'take'): existing rows survive."""
    db = tmp_path / "p.db"
    conn = sqlite3.connect(db)
    for i, m in enumerate(schema.MIGRATIONS[:5]):
        conn.executescript(f"BEGIN;\n{m}\nPRAGMA user_version = {i + 1};\nCOMMIT;")
    conn.execute(
        "INSERT INTO source_file VALUES (1, 'copy', 's.wav', '/s.wav', 's.wav', 'abc', 1, 'wav',"
        " 'pcm_s16le', 1, 16000, 1, 16, NULL, 16000, 1.0, 's', 0, 'now')"
    )
    conn.execute(
        "INSERT INTO edit_region VALUES (7, 1, 10, 20, 'stutter', 'removed', 'auto', 'auto',"
        " 0.9, 'r', 'the', 'k', NULL, NULL, '{}', 'now', 'now')"
    )
    conn.commit()
    conn.close()
    c = schema.connect(str(db))
    schema.migrate(c)
    row = dict(c.execute("SELECT * FROM edit_region").fetchone())
    assert (row["id"], row["kind"], row["start_sample"], row["stable_key"]) == (
        7,
        "stutter",
        10,
        "k",
    )
    c.execute(
        "INSERT INTO edit_region VALUES (8, 1, 30, 40, 'take', 'removed', 'auto', 'auto',"
        " NULL, 'r', '', NULL, NULL, NULL, '{}', 'now', 'now')"
    )
    assert c.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
    with pytest.raises(sqlite3.IntegrityError):
        c.execute(
            "INSERT INTO edit_region VALUES (9, 1, 30, 40, 'bogus', 'removed', 'auto', 'auto',"
            " NULL, 'r', '', NULL, NULL, NULL, '{}', 'now', 'now')"
        )
