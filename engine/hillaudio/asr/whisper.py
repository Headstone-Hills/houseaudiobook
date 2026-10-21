"""faster-whisper backend (D-028).

Models are downloaded explicitly, verified, into `$HILLAUDIO_HOME/models`
(asr/models.py, D-055): the only network use in the app. Audio never leaves the
machine: transcription runs locally on the CPU.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from hillaudio import settings as app_settings
from hillaudio.asr.base import AsrSettings, AsrUnavailable, TranscriptWord
from hillaudio.progress import CheckCancel, Progress

_models: dict[tuple[str, str], Any] = {}
_lock = threading.Lock()


def models_dir() -> Path:
    """Where weights live: $HILLAUDIO_MODELS, else $HILLAUDIO_HOME/models."""
    override = os.environ.get("HILLAUDIO_MODELS")
    return Path(override).expanduser() if override else app_settings.app_home() / "models"


def installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _model(s: AsrSettings) -> Any:
    """Load once per process; loading small.en takes seconds."""
    key = (s.model, s.compute_type)
    with _lock:
        if key not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise AsrUnavailable(
                    "speech recognition isn't installed (pip install 'hillaudio[asr]')"
                ) from None
            from hillaudio.asr import models

            spec = models.CATALOG.get(s.model)
            local = models.local_dir(spec) if spec else None
            if local is None:
                size = f" (about {spec.size_mb} MB)" if spec else ""
                raise AsrUnavailable(
                    f"the {s.model} speech model isn't downloaded yet{size}. Download it under "
                    "Speech models; it's fetched once from huggingface.co, and audio never "
                    "leaves this computer"
                )
            try:
                _models[key] = WhisperModel(str(local), device="cpu", compute_type=s.compute_type)
            except Exception as e:  # a damaged copy, or not enough memory
                raise AsrUnavailable(f"couldn't load the {s.model} speech model ({e})") from None
        return _models[key]


class WhisperTranscriber:
    def __init__(self, settings: AsrSettings) -> None:
        self.settings = settings

    def transcribe(
        self, path: Path, duration: float, progress: Progress, check_cancel: CheckCancel
    ) -> list[TranscriptWord]:
        s = self.settings
        progress(0.0, f"loading {s.model}")
        model = _model(s)
        segments, _info = model.transcribe(
            str(path),
            language=s.language,
            word_timestamps=True,
            beam_size=s.beam_size,
            initial_prompt=s.prompt or None,
        )
        words: list[TranscriptWord] = []
        # Segments are produced lazily as decoding proceeds, so this loop is where
        # the time goes: report progress and honour cancellation per segment.
        for seg in segments:
            check_cancel()
            for w in seg.words or []:
                words.append(
                    TranscriptWord(
                        w.word.strip(), float(w.start), float(w.end), float(w.probability)
                    )
                )
            progress(min(float(seg.end) / max(duration, 1e-6), 1.0), "transcribing")
        return words
