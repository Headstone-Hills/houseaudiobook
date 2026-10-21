"""Stream-decode audio with ffmpeg.

Decoding streams in fixed-size blocks so a 2 GB WAV never has to fit in memory.
The source is only ever opened for reading, by ffmpeg.
"""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from pathlib import Path

import numpy as np
import numpy.typing as npt

from hillaudio import tools

BLOCK_FRAMES = 1 << 16


def iter_blocks(
    path: Path, channels: int, block_frames: int = BLOCK_FRAMES
) -> Generator[npt.NDArray[np.float32], None, None]:
    """Yield (frames, channels) float32 blocks at the file's native sample rate."""
    proc = subprocess.Popen(
        [
            tools.ffmpeg(),
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    frame_bytes = 4 * channels
    want = block_frames * frame_bytes
    leftover = b""
    finished = False
    try:
        # A buffered read(n) blocks until n bytes or EOF, so an empty read is EOF.
        while data := proc.stdout.read(want):
            buf = leftover + data
            usable = len(buf) - len(buf) % frame_bytes
            leftover = buf[usable:]
            if usable:
                yield np.frombuffer(buf[:usable], dtype=np.float32).reshape(-1, channels)
        finished = True
    finally:
        if not finished:
            # Consumer stopped early (e.g. job cancelled): don't leave ffmpeg running.
            proc.kill()
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace")
        proc.stderr.close()
        code = proc.wait()
    if finished and code != 0:
        raise RuntimeError(f"ffmpeg failed to decode {path.name}: {err.strip()[:200]}")


def excerpt_wav(path: Path, start: float, end: float) -> bytes:
    """[start, end) of the source as a 16-bit WAV, for listening in the browser.

    A preview only: exports always render from the original source (CLAUDE.md).
    Seeking before -i is sample-accurate when decoding in modern ffmpeg.
    """
    proc = subprocess.run(
        [
            tools.ffmpeg(),
            "-v",
            "error",
            "-nostdin",
            "-ss",
            f"{max(start, 0.0):.6f}",
            "-t",
            f"{max(end - start, 0.0):.6f}",
            "-i",
            str(path),
            "-f",
            "wav",
            "-acodec",
            "pcm_s16le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()[:200]
        raise RuntimeError(f"ffmpeg failed to decode {path.name}: {err}")
    return proc.stdout
