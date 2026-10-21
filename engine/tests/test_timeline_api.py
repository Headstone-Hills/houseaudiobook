"""The timeline editor through the API (M7): split, ripple delete, trim, roll,
move (within and across tracks), clip gain/fades, tracks, markers, lanes, and
the round trip back to the original audio after undoing everything."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import test_analysis_api as base
import test_edl_api as edl_api
from conftest import FIXTURES
from fastapi.testclient import TestClient

from hillaudio.edl import timeline_store
from hillaudio.edl.model import duration
from hillaudio.edl.render import Input, render
from hillaudio.ingest.decode import iter_blocks

client, factory, setup_book, wait = base.client, base.factory, base.setup_book, base.wait
project = edl_api.project
ORIGINAL = edl_api.ORIGINAL[:, 0]  # mono fixtures
RATE = 16000
SRC = {n[:-4]: np.concatenate(list(iter_blocks(FIXTURES / n, 1)))[:, 0] for n in base.AUDIO}


def import_audio(c: TestClient) -> None:
    j = wait(
        c,
        c.post(
            "/api/project/sources/import", json={"paths": [str(FIXTURES / n) for n in base.AUDIO]}
        ),
    )
    assert j["status"] == "done"


def tl(c: TestClient) -> dict[str, Any]:
    r = c.get("/api/project/timeline")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def post(c: TestClient, path: str, body: dict[str, Any], status: int = 200) -> dict[str, Any]:
    r = c.post(f"/api/project/timeline/{path}", json=body)
    assert r.status_code == status, r.text
    out: dict[str, Any] = r.json()
    return out


def mixdown(c: TestClient, channels: int | None = None) -> np.ndarray:
    p = project(c)
    st = timeline_store.load(p)
    inputs = {
        s.id: Input(p.resolve(s), s.sample_rate, s.channels, s.num_frames) for s in p.sources()
    }
    ch = channels or timeline_store.mix_channels(st)
    return render(st.clips, inputs, st.rate, ch, 0, duration(st.clips), tracks=st.tracks)


def narration(view: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in view["clips"] if x["track_id"] == 1]


def undo_all(c: TestClient) -> None:
    while c.post("/api/project/edl/undo").status_code == 200:
        pass


def test_default_timeline(client: TestClient) -> None:
    import_audio(client)
    v = tl(client)
    assert [t["name"] for t in v["tracks"]] == ["Narration"]
    clips = narration(v)
    assert [x["source_label"] for x in clips] == ["ch01_take1", "ch01_take2", "ch02_take1"]
    assert clips[1]["timeline_start"] == pytest.approx(clips[0]["timeline_end"])
    assert v["duration"] == pytest.approx(len(ORIGINAL) / RATE)
    assert v["channels"] == 1


def test_split_ripple_delete_trim_and_roll(client: TestClient) -> None:
    import_audio(client)
    v = post(client, "split", {"t": 5.0})
    assert len(narration(v)) == 4 and v["undo"] == "Split"
    assert narration(v)[1]["timeline_start"] == pytest.approx(5.0)

    # Ripple delete 1 s: everything after moves up.
    d0 = v["duration"]
    v = post(client, "ripple-delete", {"start": 6.0, "end": 7.0})
    assert v["duration"] == pytest.approx(d0 - 1.0, abs=1e-4)
    out = mixdown(client)
    want = np.concatenate([ORIGINAL[: 6 * RATE], ORIGINAL[7 * RATE :]])
    h = 80  # the join's crossfade
    np.testing.assert_array_equal(out[: 6 * RATE - h, 0], want[: 6 * RATE - h])
    np.testing.assert_array_equal(out[6 * RATE + h :, 0], want[6 * RATE + h :])

    # Rolling edit on that join: the length doesn't change.
    v2 = post(client, "roll", {"t": 6.0, "delta": 0.25})
    assert v2["duration"] == pytest.approx(v["duration"], abs=1e-4)
    after = next(x for x in v2["removed"] if x["kind"] == "cut")
    assert after["start"] == pytest.approx(6.25, abs=1e-3)
    client.post("/api/project/edl/undo")

    # Trim the clip after the join: inward cuts, outward brings it back.
    clip = next(x for x in narration(tl(client)) if x["timeline_start"] == pytest.approx(6.0))
    v3 = post(client, "trim", {"ref": clip["ref"], "edge": "start", "t": 6.5})
    assert v3["duration"] == pytest.approx(v["duration"] - 0.5, abs=1e-3)
    clip = next(x for x in narration(v3) if x["timeline_start"] == pytest.approx(6.0))
    # Drag 1 s outward: the 0.5 s trim and half of the deleted second come back.
    v4 = post(client, "trim", {"ref": clip["ref"], "edge": "start", "t": 5.0})
    assert v4["duration"] == pytest.approx(v["duration"] + 0.5, abs=1e-3)
    # (The clip still starts at 6.0: what came back is part of it now.)
    clip = next(x for x in narration(v4) if x["timeline_start"] == pytest.approx(6.0))
    v4 = post(client, "trim", {"ref": clip["ref"], "edge": "start", "t": 5.5})  # the rest
    assert v4["duration"] == pytest.approx(v["duration"] + 1.0, abs=1e-3)
    np.testing.assert_array_equal(mixdown(client)[:, 0], ORIGINAL)  # restored, sample for sample

    post(
        client, "trim", {"ref": narration(v4)[0]["ref"], "edge": "start", "t": 0.0}, 400
    )  # nothing to bring back


def test_move_within_the_narration(client: TestClient) -> None:
    import_audio(client)
    v = tl(client)
    last = narration(v)[2]
    v = post(client, "move", {"ref": last["ref"], "t": 0.0, "track_id": 1})
    assert [x["source_label"] for x in narration(v)] == ["ch02_take1", "ch01_take1", "ch01_take2"]
    out = mixdown(client)[:, 0]
    np.testing.assert_array_equal(out[: len(SRC["ch02_take1"])], SRC["ch02_take1"])
    # To the very end, then undo both.
    first = narration(v)[0]
    v = post(client, "move", {"ref": first["ref"], "t": v["duration"] + 5, "track_id": 1})
    assert [x["source_label"] for x in narration(v)] == ["ch01_take1", "ch01_take2", "ch02_take1"]
    undo_all(client)
    np.testing.assert_array_equal(mixdown(client)[:, 0], ORIGINAL)


def test_clip_gain_and_fades(client: TestClient) -> None:
    import_audio(client)
    take2 = narration(tl(client))[1]
    v = post(
        client, "clip", {"ref": take2["ref"], "gain_db": -6.0, "fade_in": 0.1, "fade_out": 0.2}
    )
    c = narration(v)[1]
    assert (c["gain_db"], c["fade_in"], c["fade_out"]) == (
        -6.0,
        pytest.approx(0.1),
        pytest.approx(0.2),
    )
    out = mixdown(client)[:, 0]
    a = round(take2["timeline_start"] * RATE)
    n = len(SRC["ch01_take2"])
    got = out[a : a + n]
    env = np.ones(n, dtype=np.float32)
    env[:1600] = np.linspace(0, 1, 1600)
    env[-3200:] = np.linspace(1, 0, 3200)
    np.testing.assert_allclose(
        got, SRC["ch01_take2"] * env * np.float32(10 ** (-6 / 20)), rtol=1e-5, atol=1e-7
    )
    np.testing.assert_array_equal(out[:a], SRC["ch01_take1"])  # neighbours untouched
    post(client, "clip", {"ref": take2["ref"], "gain_db": 99}, 400)
    post(client, "clip", {"ref": {"source_id": 9, "source_in": 0}, "gain_db": 1}, 404)


def test_tracks_free_clips_mute_solo_pan(client: TestClient) -> None:
    import_audio(client)
    v = post(client, "tracks", {"name": "Room tone"})
    room = next(t for t in v["tracks"] if t["name"] == "Room tone")
    take2 = next(s for s in project(client).sources() if s.label == "ch01_take2")
    v = post(
        client,
        f"tracks/{room['id']}/add-source",
        {"source_id": take2.id, "t": 1.0, "start": 0.0, "end": 1.0},
    )
    (free,) = [x for x in v["clips"] if x["track_id"] == room["id"]]
    assert (free["timeline_start"], free["timeline_end"]) == (
        pytest.approx(1.0),
        pytest.approx(2.0),
    )
    out = mixdown(client)[:, 0]
    want = ORIGINAL.copy()
    want[RATE : 2 * RATE] += SRC["ch01_take2"][:RATE]
    np.testing.assert_allclose(out, want, atol=1e-6)

    r = client.patch(f"/api/project/timeline/tracks/{room['id']}", json={"mute": True})
    assert r.status_code == 200
    np.testing.assert_array_equal(mixdown(client)[:, 0], ORIGINAL)
    client.patch(f"/api/project/timeline/tracks/{room['id']}", json={"mute": False, "solo": True})
    solo = mixdown(client)[:, 0]
    assert not solo[:RATE].any() and np.allclose(solo[RATE : 2 * RATE], SRC["ch01_take2"][:RATE])
    v = client.patch(
        f"/api/project/timeline/tracks/{room['id']}", json={"solo": False, "pan": -1.0}
    ).json()
    assert v["channels"] == 2
    st = mixdown(client)
    np.testing.assert_array_equal(st[:, 1], ORIGINAL)  # hard left: the right side is narration only
    assert client.patch("/api/project/timeline/tracks/1", json={"pan": 3}).status_code == 400

    # Move the free clip, then delete it; a track with no clips can go.
    v = post(client, "move", {"ref": free["ref"], "t": 3.0, "track_id": room["id"]})
    assert next(x for x in v["clips"] if x["track_id"] == room["id"])[
        "timeline_start"
    ] == pytest.approx(3.0)
    assert client.delete(f"/api/project/timeline/tracks/{room['id']}").status_code == 400
    assert client.delete(f"/api/project/timeline/clips/{free['ref']['clip_id']}").status_code == 200
    assert client.delete(f"/api/project/timeline/tracks/{room['id']}").status_code == 200
    assert client.delete("/api/project/timeline/tracks/1").status_code == 400


def test_move_a_clip_to_another_track_and_back(client: TestClient) -> None:
    import_audio(client)
    v = post(client, "tracks", {"name": "Alternates"})
    alt = next(t for t in v["tracks"] if t["name"] == "Alternates")
    take2 = narration(v)[1]
    v = post(client, "move", {"ref": take2["ref"], "t": 0.0, "track_id": alt["id"]})
    assert [x["source_label"] for x in narration(v)] == ["ch01_take1", "ch02_take1"]  # closed up
    (moved,) = [x for x in v["clips"] if x["track_id"] == alt["id"]]
    assert moved["source_label"] == "ch01_take2" and moved["timeline_start"] == 0.0
    v = post(client, "move", {"ref": moved["ref"], "t": take2["timeline_start"], "track_id": 1})
    assert [x["source_label"] for x in narration(v)] == ["ch01_take1", "ch01_take2", "ch02_take1"]
    np.testing.assert_array_equal(mixdown(client)[:, 0], ORIGINAL)


def test_markers_follow_edits(client: TestClient) -> None:
    import_audio(client)
    v = post(client, "markers", {"t": 10.0, "label": "breath"})
    (m,) = v["markers"]
    assert m["t"] == pytest.approx(10.0) and m["label"] == "breath"
    v = post(client, "ripple-delete", {"start": 2.0, "end": 3.0})
    assert v["markers"][0]["t"] == pytest.approx(9.0, abs=1e-3)  # moved with the audio
    r = client.patch(f"/api/project/timeline/markers/{m['id']}", json={"label": "check"})
    assert r.json()["markers"][0]["label"] == "check"
    v = post(client, "ripple-delete", {"start": 8.5, "end": 9.5})
    assert v["markers"][0]["t"] is None  # its audio was cut
    assert client.delete(f"/api/project/timeline/markers/{m['id']}").json()["markers"] == []


def test_round_trip_after_every_kind_of_edit(client: TestClient) -> None:
    """SPEC §11 round trip, with the M7 tools in the mix."""
    import_audio(client)
    v = post(client, "split", {"t": 4.0})
    post(client, "ripple-delete", {"start": 1.0, "end": 1.5})
    post(client, "clip", {"ref": narration(tl(client))[1]["ref"], "gain_db": 3.0, "fade_in": 0.05})
    v = post(client, "tracks", {"name": "Room tone"})
    room = next(t for t in v["tracks"] if t["name"] == "Room tone")
    post(client, f"tracks/{room['id']}/add-source", {"source_id": 2, "t": 0.5, "end": 0.5})
    client.patch(f"/api/project/timeline/tracks/{room['id']}", json={"pan": 0.5, "gain_db": -3})
    post(client, "move", {"ref": narration(tl(client))[-1]["ref"], "t": 0.0, "track_id": 1})
    post(client, "markers", {"t": 2.0})
    assert len(mixdown(client)) != len(ORIGINAL)
    undo_all(client)
    v = tl(client)
    assert [t["name"] for t in v["tracks"]] == ["Narration"] and v["markers"] == []
    np.testing.assert_array_equal(mixdown(client)[:, 0], ORIGINAL)


def test_lanes_follow_the_timeline(client: TestClient) -> None:
    setup_book(client)
    wait(
        client,
        client.post("/api/project/analysis", json={"stages": ["transcribe", "compare_text"]}),
    )
    lanes = client.get("/api/project/timeline/lanes", params={"start": 0, "end": 30}).json()
    assert lanes["words"][0]["text"] == "Chapter" and lanes["words"][0]["t0"] == pytest.approx(
        0.5, abs=0.06
    )
    assert lanes["sentences"][0]["ordinal"] == 0
    assert any(p["t1"] - p["t0"] > 3.0 for p in lanes["pauses"])  # the long paragraph pause
    post(client, "ripple-delete", {"start": 0.0, "end": 0.4})
    moved = client.get("/api/project/timeline/lanes", params={"start": 0, "end": 30}).json()
    assert moved["words"][0]["t0"] == pytest.approx(lanes["words"][0]["t0"] - 0.4, abs=0.01)
    assert (
        client.get("/api/project/timeline/lanes", params={"start": 5, "end": 1}).status_code == 400
    )


def test_issue_and_removed_markers(client: TestClient) -> None:
    setup_book(client)
    wait(client, client.post("/api/project/analysis", json={}))
    v = tl(client)
    kinds = sorted(i["kind"] for i in v["issues"])
    assert kinds == ["flub", "silence", "silence"]
    assert all(i["t"] is not None for i in v["issues"])
    assert {r["kind"] for r in v["removed"]} >= {"stutter", "false_start", "commentary", "take"}


def test_bad_requests(client: TestClient) -> None:
    import_audio(client)
    post(client, "split", {"t": 999.0}, 400)
    post(client, "ripple-delete", {"start": 2.0, "end": 1.0}, 400)
    post(client, "roll", {"t": 3.0, "delta": 0.1}, 400)  # not on a join
    post(client, "trim", {"ref": {"clip_id": 77}, "edge": "start", "t": 1}, 404)
    post(client, "trim", {"ref": narration(tl(client))[0]["ref"], "edge": "middle", "t": 1}, 400)
    post(client, "move", {"ref": narration(tl(client))[0]["ref"], "t": 1, "track_id": 9}, 404)
    post(client, "tracks/1/add-source", {"source_id": 1, "t": 0}, 400)
    assert client.patch("/api/project/timeline/markers/5", json={"label": "x"}).status_code == 404


def test_locator_matches_the_simple_mapping_on_a_big_book() -> None:
    """The view maps thousands of issues/cuts/markers: binary search, same answers."""
    import random
    import time
    from types import SimpleNamespace

    from hillaudio.api.timeline import _Locator
    from hillaudio.edl.model import Region, SourceInfo, default_segments, layout, place, to_timeline

    rng = random.Random(3)
    srcs = [SourceInfo(i, 48000, 48000 * 720) for i in range(1, 51)]  # 10 h
    regs = []
    for k in range(3000):
        s, a = rng.randint(1, 50), rng.randint(0, 48000 * 700)
        regs.append(Region(k, s, a, a + rng.randint(2000, 48000), "removed"))
    segs = default_segments(srcs)
    for _ in range(100):
        s, a = rng.randint(1, 50), rng.randint(0, 48000 * 600)
        segs = place(
            segs, s, a, a + 48000 * 5, before=(rng.randint(1, 50), rng.randint(0, 48000 * 700))
        )
    clips = layout(srcs, regs, 48000, segs)
    st = SimpleNamespace(clips=clips, sources={s.id: s for s in srcs}, rate=48000)
    loc = _Locator(st)  # type: ignore[arg-type]
    probes = [(rng.randint(1, 50), rng.randint(0, 48000 * 720 - 1)) for _ in range(3000)]
    t0 = time.perf_counter()
    fast = [loc.at_sample(s, x) for s, x in probes]
    elapsed = time.perf_counter() - t0
    slow = [to_timeline(clips, st.sources, 48000, s, x) for s, x in probes]
    for f, sl in zip(fast, slow, strict=True):
        assert (f is None) == (sl is None)
        if f is not None and sl is not None:
            assert f == pytest.approx(sl / 48000, abs=1 / 48000)
    assert elapsed < 0.5, elapsed  # 3000 lookups on a 10 h timeline
