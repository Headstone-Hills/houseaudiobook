"""Timeline editing (M7): the tools of SPEC §6.6 as undoable EDL operations.

Positions come in as timeline samples (what the user sees); each tool maps them
to the recording underneath and changes only EDL rows: narration segments and
removed regions (D-038/D-043), free clips on other tracks, tracks, markers.
Every tool is one operation-log entry (`timeline_edit`, origin `user`), so undo
and redo cover them like every other edit (D-039).
"""

from __future__ import annotations

import bisect
import json
import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from hillaudio.edl import store
from hillaudio.edl.model import (
    NARRATION,
    Clip,
    FreeClip,
    Region,
    Segment,
    SourceInfo,
    Track,
    layout,
    place,
    set_attrs,
    split,
    timeline_rate,
)
from hillaudio.store.project import Project, now_iso

OP = "timeline_edit"
MIN_CLIP = 1  # samples


class TimelineError(ValueError):
    """A user-facing reason a timeline edit can't be made."""


@dataclass
class State:
    rate: int
    sources: dict[int, SourceInfo]
    labels: dict[int, str]
    regions: list[Region]
    region_rows: list[dict[str, Any]]
    segments: list[Segment]
    tracks: dict[int, Track]
    track_rows: list[dict[str, Any]]
    frees: list[FreeClip]
    clip_rows: dict[int, dict[str, Any]]
    markers: list[dict[str, Any]]
    clips: list[Clip]
    channels: int  # the widest recording


def mix_channels(st: State) -> int:
    """Stereo when any track is panned, else as wide as the widest recording."""
    return 2 if any(tr.pan for tr in st.tracks.values()) else max(1, st.channels)


def _track(r: dict[str, Any]) -> Track:
    return Track(
        r["id"], r["name"], r["kind"], r["gain_db"], r["pan"], bool(r["mute"]), bool(r["solo"])
    )


def _free(r: dict[str, Any]) -> FreeClip:
    return FreeClip(
        r["id"],
        r["track_id"],
        r["source_file_id"],
        r["source_in"],
        r["source_out"],
        r["timeline_start"],
        r["gain_db"],
        r["fade_in"],
        r["fade_out"],
        bool(r["muted"]),
    )


def load(project: Project, conn: sqlite3.Connection | None = None) -> State:
    srcs = project.sources()
    infos = [SourceInfo(s.id, s.sample_rate, s.num_frames) for s in srcs]
    rate = timeline_rate(infos)

    def read(c: sqlite3.Connection) -> State:
        region_rows = store._rows(c)
        regions = store.model_regions(region_rows)
        segs = store.segments(c, infos)
        track_rows = [dict(r) for r in c.execute("SELECT * FROM track ORDER BY position, id")]
        clip_rows = {r["id"]: dict(r) for r in c.execute("SELECT * FROM edl_clip ORDER BY id")}
        markers = [dict(r) for r in c.execute("SELECT * FROM marker ORDER BY id")]
        frees = [_free(r) for r in clip_rows.values()]
        return State(
            rate,
            {s.id: s for s in infos},
            {s.id: s.label for s in srcs},
            regions,
            region_rows,
            segs,
            {r["id"]: _track(r) for r in track_rows},
            track_rows,
            frees,
            clip_rows,
            markers,
            layout(infos, regions, rate, segs, frees),
            max((s.channels for s in srcs), default=1),
        )

    if conn is not None:
        return read(conn)
    with project.db() as c:
        return read(c)


# -- mapping ------------------------------------------------------------------------------


def _src_sample(st: State, c: Clip, t: int) -> int:
    sr = st.sources[c.source_id].sample_rate
    return c.source_in + int(Fraction(t - c.timeline_start, st.rate) * sr)


def clip_at(st: State, t: int, track_id: int = NARRATION) -> Clip | None:
    return next(
        (c for c in st.clips if c.track_id == track_id and c.timeline_start <= t < c.timeline_end),
        None,
    )


