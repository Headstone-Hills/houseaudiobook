"""Engine state shared by the API routers: the open project and the job runner."""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import HTTPException, Request

from hillaudio.api.jobs import JobManager
from hillaudio.store.project import Project


class EngineState:
    def __init__(self) -> None:
        self.jobs = JobManager()
        self.project: Project | None = None
        self.status: dict[int, str] = {}  # source id -> ok | missing | modified | checking
        self.manuscript_status: str | None = None  # ok | modified | missing | checking
        self.warnings: list[str] = []
        self.uploads: dict[str, tuple[Path, str]] = {}  # upload id -> (temp file, name)
        self.ws_clients = 0  # open job WebSockets (tests check handlers exit)

    def require_project(self) -> Project:
        if self.project is None:
            raise HTTPException(409, "no project is open")
        return self.project

    def discard_uploads(self) -> None:
        """Remove our own temporary upload files (never user files)."""
        for path, _name in self.uploads.values():
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        self.uploads.clear()


def engine(request: Request) -> EngineState:
    """FastAPI dependency: the app's EngineState."""
    state: EngineState = request.app.state.engine
    return state
