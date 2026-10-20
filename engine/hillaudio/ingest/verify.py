"""Verify sources against their recorded hashes on every open (SPEC §5), and relink.

Verification only reads. A mismatch is reported, never "repaired": the user
decides whether to relink.
"""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from hillaudio.ingest.hashing import sha256_file
from hillaudio.ingest.importer import IngestError
from hillaudio.progress import CheckCancel, Progress, never_cancel, no_progress
from hillaudio.store.project import Project, Source

Status = Literal["ok", "missing", "modified"]


@dataclass(frozen=True)
class SourceStatus:
    source_id: int
    status: Status
    path: str


def check(project: Project, src: Source, check_cancel: CheckCancel = never_cancel) -> SourceStatus:
    path = project.resolve(src)
    if not path.is_file():
        return SourceStatus(src.id, "missing", str(path))

    def tick(_n: int) -> None:
        check_cancel()

    ok = sha256_file(path, tick) == src.sha256
    return SourceStatus(src.id, "ok" if ok else "modified", str(path))


def verify_all(
    project: Project, progress: Progress = no_progress, check_cancel: CheckCancel = never_cancel
) -> list[SourceStatus]:
    sources = project.sources()
    out = []
    for i, src in enumerate(sources):
        progress(i / max(len(sources), 1), f"verifying {src.label}")
        out.append(check(project, src, check_cancel))
    progress(1.0, "verified")
    return out


def relink(project: Project, source_id: int, new_path: Path) -> Source:
    """Point a source at a file with the *same* content (hash must match).

    For a reference source this records the new location. For a copied source
    whose copy is gone or damaged, the matching file is copied back in.
    """
    src = project.source(source_id)
    new_path = new_path.expanduser().resolve()
    if not new_path.is_file():
        raise IngestError(f"file not found: {new_path}")
    if sha256_file(new_path) != src.sha256:
        raise IngestError(
            f"{new_path.name} is not the same audio as {src.label!r} (content differs)"
        )
    if src.mode == "copy":
        dest = project.root / src.path
        if dest.exists():
            # Never delete a source, even a damaged copy: set it aside for the user.
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            dest.rename(dest.with_name(f"{dest.name}.damaged-{stamp}"))
        shutil.copyfile(new_path, dest)
        dest.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        stored = src.path
    else:
        stored = str(new_path)
    with project.db() as conn:
        conn.execute("UPDATE source_file SET path = ? WHERE id = ?", (stored, source_id))
        project.log(
            conn,
            "relink_source",
            {"source_id": source_id, "from": src.path, "to": str(new_path)},
            "user",
        )
    return project.source(source_id)
