from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from hillaudio.store.project import Project

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MakeAudio = Callable[..., Path]


@pytest.fixture
def make_audio(tmp_path: Path) -> MakeAudio:
    """Write a test tone file. MP3s are encoded once from a WAV with ffmpeg."""
    out_dir = tmp_path / "audio"
    out_dir.mkdir(exist_ok=True)

    def make(
        name: str = "tone.wav",
        seconds: float = 1.0,
        sample_rate: int = 44_100,
        channels: int = 2,
        subtype: str = "PCM_24",
        freq: float = 440.0,
    ) -> Path:
        t = np.arange(round(seconds * sample_rate)) / sample_rate
        mono = 0.5 * np.sin(2 * np.pi * freq * t)
        data = np.stack(
            [mono * (0.5 + 0.5 * c / max(channels - 1, 1)) for c in range(channels)], axis=1
        )
        path = out_dir / name
        if path.suffix == ".mp3":
            wav = out_dir / (path.stem + ".src.wav")
            sf.write(str(wav), data, sample_rate, subtype="PCM_16")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-b:a", "128k", str(path)],
                check=True,
            )
            wav.unlink()
        else:
            sf.write(str(path), data, sample_rate, subtype=subtype)
        return path

    return make


@pytest.fixture
def project(tmp_path: Path) -> Project:
    loc = tmp_path / "projects"
    loc.mkdir()
    return Project.create(loc, "Test Book")


@pytest.fixture
def fixture_copy(tmp_path: Path) -> Callable[[str], Path]:
    """Copy a committed fixture into tmp, for tests that need to alter the file."""

    def copy(name: str) -> Path:
        dest = tmp_path / "fixture-copies" / name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(FIXTURES / name, dest)
        return dest

    return copy


def _hashes(root: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.iterdir())
        if p.is_file()
    }


@pytest.fixture(scope="session", autouse=True)
def fixtures_untouched() -> Iterator[None]:
    """Fail the run if any test modified committed fixture audio or text.

    Fixtures stand in for user source files, which must never be modified.
    """
    before = _hashes(FIXTURES)
    yield
    after = _hashes(FIXTURES)
    assert after == before, "a test modified files in engine/fixtures"
