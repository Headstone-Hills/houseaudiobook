"""Analysis endpoints: run Transcribe / Compare to Text, read the results."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from hillaudio import pipeline
from hillaudio.api.jobs import JobContext
from hillaudio.api.state import EngineState, engine
from hillaudio.asr import whisper
from hillaudio.asr.base import MODEL_SIZES_MB, AsrSettings

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]


class AnalysisRequest(BaseModel):
    # Default: every pipeline stage the project's wizard selection enables.
    stages: list[str] | None = None
    asr: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/asr")
def asr_info() -> dict[str, Any]:
    return {
        "installed": whisper.installed(),
        "models": [{"name": m, "size_mb": mb} for m, mb in MODEL_SIZES_MB.items()],
        "default": AsrSettings().to_json(),
    }


@router.post("/api/project/analysis")
def run_analysis(state: State, body: AnalysisRequest) -> dict[str, str]:
    p = state.require_project()
    stages = body.stages
    if stages is None:
        stages = [s for s in pipeline.STAGES if s in p.functions]
    if not stages:
        raise HTTPException(400, "no analysis stages are enabled for this project")
    unknown = set(stages) - set(pipeline.STAGES)
    if unknown:
        raise HTTPException(400, f"unknown stage(s): {', '.join(sorted(unknown))}")
    try:
        settings = AsrSettings.from_json(body.asr)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e)) from None
    transcriber = state.transcriber_factory(settings)

    def run(ctx: JobContext) -> dict[str, Any]:
        return pipeline.run_analysis(p, stages, transcriber, ctx.progress, ctx.check_cancel)

    label = " + ".join(
        {
            "transcribe": "Transcribe",
            "compare_text": "Compare to Text",
            "flubs": "Flag flubs",
            "silences": "Flag silences",
        }[s]
        for s in stages
    )
    # One lane: transcription saturates the CPU, so runs never overlap.
    return {"job_id": state.jobs.submit("analysis", label, run, lane="asr").id}


@router.get("/api/project/comparison")
def comparison(state: State) -> dict[str, Any] | None:
    p = state.require_project()
    run_id = pipeline.latest_run(p)
    return pipeline.comparison(p, run_id) if run_id is not None else None


@router.get("/api/project/sources/{source_id}/transcript")
def transcript(state: State, source_id: int) -> dict[str, Any]:
    p = state.require_project()
    try:
        p.source(source_id)
    except KeyError:
        raise HTTPException(404, "no such source") from None
    words = pipeline.transcript(p, source_id)
    return {
        "source_id": source_id,
        "words": [{"text": w.text, "start": w.start, "end": w.end, "prob": w.prob} for w in words],
    }
