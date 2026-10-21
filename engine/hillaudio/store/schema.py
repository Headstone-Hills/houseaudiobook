"""SQLite schema, applied as ordered migrations tracked by PRAGMA user_version.

Tables arrive with the milestone that first needs them (SPEC §4 lists the full
set). Migrations are append-only: never edit one that has shipped.
"""

from __future__ import annotations

import sqlite3

MIGRATIONS: list[str] = [
    # 1: project metadata, sources, and the operation log (M1)
    """
    CREATE TABLE project_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE source_file (
        id            INTEGER PRIMARY KEY,
        -- 'copy': stored under sources/ (path is project-relative).
        -- 'reference': left where it is (path is absolute).
        mode          TEXT NOT NULL CHECK (mode IN ('copy', 'reference')),
        path          TEXT NOT NULL,
        original_path TEXT NOT NULL,
        original_name TEXT NOT NULL,
        sha256        TEXT NOT NULL UNIQUE,
        size_bytes    INTEGER NOT NULL,
        format        TEXT NOT NULL,
        codec         TEXT NOT NULL,
        lossless      INTEGER NOT NULL,
        sample_rate   INTEGER NOT NULL,
        channels      INTEGER NOT NULL,
        bit_depth     INTEGER,
        bit_rate      INTEGER,
        num_frames    INTEGER NOT NULL,  -- exact, from decoding
        duration      REAL NOT NULL,     -- num_frames / sample_rate
        label         TEXT NOT NULL,
        order_index   INTEGER NOT NULL,
        imported_at   TEXT NOT NULL
    );

    CREATE TABLE operation_log (
        id           INTEGER PRIMARY KEY,
        seq          INTEGER NOT NULL UNIQUE,
        op_type      TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        origin       TEXT NOT NULL CHECK (origin IN ('auto', 'user')),
        created_at   TEXT NOT NULL,
        reverted_at  TEXT
    );
    """,
    # 2: the manuscript and its units (M2). One manuscript per project (D-023).
    """
    CREATE TABLE manuscript (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        original_path TEXT NOT NULL,
        original_name TEXT NOT NULL,
        stored_path   TEXT NOT NULL,     -- read-only copy, project-relative, under text/
        sha256        TEXT NOT NULL,
        size_bytes    INTEGER NOT NULL,
        format        TEXT NOT NULL CHECK (format IN ('txt', 'md', 'docx', 'pdf')),
        settings_json TEXT NOT NULL,     -- PageSettings used for the current mapping
        page_mode     TEXT NOT NULL,     -- the mode actually used (auto resolved)
        page_count    INTEGER NOT NULL,
        warnings_json TEXT NOT NULL,
        full_text     TEXT NOT NULL,     -- extracted text; unit offsets index into it
        imported_at   TEXT NOT NULL,
        mapped_at     TEXT NOT NULL
    );

    CREATE TABLE manuscript_unit (
        id             INTEGER PRIMARY KEY,
        kind           TEXT NOT NULL CHECK (kind IN ('chapter', 'paragraph', 'sentence')),
        ordinal        INTEGER NOT NULL,
        parent_id      INTEGER REFERENCES manuscript_unit(id),
        text           TEXT NOT NULL,
        char_start     INTEGER NOT NULL,
        char_end       INTEGER NOT NULL,
        page           INTEGER,          -- printed number; NULL if not numeric
        page_label     TEXT NOT NULL,    -- "12", "approx. 12", "iii", "front matter"
        page_end       INTEGER,
        page_end_label TEXT NOT NULL,
        page_estimated INTEGER NOT NULL,
        front_matter   INTEGER NOT NULL,
        UNIQUE (kind, ordinal)
    );
    """,
    # 3: analysis runs, transcripts, and Compare to Text results (M3).
    """
    CREATE TABLE analysis_run (
        id           INTEGER PRIMARY KEY,
        started_at   TEXT NOT NULL,
        finished_at  TEXT,
        stages_json  TEXT NOT NULL,
        options_json TEXT NOT NULL,
        status       TEXT NOT NULL CHECK (status IN ('running', 'done', 'failed', 'cancelled'))
    );

    -- The current transcript of each source (also cached in cache/transcripts/).
    CREATE TABLE transcript_word (
        id             INTEGER PRIMARY KEY,
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        idx            INTEGER NOT NULL,
        text           TEXT NOT NULL,
        start          REAL NOT NULL,
        "end"          REAL NOT NULL,
        confidence     REAL NOT NULL,
        settings_key   TEXT NOT NULL,
        UNIQUE (source_file_id, idx)
    );

    -- One row per reading of a sentence. Re-mapping the manuscript replaces its
    -- units, so readings of old units go with them (ON DELETE CASCADE).
    CREATE TABLE alignment (
        id                 INTEGER PRIMARY KEY,
        run_id             INTEGER NOT NULL REFERENCES analysis_run(id),
        manuscript_unit_id INTEGER NOT NULL REFERENCES manuscript_unit(id) ON DELETE CASCADE,
        source_file_id     INTEGER NOT NULL REFERENCES source_file(id),
        start              REAL NOT NULL,
        "end"              REAL NOT NULL,
        coverage           REAL NOT NULL,
        match_score        REAL NOT NULL,
        matches            INTEGER NOT NULL,
        subs               INTEGER NOT NULL,
        dels               INTEGER NOT NULL,
        ins                INTEGER NOT NULL,
        heard              TEXT NOT NULL,
        diffs_json         TEXT NOT NULL
    );
    CREATE INDEX alignment_unit ON alignment (run_id, manuscript_unit_id);

    -- Speech that matches no sentence: commentary, asides, noise (cleanup, M5).
    CREATE TABLE unaligned_segment (
        id             INTEGER PRIMARY KEY,
        run_id         INTEGER NOT NULL REFERENCES analysis_run(id),
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        start          REAL NOT NULL,
        "end"          REAL NOT NULL,
        text           TEXT NOT NULL
    );
    """,
    # 4: issues (M4). One row per stable key: re-runs update it in place, so the
    # user's decision (status, note) survives (SPEC §4, §6.7).
    """
    CREATE TABLE issue (
        id                 INTEGER PRIMARY KEY,
        stable_key         TEXT NOT NULL UNIQUE,
        kind               TEXT NOT NULL CHECK (kind IN ('flub', 'missing_line', 'silence')),
        severity           TEXT NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
        status             TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'dismissed')),
        decided_by         TEXT CHECK (decided_by IN ('auto', 'user')),
        decided_at         TEXT,
        note               TEXT NOT NULL DEFAULT '',
        change             TEXT NOT NULL
                           CHECK (change IN ('new', 'still_open', 'auto_resolved', 'regressed')),
        first_run_id       INTEGER NOT NULL REFERENCES analysis_run(id),
        last_run_id        INTEGER NOT NULL REFERENCES analysis_run(id),
        source_file_id     INTEGER REFERENCES source_file(id),
        start              REAL,
        "end"              REAL,
        page_label         TEXT,
        -- Re-mapping replaces units; the issue keeps its text and key (SET NULL).
        manuscript_unit_id INTEGER REFERENCES manuscript_unit(id) ON DELETE SET NULL,
        sentence_text      TEXT NOT NULL,
        expected           TEXT NOT NULL,
        heard              TEXT NOT NULL,
        detail_json        TEXT NOT NULL,
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    );
    CREATE INDEX issue_kind ON issue (kind, status);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    # WAL: background jobs write while the API reads; survives crashes (SPEC §7).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"project schema v{current} is newer than this app (v{SCHEMA_VERSION}); update the app"
        )
    for version in range(current, SCHEMA_VERSION):
        # executescript() doesn't join an open transaction, so wrap each migration
        # and its version bump in one explicitly: a crash leaves either all or none.
        conn.executescript(
            f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;"
        )
