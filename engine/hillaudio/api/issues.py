"""Issues endpoints: the action list, decisions, check settings, audio excerpts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from hillaudio import issue_store, pipeline
from hillaudio.analysis.issues import IssueSettings
from hillaudio.api.state import EngineState, engine
from hillaudio.ingest.decode import excerpt_wav

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]

CONTEXT_S = 3.0  # SPEC §6.4: jump to the spot with ±3 s of context
MAX_EXCERPT_S = 60.0


class Decision(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=10_000)
    status: str | None = None
    note: str | None = Field(default=None, max_length=10_000)


@router.get("/api/project/issues")
def list_issues(state: State) -> dict[str, Any]:
    """Every issue; the UI sorts and filters (hundreds is normal, SPEC §7)."""
    p = state.require_project()
    issues = issue_store.list_issues(p)
    last_check = max((i["last_run_id"] for i in issues), default=None)
    compare = pipeline.latest_run(p)
    return {
        "issues": issues,
        # The run whose "change" badges apply (issues it didn't touch show none).
        "last_check_run": last_check,
        # Flubs were checked against units a re-map has since replaced.
        "stale": compare is not None and pipeline.is_stale(p, compare),
    }


@router.patch("/api/project/issues")
def decide(state: State, body: Decision) -> dict[str, Any]:
    p = state.require_project()
    if body.status is None and body.note is None:
        raise HTTPException(400, "nothing to change: give a status or a note")
    try:
        return {"issues": issue_store.decide(p, body.ids, body.status, body.note)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "no such issue") from None


@router.get("/api/project/issue-settings")
def get_settings(state: State) -> dict[str, Any]:
    return asdict(issue_store.settings(state.require_project()))


@router.put("/api/project/issue-settings")
def put_settings(state: State, body: dict[str, float]) -> dict[str, Any]:
    p = state.require_project()
    try:
        s = IssueSettings(**(asdict(issue_store.settings(p)) | body))
        return asdict(issue_store.save_settings(p, s))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/project/sources/{source_id}/excerpt")
def excerpt(
    state: State,
    source_id: int,
    start: Annotated[float, Query(ge=0)],
    end: Annotated[float, Query(ge=0)],
    context: Annotated[float, Query(ge=0, le=10)] = CONTEXT_S,
) -> Response:
    """WAV of [start - context, end + context], clamped to the source. The
    response headers say where the excerpt begins, so the UI can place the issue."""
    p = state.require_project()
    try:
        src = p.source(source_id)
    except KeyError:
        raise HTTPException(404, "no such source") from None
    if end < start:
        raise HTTPException(400, "end is before start")
    a = max(0.0, start - context)
    b = min(src.duration, end + context, a + MAX_EXCERPT_S)
    path = p.resolve(src)
    if not path.is_file():
        raise HTTPException(409, "source file is missing; relink it first")
    try:
        data = excerpt_wav(path, a, b)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from None
    return Response(
        data,
        media_type="audio/wav",
        headers={"X-Excerpt-Start": f"{a:.6f}", "X-Excerpt-End": f"{b:.6f}"},
    )
