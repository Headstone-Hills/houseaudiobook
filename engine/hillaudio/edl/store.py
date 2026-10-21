"""The EDL in the project DB: removed regions, edits, and unlimited undo/redo.

Every edit, automated or manual, is one `operation_log` entry whose payload holds
the affected `edit_region` rows before and after (CLAUDE.md: every automated
change logged, labeled `auto`, reversible). Undo writes the "before" rows back
and stamps `reverted_at`; redo writes the "after" rows and clears it. Both
survive closing the project, because the log is in the project DB (SPEC §5).

The undo stack is the edit entries not reverted; the redo stack is the reverted
ones newer than the latest entry still in effect, so making a new edit after an
undo drops the redo stack, as in any editor, without deleting history.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from typing import Any

from hillaudio.analysis.cleanup import CleanupSettings, Cut
from hillaudio.edl.model import (
    Clip,
    Region,
    Segment,
    SourceInfo,
    clips,
    default_segments,
    normalize,
    timeline_rate,
)
from hillaudio.store.project import Origin, Project, now_iso

EDIT_OPS = (
    "cleanup_apply",
    "takes_detect",
    "timeline_edit",
    "take_choice",
    "region_status",
    "region_edit",
    "cut",
    "shorten_pause",
)
SETTINGS_KEY = "cleanup_settings"
COLUMNS = (
    "id",
    "source_file_id",
    "start_sample",
    "end_sample",
    "kind",
    "status",
    "origin",
    "decided_by",
    "confidence",
    "reason",
    "text",
    "stable_key",
    "issue_id",
    "run_id",
    "detail_json",
    "created_at",
    "updated_at",
)


class EdlError(ValueError):
    """A user-facing reason an edit can't be made."""


# -- settings ----------------------------------------------------------------------------


def settings(project: Project) -> CleanupSettings:
    try:
        raw = json.loads(project.meta(SETTINGS_KEY))
    except KeyError:
        return CleanupSettings()
    known = {f.name for f in fields(CleanupSettings)}
    return CleanupSettings(**{k: v for k, v in raw.items() if k in known})


def save_settings(project: Project, s: CleanupSettings) -> CleanupSettings:
    s.validate()
    with project.db() as conn:
        conn.execute(
            "INSERT INTO project_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (SETTINGS_KEY, json.dumps(asdict(s))),
        )
        project.log(conn, "cleanup_settings", asdict(s), "user")
    return s


# -- reading -----------------------------------------------------------------------------


def _rows(
    conn: sqlite3.Connection, where: str = "1", args: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            f"SELECT * FROM edit_region WHERE {where} ORDER BY source_file_id, start_sample, id",
            args,
        )
    ]


def regions(project: Project) -> list[dict[str, Any]]:
    with project.db() as conn:
        return _rows(conn)


def model_regions(rows: Sequence[dict[str, Any]]) -> list[Region]:
    return [
        Region(r["id"], r["source_file_id"], r["start_sample"], r["end_sample"], r["status"])
        for r in rows
    ]


@dataclass(frozen=True)
class Timeline:
    rate: int
    sources: dict[int, SourceInfo]
    clips: list[Clip]
    regions: list[Region]
    segments: list[Segment]


def timeline(project: Project) -> Timeline:
    srcs = [SourceInfo(s.id, s.sample_rate, s.num_frames) for s in project.sources()]
    rate = timeline_rate(srcs)
    with project.db() as conn:
        regs = model_regions(_rows(conn))
        segs = segments(conn, srcs)
    return Timeline(rate, {s.id: s for s in srcs}, clips(srcs, regs, rate, segs), regs, segs)


# -- the edit primitive ------------------------------------------------------------------


TABLES = ("edit_region", "take_group", "take", "track", "edl_clip", "marker")
# A change is (table, row before, row after); None means "no such row". The
# timeline order is one value: ("edl_segment", segments before, segments after).
Change = tuple[str, Any, Any]


