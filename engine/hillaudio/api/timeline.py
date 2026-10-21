"""Timeline endpoints (M7): the multitrack view, lanes for snapping and the
transcript, and the editing tools. Times in the API are seconds; the engine
works in integer samples underneath (CLAUDE.md)."""

from __future__ import annotations

import bisect
from collections.abc import Callable
from fractions import Fraction
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from hillaudio import issue_store, pipeline
from hillaudio.analysis.boundaries import speech_mask
from hillaudio.analysis.issues import pauses
from hillaudio.api.edl import editable_project
from hillaudio.api.state import EngineState, engine
from hillaudio.edl import store, timeline_store
from hillaudio.edl.model import NARRATION, Clip, duration
from hillaudio.edl.timeline_store import State as TState
from hillaudio.edl.timeline_store import TimelineError
from hillaudio.store.project import Project

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]
MAX_LANE_WINDOW_S = 900.0
COLORS = {"flub": "red", "missing_line": "orange", "silence": "purple"}


class Ref(BaseModel):
    clip_id: int | None = None  # a clip on another track
    source_id: int | None = None  # or a narration clip, by where it starts
    source_in: int | None = None  # (samples)


class At(BaseModel):
    t: float = Field(ge=0)
    track_id: int = NARRATION


class Range(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class Trim(BaseModel):
    ref: Ref
    edge: str
    t: float = Field(ge=0)


class Roll(BaseModel):
    t: float = Field(ge=0)
    delta: float


class Move(BaseModel):
    ref: Ref
    t: float = Field(ge=0)
    track_id: int = NARRATION


class ClipAttrs(BaseModel):
    ref: Ref
    gain_db: float | None = None
    fade_in: float | None = Field(default=None, ge=0)  # seconds
    fade_out: float | None = Field(default=None, ge=0)
    muted: bool | None = None


class NewTrack(BaseModel):
    name: str = Field(default="Track", max_length=80)


class TrackAttrs(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    gain_db: float | None = None
    pan: float | None = None
    mute: bool | None = None
    solo: bool | None = None


class AddSource(BaseModel):
    source_id: int
    t: float = Field(ge=0)
    start: float | None = Field(default=None, ge=0)  # seconds in the recording
    end: float | None = Field(default=None, ge=0)


class NewMarker(BaseModel):
    t: float = Field(ge=0)
    label: str = Field(default="Marker", max_length=200)
    color: str = Field(default="blue", max_length=20)


class MarkerAttrs(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    t: float | None = Field(default=None, ge=0)


# -- the view -------------------------------------------------------------------------------


def _to_t(st: TState, source_id: int, seconds: float) -> float | None:
    """A recording position (seconds) on the timeline (seconds); None if cut."""
    src = st.sources.get(source_id)
    if src is None:
        return None
    x = round(seconds * src.sample_rate)
    for c in st.clips:
        if c.track_id == NARRATION and c.source_id == source_id and c.source_in <= x < c.source_out:
            return (
                c.timeline_start + float(Fraction(x - c.source_in, src.sample_rate) * st.rate)
            ) / st.rate
    return None


def view(p: Project) -> dict[str, Any]:
    st = timeline_store.load(p)
    r = st.rate

    def clip(c: Clip) -> dict[str, Any]:
        sr = st.sources[c.source_id].sample_rate
        ref = (
            {"clip_id": c.clip_id}
            if c.clip_id is not None
            else {"source_id": c.source_id, "source_in": c.source_in}
        )
        return {
            "ref": ref,
            "track_id": c.track_id,
            "source_id": c.source_id,
            "source_label": st.labels.get(c.source_id, "?"),
            "source_in": c.source_in / sr,
            "source_out": c.source_out / sr,
            "timeline_start": c.timeline_start / r,
            "timeline_end": c.timeline_end / r,
            "gain_db": c.gain_db,
            "fade_in": c.fade_in / r,
            "fade_out": c.fade_out / r,
            "muted": c.muted,
        }

    issues = []
    for i in issue_store.list_issues(p):
        if i["source_file_id"] is None or i["start"] is None or i["status"] != "open":
            continue
        t = _to_t(st, i["source_file_id"], i["start"])
        if t is not None:
            issues.append(
                {
                    "id": i["id"],
                    "kind": i["kind"],
                    "severity": i["severity"],
                    "t": t,
                    "color": COLORS.get(i["kind"], "red"),
                    "label": i["sentence_text"][:60],
                }
            )
    removed = []
    for row in st.region_rows:
        if row["status"] == "restored" and row["kind"] == "take":
            continue
        sr = st.sources[row["source_file_id"]].sample_rate
        # Where it would play: just after the kept audio before it.
        t = _to_t(st, row["source_file_id"], max(0, row["start_sample"] - 1) / sr)
        removed.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "status": row["status"],
                "t": t,
                "seconds": (row["end_sample"] - row["start_sample"]) / sr,
                "text": row["text"],
                "source_id": row["source_file_id"],
                "start": row["start_sample"] / sr,
                "end": row["end_sample"] / sr,
            }
        )
    markers = []
    for m in st.markers:
        t = timeline_store.marker_time(st, m)
        markers.append(
            {
                "id": m["id"],
                "label": m["label"],
                "color": m["color"],
                "t": None if t is None else t / r,
            }
        )
    h = store.history(p)
    return {
        "rate": r,
        "duration": duration(st.clips) / r,
        "channels": timeline_store.mix_channels(st),
        "tracks": [
            {
                "id": tr["id"],
                "name": tr["name"],
                "kind": tr["kind"],
                "gain_db": tr["gain_db"],
                "pan": tr["pan"],
                "mute": bool(tr["mute"]),
                "solo": bool(tr["solo"]),
            }
            for tr in st.track_rows
        ],
        "clips": [clip(c) for c in st.clips],
        "markers": markers,
        "issues": issues,
        "removed": removed,
        "undo": h["undo"],
        "redo": h["redo"],
    }


@router.get("/api/project/timeline")
def get_timeline(state: State) -> dict[str, Any]:
    return view(state.require_project())


@router.get("/api/project/timeline/lanes")
def lanes(
    state: State,
    start: Annotated[float, Query(ge=0)],
    end: Annotated[float, Query(ge=0)],
) -> dict[str, Any]:
    """Words, sentences and pauses in [start, end) of the timeline (seconds):
    the transcript lane, and snap points for the tools (SPEC §6.6)."""
    p = state.require_project()
    if end <= start or end - start > MAX_LANE_WINDOW_S:
        raise HTTPException(400, f"ask for 0 to {MAX_LANE_WINDOW_S:.0f} s at a time")
    st = timeline_store.load(p)
    t0, t1 = round(start * st.rate), round(end * st.rate)
    narr = [
        c
        for c in st.clips
        if c.track_id == NARRATION and c.timeline_end > t0 and c.timeline_start < t1
    ]
    used = {c.source_id for c in narr}

    def mapper(src_id: int) -> Callable[[float], float | None]:
        sr = st.sources[src_id].sample_rate
        mine = [c for c in narr if c.source_id == src_id]

        def f(sec: float) -> float | None:
            x = round(sec * sr)
            for c in mine:
                if c.source_in <= x <= c.source_out:
                    t = (
                        c.timeline_start + float(Fraction(x - c.source_in, sr) * st.rate)
                    ) / st.rate
                    return t if start <= t < end else None
            return None

        return f

    words: list[dict[str, Any]] = []
    sentences: list[dict[str, Any]] = []
    pause_list: list[dict[str, Any]] = []
    run = pipeline.latest_run(p)
    sources = {s.id: s for s in p.sources()}
    for sid in sorted(used):
        f = mapper(sid)
        tw = pipeline.transcript(p, sid)
        starts = [w.start for w in tw]
        sr = st.sources[sid].sample_rate
        for c in (c for c in narr if c.source_id == sid):
            a, b = c.source_in / sr, c.source_out / sr
            for w in tw[bisect.bisect_left(starts, a) : bisect.bisect_left(starts, b)]:
                ws, we = f(w.start), f(min(w.end, b))
                if ws is not None:
                    words.append({"text": w.text, "t0": ws, "t1": we if we is not None else ws})
        env = pipeline.envelope(p, sources[sid])
        for a_s, b_s, _ in pauses(speech_mask(env), env.fps):
            pa, pb = f(a_s), f(b_s)
            if pa is not None and pb is not None and pb > pa:
                pause_list.append({"t0": pa, "t1": pb})
        if run is not None:
            for r in issue_store.reading_infos(p, run):
                if r.source_id == sid and r.coverage >= 0.999:
                    ra, rb = f(r.start), f(r.end)
                    if ra is not None and rb is not None:
                        sentences.append({"ordinal": r.sentence_ordinal, "t0": ra, "t1": rb})
    key: Callable[[dict[str, Any]], float] = lambda d: d["t0"]  # noqa: E731
    return {
        "words": sorted(words, key=key),
        "sentences": sorted(sentences, key=key),
        "pauses": sorted(pause_list, key=key),
    }


# -- tools ----------------------------------------------------------------------------------


def _edit(state: EngineState, fn: Callable[[Project, int], Any]) -> dict[str, Any]:
    p = editable_project(state)
    rate = timeline_store.load(p).rate
    try:
        fn(p, rate)
    except TimelineError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]) if e.args else "not found") from None
    return view(p)


