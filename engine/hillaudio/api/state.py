"""Engine state shared by the API routers: the open project and the job runner."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException, Request

from hillaudio.api.jobs import JobManager
from hillaudio.asr.base import AsrSettings, Transcriber
from hillaudio.store.project import Project

TranscriberFactory = Callable[[AsrSettings], Transcriber]


def _whisper(settings: AsrSettings) -> Transcriber:
    from hillaudio.asr.whisper import WhisperTranscriber

    return WhisperTranscriber(settings)


class EngineState:
    def __init__(self, transcriber_factory: TranscriberFactory = _whisper) -> None:
        self.jobs = JobManager()
        # Tests inject a fake; the app uses faster-whisper (D-028).
        self.transcriber_factory = transcriber_factory
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
