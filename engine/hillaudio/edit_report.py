"""What the edit lost (D-061): the edit check (analysis/edit_check.py) run on
the project's latest comparison and its timeline as it is now. Nothing stored:
it's quick enough to compute on request, after every edit."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from hillaudio import issue_store
from hillaudio.analysis.edit_check import Removal, Word, check
from hillaudio.edl import timeline_store
from hillaudio.edl.model import NARRATION
from hillaudio.store.project import Project


def report(project: Project) -> dict[str, Any]:
    from hillaudio import pipeline  # pipeline imports issue_store

    run = pipeline.latest_run(project)
    if run is None:
        return {"run_id": None, "stale": False, "checked": 0, "lines": []}
    st = timeline_store.load(project)
    rates = {sid: s.sample_rate for sid, s in st.sources.items()}
    narration_muted = st.tracks[NARRATION].mute if NARRATION in st.tracks else False
    heard: dict[int, list[tuple[float, float]]] = {}
    removals: dict[int, list[Removal]] = {}
    for c in st.clips:
        if c.track_id != NARRATION:
            continue
        sr = rates[c.source_id]
        span = (c.source_in / sr, c.source_out / sr)
        if c.muted or narration_muted:
            removals.setdefault(c.source_id, []).append(Removal(None, "muted", *span))
        else:
            heard.setdefault(c.source_id, []).append(span)
    for r in st.region_rows:
        if r["status"] == "removed" and r["end_sample"] > r["start_sample"]:
            sr = rates[r["source_file_id"]]
            removals.setdefault(r["source_file_id"], []).append(
                Removal(r["id"], r["kind"], r["start_sample"] / sr, r["end_sample"] / sr)
            )
    words = {
        sid: [Word(w.text, w.start, w.end) for w in pipeline.transcript(project, sid)]
        for sid in st.sources
    }
    checked, lost = check(
        issue_store.sentence_infos(project),
        issue_store.reading_infos(project, run),
        words,
        heard,
        removals,
    )
    return {
        "run_id": run,
        "stale": pipeline.is_stale(project, run),
        "checked": checked,
        "lines": [asdict(x) for x in lost],
    }
