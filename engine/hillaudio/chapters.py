"""Chapters as workspaces (D-059): each chapter of the manuscript, what the
recordings cover of it, and where it is in each recording and on the timeline,
so the UI can narrow every step to one chapter at a time.

Nothing here is stored: chapters come from the manuscript's chapter headings, and
which audio belongs to which chapter comes from the latest Compare to Text run.
"""

from __future__ import annotations

from typing import Any

from hillaudio import issue_store
from hillaudio.analysis.issues import ReadingInfo, SentenceInfo, unrecorded
from hillaudio.edl import timeline_store
from hillaudio.store.project import Project

# Text before the first chapter heading (a title page, a dedication) is one more
# workspace, so nothing in the book is out of reach while working by chapter.
OPENING_ID = -1
OPENING_TITLE = "Opening pages"

# A reading has to cover this much of its line to place the chapter in a recording:
# a false start elsewhere shouldn't stretch the chapter's span across the file.
MIN_COVERAGE = 0.6


def unit_chapters(project: Project) -> dict[int, int]:
    """Sentence unit id -> its chapter's id (OPENING_ID before the first heading)."""
    return {s.unit_id: _chapter_id(s) for s in issue_store.sentence_infos(project)}


def _chapter_id(s: SentenceInfo) -> int:
    return s.chapter if s.chapter >= 0 else OPENING_ID


def chapters(project: Project) -> list[dict[str, Any]]:
    """Every chapter in book order. Empty when the manuscript has no chapter
    headings (or there's no manuscript): the whole book is then the one workspace."""
    from hillaudio import pipeline  # pipeline imports issue_store, which this uses

    with project.db() as conn:
        heads = conn.execute(
            "SELECT id, ordinal, text, page_label FROM manuscript_unit"
            " WHERE kind = 'chapter' ORDER BY ordinal"
        ).fetchall()
        open_by_unit: dict[int, int] = {}
        for r in conn.execute(
            "SELECT manuscript_unit_id AS u, COUNT(*) AS n FROM issue"
            " WHERE status = 'open' AND manuscript_unit_id IS NOT NULL GROUP BY u"
        ):
            open_by_unit[r["u"]] = r["n"]
    if not heads:
        return []
    sents = issue_store.sentence_infos(project)
    run = pipeline.latest_run(project)
    readings = issue_store.reading_infos(project, run) if run is not None else []
    not_recorded = unrecorded(sents, (r.sentence_ordinal for r in readings))
    by_ordinal: dict[int, list[ReadingInfo]] = {}
    for r in readings:
        by_ordinal.setdefault(r.sentence_ordinal, []).append(r)
    loc = _locator(project)

    groups: dict[int, list[SentenceInfo]] = {}
    for s in sents:
        groups.setdefault(_chapter_id(s), []).append(s)
    order = [(OPENING_ID, OPENING_TITLE, "")] if groups.get(OPENING_ID) else []
    order += [(h["id"], h["text"], h["page_label"] or "") for h in heads]

    out = []
    for n, (cid, title, page) in enumerate(order):
        lines = groups.get(cid, [])
        read = [s for s in lines if s.ordinal in by_ordinal]
        missing_scope = sum(1 for s in lines if s.ordinal in not_recorded)
        spans: dict[int, list[float]] = {}
        tl: list[float] = []
        for s in read:
            for r in by_ordinal[s.ordinal]:
                if r.coverage < MIN_COVERAGE:
                    continue
                span = spans.setdefault(r.source_id, [r.start, r.end])
                span[0], span[1] = min(span[0], r.start), max(span[1], r.end)
                if loc is not None:
                    ends = (loc.at(r.source_id, r.start), loc.at(r.source_id, r.end))
                    tl += [t for t in ends if t is not None]
        if run is None:
            status = "not_compared"
        elif not read:
            status = "not_recorded"
        elif missing_scope:
            status = "partial"
        else:
            status = "recorded"
        out.append(
            {
                "id": cid,
                "number": n + (0 if order[0][0] == OPENING_ID else 1),
                "title": title,
                "page_label": page or (lines[0].page_label if lines else ""),
                "status": status,
                "sentences": len(lines),
                "read": len(read),
                "not_recorded": missing_scope,
                "sentence_ids": [s.unit_id for s in lines],
                "spans": [
                    {"source_id": sid, "start": a, "end": b}
                    for sid, (a, b) in sorted(spans.items())
                ],
                "timeline": {"start": min(tl), "end": max(tl)} if tl else None,
                "open_issues": sum(open_by_unit.get(s.unit_id, 0) for s in lines),
            }
        )
    return out


def _locator(project: Project) -> timeline_store.Locator | None:
    st = timeline_store.load(project)
    return timeline_store.Locator(st) if st.clips else None