def _ref(r: Ref) -> dict[str, Any]:
    return r.model_dump()


@router.post("/api/project/timeline/split")
def split(state: State, body: At) -> dict[str, Any]:
    return _edit(state, lambda p, r: timeline_store.split_at(p, round(body.t * r), body.track_id))


@router.post("/api/project/timeline/ripple-delete")
def ripple_delete(state: State, body: Range) -> dict[str, Any]:
    return _edit(
        state,
        lambda p, r: timeline_store.ripple_delete(p, round(body.start * r), round(body.end * r)),
    )


@router.post("/api/project/timeline/trim")
def trim(state: State, body: Trim) -> dict[str, Any]:
    return _edit(
        state, lambda p, r: timeline_store.trim(p, _ref(body.ref), body.edge, round(body.t * r))
    )


@router.post("/api/project/timeline/roll")
def roll(state: State, body: Roll) -> dict[str, Any]:
    return _edit(
        state, lambda p, r: timeline_store.roll(p, round(body.t * r), round(body.delta * r))
    )


@router.post("/api/project/timeline/move")
def move(state: State, body: Move) -> dict[str, Any]:
    return _edit(
        state, lambda p, r: timeline_store.move(p, _ref(body.ref), round(body.t * r), body.track_id)
    )


@router.post("/api/project/timeline/clip")
def clip_attrs(state: State, body: ClipAttrs) -> dict[str, Any]:
    attrs = body.model_dump(exclude={"ref"}, exclude_none=True)
    return _edit(state, lambda p, r: timeline_store.set_clip(p, _ref(body.ref), **attrs))


