"""Speech models: a pinned catalog, and explicit, verified, resumable downloads.

First run (M9, D-055): the app shows what it needs (name, size, source) and
downloads only when the user says so, with progress, cancel and resume. Each
file is fetched at a pinned Hugging Face revision and checked against hashes
recorded here (SHA-256 for the weights, the git blob SHA-1 for small files), so
a changed or corrupted file is never used. Transcription uses installed models
only: nothing downloads behind the user's back, and audio never leaves the
machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from hillaudio.progress import CheckCancel, Progress, never_cancel, no_progress

ENDPOINT = "https://huggingface.co"
CHUNK = 1 << 20
MARKER = ".installed.json"


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str | None = None  # LFS files (the weights)
    git_sha1: str | None = None  # small files: the git blob id


@dataclass(frozen=True)
class Model:
    name: str
    repo: str
    revision: str  # pinned commit
    license: str
    files: tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def size_mb(self) -> int:
        return round(self.size / 1e6)


_TOK = ModelFile("tokenizer.json", 2128466, git_sha1="15d7bdf9ba25718ca2504eec6a8f02bc55af0a6a")
_VOCAB = ModelFile("vocabulary.txt", 422309, git_sha1="ee695b8d3e3c10d488304e04468efec4ca27554a")

# Read from https://huggingface.co/api/models/<repo>?blobs=true on 2026-10-21.
CATALOG: dict[str, Model] = {
    m.name: m
    for m in (
        Model(
            "tiny.en",
            "Systran/faster-whisper-tiny.en",
            "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
            "MIT",
            (
                ModelFile("config.json", 2317, git_sha1="4065bb3bed375b176d5465be117d2d202e210434"),
                ModelFile(
                    "model.bin",
                    75537502,
                    "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a",
                ),
                _TOK,
                _VOCAB,
            ),
        ),
        Model(
            "base.en",
            "Systran/faster-whisper-base.en",
            "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
            "MIT",
            (
                ModelFile("config.json", 2227, git_sha1="594369787efe617005d199b03739ee0ead7e3ab7"),
                ModelFile(
                    "model.bin",
                    145216508,
                    "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
                ),
                _TOK,
                _VOCAB,
            ),
        ),
        Model(
            "small.en",
            "Systran/faster-whisper-small.en",
            "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
            "MIT",
            (
                ModelFile("config.json", 2657, git_sha1="ced3fd5bbb3e9f7664658065d2d36164712cf1d5"),
                ModelFile(
                    "model.bin",
                    483545366,
                    "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
                ),
                _TOK,
                _VOCAB,
            ),
        ),
        Model(
            "medium.en",
            "Systran/faster-whisper-medium.en",
            "a29b04bd15381511a9af671baec01072039215e3",
            "MIT",
            (
                ModelFile("config.json", 2643, git_sha1="094bf1535b71e750d2c721808455dbb34237db98"),
                ModelFile(
                    "model.bin",
                    1527904330,
                    "11b220779aea4c6f3ce9d2549c8a95ea869ed84066864b999531ef53e594fe5b",
                ),
                _TOK,
                _VOCAB,
            ),
        ),
    )
}


class ModelError(RuntimeError):
    """A user-facing reason a model can't be downloaded or used."""


def models_dir() -> Path:
    from hillaudio.asr.whisper import models_dir as d

    return d()


def endpoint() -> str:
    return os.environ.get("HILLAUDIO_MODEL_ENDPOINT", ENDPOINT).rstrip("/")


def get(name: str) -> Model:
    try:
        return CATALOG[name]
    except KeyError:
        raise ModelError(f"unknown model {name!r}") from None


def _hf_cache_dir(m: Model) -> Path:
    """Where an earlier version (huggingface_hub's cache) put this revision."""
    return models_dir() / f"models--{m.repo.replace('/', '--')}" / "snapshots" / m.revision


