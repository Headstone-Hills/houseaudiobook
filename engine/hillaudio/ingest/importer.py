"""Import source audio into a project.

The rule this module exists to keep: **a user's source file is only ever read.**
Copy mode reads it once into `sources/` (then verifies the copy's hash and marks
it read-only); reference mode records where it is. Either way the original's bytes
and timestamps are untouched; `tests/test_source_immutability.py` proves it.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from hillaudio.analysis.peaks import PeakBuilder, Peaks
from hillaudio.ingest import peaks_cache
from hillaudio.ingest.decode import iter_blocks
from hillaudio.ingest.hashing import sha256_file
from hillaudio.ingest.probe import ProbeInfo, probe
from hillaudio.progress import CheckCancel, Progress, never_cancel, no_progress
from hillaudio.store.project import Project, Source, now_iso

Mode = Literal["copy", "reference"]


class IngestError(ValueError):
    """A user-facing reason a file can't be imported or relinked."""


@dataclass
class ImportResult:
    source: Source
    created: bool
    warnings: list[str] = field(default_factory=list)


def safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "audio"
    return stem[:120]


def build_peaks(
    path: Path, info: ProbeInfo, progress: Progress, check_cancel: CheckCancel
) -> Peaks:
    builder = PeakBuilder()
    expected = max(1, round(info.duration * info.sample_rate))
    for block in iter_blocks(path, info.channels):
        check_cancel()
        builder.add(block)
        progress(min(builder.num_frames / expected, 1.0), "computing waveform")
    return builder.finish()


def sample_rate_warning(sources: list[Source], new_rate: int) -> str | None:
    rates = sorted({s.sample_rate for s in sources} | {new_rate})
    if len(rates) > 1:
        listed = ", ".join(f"{r} Hz" for r in rates)
        return f"sources have mixed sample rates ({listed}); the engine resamples internally"
    return None


def import_file(
    project: Project,
    path: Path,
    mode: Mode = "copy",
    *,
    original_name: str | None = None,
    progress: Progress = no_progress,
    check_cancel: CheckCancel = never_cancel,
) -> ImportResult:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise IngestError(f"file not found: {path}")
    if project.sources_dir.resolve() in path.parents:
        raise IngestError("that file is already inside this project's sources folder")
    name = original_name or path.name

    info = probe(path)  # raises UnsupportedAudio (a ValueError) for non-wav/mp3

    size = path.stat().st_size
    done = 0

    def hashed(n: int) -> None:
        nonlocal done
        done += n
        check_cancel()
        progress(0.3 * done / max(size, 1), "hashing")

    digest = sha256_file(path, hashed)

    existing = {s.sha256: s for s in project.sources()}
    if digest in existing:
        dup = existing[digest]
        return ImportResult(
            dup, created=False, warnings=[f"{name} is identical to already-imported {dup.label!r}"]
        )

    stored_rel: str
    partial: Path | None = None
    if mode == "copy":
        final = project.sources_dir / f"{digest[:12]}_{safe_filename(name)}"
        partial = final.with_name(final.name + ".partial")
        try:
            progress(0.3, "copying into project")
            shutil.copyfile(path, partial)  # reads `path`; writes only our own file
            if sha256_file(partial) != digest:
                raise IngestError(f"copy of {name} did not match the original; nothing imported")
            # Read-only: guards against tools (or our own bugs) writing into sources.
            partial.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(partial, final)
            partial = None
        finally:
            if partial is not None:
                # Only ever our own half-written copy, never the user's file.
                with contextlib.suppress(FileNotFoundError):
                    partial.chmod(stat.S_IWUSR | stat.S_IRUSR)
                    partial.unlink()
        stored_rel = str(final.relative_to(project.root))
        stored_abs = final
    else:
        stored_rel = str(path)
        stored_abs = path

    try:
        scaled: Progress = lambda f, m: progress(0.4 + 0.6 * f, m)  # noqa: E731
        peaks = build_peaks(stored_abs, info, scaled, check_cancel)
        if peaks.num_frames == 0:
            raise IngestError(f"{name} contains no audio")
        peaks_cache.save(project.cache_dir, digest, peaks)
    except BaseException:
        if mode == "copy":
            # Roll back our own copy so a cancelled import leaves no trace.
            stored_abs.chmod(stat.S_IWUSR | stat.S_IRUSR)
            stored_abs.unlink()
        raise

    warnings: list[str] = []
    w = sample_rate_warning(list(existing.values()), info.sample_rate)
    if w:
        warnings.append(w)

    with project.db() as conn:
        order = conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 FROM source_file"
        ).fetchone()[0]
        cur = conn.execute(
            """INSERT INTO source_file (mode, path, original_path, original_name, sha256,
                size_bytes, format, codec, lossless, sample_rate, channels, bit_depth,
                bit_rate, num_frames, duration, label, order_index, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mode,
                stored_rel,
                str(path),
                name,
                digest,
                size,
                info.format,
                info.codec,
                int(info.lossless),
                info.sample_rate,
                info.channels,
                info.bit_depth,
                info.bit_rate,
                peaks.num_frames,
                peaks.num_frames / info.sample_rate,
                Path(name).stem,
                order,
                now_iso(),
            ),
        )
        source_id = cur.lastrowid
        project.log(
            conn,
            "import_source",
            {"source_id": source_id, "sha256": digest, "mode": mode, "name": name},
            "user",
        )
    assert source_id is not None
    progress(1.0, "imported")
    return ImportResult(project.source(source_id), created=True, warnings=warnings)
