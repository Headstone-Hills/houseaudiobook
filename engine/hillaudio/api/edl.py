"""EDL endpoints: the timeline, removed material, edits, undo/redo, previews."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from hillaudio import issue_store
from hillaudio.analysis.cleanup import CleanupSettings
from hillaudio.api.jobs import TERMINAL
from hillaudio.api.state import EngineState, engine
from hillaudio.edl import store, timeline_store
from hillaudio.edl.model import Clip, duration, to_timeline
from hillaudio.edl.render import Input, render, wav_bytes
from hillaudio.store.project import Project

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]
MAX_PREVIEW_S = 60.0


class StatusChange(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=10_000)
    status: str


class Bounds(BaseModel):
    start: float | None = Field(default=None, ge=0)  # seconds in the source
    end: float | None = Field(default=None, ge=0)


class Roll(BaseModel):
    delta: float  # seconds; positive moves the boundary later


class NewCut(BaseModel):
    source_id: int
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    note: str = Field(default="", max_length=500)


class Shorten(BaseModel):
    keep: float | None = Field(default=None, ge=0.1, le=10)


def _edl(p: Project) -> dict[str, Any]:
    tl = store.timeline(p)
    labels = {s.id: s.label for s in p.sources()}
    rows = store.regions(p)

    def where(sid: int, start: int) -> float | None:
        """Timeline seconds where a region sits (or would sit when restored)."""
        t = to_timeline(tl.clips, tl.sources, tl.rate, sid, start)
        if t is None:
            # Cut: it sits at the end of the last kept clip before it.
            before = [c for c in tl.clips if c.source_id == sid and c.source_out <= start]
            if before:
                t = before[-1].timeline_end
            else:
                after = [c for c in tl.clips if c.source_id == sid]
                t = after[0].timeline_start if after else None
        return None if t is None else t / tl.rate

    def clip(c: Clip) -> dict[str, Any]:
        sr = tl.sources[c.source_id].sample_rate
        return {
            "source_id": c.source_id,
            "source_in": c.source_in / sr,
            "source_out": c.source_out / sr,
            "timeline_start": c.timeline_start / tl.rate,
            "timeline_end": c.timeline_end / tl.rate,
        }

    regions = []
    for r in rows:
        sr = tl.sources[r["source_file_id"]].sample_rate
        d = dict(r)
        d["detail"] = json.loads(d.pop("detail_json"))
        regions.append(
            d
            | {
                "source_label": labels.get(r["source_file_id"], "?"),
                "start": r["start_sample"] / sr,
                "end": r["end_sample"] / sr,
                "seconds": (r["end_sample"] - r["start_sample"]) / sr,
                "timeline_at": where(r["source_file_id"], r["start_sample"]),
            }
        )
    original = sum(s.num_frames / s.sample_rate for s in tl.sources.values())
    h = store.history(p)
    return {
        "rate": tl.rate,
        "duration": duration(tl.clips) / tl.rate,
        "original_duration": original,
        "clips": [clip(c) for c in tl.clips],
        "regions": regions,
        "undo": h["undo"],
        "redo": h["redo"],
    }


@router.get("/api/project/edl")
def get_edl(state: State) -> dict[str, Any]:
    return _edl(state.require_project())


def editable_project(state: EngineState) -> Project:
    p = state.require_project()
    # Edits and a running cleanup would interleave their undo entries.
    if any(j.kind == "analysis" and j.status not in TERMINAL for j in state.jobs.all()):
        raise HTTPException(409, "wait for the running analysis to finish")
    return p


@router.post("/api/project/edl/regions/status")
def set_status(state: State, body: StatusChange) -> dict[str, Any]:
    p = editable_project(state)
    try:
        store.set_status(p, body.ids, body.status)
    except store.EdlError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "no such region") from None
    return _edl(p)


def _sr(p: Project, rid: int) -> int:
    for r in store.regions(p):
        if r["id"] == rid:
            return p.source(r["source_file_id"]).sample_rate
    raise HTTPException(404, "no such region")


@router.patch("/api/project/edl/regions/{rid}")
def edit_bounds(state: State, rid: int, body: Bounds) -> dict[str, Any]:
    p = editable_project(state)
    sr = _sr(p, rid)
    try:
        store.edit_bounds(
            p,
            rid,
            None if body.start is None else round(body.start * sr),
            None if body.end is None else round(body.end * sr),
        )
    except store.EdlError as e:
        raise HTTPException(400, str(e)) from None
    return _edl(p)


@router.post("/api/project/edl/regions/{rid}/roll")
def roll(state: State, rid: int, body: Roll) -> dict[str, Any]:
    p = editable_project(state)
    store.roll(p, rid, round(body.delta * _sr(p, rid)))
    return _edl(p)


@router.post("/api/project/edl/cut")
def cut(state: State, body: NewCut) -> dict[str, Any]:
    p = editable_project(state)
    try:
        sr = p.source(body.source_id).sample_rate
    except KeyError:
        raise HTTPException(404, "no such source") from None
    try:
        store.cut(p, body.source_id, round(body.start * sr), round(body.end * sr), body.note)
    except store.EdlError as e:
        raise HTTPException(400, str(e)) from None
    return _edl(p)


@router.post("/api/project/edl/undo")
def undo(state: State) -> dict[str, Any]:
    p = editable_project(state)
    if store.undo(p) is None:
        raise HTTPException(409, "nothing to undo")
    return _edl(p)


@router.post("/api/project/edl/redo")
def redo(state: State) -> dict[str, Any]:
    p = editable_project(state)
    if store.redo(p) is None:
        raise HTTPException(409, "nothing to redo")
    return _edl(p)


@router.get("/api/project/edl/history")
def history(state: State) -> dict[str, Any]:
    return store.history(state.require_project())


@router.post("/api/project/issues/{issue_id}/shorten")
def shorten(state: State, issue_id: int, body: Shorten) -> dict[str, Any]:
    p = editable_project(state)
    issue = next((i for i in issue_store.list_issues(p) if i["id"] == issue_id), None)
    if issue is None:
        raise HTTPException(404, "no such issue")
    try:
        store.shorten_pause(p, issue, body.keep)
    except store.EdlError as e:
        raise HTTPException(400, str(e)) from None
    return _edl(p)


@router.get("/api/project/edl/render")
def render_range(
    state: State,
    start: Annotated[float, Query(ge=0)],
    end: Annotated[float, Query(ge=0)],
) -> Response:
    """The edited timeline [start, end) seconds as WAV, rendered from the sources."""
    p = state.require_project()
    if end <= start:
        raise HTTPException(400, "end must be after start")
    end = min(end, start + MAX_PREVIEW_S)
    st = timeline_store.load(p)
    inputs = {}
    for s in p.sources():
        path = p.resolve(s)
        if not path.is_file():
            raise HTTPException(409, f"{s.label} is missing; relink it first")
        inputs[s.id] = Input(path, s.sample_rate, s.channels, s.num_frames)
    channels = timeline_store.mix_channels(st)
    t0 = round(start * st.rate)
    t1 = min(round(end * st.rate), duration(st.clips))
    if t1 <= t0:
        raise HTTPException(400, "that range is past the end of the timeline")
    try:
        audio = render(st.clips, inputs, st.rate, channels, t0, t1, tracks=st.tracks)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None
    return Response(
        wav_bytes(audio, st.rate),
        media_type="audio/wav",
        headers={"X-Render-Start": f"{t0 / st.rate:.6f}", "X-Render-End": f"{t1 / st.rate:.6f}"},
    )


@router.get("/api/project/cleanup-settings")
def get_settings(state: State) -> dict[str, Any]:
    return asdict(store.settings(state.require_project()))


@router.put("/api/project/cleanup-settings")
def put_settings(state: State, body: dict[str, Any]) -> dict[str, Any]:
    p = state.require_project()
    try:
        s = CleanupSettings(**(asdict(store.settings(p)) | body))
        return asdict(store.save_settings(p, s))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
