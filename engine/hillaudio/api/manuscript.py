"""Manuscript endpoints: upload, preview the page mapping, import, re-map, browse units."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from hillaudio.api.jobs import JobContext
from hillaudio.api.state import EngineState, engine
from hillaudio.text import importer
from hillaudio.text.pages import PageSettings, PageSettingsError

router = APIRouter(prefix="/api/project/manuscript")
State = Annotated[EngineState, Depends(engine)]
MAX_UNITS_PER_REQUEST = 1000


class ManuscriptSource(BaseModel):
    """Exactly one of: a local path, or the id of an earlier upload."""

    path: str | None = None
    upload_id: str | None = None


class ManuscriptRequest(BaseModel):
    source: ManuscriptSource
    settings: dict[str, Any] = Field(default_factory=dict)


class SettingsRequest(BaseModel):
    settings: dict[str, Any]


def parse_settings(raw: dict[str, Any]) -> PageSettings:
    try:
        return PageSettings.from_json(raw)
    except (PageSettingsError, TypeError) as e:
        raise HTTPException(400, str(e)) from None


def resolve(state: EngineState, src: ManuscriptSource) -> tuple[Path, str, str | None]:
    """(file, original name, upload id if it was an upload)."""
    if (src.path is None) == (src.upload_id is None):
        raise HTTPException(400, "give either a path or an upload_id")
    if src.upload_id is not None:
        if src.upload_id not in state.uploads:
            raise HTTPException(404, "upload not found; upload the file again")
        path, name = state.uploads[src.upload_id]
        return path, name, src.upload_id
    path = Path(src.path or "").expanduser()
    if not path.is_file():
        raise HTTPException(400, f"file not found: {path}")
    return path, path.name, None


@router.put("/upload")
async def upload(
    state: State, request: Request, filename: str = Query(min_length=1)
) -> dict[str, str]:
    p = state.require_project()
    name = Path(filename).name  # never trust a client path
    try:
        importer.detect_format(Path(name))
    except importer.ManuscriptError as e:
        raise HTTPException(400, str(e)) from None
    tmp_dir = p.cache_dir / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4().hex
    tmp = tmp_dir / f"{upload_id}{Path(name).suffix.lower()}"
    try:
        with tmp.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    state.uploads[upload_id] = (tmp, name)
    return {"upload_id": upload_id, "name": name}


@router.post("/preview")
def preview(state: State, body: ManuscriptRequest) -> dict[str, Any]:
    state.require_project()
    path, name, _ = resolve(state, body.source)
    settings = parse_settings(body.settings)
    try:
        fmt = importer.detect_format(Path(name))
        return importer.preview(path.read_bytes(), fmt, settings) | {"name": name}
    except importer.ManuscriptError as e:
        raise HTTPException(400, str(e)) from None


@router.post("")
def import_manuscript(state: State, body: ManuscriptRequest) -> dict[str, str]:
    p = state.require_project()
    path, name, upload_id = resolve(state, body.source)
    settings = parse_settings(body.settings)
    try:
        importer.detect_format(Path(name))
    except importer.ManuscriptError as e:
        raise HTTPException(400, str(e)) from None

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0.1, "reading manuscript")
        try:
            result = importer.import_manuscript(p, path, settings, original_name=name)
        finally:
            if upload_id is not None:
                # Our temporary upload; the project now holds its own copy (or failed).
                state.uploads.pop(upload_id, None)
                path.unlink(missing_ok=True)
        if state.project is p:
            state.manuscript_status = "ok"
        return result

    return {"job_id": state.jobs.submit("manuscript", f"Importing {name}", run, lane="import").id}


@router.put("/settings")
def remap(state: State, body: SettingsRequest) -> dict[str, str]:
    p = state.require_project()
    settings = parse_settings(body.settings)

    def run(ctx: JobContext) -> dict[str, Any]:
        ctx.progress(0.1, "re-mapping pages")
        return importer.remap(p, settings)

    return {"job_id": state.jobs.submit("manuscript", "Re-mapping pages", run, lane="import").id}


@router.get("")
def info(state: State) -> dict[str, Any] | None:
    result = importer.manuscript_info(state.require_project())
    if result is not None:
        result["status"] = state.manuscript_status or "ok"
    return result


@router.get("/units")
def units(
    state: State,
    kind: Literal["chapter", "paragraph", "sentence"] = "sentence",
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=MAX_UNITS_PER_REQUEST),
) -> dict[str, Any]:
    return importer.list_units(state.require_project(), kind, offset, limit)