def _write(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    assert table in TABLES
    cols = list(row)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        [row[c] for c in cols],
    )


def _apply(conn: sqlite3.Connection, table: str, key: Any, value: Any) -> None:
    if table == "edl_segment":
        _write_segments(conn, value)
    elif value is None:
        assert table in TABLES
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (key,))
    else:
        _write(conn, table, value)


def _write_changes(conn: sqlite3.Connection, changes: Sequence[Change]) -> list[dict[str, Any]]:
    """Write changes now; return their log entries. Several batches can be
    written in turn (each computed against the last) and logged as one entry."""
    entries = []
    for table, before, after in changes:
        if before == after:
            continue
        key = None if table == "edl_segment" else (after or before)["id"]
        _apply(conn, table, key, after)
        entries.append({"table": table, "id": key, "before": before, "after": after})
    return entries


def _log_entries(
    project: Project,
    conn: sqlite3.Connection,
    op_type: str,
    entries: list[dict[str, Any]],
    origin: Origin,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> int | None:
    if not entries:
        return None
    return project.log(
        conn, op_type, {"summary": summary, "changes": entries, **(extra or {})}, origin
    )


def _commit(
    project: Project,
    conn: sqlite3.Connection,
    op_type: str,
    changes: Sequence[Change],
    origin: Origin,
    summary: str,
    extra: dict[str, Any] | None = None,
) -> int | None:
    """Write changes and log them as one undoable entry. None: nothing changed."""
    entries = _write_changes(conn, changes)
    return _log_entries(project, conn, op_type, entries, origin, summary, extra)


def _r(before: dict[str, Any] | None, after: dict[str, Any] | None) -> Change:
    return ("edit_region", before, after)


def _next_id(conn: sqlite3.Connection, table: str = "edit_region") -> int:
    # Ids are never reused, even after an undo deleted the newest row: a redo
    # writes the row back with its id, and later entries may refer to it.
    assert table in TABLES
    used = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
    logged = 0
    for (payload,) in conn.execute(
        "SELECT payload_json FROM operation_log WHERE op_type IN ({})".format(
            ",".join("?" * len(EDIT_OPS))
        ),
        EDIT_OPS,
    ):
        for c in json.loads(payload).get("changes", []):
            if c.get("table", "edit_region") == table:
                logged = max(logged, int(c["id"]))
    return int(max(used, logged)) + 1


# -- timeline order ------------------------------------------------------------------------


SEG_ATTRS = ("gain_db", "fade_in", "fade_out", "muted", "keep")


def _seg_dict(s: Segment) -> dict[str, Any]:
    return {
        "source_file_id": s.source_id,
        "source_in": s.source_in,
        "source_out": s.source_out,
        "gain_db": s.gain_db,
        "fade_in": s.fade_in,
        "fade_out": s.fade_out,
        "muted": int(s.muted),
        "keep": int(s.keep),
    }


def _seg(d: Any) -> Segment:
    # Entries logged before M7 are [source, in, out].
    if isinstance(d, list | tuple):
        return Segment(int(d[0]), int(d[1]), int(d[2]))
    return Segment(
        d["source_file_id"],
        d["source_in"],
        d["source_out"],
        float(d.get("gain_db", 0.0)),
        int(d.get("fade_in", 0)),
        int(d.get("fade_out", 0)),
        bool(d.get("muted", 0)),
        bool(d.get("keep", 0)),
    )


def _read_segments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Stored order; [] means the default order."""
    return [
        _seg_dict(_seg(dict(r)))
        for r in conn.execute("SELECT * FROM edl_segment ORDER BY position")
    ]


def _write_segments(conn: sqlite3.Connection, segs: Sequence[Any]) -> None:
    conn.execute("DELETE FROM edl_segment")
    conn.executemany(
        "INSERT INTO edl_segment (position, source_file_id, source_in, source_out, gain_db,"
        " fade_in, fade_out, muted, keep) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                i,
                s.source_id,
                s.source_in,
                s.source_out,
                s.gain_db,
                s.fade_in,
                s.fade_out,
                int(s.muted),
                int(s.keep),
            )
            for i, s in enumerate(_seg(x) for x in segs)
        ],
    )


def segments(conn: sqlite3.Connection, sources: Sequence[SourceInfo]) -> list[Segment]:
    rows = _read_segments(conn)
    if not rows:
        return default_segments(sources)
    known = {s.id for s in sources}
    segs = [_seg(r) for r in rows if r["source_file_id"] in known]
    # A recording imported after something was moved goes at the end.
    placed = {s.source_id for s in segs}
    segs += [Segment(s.id, 0, s.num_frames) for s in sources if s.id not in placed]
    return segs


def segments_change(
    conn: sqlite3.Connection, sources: Sequence[SourceInfo], new: Sequence[Segment]
) -> Change:
    """The change that sets the timeline order (stored empty when it's the default)."""
    before = _read_segments(conn)
    norm = normalize(new)
    after = [] if norm == default_segments(sources) else [_seg_dict(s) for s in norm]
    return ("edl_segment", before, after)


def _new_row(
    rid: int,
    source_id: int,
    start: int,
    end: int,
    kind: str,
    status: str,
    origin: str,
    reason: str,
    text: str,
    *,
    confidence: float | None = None,
    stable_key: str | None = None,
    issue_id: int | None = None,
    run_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_iso()
    return {
        "id": rid,
        "source_file_id": source_id,
        "start_sample": start,
        "end_sample": end,
        "kind": kind,
        "status": status,
        "origin": origin,
        "decided_by": origin,
        "confidence": confidence,
        "reason": reason,
        "text": text,
        "stable_key": stable_key,
        "issue_id": issue_id,
        "run_id": run_id,
        "detail_json": json.dumps(detail or {}),
        "created_at": now,
        "updated_at": now,
    }


# -- automated cleanup -------------------------------------------------------------------


def apply_cleanup(
    project: Project, run_id: int, found: Sequence[Cut], s: CleanupSettings
) -> dict[str, int]:
    """Turn cleanup candidates into regions, as one undoable `auto` entry.

    New candidates are removed (confidence at or above the threshold) or
    suggested. A candidate seen before keeps whatever status it has: if the user
    restored it, it stays restored (stable keys, CLAUDE.md). Its bounds are
    refreshed only while no one has touched it. Candidates not found again are
    left alone: silently putting audio back would be a hidden change.
    """
    rates = {src.id: src.sample_rate for src in project.sources()}
    counts = {"removed": 0, "suggested": 0, "kept": 0}
    with project.db() as conn:
        by_key = {r["stable_key"]: r for r in _rows(conn, "stable_key IS NOT NULL")}
        nid = _next_id(conn)
        changes: list[Change] = []
        for c in found:
            sr = rates[c.source_id]
            a, b = round(c.start * sr), round(c.end * sr)
            if b <= a:
                continue  # cuts nothing; detection shouldn't send one, but never store it
            old = by_key.get(c.stable_key)
            if old is not None:
                counts["kept"] += 1
                moved = (old["start_sample"], old["end_sample"]) != (a, b)
                if old["decided_by"] == "auto" and moved:
                    changes.append(
                        _r(old, old | {"start_sample": a, "end_sample": b, "run_id": run_id})
                    )
                continue
            status = "removed" if c.confidence >= s.auto_apply else "suggested"
            counts[status] += 1
            row = _new_row(
                nid,
                c.source_id,
                a,
                b,
                c.kind,
                status,
                "auto",
                c.reason,
                c.text,
                confidence=c.confidence,
                stable_key=c.stable_key,
                run_id=run_id,
                detail={**c.detail, "sentence_ordinal": c.sentence_ordinal},
            )
            nid += 1
            changes.append(_r(None, row))
        _commit(
            project,
            conn,
            "cleanup_apply",
            changes,
            "auto",
            f"Cleanup: {counts['removed']} removed, {counts['suggested']} suggested",
            {"run_id": run_id},
        )
    return counts


# -- user edits --------------------------------------------------------------------------


def _get(conn: sqlite3.Connection, rid: int) -> dict[str, Any]:
    rows = _rows(conn, "id = ?", (rid,))
    if not rows:
        raise KeyError(rid)
    return rows[0]


def set_status(project: Project, ids: Sequence[int], status: str) -> int | None:
    """Restore regions, or (re)apply them: suggested or restored -> removed."""
    if status not in ("removed", "restored"):
        raise EdlError("status must be removed or restored")
    with project.db() as conn:
        rows = [_get(conn, i) for i in ids]
        now = now_iso()
        changes = [
            _r(r, r | {"status": status, "decided_by": "user", "updated_at": now})
            for r in rows
            if r["status"] != status
        ]
        verb = "Restore" if status == "restored" else "Remove"
        label = (
            f"{verb} {rows[0]['kind'].replace('_', ' ')}"
            if len(rows) == 1
            else f"{verb} {len(rows)}"
        )
        return _commit(project, conn, "region_status", changes, "user", label)


def edit_bounds(
    project: Project, rid: int, start: int | None = None, end: int | None = None
) -> int | None:
    """Move a region's edges (ripple trim of the clip edges next to it).

    Dragging the edge of the clip before the region outward moves the region's
    start later; at the region's end it re-exposes all of it (SPEC §5: "dragging an
    edge outward re-exposes removed audio").
    """
    with project.db() as conn:
        r = _get(conn, rid)
        n = project.source(r["source_file_id"]).num_frames
        a = r["start_sample"] if start is None else start
        b = r["end_sample"] if end is None else end
        a, b = max(0, min(n, a)), max(0, min(n, b))
        if b < a:
            raise EdlError("a region's end can't be before its start")
        after = r | {
            "start_sample": a,
            "end_sample": b,
            "decided_by": "user",
            "updated_at": now_iso(),
        }
        return _commit(project, conn, "region_edit", [_r(r, after)], "user", "Trim edit")


def roll(project: Project, rid: int, delta: int) -> int | None:
    """Rolling edit: move the boundary shared by the clips either side of a region
    without changing the timeline's length, i.e. shift the region by delta."""
    with project.db() as conn:
        r = _get(conn, rid)
        n = project.source(r["source_file_id"]).num_frames
        delta = max(-r["start_sample"], min(n - r["end_sample"], delta))
        after = r | {
            "start_sample": r["start_sample"] + delta,
            "end_sample": r["end_sample"] + delta,
            "decided_by": "user",
            "updated_at": now_iso(),
        }
        return _commit(project, conn, "region_edit", [_r(r, after)], "user", "Rolling edit")


def cut(project: Project, source_id: int, start: int, end: int, note: str = "") -> int:
    """The user's own cut: remove [start, end) of a source."""
    src = project.source(source_id)
    start, end = max(0, start), min(src.num_frames, end)
    if end <= start:
        raise EdlError("nothing to cut: the end must be after the start")
    with project.db() as conn:
        rid = _next_id(conn)
        row = _new_row(rid, source_id, start, end, "cut", "removed", "user", note or "cut", "")
        _commit(project, conn, "cut", [_r(None, row)], "user", "Cut")
    return rid


def shorten_pause(project: Project, issue: dict[str, Any], keep_s: float | None = None) -> int:
    """Cut the middle of a long pause, leaving keep_s of it (SPEC §6.3.6: "offer
    one-click shorten to N seconds"). Doing it again for the same issue changes
    the same region."""
    if issue["kind"] != "silence" or issue["source_file_id"] is None:
        raise EdlError("only long pauses can be shortened")
    keep = float(keep_s if keep_s is not None else issue["detail"].get("suggested_s", 0.6))
    if keep < 0.1:
        raise EdlError("keep at least 0.1 s of the pause")
    src = project.source(issue["source_file_id"])
    sr = src.sample_rate
    a, b = round(issue["start"] * sr), round(issue["end"] * sr)
    if (b - a) / sr <= keep:
        raise EdlError(f"the pause is already {(b - a) / sr:.1f} s or shorter")
    half = round(keep * sr / 2)
    start, end = a + half, b - half
    key = f"pause:{issue['stable_key']}"
    with project.db() as conn:
        old = _rows(conn, "stable_key = ?", (key,))
        reason = f"shortened a {(b - a) / sr:.1f} s pause to {keep:.1f} s"
        if old:
            r = old[0]
            after = r | {
                "start_sample": start,
                "end_sample": end,
                "status": "removed",
                "reason": reason,
                "decided_by": "user",
                "updated_at": now_iso(),
            }
            _commit(project, conn, "shorten_pause", [_r(r, after)], "user", "Shorten pause")
            return int(r["id"])
        rid = _next_id(conn)
        row = _new_row(
            rid,
            src.id,
            start,
            end,
            "pause",
            "removed",
            "user",
            reason,
            "",
            stable_key=key,
            issue_id=issue["id"],
            detail={"keep_s": keep},
        )
        _commit(project, conn, "shorten_pause", [_r(None, row)], "user", "Shorten pause")
        return rid


# -- undo / redo -------------------------------------------------------------------------


def _edit_ops(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM operation_log WHERE op_type IN ({}) ORDER BY seq".format(
            ",".join("?" * len(EDIT_OPS))
        ),
        EDIT_OPS,
    ).fetchall()


def _stacks(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    ops = _edit_ops(conn)
    done = [o for o in ops if o["reverted_at"] is None]
    last = done[-1]["seq"] if done else 0
    redo = [o for o in ops if o["reverted_at"] is not None and o["seq"] > last]
    return done, redo


def undo(project: Project) -> dict[str, Any] | None:
    with project.db() as conn:
        done, _ = _stacks(conn)
        if not done:
            return None
        op = done[-1]
        payload = json.loads(op["payload_json"])
        for c in reversed(payload["changes"]):
            _apply(conn, c.get("table", "edit_region"), c["id"], c["before"])
        conn.execute("UPDATE operation_log SET reverted_at = ? WHERE id = ?", (now_iso(), op["id"]))
        return {"seq": op["seq"], "summary": payload["summary"]}


def redo(project: Project) -> dict[str, Any] | None:
    with project.db() as conn:
        _, stack = _stacks(conn)
        if not stack:
            return None
        op = stack[0]
        payload = json.loads(op["payload_json"])
        for c in payload["changes"]:
            _apply(conn, c.get("table", "edit_region"), c["id"], c["after"])
        conn.execute("UPDATE operation_log SET reverted_at = NULL WHERE id = ?", (op["id"],))
        return {"seq": op["seq"], "summary": payload["summary"]}


def history(project: Project) -> dict[str, Any]:
    with project.db() as conn:
        ops = _edit_ops(conn)
        done, redo_stack = _stacks(conn)
    redoable = {o["seq"] for o in redo_stack}
    return {
        "entries": [
            {
                "seq": o["seq"],
                "op_type": o["op_type"],
                "origin": o["origin"],
                "summary": json.loads(o["payload_json"])["summary"],
                "created_at": o["created_at"],
                # done; undone (can be redone); undone and superseded by a newer edit
                "state": "done"
                if o["reverted_at"] is None
                else ("undone" if o["seq"] in redoable else "dropped"),
            }
            for o in ops
        ],
        "undo": json.loads(done[-1]["payload_json"])["summary"] if done else None,
        "redo": json.loads(redo_stack[0]["payload_json"])["summary"] if redo_stack else None,
    }
