"""Takes endpoints: groups, choosing the preferred take, confirm, reject, back."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from hillaudio import pickups
from hillaudio.api.edl import editable_project
from hillaudio.api.state import EngineState, engine
from hillaudio.edl import store, take_store
from hillaudio.store.project import Project

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]


class Choice(BaseModel):
    take_id: int


class Confirm(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=10_000)


class Mark(BaseModel):
    status: str


def _out(p: Project) -> dict[str, Any]:
    h = store.history(p)
    return {"groups": take_store.groups(p), "undo": h["undo"], "redo": h["redo"]}


def _run(p: Project, fn: Any) -> dict[str, Any]:
    try:
        fn()
    except take_store.TakeError as e:
        raise HTTPException(400, str(e)) from None
    except KeyError:
        raise HTTPException(404, "no such take group or take") from None
    return _out(p)


@router.get("/api/project/takes")
def list_groups(state: State) -> dict[str, Any]:
    return _out(state.require_project())


@router.post("/api/project/takes/{group_id}/choose")
def choose(state: State, group_id: int, body: Choice) -> dict[str, Any]:
    p = editable_project(state)
    return _run(p, lambda: take_store.choose(p, group_id, body.take_id))


@router.post("/api/project/takes/{group_id}/back")
def back(state: State, group_id: int) -> dict[str, Any]:
    p = editable_project(state)
    return _run(p, lambda: take_store.back(p, group_id))


@router.post("/api/project/takes/confirm")
def confirm(state: State, body: Confirm) -> dict[str, Any]:
    p = editable_project(state)
    return _run(p, lambda: take_store.confirm(p, body.ids))


@router.post("/api/project/takes/take/{take_id}/mark")
def mark(state: State, take_id: int, body: Mark) -> dict[str, Any]:
    p = editable_project(state)
    return _run(p, lambda: take_store.mark(p, take_id, body.status))


@router.get("/api/project/pickups")
def pickup_report(state: State) -> list[dict[str, Any]]:
    """Each pickup recording: its lines, placed or not, and speech matching no line (D-062)."""
    return pickups.report(state.require_project())
