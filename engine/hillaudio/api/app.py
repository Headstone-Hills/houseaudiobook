"""FastAPI application: the only way the UI reaches projects and audio (SPEC §3).

The engine binds to localhost only (see `main`), and LocalOnlyGuard refuses
requests that don't come from our own UI. One project is open at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from hillaudio import __version__, functions, settings, tools
from hillaudio.analysis.peaks import BASE, FACTOR, LEVELS, Peaks
from hillaudio.api import (
    analysis,
    chapters,
    edl,
    finalize,
    issues,
    manuscript,
    models,
    takes,
    timeline,
)
from hillaudio.api.guard import DEFAULT_HOSTS, LocalOnlyGuard, allowed_origins
from hillaudio.api.jobs import JobContext
from hillaudio.api.state import EngineState, TranscriberFactory
from hillaudio.ingest import peaks_cache, verify
from hillaudio.ingest.importer import IngestError, build_peaks, import_file
from hillaudio.ingest.probe import UnsupportedAudio, probe
from hillaudio.progress import never_cancel, no_progress
from hillaudio.store.project import Project, ProjectError
from hillaudio.text import importer as text_importer
from hillaudio.text.pages import PageSettings

MAX_PEAKS_PER_REQUEST = 50_000
WS_POLL_S = 0.1


class CreateProject(BaseModel):
    location: str
    name: str
    functions: list[str] = Field(default_factory=lambda: list(functions.DEFAULT))


class OpenProject(BaseModel):
    path: str


class ImportPaths(BaseModel):
    paths: list[str] = Field(min_length=1)
    mode: Literal["copy", "reference"] = "copy"
    role: Literal["narration", "pickup"] = "narration"  # D-062


class Relink(BaseModel):
    path: str


def create_app(
    hosts: frozenset[str] = DEFAULT_HOSTS,
    origins: frozenset[str] | None = None,
    transcriber_factory: TranscriberFactory | None = None,
    web_dir: Path | None = None,
) -> FastAPI:
    """The engine. `web_dir`: a built UI (web/dist) to serve at /, so one localhost
    port serves both (the packaged app, `make serve`); without it, the Vite dev
    server serves the UI and proxies /api here (`make dev`)."""
    state = EngineState(transcriber_factory) if transcriber_factory else EngineState()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        state.jobs.shutdown()

    app = FastAPI(title="HillAudiobook engine", version=__version__, lifespan=lifespan)
    app.state.engine = state

    # -- helpers ---------------------------------------------------------------------

    def summary(p: Project) -> dict[str, Any]:
        return {
            "name": p.name,
            "path": str(p.root),
            "functions": p.functions,
            "sources": [
                s.to_json() | {"status": state.status.get(s.id, "ok")} for s in p.sources()
            ],
            "warnings": state.warnings,
            "manuscript": manuscript.info(state),
        }

    def switch_to(p: Project) -> dict[str, Any]:
        if state.jobs.active():
            raise HTTPException(409, "wait for running jobs to finish or cancel them first")
        state.jobs.clear_finished()
        state.discard_uploads()
        state.project, state.warnings = p, []
        state.status = {s.id: "checking" for s in p.sources()}
        has_manuscript = text_importer.manuscript_info(p) is not None
        state.manuscript_status = "checking" if has_manuscript else None
        settings.remember(p.root, p.name)
        if state.status or has_manuscript:
            start_verify(p)
        return summary(p)

    def start_verify(p: Project) -> str:
        def run(ctx: JobContext) -> dict[str, Any]:
            results = verify.verify_all(p, ctx.progress, ctx.check_cancel)
            text_status = text_importer.verify_copy(p)
            if state.project is p:
                state.status.update({r.source_id: r.status for r in results})
                state.manuscript_status = text_status
            problems = [asdict(r) for r in results if r.status != "ok"]
            return {"checked": len(results), "problems": problems, "manuscript": text_status}

        return state.jobs.submit("verify", f"Verifying sources of {p.name}", run).id

    def start_import(
        p: Project,
        paths: list[tuple[Path, str | None]],
        mode: str,
        cleanup: Path | None = None,
        role: Literal["narration", "pickup"] = "narration",
    ) -> str:
        def run(ctx: JobContext) -> dict[str, Any]:
            results: list[dict[str, Any]] = []
            try:
                for i, (path, original_name) in enumerate(paths):
                    name = original_name or path.name
                    scaled = lambda f, m, i=i, name=name: ctx.progress(  # noqa: E731
                        (i + f) / len(paths), f"{name}: {m}"
                    )
                    try:
                        r = import_file(
                            p,
                            path,
                            mode,  # type: ignore[arg-type]
                            original_name=original_name,
                            role=role,
                            progress=scaled,
                            check_cancel=ctx.check_cancel,
                        )
                    except (IngestError, UnsupportedAudio) as e:
                        # One bad file doesn't stop the rest of a multi-file import.
                        results.append({"name": name, "error": str(e)})
                        continue
                    state.status[r.source.id] = "ok"
                    state.warnings = sorted(set(state.warnings) | set(r.warnings))
                    results.append(
                        {
                            "name": name,
                            "source_id": r.source.id,
                            "created": r.created,
                            "warnings": r.warnings,
                        }
                    )
            finally:
                if cleanup is not None:
                    # Our own temporary upload, now copied into sources/ (or rejected).
                    cleanup.unlink(missing_ok=True)
            return {"files": results}

        label = paths[0][1] or paths[0][0].name if len(paths) == 1 else f"{len(paths)} files"
        what = "pickups " if role == "pickup" else ""
        return state.jobs.submit("import", f"Importing {what}{label}", run, lane="import").id

    def load_peaks(p: Project, source_id: int) -> tuple[Any, Peaks]:
        try:
            src = p.source(source_id)
        except KeyError:
            raise HTTPException(404, "no such source") from None
        peaks = peaks_cache.load(p.cache_dir, src.sha256)
        if peaks is None:
            # The cache is regenerable (SPEC §4); rebuild if it was cleared.
            path = p.resolve(src)
            if not path.is_file():
                raise HTTPException(409, "source file is missing; relink it first")
            peaks = build_peaks(path, probe(path), no_progress, never_cancel)
            peaks_cache.save(p.cache_dir, src.sha256, peaks)
        return src, peaks

    # -- routes ------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        # Which ffmpeg is in use: the packaged app must use its own copy (D-056).
        return {"status": "ok", "version": __version__, "ffmpeg": tools.ffmpeg()}

    @app.get("/api/functions")
    def list_functions() -> list[dict[str, Any]]:
        return [asdict(f) for f in functions.FUNCTIONS]

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return {
            "default_location": str(settings.default_location()),
            "recent": [asdict(r) for r in settings.load_recent()],
            # The UI picks a peak level for its zoom from these, rather than hard-coding them.
            "peaks": {"base": BASE, "factor": FACTOR, "levels": LEVELS},
            "page_defaults": PageSettings().to_json(),
        }

    @app.post("/api/projects")
    def create_project(body: CreateProject) -> dict[str, Any]:
        if state.jobs.active():
            raise HTTPException(409, "wait for running jobs to finish or cancel them first")
        location = Path(body.location).expanduser()
        if location == settings.default_location():
            location.mkdir(parents=True, exist_ok=True)  # our own default; safe to create
        try:
            p = Project.create(location, body.name, body.functions)
        except ProjectError as e:
            raise HTTPException(400, str(e)) from None
        return switch_to(p)

    @app.post("/api/projects/open")
    def open_project(body: OpenProject) -> dict[str, Any]:
        try:
            p = Project.open(Path(body.path))
        except (ProjectError, RuntimeError) as e:
            raise HTTPException(400, str(e)) from None
        return switch_to(p)

    @app.post("/api/project/close")
    def close_project() -> dict[str, bool]:
        if state.jobs.active():
            raise HTTPException(409, "wait for running jobs to finish or cancel them first")
        state.discard_uploads()
        state.project, state.status, state.warnings = None, {}, []
        state.manuscript_status = None
        return {"closed": True}

    @app.get("/api/project")
    def get_project() -> dict[str, Any] | None:
        """The open project, or null: "none open" is a normal answer here, not an error."""
        return summary(state.project) if state.project is not None else None

    @app.post("/api/project/sources/import")
    def import_paths(body: ImportPaths) -> dict[str, str]:
        p = state.require_project()
        return {
            "job_id": start_import(
                p, [(Path(x), None) for x in body.paths], body.mode, role=body.role
            )
        }

    @app.put("/api/project/sources/upload")
    async def upload(
        request: Request,
        filename: str = Query(min_length=1),
        role: Literal["narration", "pickup"] = "narration",
    ) -> dict[str, str]:
        """Browsers can't share file paths, so drag-and-drop streams the bytes here."""
        p = state.require_project()
        name = Path(filename).name  # never trust a client path
        if Path(name).suffix.lower() not in (".wav", ".wave", ".mp3"):
            raise HTTPException(400, "only .wav and .mp3 files can be imported")
        tmp_dir = p.cache_dir / "uploads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
        try:
            with tmp.open("wb") as f:
                async for chunk in request.stream():
                    f.write(chunk)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return {"job_id": start_import(p, [(tmp, name)], "copy", cleanup=tmp, role=role)}

    @app.post("/api/project/sources/{source_id}/relink")
    def relink(source_id: int, body: Relink) -> dict[str, Any]:
        p = state.require_project()
        try:
            src = verify.relink(p, source_id, Path(body.path))
        except KeyError:
            raise HTTPException(404, "no such source") from None
        except IngestError as e:
            raise HTTPException(400, str(e)) from None
        state.status[src.id] = "ok"
        return summary(p)

    @app.get("/api/project/sources/{source_id}/peaks")
    def get_peaks(
        source_id: int,
        level: int = Query(0, ge=0, lt=LEVELS),
        start: int = Query(0, ge=0),
        count: int = Query(MAX_PEAKS_PER_REQUEST, ge=1, le=MAX_PEAKS_PER_REQUEST),
    ) -> dict[str, Any]:
        src, peaks = load_peaks(state.require_project(), source_id)
        lvl = peaks.levels[level]
        end = min(start + count, len(lvl.max))
        return {
            "source_id": src.id,
            "sample_rate": src.sample_rate,
            "num_frames": peaks.num_frames,
            "level": level,
            "frames_per_bucket": lvl.frames_per_bucket,
            "total_buckets": len(lvl.max),
            "start": start,
            "min": lvl.min[start:end].tolist(),
            "max": lvl.max[start:end].tolist(),
        }

    @app.get("/api/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return state.jobs.snapshot()[1]

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return state.jobs.cancel(job_id).to_json()
        except KeyError:
            raise HTTPException(404, "no such job") from None

    @app.websocket("/api/jobs/ws")
    async def jobs_ws(ws: WebSocket) -> None:
        await ws.accept()
        state.ws_clients += 1

        async def pump() -> None:
            seen = -1
            while True:
                version, jobs = state.jobs.snapshot()
                if version != seen:
                    await ws.send_json({"type": "jobs", "jobs": jobs})
                    seen = version
                await asyncio.sleep(WS_POLL_S)

        # The pump only sends when jobs change, so on its own it would never notice
        # a client that left (regression: handlers leaked and blocked shutdown).
        # Waiting on receive() returns as soon as the client or server closes.
        pump_task = asyncio.create_task(pump())
        try:
            while (await ws.receive())["type"] != "websocket.disconnect":
                pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await pump_task
            state.ws_clients -= 1

    app.include_router(manuscript.router)
    app.include_router(analysis.router)
    app.include_router(chapters.router)
    app.include_router(issues.router)
    app.include_router(edl.router)
    app.include_router(takes.router)
    app.include_router(timeline.router)
    app.include_router(finalize.router)
    app.include_router(models.router)
    if web_dir is not None:
        if not (web_dir / "index.html").is_file():
            raise RuntimeError(f"no built UI in {web_dir} (run: cd web && npm run build)")
        from fastapi.staticfiles import StaticFiles

        # Mounted last, so every /api route above wins.
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    app.add_middleware(
        LocalOnlyGuard,
        hosts=hosts,
        origins=origins if origins is not None else allowed_origins(),
    )
    return app


def main() -> None:
    """`make dev`: the API on :8765 with auto-reload (the UI comes from Vite)."""
    import uvicorn

    uvicorn.run(
        "hillaudio.api.app:create_app", factory=True, host="127.0.0.1", port=8765, reload=True
    )


if __name__ == "__main__":
    main()
