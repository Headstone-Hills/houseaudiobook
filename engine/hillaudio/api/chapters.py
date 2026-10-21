"""Chapters: the workspaces the UI narrows every step to (D-059)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from hillaudio import chapters as ch
from hillaudio.api.state import EngineState, engine

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]


@router.get("/api/project/chapters")
def list_chapters(state: State) -> list[dict[str, Any]]:
    return ch.chapters(state.require_project())
