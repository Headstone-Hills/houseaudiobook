"""Speech-model endpoints: what's installed, explicit downloads, removal (M9)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from hillaudio.api.jobs import TERMINAL, JobContext
from hillaudio.api.state import EngineState, engine
from hillaudio.asr import models

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]


@router.get("/api/models")
def list_models(state: State) -> dict[str, Any]:
    downloading = {
        j.label.split(" ", 1)[-1]: j.id
        for j in state.jobs.all()
        if j.kind == "model" and j.status not in TERMINAL
    }
    return {
        "dir": str(models.models_dir()),
        "source": models.endpoint(),
        "models": [m | {"job_id": downloading.get(str(m["name"]))} for m in models.status()],
    }


@router.post("/api/models/{name}/download")
def download(state: State, name: str) -> dict[str, str]:
    try:
        m = models.get(name)
    except models.ModelError as e:
        raise HTTPException(404, str(e)) from None
    for j in state.jobs.all():
        if j.kind == "model" and j.label == f"Download {m.name}" and j.status not in TERMINAL:
            return {"job_id": j.id}  # already on its way

    def run(ctx: JobContext) -> dict[str, Any]:
        path = models.download(m.name, ctx.progress, ctx.check_cancel)
        return {"name": m.name, "path": str(path)}

    return {"job_id": state.jobs.submit("model", f"Download {m.name}", run, lane="download").id}


@router.delete("/api/models/{name}")
def remove(state: State, name: str) -> dict[str, Any]:
    try:
        removed = models.remove(name)
    except models.ModelError as e:
        raise HTTPException(404, str(e)) from None
    return {"removed": removed} | list_models(state)
