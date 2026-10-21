"""The engine as the browser e2e tests run it: real, except speech recognition.

Real transcription would download a 464 MB model in every CI run, so the e2e
engine uses the ground-truth fake transcriber (tests/fakeasr.py). The real model
is covered by the opt-in tests/test_real_model.py.

Speech models come from a fake Hugging Face (tests/fakehf.py) with small fake
weights: small.en is installed at startup (the specs that transcribe need it);
tiny.en is left for the first-run download spec to fetch through the UI.

    uvicorn e2e_app:create --factory --app-dir engine/tests   (PYTHONPATH=scripts)
"""

from __future__ import annotations

import os

from fakeasr import FakeTranscriber
from fakehf import FakeHub, fake_model
from fastapi import FastAPI

from hillaudio.api.app import create_app
from hillaudio.asr import models


def create() -> FastAPI:
    hub = FakeHub()
    hub.delay_s = 0.02  # a few seconds of visible progress for tiny.en
    models.CATALOG.clear()
    for name, size in (("tiny.en", 6_000_000), ("small.en", 1_000_000)):
        m, files = fake_model(name, bytes(range(256)) * (size // 256))
        models.CATALOG[name] = m
        hub.add(m, files)
    os.environ["HILLAUDIO_MODEL_ENDPOINT"] = hub.url
    models.download("small.en")
    return create_app(transcriber_factory=FakeTranscriber)
