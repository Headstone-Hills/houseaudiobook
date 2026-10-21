"""Issues in the project DB: detection runs, reconciliation, and user decisions.

The pure detectors live in analysis/issues.py. This module feeds them from the
project (sentences, alignments, speech masks), writes their findings to the
`issue` table, and records every status change in the operation log: changes a
re-run makes are logged `auto` with the previous status, so each is reversible;
the user's own decisions are logged `user` (CLAUDE.md non-negotiables).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, fields
from typing import Any

from hillaudio.analysis.boundaries import speech_mask
from hillaudio.analysis.issues import (
    KINDS,
    Existing,
    IssueDraft,
    IssueSettings,
    ReadingInfo,
    SentenceInfo,
    reconcile,
    silence_issues,
    text_issues,
)
from hillaudio.progress import CheckCancel, never_cancel
from hillaudio.store.project import Project, now_iso

SETTINGS_KEY = "issue_settings"
STATUSES = ("open", "resolved", "dismissed")


# -- settings ------------------------------------------------------------------------------


def settings(project: Project) -> IssueSettings:
    try:
        raw = json.loads(project.meta(SETTINGS_KEY))
    except KeyError:
        return IssueSettings()
    known = {f.name for f in fields(IssueSettings)}
    return IssueSettings(**{k: v for k, v in raw.items() if k in known})


def save_settings(project: Project, s: IssueSettings) -> IssueSettings:
    s.validate()
    with project.db() as conn:
        conn.execute(
            "INSERT INTO project_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (SETTINGS_KEY, json.dumps(asdict(s))),
        )
        project.log(conn, "issue_settings", asdict(s), "user")
    return s


# -- inputs from the project ----------------------------------------------------------------


def sentence_infos(project: Project) -> list[SentenceInfo]:
    with project.db() as conn:
        rows = conn.execute(
            "SELECT s.id, s.ordinal, s.text, s.page_label, p.id AS para, p.parent_id AS chapter"
            " FROM manuscript_unit s LEFT JOIN manuscript_unit p ON p.id = s.parent_id"
            " WHERE s.kind = 'sentence' ORDER BY s.ordinal"
        ).fetchall()
    return [
        SentenceInfo(
            r["id"],
            r["ordinal"],
            r["text"],
            r["page_label"],
            r["para"] if r["para"] is not None else -1,
            r["chapter"] if r["chapter"] is not None else -1,
        )
        for r in rows
    ]


def reading_infos(project: Project, run_id: int) -> list[ReadingInfo]:
    with project.db() as conn:
        rows = conn.execute(
            'SELECT u.ordinal, a.source_file_id, a.start, a."end", a.coverage, a.match_score,'
            " a.heard, a.diffs_json FROM alignment a"
            " JOIN manuscript_unit u ON u.id = a.manuscript_unit_id"
            " WHERE a.run_id = ? ORDER BY a.source_file_id, a.start",
            (run_id,),
        ).fetchall()
    return [
        ReadingInfo(
            r["ordinal"],
            r["source_file_id"],
            r["start"],
            r["end"],
            r["coverage"],
            r["match_score"],
            r["heard"],
            tuple(json.loads(r["diffs_json"])),
        )
        for r in rows
    ]


# -- a check run ------------------------------------------------------------------------------


def detect(
    project: Project,
    compare_run: int | None,
    kinds: list[str],
    check_cancel: CheckCancel = never_cancel,
) -> list[IssueDraft]:
    """Findings for the requested kinds. `compare_run`: the alignment to use."""
    from hillaudio import pipeline  # pipeline imports this module

    s = settings(project)
    sents = sentence_infos(project)
    readings = reading_infos(project, compare_run) if compare_run is not None else []
    drafts: list[IssueDraft] = []
    if {"flub", "missing_line"} & set(kinds):
        drafts += [d for d in text_issues(sents, readings, s) if d.kind in kinds]
    if "silence" in kinds:
        for src in project.sources():
            check_cancel()
            env = pipeline.envelope(project, src, check_cancel)
            # Keyed by the source's content hash: survives re-ordering and relabeling.
            drafts += silence_issues(
                src.id, src.sha256, speech_mask(env), env.fps, sents, readings, s
            )
    return drafts


def _existing(conn: sqlite3.Connection) -> list[Existing]:
    return [
        Existing(r["stable_key"], r["kind"], r["status"], r["decided_by"])
        for r in conn.execute("SELECT stable_key, kind, status, decided_by FROM issue")
    ]


def apply(
    project: Project, run_id: int, drafts: list[IssueDraft], kinds: list[str]
) -> dict[str, int]:
    """Reconcile this run's findings with the stored issues (SPEC §6.7)."""
    now = now_iso()
    counts = {"new": 0, "still_open": 0, "auto_resolved": 0, "regressed": 0}
    with project.db() as conn:
        before = {e.stable_key: e for e in _existing(conn)}
        # Issues this run leaves alone keep their old last_run_id: the "change"
        # badge only means something for issues the latest check touched.
        updates = reconcile(list(before.values()), drafts, kinds)
        for u in updates:
            counts[u.change] += 1
            prev = before.get(u.stable_key)
            d = u.draft
            if d is not None:
                values = (
                    d.kind,
                    d.severity,
                    d.source_id,
                    d.start,
                    d.end,
                    d.page_label,
                    d.unit_id,
                    d.sentence_text,
                    d.expected,
                    d.heard,
                    json.dumps(d.detail),
                )
                if prev is None:
                    conn.execute(
                        'INSERT INTO issue (kind, severity, source_file_id, start, "end",'
                        " page_label, manuscript_unit_id, sentence_text, expected, heard,"
                        " detail_json, stable_key, status, change, first_run_id, last_run_id,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 'new', ?, ?, ?, ?)",
                        (*values, u.stable_key, run_id, run_id, now, now),
                    )
                else:
                    conn.execute(
                        "UPDATE issue SET kind = ?, severity = ?, source_file_id = ?, start = ?,"
                        ' "end" = ?, page_label = ?, manuscript_unit_id = ?, sentence_text = ?,'
                        " expected = ?, heard = ?, detail_json = ?, status = ?, change = ?,"
                        " decided_by = ?, last_run_id = ?, updated_at = ? WHERE stable_key = ?",
                        (*values, u.status, u.change, u.decided_by, run_id, now, u.stable_key),
                    )
            else:
                conn.execute(
                    "UPDATE issue SET status = ?, change = ?, decided_by = ?, decided_at = ?,"
                    " last_run_id = ?, updated_at = ? WHERE stable_key = ?",
                    (u.status, u.change, u.decided_by, now, run_id, now, u.stable_key),
                )
            if prev is not None and prev.status != u.status:
                # A re-run changed a status: logged `auto`, with what it was, so undoable.
                project.log(
                    conn,
                    "issue_status",
                    {
                        "stable_key": u.stable_key,
                        "from": prev.status,
                        "to": u.status,
                        "reason": u.change,
                        "run_id": run_id,
                    },
                    "auto",
                )
        project.log(conn, "issue_check", {"run_id": run_id, "kinds": kinds, **counts}, "auto")
    return counts


