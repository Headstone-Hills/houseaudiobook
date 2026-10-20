"""The engine as the browser e2e tests run it: real, except speech recognition.

Real transcription would download a 464 MB model in every CI run, so the e2e
engine uses the ground-truth fake transcriber (tests/fakeasr.py). The real model
is covered by the opt-in tests/test_real_model.py.

    uvicorn e2e_app:create --factory --app-dir engine/tests   (PYTHONPATH=scripts)
"""

from __future__ import annotations

from fakeasr import FakeTranscriber
from fastapi import FastAPI

from hillaudio.api.app import create_app


def create() -> FastAPI:
    return create_app(transcriber_factory=FakeTranscriber)