@router.delete("/api/project/timeline/clips/{clip_id}")
def delete_clip(state: State, clip_id: int) -> dict[str, Any]:
    return _edit(state, lambda p, r: timeline_store.delete_clip(p, clip_id))


@router.post("/api/project/timeline/tracks")
def add_track(state: State, body: NewTrack) -> dict[str, Any]:
    return _edit(state, lambda p, r: timeline_store.add_track(p, body.name))


@router.patch("/api/project/timeline/tracks/{track_id}")
def update_track(state: State, track_id: int, body: TrackAttrs) -> dict[str, Any]:
    attrs = body.model_dump(exclude_none=True)
    return _edit(state, lambda p, r: timeline_store.update_track(p, track_id, **attrs))


@router.delete("/api/project/timeline/tracks/{track_id}")
def delete_track(state: State, track_id: int) -> dict[str, Any]:
    return _edit(state, lambda p, r: timeline_store.delete_track(p, track_id))


@router.post("/api/project/timeline/tracks/{track_id}/add-source")
def add_source(state: State, track_id: int, body: AddSource) -> dict[str, Any]:
    def go(p: Project, r: int) -> None:
        try:
            sr = p.source(body.source_id).sample_rate
        except KeyError:
            raise KeyError("no such source") from None
        timeline_store.add_source(
            p,
            track_id,
            body.source_id,
            round(body.t * r),
            None if body.start is None else round(body.start * sr),
            None if body.end is None else round(body.end * sr),
        )

    return _edit(state, go)


@router.post("/api/project/timeline/markers")
def add_marker(state: State, body: NewMarker) -> dict[str, Any]:
    return _edit(
        state, lambda p, r: timeline_store.add_marker(p, round(body.t * r), body.label, body.color)
    )


@router.patch("/api/project/timeline/markers/{marker_id}")
def update_marker(state: State, marker_id: int, body: MarkerAttrs) -> dict[str, Any]:
    return _edit(
        state,
        lambda p, r: timeline_store.update_marker(
            p, marker_id, body.label, None if body.t is None else round(body.t * r)
        ),
    )


@router.delete("/api/project/timeline/markers/{marker_id}")
def delete_marker(state: State, marker_id: int) -> dict[str, Any]:
    return _edit(state, lambda p, r: timeline_store.delete_marker(p, marker_id))
