"""Takes in the project DB: groups, choices, and what a choice does to the EDL.

Choosing the preferred take of a line (SPEC §6.5: "Choosing a preferred take
updates the EDL; the previous choice remains one click away"):
- every other take of the line is removed from the timeline (a `take` region
  from its onset to the next speech), and the chosen one is restored;
- if the chosen take is in a different recording from the line's first reading
  (a pickup file), it is moved to play where that first reading was (D-045);
  the previous choice goes back home first.
All of it is one undoable operation-log entry (D-039). Detection applies its
suggestions the same way, as one `auto` entry; re-running keeps the user's
choices (stable keys).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from hillaudio.analysis.takes import TakeGroup
from hillaudio.edl import store
from hillaudio.edl.model import SourceInfo, place
from hillaudio.store.project import Origin, Project, now_iso

STATUSES = ("preferred", "alternate", "rejected")


class TakeError(ValueError):
    """A user-facing reason a take action can't be done."""


def _sources(project: Project) -> tuple[list[SourceInfo], dict[int, Any]]:
    srcs = project.sources()
    return [store.infos(s) for s in srcs], {s.id: s for s in srcs}


def _group_rows(
    conn: sqlite3.Connection, where: str = "1", args: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    return [
        dict(r) for r in conn.execute(f"SELECT * FROM take_group WHERE {where} ORDER BY id", args)
    ]


def _take_rows(conn: sqlite3.Connection, group_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM take WHERE take_group_id = ? ORDER BY rank", (group_id,)
        )
    ]


def _anchor(takes: Sequence[dict[str, Any]], order: dict[int, int]) -> dict[str, Any]:
    """The line's first reading in recording order: where the line belongs."""
    return min(takes, key=lambda t: (order.get(t["source_file_id"], 0), t["start"]))


def _choice_changes(
    conn: sqlite3.Connection,
    project: Project,
    group: dict[str, Any],
    takes: list[dict[str, Any]],
    chosen: dict[str, Any],
    origin: Origin,
    run_id: int | None,
    update: dict[str, Any] | None = None,
    compare_run: int | None = None,
) -> list[store.Change]:
    infos, by_id = _sources(project)
    order = {s.id: s.order_index for s in by_id.values()}
    now = now_iso()
    changes: list[store.Change] = []

    def samples(t: dict[str, Any]) -> tuple[int, int]:
        sr = by_id[t["source_file_id"]].sample_rate
        return round(t["start"] * sr), round(t["span_end"] * sr)

    def pickup(t: dict[str, Any]) -> bool:
        return bool(by_id[t["source_file_id"]].role == "pickup")

    # Timeline order: the previous choice goes home (a pickup has none: it leaves
    # the timeline), and the new one moves into place if it's in another
    # recording than the line's first reading in the narration. A line read only
    # in pickups goes before the next line that was read (D-062).
    segs = store.segments(conn, infos)
    old = next((t for t in takes if t["status"] == "preferred"), None)
    if old is not None:
        a, b = samples(old)
        segs = place(segs, old["source_file_id"], a, b, homeless=pickup(old))
    narration = [t for t in takes if not pickup(t)]
    a, b = samples(chosen)
    if narration:
        anchor = _anchor(narration, order)
        if chosen["source_file_id"] != anchor["source_file_id"]:
            before = (anchor["source_file_id"], samples(anchor)[0])
            segs = place(segs, chosen["source_file_id"], a, b, before=before)
    else:
        where = _line_position(conn, project, group, by_id, compare_run)
        segs = place(segs, chosen["source_file_id"], a, b, before=where, at_end=where is None)
    changes.append(store.segments_change(conn, infos, segs))

    # Cuts: every take but the chosen one is off the timeline.
    by_key = {r["stable_key"]: r for r in store._rows(conn, "kind = 'take'")}
    nid = store._next_id(conn)
    label = by_id[chosen["source_file_id"]].label
    for t in takes:
        want = "restored" if t["id"] == chosen["id"] else "removed"
        key = f"take:{t['stable_key']}"
        a, b = samples(t)
        reason = (
            "the preferred take" if want == "restored" else f"another take is preferred ({label})"
        )
        r = by_key.get(key)
        if r is None:
            row = store._new_row(
                nid,
                t["source_file_id"],
                a,
                b,
                "take",
                want,
                origin,
                reason,
                t["heard"],
                stable_key=key,
                run_id=run_id,
                detail={"take_id": t["id"], "group_id": group["id"]},
            )
            nid += 1
            changes.append(store._r(None, row))
        elif (r["status"], r["start_sample"], r["end_sample"]) != (want, a, b):
            changes.append(
                store._r(
                    r,
                    r
                    | {
                        "status": want,
                        "start_sample": a,
                        "end_sample": b,
                        "reason": reason,
                        "decided_by": origin,
                        "updated_at": now,
                    },
                )
            )

    # The takes and the group.
    for t in takes:
        if t["id"] == chosen["id"]:
            new = t | {"status": "preferred", "decided_by": origin}
        elif t["status"] == "preferred":
            new = t | {"status": "alternate", "decided_by": origin}
        else:
            continue
        if new != t:
            changes.append(("take", t, new))
    g = (
        group
        | {
            "previous_take_id": old["id"]
            if old is not None and old["id"] != chosen["id"]
            else group["previous_take_id"],
            "state": "confirmed" if origin == "user" else group["state"],
            "updated_at": now,
        }
        | (update or {})
    )
    changes.append(("take_group", group, g))
    return changes


