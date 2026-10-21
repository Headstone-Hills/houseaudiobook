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
    # 5: the narration EDL as removed regions (M5, D-038). Positions are integer
    # samples at the source's rate. Every change is an operation_log entry with
    # the rows before and after, which undo/redo replays.
    """
    CREATE TABLE edit_region (
        id             INTEGER PRIMARY KEY,
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        start_sample   INTEGER NOT NULL,
        end_sample     INTEGER NOT NULL,
        kind           TEXT NOT NULL
                       CHECK (kind IN ('stutter', 'false_start', 'commentary', 'pause', 'cut')),
        status         TEXT NOT NULL CHECK (status IN ('removed', 'restored', 'suggested')),
        origin         TEXT NOT NULL CHECK (origin IN ('auto', 'user')),
        decided_by     TEXT NOT NULL CHECK (decided_by IN ('auto', 'user')),
        confidence     REAL,
        reason         TEXT NOT NULL,
        text           TEXT NOT NULL,
        stable_key     TEXT UNIQUE,
        issue_id       INTEGER REFERENCES issue(id) ON DELETE SET NULL,
        run_id         INTEGER REFERENCES analysis_run(id),
        detail_json    TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        CHECK (end_sample >= start_sample)
    );
    CREATE INDEX edit_region_source ON edit_region (source_file_id, start_sample);
    """,
    # 6: takes and timeline order (M6, D-043/D-045). edit_region gains the kind
    # 'take' (SQLite can't alter a CHECK, so the table is rebuilt, rows kept).
    """
    CREATE TABLE edit_region_v6 (
        id             INTEGER PRIMARY KEY,
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        start_sample   INTEGER NOT NULL,
        end_sample     INTEGER NOT NULL,
        kind           TEXT NOT NULL CHECK (
                           kind IN ('stutter', 'false_start', 'commentary', 'pause', 'cut', 'take')
                       ),
        status         TEXT NOT NULL CHECK (status IN ('removed', 'restored', 'suggested')),
        origin         TEXT NOT NULL CHECK (origin IN ('auto', 'user')),
        decided_by     TEXT NOT NULL CHECK (decided_by IN ('auto', 'user')),
        confidence     REAL,
        reason         TEXT NOT NULL,
        text           TEXT NOT NULL,
        stable_key     TEXT UNIQUE,
        issue_id       INTEGER REFERENCES issue(id) ON DELETE SET NULL,
        run_id         INTEGER REFERENCES analysis_run(id),
        detail_json    TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL,
        CHECK (end_sample >= start_sample)
    );
    INSERT INTO edit_region_v6 SELECT * FROM edit_region;
    DROP TABLE edit_region;
    ALTER TABLE edit_region_v6 RENAME TO edit_region;
    CREATE INDEX edit_region_source ON edit_region (source_file_id, start_sample);

    -- The timeline's order: segments of recordings. Empty means the default,
    -- each recording once in order; rows exist only once something was moved.
    CREATE TABLE edl_segment (
        id             INTEGER PRIMARY KEY,
        position       INTEGER NOT NULL UNIQUE,
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        source_in      INTEGER NOT NULL,
        source_out     INTEGER NOT NULL,
        CHECK (source_out >= source_in)
    );

    CREATE TABLE take_group (
        id                 INTEGER PRIMARY KEY,
        stable_key         TEXT NOT NULL UNIQUE,
        manuscript_unit_id INTEGER REFERENCES manuscript_unit(id) ON DELETE SET NULL,
        sentence_text      TEXT NOT NULL,
        page_label         TEXT,
        state              TEXT NOT NULL CHECK (state IN ('suggested', 'confirmed')),
        confident          INTEGER NOT NULL,
        reason             TEXT NOT NULL,
        previous_take_id   INTEGER,  -- the choice before the current one
        run_id             INTEGER REFERENCES analysis_run(id),
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    );

    CREATE TABLE take (
        id             INTEGER PRIMARY KEY,
        take_group_id  INTEGER NOT NULL REFERENCES take_group(id),
        stable_key     TEXT NOT NULL UNIQUE,
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        start          REAL NOT NULL,
        "end"          REAL NOT NULL,
        span_end       REAL NOT NULL,
        coverage       REAL NOT NULL,
        score          REAL NOT NULL,
        hard_errors    INTEGER NOT NULL,
        soft_errors    INTEGER NOT NULL,
        extras         INTEGER NOT NULL,
        loudness_db    REAL,
        rank           INTEGER NOT NULL,
        status         TEXT NOT NULL CHECK (status IN ('preferred', 'alternate', 'rejected')),
        decided_by     TEXT NOT NULL CHECK (decided_by IN ('auto', 'user')),
        heard          TEXT NOT NULL,
        diffs_json     TEXT NOT NULL
    );
    CREATE INDEX take_group_idx ON take (take_group_id, rank);
    """,
    # 7: the multitrack timeline (M7, D-046). Narration segments gain clip
    # attributes; other tracks hold free clips (edl_clip, SPEC §4); markers are
    # anchored to a recording position so they follow edits.
    """
    ALTER TABLE edl_segment ADD COLUMN gain_db REAL NOT NULL DEFAULT 0;
    ALTER TABLE edl_segment ADD COLUMN fade_in INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE edl_segment ADD COLUMN fade_out INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE edl_segment ADD COLUMN muted INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE edl_segment ADD COLUMN keep INTEGER NOT NULL DEFAULT 0;

    CREATE TABLE track (
        id       INTEGER PRIMARY KEY,
        name     TEXT NOT NULL,
        kind     TEXT NOT NULL CHECK (kind IN ('narration', 'audio')),
        position INTEGER NOT NULL,
        gain_db  REAL NOT NULL DEFAULT 0,
        pan      REAL NOT NULL DEFAULT 0 CHECK (pan BETWEEN -1 AND 1),
        mute     INTEGER NOT NULL DEFAULT 0,
        solo     INTEGER NOT NULL DEFAULT 0
    );
    INSERT INTO track (id, name, kind, position) VALUES (1, 'Narration', 'narration', 0);

    CREATE TABLE edl_clip (
        id             INTEGER PRIMARY KEY,
        track_id       INTEGER NOT NULL REFERENCES track(id),
        source_file_id INTEGER NOT NULL REFERENCES source_file(id),
        source_in      INTEGER NOT NULL,
        source_out     INTEGER NOT NULL,
        timeline_start INTEGER NOT NULL,
        gain_db        REAL NOT NULL DEFAULT 0,
        fade_in        INTEGER NOT NULL DEFAULT 0,
        fade_out       INTEGER NOT NULL DEFAULT 0,
        muted          INTEGER NOT NULL DEFAULT 0,
        CHECK (source_out > source_in AND timeline_start >= 0)
    );

    CREATE TABLE marker (
        id             INTEGER PRIMARY KEY,
        label          TEXT NOT NULL,
        color          TEXT NOT NULL DEFAULT 'blue',
        source_file_id INTEGER REFERENCES source_file(id),
        sample         INTEGER,
        created_at     TEXT NOT NULL
    );
    """,
    # 8: pickups (D-062). A pickup recording holds re-recorded lines: it isn't laid
    # out on the timeline; only the takes chosen from it play, where their lines go.
    """
    ALTER TABLE source_file ADD COLUMN role TEXT NOT NULL DEFAULT 'narration'
        CHECK (role IN ('narration', 'pickup'));
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
