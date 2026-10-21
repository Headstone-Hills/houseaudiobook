"""What the pickups did (D-062): for each pickup recording, the lines found in it,
whether each now plays (and what it replaced), which wait for the user to choose,
and speech in it that matched no line."""

from __future__ import annotations

from typing import Any

from hillaudio.store.project import Project


def report(project: Project) -> list[dict[str, Any]]:
    from hillaudio import pipeline  # pipeline imports what this reads

    pickups = [s for s in project.sources() if s.role == "pickup"]
    if not pickups:
        return []
    roles = {s.id: s.role for s in project.sources()}
    run = pipeline.latest_run(project)
    out = []
    with project.db() as conn:
        flubbed = {
            r["u"]
            for r in conn.execute(
                "SELECT DISTINCT manuscript_unit_id AS u FROM issue"
                " WHERE kind IN ('flub', 'missing_line') AND manuscript_unit_id IS NOT NULL"
            )
        }
        for src in pickups:
            lines = []
            rows = conn.execute(
                'SELECT t.id AS take_id, t.status, t.start, t."end", g.id AS group_id,'
                " g.manuscript_unit_id, g.sentence_text, g.page_label, g.state, g.confident"
                " FROM take t JOIN take_group g ON g.id = t.take_group_id"
                " WHERE t.source_file_id = ? ORDER BY t.start",
                (src.id,),
            ).fetchall()
            for r in rows:
                others = conn.execute(
                    "SELECT source_file_id FROM take WHERE take_group_id = ? AND id != ?",
                    (r["group_id"], r["take_id"]),
                ).fetchall()
                in_narration = any(roles.get(o["source_file_id"]) == "narration" for o in others)
                choose = r["state"] == "suggested" and not r["confident"]
                if choose:
                    status = "choose"
                elif r["status"] == "preferred":
                    status = "placed"
                else:
                    status = "not_used"
                if not in_narration:
                    replaces = "missing"  # the line had no reading in the narration
                elif r["manuscript_unit_id"] in flubbed:
                    replaces = "flub"
                else:
                    replaces = "reading"  # a line that was fine, read again
                lines.append(
                    {
                        "take_id": r["take_id"],
                        "group_id": r["group_id"],
                        "unit_id": r["manuscript_unit_id"],
                        "page_label": r["page_label"],
                        "text": r["sentence_text"],
                        "start": r["start"],
                        "end": r["end"],
                        "status": status,
                        "replaces": replaces,
                    }
                )
            unmatched = (
                [
                    dict(u)
                    for u in conn.execute(
                        'SELECT start, "end", text FROM unaligned_segment'
                        " WHERE run_id = ? AND source_file_id = ? ORDER BY start",
                        (run, src.id),
                    )
                ]
                if run is not None
                else []
            )
            transcribed = run is not None and bool(
                conn.execute(
                    "SELECT 1 FROM alignment WHERE run_id = ? AND source_file_id = ? LIMIT 1",
                    (run, src.id),
                ).fetchone()
                or unmatched
            )
            out.append(
                {
                    "source_id": src.id,
                    "label": src.label,
                    "duration": src.duration,
                    "checked": transcribed,
                    "lines": lines,
                    "unmatched": unmatched,
                }
            )
    return out