def _line_position(
    conn: sqlite3.Connection,
    project: Project,
    group: dict[str, Any],
    by_id: dict[int, Any],
    compare_run: int | None,
) -> tuple[int, int] | None:
    """Where a line no narration recording has goes: just before the next line
    read in the narration, as (recording, sample); None for the end.
    `compare_run`: the alignment to go by (default: the latest finished)."""
    from hillaudio import pipeline  # pipeline imports this module

    run = compare_run if compare_run is not None else pipeline.latest_run(project)
    if run is None or group["manuscript_unit_id"] is None:
        return None
    rows = conn.execute(
        "SELECT a.source_file_id, a.start FROM alignment a"
        " JOIN manuscript_unit u ON u.id = a.manuscript_unit_id"
        " WHERE a.run_id = ? AND a.coverage > 0.6 AND u.kind = 'sentence'"
        " AND u.ordinal > (SELECT ordinal FROM manuscript_unit WHERE id = ?)"
        " ORDER BY u.ordinal, a.start",
        (run, group["manuscript_unit_id"]),
    ).fetchall()
    for r in rows:
        src = by_id.get(r["source_file_id"])
        if src is None or src.role == "pickup":
            continue
        # Nothing read before it in that recording (its lead-in is room tone):
        # before the whole recording, so the lead-in stays with what follows it.
        earlier = conn.execute(
            "SELECT 1 FROM alignment WHERE run_id = ? AND source_file_id = ? AND start < ?",
            (run, src.id, r["start"]),
        ).fetchone()
        return (src.id, round(r["start"] * src.sample_rate) if earlier else 0)
    return None


# -- detection ---------------------------------------------------------------------------


def apply_detection(
    project: Project, run_id: int, found: Sequence[TakeGroup], compare_run: int | None = None
) -> dict[str, int]:
    """Store new groups and apply their suggestions, as one `auto` entry.

    A group seen before keeps its takes' statuses and the user's choice; takes
    new to it join as alternates (cut from the timeline)."""
    counts = {"groups": 0, "kept": 0, "takes_added": 0}
    now = now_iso()
    with project.db() as conn:
        existing = {g["stable_key"]: g for g in _group_rows(conn)}
        entries: list[dict[str, Any]] = []
        for grp in found:
            old = existing.get(grp.stable_key)
            if old is not None:
                counts["kept"] += 1
                takes = _take_rows(conn, old["id"])
                known = {t["stable_key"] for t in takes}
                added = [t for t in grp.takes if t.stable_key not in known]
                if not added:
                    continue
                counts["takes_added"] += len(added)
                tid = store._next_id(conn, "take")
                rows = [
                    _take_row(tid + i, old["id"], t, len(takes) + i, "alternate")
                    for i, t in enumerate(added)
                ]
                entries += store._write_changes(conn, [("take", None, r) for r in rows])
                takes = _take_rows(conn, old["id"])
                chosen = next((t for t in takes if t["status"] == "preferred"), takes[0])
                update: dict[str, Any] = {}
                pickups = {s.id for s in project.sources() if s.role == "pickup"}
                if any(t.source_id in pickups for t in added):
                    # A pickup for this line (D-062): the fresh ranking decides when
                    # the choice was the app's; a choice the user made is put back
                    # to them to confirm, with the pickup to listen to.
                    best = next(t for t in takes if t["stable_key"] == grp.takes[0].stable_key)
                    user_chose = chosen["decided_by"] == "user"
                    if not user_chose:
                        chosen = best
                    update = {
                        "state": "suggested",
                        "confident": int(grp.confident and not user_chose),
                        "reason": ", ".join(grp.reasons),
                    }
                # Re-apply the choice so the new takes are cut too.
                entries += store._write_changes(
                    conn,
                    _choice_changes(
                        conn, project, old, takes, chosen, "auto", run_id, update, compare_run
                    ),
                )
                continue
            counts["groups"] += 1
            group: dict[str, Any] = {
                "id": store._next_id(conn, "take_group"),
                "stable_key": grp.stable_key,
                "manuscript_unit_id": grp.unit_id,
                "sentence_text": grp.text,
                "page_label": grp.page_label,
                "state": "suggested",
                "confident": int(grp.confident),
                "reason": ", ".join(grp.reasons),
                "previous_take_id": None,
                "run_id": run_id,
                "created_at": now,
                "updated_at": now,
            }
            entries += store._write_changes(conn, [("take_group", None, group)])
            tid = store._next_id(conn, "take")
            rows = [
                _take_row(tid + i, group["id"], t, i, "alternate") for i, t in enumerate(grp.takes)
            ]
            entries += store._write_changes(conn, [("take", None, r) for r in rows])
            # The suggestion, applied: the best-ranked take.
            entries += store._write_changes(
                conn,
                _choice_changes(
                    conn, project, group, rows, rows[0], "auto", run_id, None, compare_run
                ),
            )
        n = counts["groups"]
        store._log_entries(
            project,
            conn,
            "takes_detect",
            entries,
            "auto",
            f"Takes: {n} line{'s' if n != 1 else ''} with retakes",
            {"run_id": run_id},
        )
    return counts