def _find_clip(st: State, ref: dict[str, Any]) -> Clip:
    """A clip by reference: {"clip_id"} for other tracks, or a narration clip by
    {"source_id", "source_in"} (narration clips are derived, so that's their id)."""
    if ref.get("clip_id") is not None:
        c = next((c for c in st.clips if c.clip_id == ref["clip_id"]), None)
    else:
        c = next(
            (
                c
                for c in st.clips
                if c.track_id == NARRATION
                and c.source_id == ref.get("source_id")
                and c.source_in == ref.get("source_in")
            ),
            None,
        )
    if c is None:
        raise KeyError("no such clip")
    return c


def _cut_row(
    conn: sqlite3.Connection, source_id: int, a: int, b: int, reason: str, **detail: Any
) -> dict[str, Any]:
    return store._new_row(
        store._next_id(conn), source_id, a, b, "cut", "removed", "user", reason, "", detail=detail
    )


def _commit(
    project: Project, conn: sqlite3.Connection, changes: list[store.Change], summary: str
) -> None:
    if store._commit(project, conn, OP, changes, "user", summary) is None:
        raise TimelineError("nothing changed")


# -- tools ----------------------------------------------------------------------------------


def split_at(project: Project, t: int, track_id: int = NARRATION) -> None:
    """Split the clip under the playhead (S)."""
    with project.db() as conn:
        st = load(project, conn)
        c = clip_at(st, t, track_id)
        if c is None or t == c.timeline_start:
            raise TimelineError("no clip to split there")
        x = _src_sample(st, c, t)
        if c.track_id == NARRATION:
            change = store.segments_change(
                conn, list(st.sources.values()), split(st.segments, c.source_id, x)
            )
            _commit(project, conn, [change], "Split")
            return
        row = st.clip_rows[c.clip_id or 0]
        left = row | {"source_out": x, "fade_out": 0}
        right = row | {
            "id": store._next_id(conn, "edl_clip"),
            "source_in": x,
            "timeline_start": t,
            "fade_in": 0,
        }
        _commit(project, conn, [("edl_clip", row, left), ("edl_clip", None, right)], "Split")


def ripple_delete(project: Project, t0: int, t1: int) -> None:
    """Remove [t0, t1) of the narration; everything after moves up (Delete)."""
    if t1 <= t0:
        raise TimelineError("select a range first")
    with project.db() as conn:
        st = load(project, conn)
        changes: list[store.Change] = []
        for c in st.clips:
            if c.track_id != NARRATION or c.timeline_end <= t0 or c.timeline_start >= t1:
                continue
            a = _src_sample(st, c, max(t0, c.timeline_start))
            b = _src_sample(st, c, min(t1, c.timeline_end)) if t1 < c.timeline_end else c.source_out
            row = _cut_row(conn, c.source_id, a, b, "ripple delete")
            store._write_changes(conn, [store._r(None, row)])  # so the next id is fresh
            changes.append(store._r(None, row))
        if not changes:
            raise TimelineError("nothing on the narration track there")
        # Rows were written above; log them as one entry.
        store._log_entries(
            project,
            conn,
            OP,
            [
                {"table": "edit_region", "id": r[2]["id"], "before": None, "after": r[2]}
                for r in changes
            ],
            "user",
            "Ripple delete",
        )


