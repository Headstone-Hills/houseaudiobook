"""Finalize, export and the narrator guide (M8)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hillaudio.api.jobs import JobContext
from hillaudio.api.state import EngineState, engine
from hillaudio.edl.render import wav_bytes
from hillaudio.guide import guide
from hillaudio.render import finalize as fz

router = APIRouter()
State = Annotated[EngineState, Depends(engine)]
MAX_PREVIEW_S = 60.0
TYPES = {"csv": "text/csv", "md": "text/markdown", "pdf": "application/pdf"}


class ExportRequest(BaseModel):
    format: fz.Format = "mp3"
    split: fz.Split = "single"
    kbps: int | None = Field(default=None, ge=32, le=320)
    chapter: int | None = None  # a chapter workspace's export: just that chapter


class AnalyzeRequest(BaseModel):
    split: fz.Split = "single"


@router.get("/api/project/finalize")
def get_settings(state: State) -> dict[str, Any]:
    p = state.require_project()
    return {
        "settings": asdict(fz.settings(p)),
        "presets": {k: asdict(v) for k, v in fz.presets(p).items()},
        "last_analysis": fz._last_analysis(p),
    }


@router.put("/api/project/finalize")
def put_settings(state: State, body: dict[str, Any]) -> dict[str, Any]:
    p = state.require_project()
    try:
        s = fz.FinalizeSettings(**(asdict(fz.settings(p)) | body))
        fz.save_settings(p, s)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    return get_settings(state)


@router.put("/api/project/finalize/presets/{key}")
def put_preset(state: State, key: str, body: dict[str, Any]) -> dict[str, Any]:
    p = state.require_project()
    try:
        base = asdict(fz.presets(p).get(key, fz.ACX))
        base.update(body)
        base["key"] = key
        for rk in ("head_range", "tail_range"):
            base[rk] = tuple(base[rk])
        fz.save_preset(p, fz.Preset(**base))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    return get_settings(state)


def _job(state: EngineState, kind: str, label: str, fn: Any) -> dict[str, str]:
    p = state.require_project()

    def run(ctx: JobContext) -> dict[str, Any]:
        try:
            out: dict[str, Any] = fn(p, ctx)
        except fz.ExportError as e:
            raise RuntimeError(str(e)) from None
        return out

    # The render lane: one finalize at a time (each reads every source).
    return {"job_id": state.jobs.submit(kind, label, run, lane="render").id}


@router.post("/api/project/finalize/analyze")
def analyze(state: State, body: AnalyzeRequest) -> dict[str, str]:
    return _job(
        state,
        "finalize",
        "Analyze levels",
        lambda p, ctx: fz.export(
            p, "wav24", body.split, None, ctx.progress, ctx.check_cancel, analyze_only=True
        ),
    )


@router.get("/api/project/finalize/preview")
def preview(
    state: State,
    start: Annotated[float, Query(ge=0)],
    end: Annotated[float, Query(ge=0)],
) -> Response:
    """A stretch through the finalize chain, for A/B against the plain render."""
    p = state.require_project()
    if end <= start:
        raise HTTPException(400, "end must be after start")
    try:
        audio, rate = fz.preview(p, start, min(end, start + MAX_PREVIEW_S))
    except fz.ExportError as e:
        raise HTTPException(409, str(e)) from None
    return Response(wav_bytes(audio, rate), media_type="audio/wav")


@router.get("/api/project/export/preflight")
def preflight(
    state: State,
    format: fz.Format = "mp3",
    split: fz.Split = "single",
    chapter: int | None = None,
) -> dict[str, Any]:
    return fz.preflight(state.require_project(), format, split, chapter)


@router.post("/api/project/export")
def export(state: State, body: ExportRequest) -> dict[str, str]:
    p = state.require_project()
    pf = fz.preflight(p, body.format, body.split, body.chapter)
    if pf["problems"]:
        raise HTTPException(409, pf["problems"][0])
    label = f"Export {body.format.upper()}" + (
        " of one chapter"
        if body.chapter is not None
        else f" by {body.split}"
        if body.split != "single"
        else ""
    )
    return _job(
        state,
        "export",
        label,
        lambda p, ctx: fz.export(
            p,
            body.format,
            body.split,
            body.kbps,
            ctx.progress,
            ctx.check_cancel,
            chapter=body.chapter,
        ),
    )


@router.get("/api/project/exports")
def list_exports(state: State) -> list[dict[str, Any]]:
    return fz.exports(state.require_project())


@router.get("/api/project/exports/{stamp}/{name}")
def download(state: State, stamp: str, name: str) -> FileResponse:
    p = state.require_project()
    base = (p.root / "exports").resolve()
    path = (base / stamp / name).resolve()
    # Only files inside exports/ (no "..", no absolute names).
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "no such export")
    return FileResponse(path, filename=Path(name).name)


@router.get("/api/project/guide")
def guide_file(
    state: State,
    format: str = "pdf",
    reference: guide.Reference = "original",
    sort: guide.Sort = "page",
    narrator: Annotated[str, Query(max_length=200)] = "",
    include_resolved: bool = False,
    include_silences: bool = False,
    chapter: int | None = None,
) -> Response:
    p = state.require_project()
    if format not in TYPES:
        raise HTTPException(400, "format must be csv, md or pdf")
    o = guide.GuideOptions(reference, sort, narrator, include_resolved, include_silences, chapter)
    if format == "csv":
        body: bytes = guide.to_csv(p, o).encode()
    elif format == "md":
        body = guide.to_markdown(p, o).encode()
    else:
        body = guide.to_pdf(p, o)
    name = f"{fz._safe(p.name)}_corrections.{format}"
    return Response(
        body,
        media_type=TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/api/project/guide/rows")
def guide_rows(
    state: State,
    reference: guide.Reference = "original",
    sort: guide.Sort = "page",
    include_resolved: bool = False,
    include_silences: bool = False,
    chapter: int | None = None,
) -> dict[str, Any]:
    p = state.require_project()
    o = guide.GuideOptions(reference, sort, "", include_resolved, include_silences, chapter)
    rs = guide.rows(p, o)
    return {"header": guide.header(p, o, rs), "rows": rs}