def _take_row(tid: int, gid: int, t: Any, rank: int, status: str) -> dict[str, Any]:
    return {
        "id": tid,
        "take_group_id": gid,
        "stable_key": t.stable_key,
        "source_file_id": t.source_id,
        "start": t.start,
        "end": t.end,
        "span_end": t.span_end,
        "coverage": t.coverage,
        "score": t.score,
        "hard_errors": t.hard_errors,
        "soft_errors": t.soft_errors,
        "extras": t.extras,
        "loudness_db": t.loudness_db,
        "rank": rank,
        "status": status,
        "decided_by": "auto",
        "heard": t.heard,
        "diffs_json": json.dumps(list(t.diffs)),
    }


# -- user actions ------------------------------------------------------------------------


def _load(conn: sqlite3.Connection, group_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _group_rows(conn, "id = ?", (group_id,))
    if not rows:
        raise KeyError(group_id)
    return rows[0], _take_rows(conn, group_id)


def choose(project: Project, group_id: int, take_id: int) -> None:
    """Make a take the preferred one (and confirm the group)."""
    with project.db() as conn:
        group, takes = _load(conn, group_id)
        chosen = next((t for t in takes if t["id"] == take_id), None)
        if chosen is None:
            raise KeyError(take_id)
        changes = _choice_changes(conn, project, group, takes, chosen, "user", None)
        n = next(i for i, t in enumerate(takes) if t["id"] == take_id) + 1
        store._commit(
            project,
            conn,
            "take_choice",
            changes,
            "user",
            f"Take {n} for “{_short(group['sentence_text'])}”",
            {"group_id": group_id},
        )


def back(project: Project, group_id: int) -> None:
    """Go back to the previous choice (SPEC: "remains one click away")."""
    with project.db() as conn:
        group, _ = _load(conn, group_id)
    if group["previous_take_id"] is None:
        raise TakeError("there is no previous choice for this line")
    choose(project, group_id, group["previous_take_id"])


def confirm(project: Project, group_ids: Sequence[int]) -> None:
    with project.db() as conn:
        changes: list[store.Change] = []
        for gid in group_ids:
            group, _ = _load(conn, gid)
            if group["state"] != "confirmed":
                changes.append(
                    ("take_group", group, group | {"state": "confirmed", "updated_at": now_iso()})
                )
        store._commit(
            project,
            conn,
            "take_choice",
            changes,
            "user",
            f"Confirm {len(changes)} take choice{'s' if len(changes) != 1 else ''}",
        )


def mark(project: Project, take_id: int, status: str) -> None:
    """Label a take alternate or rejected. Both are off the timeline; to change
    which take plays, choose another one."""
    if status not in ("alternate", "rejected"):
        raise TakeError("mark a take alternate or rejected (choose a take to prefer it)")
    with project.db() as conn:
        row = conn.execute("SELECT * FROM take WHERE id = ?", (take_id,)).fetchone()
        if row is None:
            raise KeyError(take_id)
        t = dict(row)
        if t["status"] == "preferred":
            raise TakeError("choose another take first: this one is preferred")
        store._commit(
            project,
            conn,
            "take_choice",
            [("take", t, t | {"status": status, "decided_by": "user"})],
            "user",
            f"Mark take {status}",
        )


def _short(text: str, n: int = 40) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# -- reading -----------------------------------------------------------------------------


def groups(project: Project) -> list[dict[str, Any]]:
    labels = {s.id: s.label for s in project.sources()}
    with project.db() as conn:
        out = []
        for g in _group_rows(conn):
            takes = []
            for t in _take_rows(conn, g["id"]):
                d = dict(t)
                d["diffs"] = json.loads(d.pop("diffs_json"))
                d["source_label"] = labels.get(t["source_file_id"], "?")
                takes.append(d)
            out.append(g | {"confident": bool(g["confident"]), "takes": takes})
    out.sort(key=lambda g: (g["manuscript_unit_id"] is None, g["manuscript_unit_id"] or 0, g["id"]))
    return out