def trim(project: Project, ref: dict[str, Any], edge: str, t: int) -> None:
    """Move a clip's start or end edge to timeline sample t (Shift+[ / Shift+]).

    Narration: trimming inward cuts (ripple); outward re-exposes removed audio
    next to the edge (SPEC §5). Other tracks: the clip's edge moves in place."""
    if edge not in ("start", "end"):
        raise TimelineError("edge must be start or end")
    with project.db() as conn:
        st = load(project, conn)
        c = _find_clip(st, ref)
        sr = st.sources[c.source_id].sample_rate
        d = int(
            Fraction(t - (c.timeline_start if edge == "start" else c.timeline_end), st.rate) * sr
        )
        if c.track_id != NARRATION:
            row = st.clip_rows[c.clip_id or 0]
            if edge == "start":
                a = max(0, min(row["source_out"] - MIN_CLIP, row["source_in"] + d))
                moved = round(Fraction(a - row["source_in"], sr) * st.rate)
                new = row | {
                    "source_in": a,
                    "timeline_start": max(0, row["timeline_start"] + moved),
                }
            else:
                n = st.sources[c.source_id].num_frames
                new = row | {
                    "source_out": max(row["source_in"] + MIN_CLIP, min(n, row["source_out"] + d))
                }
            _commit(project, conn, [("edl_clip", row, new)], "Trim")
            return
        if edge == "start" and d > 0:
            row = _cut_row(
                conn,
                c.source_id,
                c.source_in,
                min(c.source_out - MIN_CLIP, c.source_in + d),
                "trim",
            )
            _commit(project, conn, [store._r(None, row)], "Trim start")
        elif edge == "end" and d < 0:
            row = _cut_row(
                conn,
                c.source_id,
                max(c.source_in + MIN_CLIP, c.source_out + d),
                c.source_out,
                "trim",
            )
            _commit(project, conn, [store._r(None, row)], "Trim end")
        else:
            # Outward: shrink the removed regions at this edge, following a chain
            # of adjacent cuts (a trim next to an earlier delete) as far as asked.
            x = c.source_in + d if edge == "start" else c.source_out + d
            removed = [
                r
                for r in st.region_rows
                if r["source_file_id"] == c.source_id and r["status"] == "removed"
            ]
            now = now_iso()
            changes: list[store.Change] = []
            e = c.source_in if edge == "start" else c.source_out
            while True:
                if edge == "start":
                    r = next(
                        (r for r in removed if r["end_sample"] == e and r["start_sample"] < e), None
                    )
                else:
                    r = next(
                        (r for r in removed if r["start_sample"] == e and r["end_sample"] > e), None
                    )
                if r is None:
                    break
                removed.remove(r)
                if edge == "start":
                    new_r = r | {"end_sample": max(r["start_sample"], x)}
                    e, done = r["start_sample"], x >= r["start_sample"]
                else:
                    new_r = r | {"start_sample": min(r["end_sample"], x)}
                    e, done = r["end_sample"], x <= r["end_sample"]
                changes.append(store._r(r, new_r | {"decided_by": "user", "updated_at": now}))
                if done:
                    break
            if not changes:
                raise TimelineError("nothing was removed at this edge to bring back")
            _commit(project, conn, changes, "Extend " + edge)


def roll(project: Project, t: int, delta: int) -> None:
    """Rolling edit at the join at timeline sample t: the cut between the two
    clips moves by delta (timeline samples); the total length doesn't change."""
    with project.db() as conn:
        st = load(project, conn)
        narr = [c for c in st.clips if c.track_id == NARRATION]
        join = next((i for i in range(1, len(narr)) if narr[i].timeline_start == t), None)
        if join is None:
            raise TimelineError("put the playhead on a join between two clips")
        prev, nxt = narr[join - 1], narr[join]
        r = next(
            (
                r
                for r in st.region_rows
                if r["source_file_id"] == prev.source_id == nxt.source_id
                and r["status"] == "removed"
                and r["start_sample"] <= prev.source_out
                and r["end_sample"] >= nxt.source_in
            ),
            None,
        )
        if r is None:
            raise TimelineError("a rolling edit needs a cut between two parts of one recording")
        sr = st.sources[prev.source_id].sample_rate
        d = int(Fraction(delta, st.rate) * sr)
        d = max(
            -(prev.source_out - prev.source_in - MIN_CLIP),
            min(nxt.source_out - nxt.source_in - MIN_CLIP, d),
        )
        new = r | {
            "start_sample": r["start_sample"] + d,
            "end_sample": r["end_sample"] + d,
            "decided_by": "user",
            "updated_at": now_iso(),
        }
        _commit(project, conn, [store._r(r, new)], "Rolling edit")


