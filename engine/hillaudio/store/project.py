"""A project is a `<Name>.hillproj/` folder (SPEC §4):

project.db   SQLite: metadata, sources, operation log, ...
sources/     copied source audio (read-only; never modified)
text/        manuscripts (M2)
cache/       regenerable: peaks, transcripts, ...
exports/
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from hillaudio import functions
from hillaudio.store import schema

SUFFIX = ".hillproj"
DB_NAME = "project.db"
SUBDIRS = ("sources", "text", "cache", "exports")
MAX_NAME = 100
Origin = Literal["auto", "user"]


class ProjectError(Exception):
    """A user-facing problem creating or opening a project."""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Source:
    id: int
    mode: Literal["copy", "reference"]
    path: str
    original_path: str
    original_name: str
    sha256: str
    size_bytes: int
    format: str
    codec: str
    lossless: bool
    sample_rate: int
    channels: int
    bit_depth: int | None
    bit_rate: int | None
    num_frames: int
    duration: float
    label: str
    order_index: int
    imported_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Source:
        d = dict(row)
        d["lossless"] = bool(d["lossless"])
        return cls(**d)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ProjectError("project name is required")
    if len(name) > MAX_NAME:
        raise ProjectError(f"project name is longer than {MAX_NAME} characters")
    if any(c in name for c in "/\\:\0") or name.startswith("."):
        raise ProjectError("project name can't contain / \\ : or start with '.'")
    return name


class Project:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.db_path = root / DB_NAME

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def create(
        cls, location: Path, name: str, selected: Sequence[str] = functions.DEFAULT
    ) -> Project:
        name = validate_name(name)
        location = location.expanduser()
        if not location.is_dir():
            raise ProjectError(f"location does not exist: {location}")
        root = location / f"{name}{SUFFIX}"
        if root.exists():
            raise ProjectError(f"{root} already exists")
        try:
            chosen = functions.with_prerequisites(selected)
        except ValueError as e:
            raise ProjectError(str(e)) from e

        root.mkdir()
        for sub in SUBDIRS:
            (root / sub).mkdir()
        project = cls(root)
        with project.db() as conn:
            conn.executemany(
                "INSERT INTO project_meta (key, value) VALUES (?, ?)",
                [
                    ("name", name),
                    ("created_at", now_iso()),
                    ("functions", json.dumps(chosen)),
                ],
            )
            project.log(conn, "create_project", {"name": name, "functions": chosen}, "user")
        return project

    @classmethod
    def open(cls, path: Path) -> Project:
        path = path.expanduser()
        if path.name == DB_NAME:
            path = path.parent
        if not (path / DB_NAME).is_file():
            raise ProjectError(f"not a HillAudiobook project: {path}")
        project = cls(path)
        for sub in SUBDIRS:
            # Regenerable folders may have been cleaned out by the user; recreate.
            (path / sub).mkdir(exist_ok=True)
        with project.db():
            pass  # applies any pending migrations
        return project

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        """A connection with one transaction: committed on success, rolled back on error."""
        conn = schema.connect(str(self.db_path))
        try:
            schema.migrate(conn)
            with conn:
                yield conn
        finally:
            conn.close()

    # -- metadata --------------------------------------------------------------

    def meta(self, key: str) -> str:
        with self.db() as conn:
            row = conn.execute("SELECT value FROM project_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return str(row["value"])

    @property
    def name(self) -> str:
        return self.meta("name")

    @property
    def functions(self) -> list[str]:
        result: list[str] = json.loads(self.meta("functions"))
        return result

    # -- folders -----------------------------------------------------------------

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    # -- sources -------------------------------------------------------------------

    def sources(self) -> list[Source]:
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM source_file ORDER BY order_index, id").fetchall()
        return [Source.from_row(r) for r in rows]

    def source(self, source_id: int) -> Source:
        with self.db() as conn:
            row = conn.execute("SELECT * FROM source_file WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise KeyError(source_id)
        return Source.from_row(row)

    def resolve(self, src: Source) -> Path:
        return self.root / src.path if src.mode == "copy" else Path(src.path)

    # -- operation log -----------------------------------------------------------

    @staticmethod
    def log(conn: sqlite3.Connection, op_type: str, payload: dict[str, Any], origin: Origin) -> int:
        """Append to the operation log inside the caller's transaction."""
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM operation_log").fetchone()[0]
        conn.execute(
            "INSERT INTO operation_log (seq, op_type, payload_json, origin, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (seq, op_type, json.dumps(payload, sort_keys=True), origin, now_iso()),
        )
        return int(seq)

    def operations(self) -> list[dict[str, Any]]:
        with self.db() as conn:
            rows = conn.execute("SELECT * FROM operation_log ORDER BY seq").fetchall()
        return [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]
