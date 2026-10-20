from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hillaudio import functions
from hillaudio.store import schema
from hillaudio.store.project import Project, ProjectError


def test_create_layout_and_metadata(tmp_path: Path) -> None:
    p = Project.create(tmp_path, "My Book", ["flubs"])
    assert p.root == tmp_path / "My Book.hillproj"
    assert sorted(x.name for x in p.root.iterdir()) == [
        "cache",
        "exports",
        "project.db",
        "sources",
        "text",
    ]
    assert p.name == "My Book"
    # Prerequisites are closed over: flubs -> compare_text -> transcribe.
    assert p.functions == ["transcribe", "compare_text", "flubs"]
    ops = p.operations()
    assert [(o["op_type"], o["origin"]) for o in ops] == [("create_project", "user")]


def test_create_defaults_to_all_functions(tmp_path: Path) -> None:
    assert Project.create(tmp_path, "B").functions == list(functions.DEFAULT)


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", ".hidden", "x" * 101])
def test_create_rejects_bad_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ProjectError):
        Project.create(tmp_path, name)


def test_create_errors(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="does not exist"):
        Project.create(tmp_path / "nope", "B")
    Project.create(tmp_path, "B")
    with pytest.raises(ProjectError, match="already exists"):
        Project.create(tmp_path, "B")
    with pytest.raises(ProjectError, match="unknown function"):
        Project.create(tmp_path, "C", ["teleport"])


def test_open_roundtrip_and_recreates_regenerable_dirs(tmp_path: Path) -> None:
    p = Project.create(tmp_path, "B")
    (p.root / "cache").rmdir()
    q = Project.open(p.root)
    assert q.name == "B" and (q.root / "cache").is_dir()
    assert Project.open(p.root / "project.db").root == p.root


def test_open_rejects_non_project(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match="not a HillAudiobook project"):
        Project.open(tmp_path)


def test_schema_version_and_newer_db_refused(tmp_path: Path) -> None:
    p = Project.create(tmp_path, "B")
    conn = sqlite3.connect(p.db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION
    conn.execute(f"PRAGMA user_version = {schema.SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(RuntimeError, match="newer than this app"):
        Project.open(p.root)


def test_with_prerequisites() -> None:
    assert functions.with_prerequisites(["takes", "silences"]) == [
        "transcribe",
        "compare_text",
        "silences",
        "takes",
    ]
    assert functions.with_prerequisites([]) == []