def local_dir(m: Model) -> Path | None:
    """The folder holding a complete, verified copy, if there is one."""
    d = models_dir() / m.name
    marker = d / MARKER
    if marker.is_file():
        try:
            info = json.loads(marker.read_text())
        except ValueError:
            info = {}
        if info.get("revision") == m.revision and all(
            (d / f.name).is_file() and (d / f.name).stat().st_size == f.size for f in m.files
        ):
            return d
    # Adopt a copy the older implicit download left behind (same pinned revision).
    hf = _hf_cache_dir(m)
    if all((hf / f.name).is_file() and (hf / f.name).stat().st_size == f.size for f in m.files):
        return hf
    return None


def status() -> list[dict[str, object]]:
    return [
        {
            "name": m.name,
            "repo": m.repo,
            "revision": m.revision,
            "license": m.license,
            "size_mb": m.size_mb,
            "installed": local_dir(m) is not None,
        }
        for m in CATALOG.values()
    ]


def _ssl() -> ssl.SSLContext:
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def _verify(f: ModelFile, path: Path) -> bool:
    if path.stat().st_size != f.size:
        return False
    if f.sha256:
        h = hashlib.sha256()
    elif f.git_sha1:
        h = hashlib.sha1(usedforsecurity=False)
        h.update(b"blob %d\0" % f.size)
    else:
        return True
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest() == (f.sha256 or f.git_sha1)


def download(
    name: str, progress: Progress = no_progress, check_cancel: CheckCancel = never_cancel
) -> Path:
    """Fetch every file of a model (resuming partial files), verify each, then
    mark the folder complete. Nothing is marked installed unless all verify."""
    m = get(name)
    have = local_dir(m)
    if have is not None:
        progress(1.0, "already downloaded")
        return have
    d = models_dir() / m.name
    d.mkdir(parents=True, exist_ok=True)
    (d / MARKER).unlink(missing_ok=True)
    total = m.size
    done_before = 0
    ctx = _ssl()
    for f in m.files:
        dest = d / f.name
        if dest.is_file() and _verify(f, dest):
            done_before += f.size
            continue
        part = d / (f.name + ".part")
        have_bytes = part.stat().st_size if part.is_file() else 0
        if have_bytes > f.size:
            part.unlink()
            have_bytes = 0
        url = f"{endpoint()}/{m.repo}/resolve/{m.revision}/{f.name}"
        req = urllib.request.Request(url, headers={"User-Agent": "HillAudiobook"})
        if have_bytes:
            req.add_header("Range", f"bytes={have_bytes}-")
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                if have_bytes and resp.status != 206:
                    have_bytes = 0  # the server ignored the range: start over
                with part.open("ab" if have_bytes else "wb") as out:
                    got = have_bytes
                    while True:
                        check_cancel()
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        got += len(chunk)
                        progress(
                            (done_before + got) / total,
                            f"{m.name}: {(done_before + got) / 1e6:.0f} of {total / 1e6:.0f} MB",
                        )
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ModelError(
                f"couldn't download {m.name} from {endpoint()} ({e}); it resumes where it "
                "stopped when you try again"
            ) from None
        if part.stat().st_size < f.size:
            # The connection ended early without an error: keep what arrived.
            raise ModelError(
                f"the download of {m.name} was interrupted; it resumes where it stopped "
                "when you try again"
            )
        if not _verify(f, part):
            part.unlink(missing_ok=True)
            raise ModelError(f"{f.name} of {m.name} didn't match its checksum; it was discarded")
        part.replace(dest)
        done_before += f.size
    (d / MARKER).write_text(json.dumps({"repo": m.repo, "revision": m.revision}))
    progress(1.0, f"{m.name} ready")
    return d


def remove(name: str) -> bool:
    """Delete a downloaded model (its weights only; nothing of the user's)."""
    m = get(name)
    d = models_dir() / m.name
    if d.is_dir():
        shutil.rmtree(d)
        return True
    return False