# -- reading and deciding ---------------------------------------------------------------------


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["detail"] = json.loads(d.pop("detail_json"))
    return d


def list_issues(project: Project) -> list[dict[str, Any]]:
    with project.db() as conn:
        rows = conn.execute(
            "SELECT i.*, s.label AS source_label FROM issue i"
            " LEFT JOIN source_file s ON s.id = i.source_file_id"
            " ORDER BY i.source_file_id IS NULL, i.source_file_id, i.start, i.id"
        ).fetchall()
    return [_row(r) for r in rows]


def decide(
    project: Project, ids: list[int], status: str | None, note: str | None
) -> list[dict[str, Any]]:
    """The user's decision on one or more issues (bulk): status and/or note."""
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    now = now_iso()
    marks = ",".join("?" * len(ids))
    with project.db() as conn:
        rows = conn.execute(
            f"SELECT id, stable_key, status, note FROM issue WHERE id IN ({marks})",
            ids,
        ).fetchall()
        if len(rows) != len(set(ids)):
            raise KeyError("no such issue")
        for r in rows:
            change: dict[str, Any] = {}
            if status is not None and status != r["status"]:
                conn.execute(
                    "UPDATE issue SET status = ?, decided_by = 'user', decided_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (status, now, now, r["id"]),
                )
                change |= {"from": r["status"], "to": status}
            if note is not None and note != r["note"]:
                conn.execute(
                    "UPDATE issue SET note = ?, updated_at = ? WHERE id = ?", (note, now, r["id"])
                )
                change |= {"note_from": r["note"], "note_to": note}
            if change:
                project.log(
                    conn, "issue_decision", {"stable_key": r["stable_key"], **change}, "user"
                )
        out = conn.execute(
            "SELECT i.*, s.label AS source_label FROM issue i"
            " LEFT JOIN source_file s ON s.id = i.source_file_id"
            f" WHERE i.id IN ({marks}) ORDER BY i.id",
            ids,
        ).fetchall()
    return [_row(r) for r in out]


__all__ = ["KINDS", "apply", "decide", "detect", "list_issues", "save_settings", "settings"]