def move(project: Project, ref: dict[str, Any], t: int, track_id: int) -> None:
    """Move a clip to timeline sample t on a track (Alt+Shift+←/→, or drag).

    Within the narration it is a ripple move (it plays just before what is at t).
    Onto another track it sits at t, and leaves the narration (which closes up).
    Back onto the narration it is put back into the narration at t."""
    with project.db() as conn:
        st = load(project, conn)
        if track_id not in st.tracks:
            raise KeyError("no such track")
        c = _find_clip(st, ref)
        infos = list(st.sources.values())
        t = max(0, t)
        if c.track_id == NARRATION and track_id == NARRATION:
            target = clip_at(st, t)
            before = (target.source_id, _src_sample(st, target, t)) if target is not None else None
            if (
                before is not None
                and before[0] == c.source_id
                and c.source_in <= before[1] < c.source_out
            ):
                raise TimelineError("that's inside the clip itself")
            segs = place(
                st.segments,
                c.source_id,
                c.source_in,
                c.source_out,
                before=before,
                at_end=before is None,
            )
            _commit(project, conn, [store.segments_change(conn, infos, segs)], "Move clip")
            return
        if c.track_id == NARRATION:
            nid = store._next_id(conn, "edl_clip")
            row: dict[str, Any] = {
                "id": nid,
                "track_id": track_id,
                "source_file_id": c.source_id,
                "source_in": c.source_in,
                "source_out": c.source_out,
                "timeline_start": t,
                "gain_db": c.gain_db,
                "fade_in": 0,
                "fade_out": 0,
                "muted": int(c.muted),
            }
            cut = _cut_row(
                conn,
                c.source_id,
                c.source_in,
                c.source_out,
                f"moved to {st.tracks[track_id].name}",
                moved_to_clip=nid,
            )
            _commit(
                project, conn, [("edl_clip", None, row), store._r(None, cut)], "Move clip to track"
            )
            return
        row = st.clip_rows[c.clip_id or 0]
        if track_id != NARRATION:
            _commit(
                project,
                conn,
                [("edl_clip", row, row | {"timeline_start": t, "track_id": track_id})],
                "Move clip",
            )
            return
        # Back into the narration: undo the cut its move made, and place it at t.
        changes: list[store.Change] = [("edl_clip", row, None)]
        for r in st.region_rows:
            if (
                json.loads(r["detail_json"]).get("moved_to_clip") == row["id"]
                and r["status"] == "removed"
            ):
                changes.append(store._r(r, r | {"status": "restored", "updated_at": now_iso()}))
        target = clip_at(st, t)
        before = (target.source_id, _src_sample(st, target, t)) if target is not None else None
        segs = place(
            st.segments,
            row["source_file_id"],
            row["source_in"],
            row["source_out"],
            before=before,
            at_end=before is None,
        )
        changes.append(store.segments_change(conn, infos, segs))
        _commit(project, conn, changes, "Move clip to narration")


def set_clip(project: Project, ref: dict[str, Any], **attrs: Any) -> None:
    """Clip gain (dB), fades (seconds) and mute (+/-, F/G, inspector)."""
    allowed = {"gain_db", "fade_in", "fade_out", "muted"}
    if not attrs or set(attrs) - allowed:
        raise TimelineError("set gain_db, fade_in, fade_out or muted")
    with project.db() as conn:
        st = load(project, conn)
        c = _find_clip(st, ref)
        sr = st.sources[c.source_id].sample_rate
        length = c.source_out - c.source_in
        vals: dict[str, Any] = {}
        if "gain_db" in attrs:
            g = float(attrs["gain_db"])
            if not -60 <= g <= 24:
                raise TimelineError("clip gain must be between -60 and +24 dB")
            vals["gain_db"] = g
        for k in ("fade_in", "fade_out"):
            if k in attrs:
                n = round(float(attrs[k]) * sr)
                if n < 0:
                    raise TimelineError("a fade can't be negative")
                vals[k] = min(n, length)
        if "muted" in attrs:
            vals["muted"] = bool(attrs["muted"])
        if c.track_id == NARRATION:
            segs = set_attrs(st.segments, c.source_id, c.source_in, c.source_out, **vals)
            change = store.segments_change(conn, list(st.sources.values()), segs)
        else:
            row = st.clip_rows[c.clip_id or 0]
            change = (
                "edl_clip",
                row,
                row | {k: int(v) if isinstance(v, bool) else v for k, v in vals.items()},
            )
        _commit(project, conn, [change], "Clip " + ", ".join(k.replace("_", " ") for k in vals))


# -- tracks -------------------------------------------------------------------------------------


def add_track(project: Project, name: str) -> int:
    name = name.strip() or "Track"
    with project.db() as conn:
        tid = store._next_id(conn, "track")
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM track").fetchone()[0]
        row = {
            "id": tid,
            "name": name[:80],
            "kind": "audio",
            "position": pos,
            "gain_db": 0.0,
            "pan": 0.0,
            "mute": 0,
            "solo": 0,
        }
        _commit(project, conn, [("track", None, row)], f"Add track {name}")
    return tid


def update_track(project: Project, track_id: int, **attrs: Any) -> None:
    allowed = {"name", "gain_db", "pan", "mute", "solo"}
    if not attrs or set(attrs) - allowed:
        raise TimelineError("set name, gain_db, pan, mute or solo")
    with project.db() as conn:
        row = conn.execute("SELECT * FROM track WHERE id = ?", (track_id,)).fetchone()
        if row is None:
            raise KeyError("no such track")
        r = dict(row)
        new = dict(r)
        if "name" in attrs:
            new["name"] = str(attrs["name"]).strip()[:80] or r["name"]
        if "gain_db" in attrs:
            g = float(attrs["gain_db"])
            if not -60 <= g <= 24:
                raise TimelineError("track gain must be between -60 and +24 dB")
            new["gain_db"] = g
        if "pan" in attrs:
            p = float(attrs["pan"])
            if not -1 <= p <= 1:
                raise TimelineError("pan must be between -1 (left) and 1 (right)")
            new["pan"] = p
        for k in ("mute", "solo"):
            if k in attrs:
                new[k] = int(bool(attrs[k]))
        _commit(project, conn, [("track", r, new)], f"Track {new['name']}")


def delete_track(project: Project, track_id: int) -> None:
    with project.db() as conn:
        st = load(project, conn)
        if track_id == NARRATION:
            raise TimelineError("the narration track can't be deleted")
        if track_id not in st.tracks:
            raise KeyError("no such track")
        if any(f.track_id == track_id for f in st.frees):
            raise TimelineError("move or delete its clips first")
        row = next(r for r in st.track_rows if r["id"] == track_id)
        _commit(project, conn, [("track", row, None)], f"Delete track {row['name']}")


def add_source(
    project: Project,
    track_id: int,
    source_id: int,
    t: int,
    a: int | None = None,
    b: int | None = None,
) -> int:
    """Put (part of) a recording on a track at t: room tone, an alternate..."""
    with project.db() as conn:
        st = load(project, conn)
        if track_id == NARRATION or track_id not in st.tracks:
            raise TimelineError("choose another track (the narration is built from the recordings)")
        src = st.sources.get(source_id)
        if src is None:
            raise KeyError("no such source")
        a = 0 if a is None else max(0, a)
        b = src.num_frames if b is None else min(src.num_frames, b)
        if b <= a:
            raise TimelineError("nothing to add")
        nid = store._next_id(conn, "edl_clip")
        row = {
            "id": nid,
            "track_id": track_id,
            "source_file_id": source_id,
            "source_in": a,
            "source_out": b,
            "timeline_start": max(0, t),
            "gain_db": 0.0,
            "fade_in": 0,
            "fade_out": 0,
            "muted": 0,
        }
        _commit(
            project,
            conn,
            [("edl_clip", None, row)],
            f"Add {st.labels[source_id]} to {st.tracks[track_id].name}",
        )
    return nid


def delete_clip(project: Project, clip_id: int) -> None:
    with project.db() as conn:
        row = conn.execute("SELECT * FROM edl_clip WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise KeyError("no such clip")
        _commit(project, conn, [("edl_clip", dict(row), None)], "Delete clip")


class Locator:
    """Recording position -> timeline position, by binary search over each
    recording's narration clips (a 10 h book has thousands of clips and cuts)."""

    def __init__(self, st: State) -> None:
        self.st = st
        self.by_src: dict[int, list[Clip]] = {}
        for c in st.clips:
            if c.track_id == NARRATION:
                self.by_src.setdefault(c.source_id, []).append(c)
        for cs in self.by_src.values():
            cs.sort(key=lambda c: c.source_in)
        self.ins = {s: [c.source_in for c in cs] for s, cs in self.by_src.items()}

    def at_sample(self, source_id: int, x: int) -> float | None:
        cs = self.by_src.get(source_id)
        if not cs:
            return None
        i = bisect.bisect_right(self.ins[source_id], x) - 1
        # Moved pieces can overlap in source order only if the same audio plays
        # twice, which the model never does: one candidate is enough.
        if i < 0 or not cs[i].source_in <= x < cs[i].source_out:
            return None
        c = cs[i]
        sr = self.st.sources[source_id].sample_rate
        return (
            c.timeline_start + float(Fraction(x - c.source_in, sr) * self.st.rate)
        ) / self.st.rate

    def at(self, source_id: int, seconds: float) -> float | None:
        src = self.st.sources.get(source_id)
        return None if src is None else self.at_sample(source_id, round(seconds * src.sample_rate))


# -- markers --------------------------------------------------------------------------------------


def marker_time(st: State, m: dict[str, Any]) -> int | None:
    """A marker's timeline position: anchored to a recording sample, it follows
    edits; None when that audio was cut."""
    if m["source_file_id"] is None:
        return int(m["sample"])
    for c in st.clips:
        if (
            c.track_id == NARRATION
            and c.source_id == m["source_file_id"]
            and c.source_in <= m["sample"] < c.source_out
        ):
            sr = st.sources[c.source_id].sample_rate
            return c.timeline_start + round(Fraction(m["sample"] - c.source_in, sr) * st.rate)
    return None


def add_marker(project: Project, t: int, label: str, color: str = "blue") -> int:
    with project.db() as conn:
        st = load(project, conn)
        c = clip_at(st, t)
        src, sample = (c.source_id, _src_sample(st, c, t)) if c else (None, t)
        mid = store._next_id(conn, "marker")
        row = {
            "id": mid,
            "label": label.strip()[:200] or "Marker",
            "color": color[:20],
            "source_file_id": src,
            "sample": sample,
            "created_at": now_iso(),
        }
        _commit(project, conn, [("marker", None, row)], f"Marker {row['label']}")
    return mid


def update_marker(
    project: Project, marker_id: int, label: str | None = None, t: int | None = None
) -> None:
    with project.db() as conn:
        row = conn.execute("SELECT * FROM marker WHERE id = ?", (marker_id,)).fetchone()
        if row is None:
            raise KeyError("no such marker")
        r = dict(row)
        new = dict(r)
        if label is not None:
            new["label"] = label.strip()[:200] or r["label"]
        if t is not None:
            st = load(project, conn)
            c = clip_at(st, t)
            new["source_file_id"], new["sample"] = (
                (c.source_id, _src_sample(st, c, t)) if c else (None, t)
            )
        _commit(project, conn, [("marker", r, new)], "Marker")


def delete_marker(project: Project, marker_id: int) -> None:
    with project.db() as conn:
        row = conn.execute("SELECT * FROM marker WHERE id = ?", (marker_id,)).fetchone()
        if row is None:
            raise KeyError("no such marker")
        _commit(project, conn, [("marker", dict(row), None)], "Delete marker")
