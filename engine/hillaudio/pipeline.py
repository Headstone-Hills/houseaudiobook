"""The analysis pipeline's I/O around the pure analysis code (SPEC §6.3).

Stages so far (M5): Transcribe, Compare to Text, Cleanup (cuts as EDL regions),
then the checks that flag issues (flubs and unread lines; overlong silences).
Each run is recorded in `analysis_run`. Expensive results are cached by file
hash + settings (CLAUDE.md): transcripts in cache/transcripts/, energy
envelopes in cache/envelope/.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import numpy as np

from hillaudio import issue_store
from hillaudio.analysis import cleanup as cl
from hillaudio.analysis import compare as cmp
from hillaudio.analysis import takes as tk
from hillaudio.analysis.boundaries import Envelope, EnvelopeBuilder, speech_mask
from hillaudio.analysis.issues import unrecorded
from hillaudio.asr import cache as asr_cache
from hillaudio.asr.base import Transcriber, TranscriptWord
from hillaudio.edl import store as edl_store
from hillaudio.edl import take_store
from hillaudio.ingest.decode import iter_blocks
from hillaudio.progress import Cancelled, CheckCancel, Progress, never_cancel, no_progress
from hillaudio.store.project import Project, Source, now_iso

# Cleanup and takes run before the checks, so they see the edited timeline
# (SPEC §6.7): flubs are judged on the preferred take.
STAGES = ("transcribe", "compare_text", "cleanup", "takes", "flubs", "silences")
# Issue kinds each checking stage produces (and so may auto-resolve).
STAGE_KINDS = {"flubs": ["flub", "missing_line"], "silences": ["silence"]}
ENVELOPE_KEY = "env-v1-10ms"


class PipelineError(ValueError):
    """A user-facing reason the analysis can't run."""


# -- runs ------------------------------------------------------------------------------


def start_run(project: Project, stages: list[str], options: dict[str, Any]) -> int:
    with project.db() as conn:
        cur = conn.execute(
            "INSERT INTO analysis_run (started_at, stages_json, options_json, status)"
            " VALUES (?, ?, ?, 'running')",
            (now_iso(), json.dumps(stages), json.dumps(options)),
        )
        run_id = int(cur.lastrowid or 0)
        project.log(conn, "analysis_run", {"run_id": run_id, "stages": stages}, "auto")
    return run_id


def finish_run(project: Project, run_id: int, status: str) -> None:
    with project.db() as conn:
        conn.execute(
            "UPDATE analysis_run SET status = ?, finished_at = ? WHERE id = ?",
            (status, now_iso(), run_id),
        )


# -- transcribe ----------------------------------------------------------------------------


def transcribe_source(
    project: Project,
    src: Source,
    transcriber: Transcriber,
    progress: Progress = no_progress,
    check_cancel: CheckCancel = never_cancel,
) -> list[TranscriptWord]:
    s = transcriber.settings
    words = asr_cache.load(project.cache_dir, src.sha256, s)
    if words is None:
        words = transcriber.transcribe(project.resolve(src), src.duration, progress, check_cancel)
        asr_cache.save(project.cache_dir, src.sha256, s, words)
    with project.db() as conn:
        conn.execute("DELETE FROM transcript_word WHERE source_file_id = ?", (src.id,))
        conn.executemany(
            'INSERT INTO transcript_word (source_file_id, idx, text, start, "end", confidence,'
            " settings_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(src.id, i, w.text, w.start, w.end, w.prob, s.key()) for i, w in enumerate(words)],
        )
    return words


def transcript(project: Project, source_id: int) -> list[TranscriptWord]:
    with project.db() as conn:
        rows = conn.execute(
            'SELECT text, start, "end", confidence FROM transcript_word'
            " WHERE source_file_id = ? ORDER BY idx",
            (source_id,),
        ).fetchall()
    return [TranscriptWord(r["text"], r["start"], r["end"], r["confidence"]) for r in rows]


# -- speech mask (for snapping boundaries) -------------------------------------------------


def envelope(project: Project, src: Source, check_cancel: CheckCancel = never_cancel) -> Envelope:
    path = project.cache_dir / "envelope" / f"{src.sha256}.{ENVELOPE_KEY}.npz"
    if path.is_file():
        with np.load(path) as z:
            return Envelope(z["db"], float(z["fps"]))
    b = EnvelopeBuilder(src.sample_rate)
    for block in iter_blocks(project.resolve(src), src.channels):
        check_cancel()
        b.add(block)
    env = b.finish()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        np.savez(f, db=env.db, fps=np.array(env.fps))
    tmp.replace(path)
    return env


# -- compare to text -------------------------------------------------------------------------


def sentences(project: Project) -> list[cmp.Sentence]:
    with project.db() as conn:
        rows = conn.execute(
            "SELECT id, text FROM manuscript_unit WHERE kind = 'sentence' ORDER BY ordinal"
        ).fetchall()
    return [cmp.Sentence(r["id"], r["text"]) for r in rows]


def compare_source(
    project: Project,
    run_id: int,
    src: Source,
    sents: list[cmp.Sentence],
    check_cancel: CheckCancel = never_cancel,
) -> cmp.Comparison:
    words = [cmp.Word(w.text, w.start, w.end, w.prob) for w in transcript(project, src.id)]
    env = envelope(project, src, check_cancel)
    result = cmp.compare(sents, words, speech_mask(env), env.fps)
    with project.db() as conn:
        conn.executemany(
            'INSERT INTO alignment (run_id, manuscript_unit_id, source_file_id, start, "end",'
            " coverage, match_score, matches, subs, dels, ins, heard, diffs_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    r.sentence_key,
                    src.id,
                    r.start,
                    r.end,
                    r.coverage,
                    r.score,
                    r.matches,
                    r.subs,
                    r.dels,
                    r.ins,
                    r.heard,
                    json.dumps([asdict(d) for d in r.diffs]),
                )
                for r in result.readings
            ],
        )
        conn.executemany(
            'INSERT INTO unaligned_segment (run_id, source_file_id, start, "end", text)'
            " VALUES (?, ?, ?, ?, ?)",
            [(run_id, src.id, u.start, u.end, u.text) for u in result.unaligned],
        )
    return result


def run_analysis(
    project: Project,
    stages: list[str],
    transcriber: Transcriber,
    progress: Progress = no_progress,
    check_cancel: CheckCancel = never_cancel,
) -> dict[str, Any]:
    """Run the requested stages over every source. Returns a summary."""
    unknown = set(stages) - set(STAGES)
    if unknown:
        raise PipelineError(f"unknown stage(s): {', '.join(sorted(unknown))}")
    sources = project.sources()
    if not sources:
        raise PipelineError("import some audio first")
    sents = sentences(project) if "compare_text" in stages else []
    if "compare_text" in stages and not sents:
        raise PipelineError("Compare to Text needs a manuscript; import one first")

    compare_run = None
    if {"flubs", "takes"} & set(stages) and "compare_text" not in stages:
        compare_run = latest_run(project)
        if compare_run is None or is_stale(project, compare_run):
            what = "flagging flubs" if "flubs" in stages else "comparing takes"
            raise PipelineError(f"{what} needs an up-to-date Compare to Text; run it first")
    if (
        ("silences" in stages or "cleanup" in stages)
        and "compare_text" not in stages
        and compare_run is None
    ):
        # Used when current: for pause context, and for cleanup (else cleanup
        # falls back to repeats in the transcript alone, D-040).
        last = latest_run(project)
        compare_run = last if last is not None and not is_stale(project, last) else None

    if {"compare_text", "cleanup"} & set(stages) and "transcribe" not in stages:
        missing = [s.label for s in sources if not transcript(project, s.id)]
        if missing:
            raise PipelineError(f"no transcript yet for {', '.join(missing)}; transcribe first")

    run_id = start_run(
        project,
        stages,
        {"asr": transcriber.settings.to_json(), "units_version": units_version(project)},
    )
    status = "failed"
    try:
        # Transcription dominates the time: give it 90% of the bar, by audio duration.
        total = sum(s.duration for s in sources) or 1.0
        done = 0.0
        if "transcribe" in stages:
            for src in sources:

                def scaled(
                    f: float,
                    msg: str,
                    base: float = done,
                    d: float = src.duration,
                    label: str = src.label,
                ) -> None:
                    progress(0.9 * (base + f * d) / total, f"{label}: {msg}")

                transcribe_source(project, src, transcriber, scaled, check_cancel)
                done += src.duration
        summary: dict[str, Any] = {"run_id": run_id, "sources": len(sources)}
        if "compare_text" in stages:
            readings = unaligned = 0
            for i, src in enumerate(sources):
                progress(0.9 + 0.1 * i / len(sources), f"{src.label}: comparing to text")
                c = compare_source(project, run_id, src, sents, check_cancel)
                readings += len(c.readings)
                unaligned += len(c.unaligned)
            summary |= {"readings": readings, "unaligned": unaligned}
            compare_run = run_id
        if "cleanup" in stages:
            progress(0.95, "finding stutters, false starts and commentary")
            summary["cleanup"] = cleanup(project, run_id, compare_run, check_cancel)
        if "takes" in stages and compare_run is not None:
            progress(0.96, "comparing takes")
            summary["takes"] = takes(project, run_id, compare_run, check_cancel)
        kinds = [k for s in stages for k in STAGE_KINDS.get(s, [])]
        if kinds:
            progress(0.97, "checking for issues")
            drafts = issue_store.detect(project, compare_run, kinds, check_cancel)
            summary["issues"] = issue_store.apply(project, run_id, drafts, kinds)
        status = "done"
        progress(1.0, "done")
        return summary
    except BaseException as e:
        status = "cancelled" if isinstance(e, Cancelled) else "failed"
        raise
    finally:
        finish_run(project, run_id, status)


# -- cleanup ----------------------------------------------------------------------------------


def cleanup(
    project: Project,
    run_id: int,
    compare_run: int | None,
    check_cancel: CheckCancel = never_cancel,
) -> dict[str, int]:
    """Cleanup candidates for every source, applied as EDL regions (D-040)."""
    s = edl_store.settings(project)
    sents = issue_store.sentence_infos(project) if compare_run is not None else []
    readings = issue_store.reading_infos(project, compare_run) if compare_run is not None else []
    found: list[cl.Cut] = []
    for src in project.sources():
        check_cancel()
        words = [cl.TWord(w.text, w.start, w.end) for w in transcript(project, src.id)]
        env = envelope(project, src, check_cancel)
        mask = speech_mask(env)
        if compare_run is None:
            if s.stutters:
                found += cl.repeat_cuts(src.id, src.sha256, words, mask, env.fps)
            continue
        with project.db() as conn:
            gaps = [
                cl.Gap(r["start"], r["end"], r["text"])
                for r in conn.execute(
                    'SELECT start, "end", text FROM unaligned_segment'
                    " WHERE run_id = ? AND source_file_id = ? ORDER BY start",
                    (compare_run, src.id),
                )
            ]
        mine = [r for r in readings if r.source_id == src.id]
        found += cl.cuts(src.id, src.sha256, sents, mine, gaps, words, mask, env.fps, s)
    return edl_store.apply_cleanup(project, run_id, found, s)


# -- takes ---------------------------------------------------------------------------------------


def takes(
    project: Project, run_id: int, compare_run: int, check_cancel: CheckCancel = never_cancel
) -> dict[str, int]:
    """Lines read more than once: groups, ranked, suggestions applied (D-044)."""
    sources = project.sources()
    levels: dict[int, tk.Level] = {}
    onsets: dict[int, list[float]] = {}
    for src in sources:
        check_cancel()
        env = envelope(project, src, check_cancel)
        levels[src.id] = tk.Level(env.db, speech_mask(env), env.fps)
        onsets[src.id] = [w.start for w in transcript(project, src.id)]
    # Readings already cut by cleanup (or the user) aren't takes; take cuts are.
    cut: dict[int, list[tuple[float, float]]] = {}
    rates = {s.id: s.sample_rate for s in sources}
    for r in edl_store.regions(project):
        if r["status"] == "removed" and r["kind"] != "take":
            sr = rates[r["source_file_id"]]
            cut.setdefault(r["source_file_id"], []).append(
                (r["start_sample"] / sr, r["end_sample"] / sr)
            )
    found = tk.groups(
        issue_store.sentence_infos(project),
        issue_store.reading_infos(project, compare_run),
        source_keys={s.id: s.sha256 for s in sources},
        source_order={s.id: s.order_index for s in sources},
        durations={s.id: s.duration for s in sources},
        onsets=onsets,
        cut=cut,
        levels=levels,
        settings=issue_store.settings(project),
    )
    return take_store.apply_detection(project, run_id, found)


# -- results -------------------------------------------------------------------------------------


def units_version(project: Project) -> int:
    """How many times the manuscript's units have been (re)written."""
    try:
        return int(project.meta("units_version"))
    except KeyError:
        return 0


def is_stale(project: Project, run_id: int) -> bool:
    """Re-mapping the manuscript after this run replaced the units it compared
    against (D-031): its results are out of date until Compare to Text re-runs."""
    with project.db() as conn:
        row = conn.execute(
            "SELECT options_json FROM analysis_run WHERE id = ?", (run_id,)
        ).fetchone()
    return bool(json.loads(row["options_json"]).get("units_version") != units_version(project))


def latest_run(project: Project, stage: str = "compare_text") -> int | None:
    with project.db() as conn:
        rows = conn.execute(
            "SELECT id, stages_json FROM analysis_run WHERE status = 'done' ORDER BY id DESC"
        ).fetchall()
    return next((r["id"] for r in rows if stage in json.loads(r["stages_json"])), None)


def comparison(project: Project, run_id: int) -> dict[str, Any]:
    """Per sentence: its page and every reading, in manuscript order."""
    with project.db() as conn:
        units = conn.execute(
            "SELECT id, ordinal, text, page_label, page_end_label FROM manuscript_unit"
            " WHERE kind = 'sentence' ORDER BY ordinal"
        ).fetchall()
        rows = conn.execute(
            "SELECT a.*, s.label AS source_label FROM alignment a JOIN source_file s"
            " ON s.id = a.source_file_id WHERE a.run_id = ? ORDER BY a.source_file_id, a.start",
            (run_id,),
        ).fetchall()
        unaligned = conn.execute(
            'SELECT u.source_file_id, s.label AS source_label, u.start, u."end", u.text'
            " FROM unaligned_segment u JOIN source_file s ON s.id = u.source_file_id"
            " WHERE u.run_id = ? ORDER BY u.source_file_id, u.start",
            (run_id,),
        ).fetchall()
    by_unit: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_unit.setdefault(r["manuscript_unit_id"], []).append(
            {
                "source_id": r["source_file_id"],
                "source_label": r["source_label"],
                "start": r["start"],
                "end": r["end"],
                "coverage": r["coverage"],
                "score": r["match_score"],
                "heard": r["heard"],
                "diffs": json.loads(r["diffs_json"]),
            }
        )
    # Text no recording reaches yet is "not recorded", not "not read" (see unrecorded).
    sents = issue_store.sentence_infos(project)
    unit_ordinal = {u["id"]: u["ordinal"] for u in units}
    not_recorded = unrecorded(sents, (unit_ordinal[uid] for uid in by_unit if uid in unit_ordinal))
    sentences_out = []
    for u in units:
        rs = by_unit.get(u["id"], [])
        complete = [r for r in rs if r["coverage"] >= 0.999]
        best = max(complete, key=lambda r: r["score"], default=None)
        sentences_out.append(
            {
                "unit_id": u["id"],
                "ordinal": u["ordinal"],
                "text": u["text"],
                "page_label": u["page_label"],
                "page_end_label": u["page_end_label"],
                "readings": rs,
                "best_score": best["score"] if best else None,
                "recorded": u["ordinal"] not in not_recorded,
            }
        )
    return {
        "run_id": run_id,
        "stale": is_stale(project, run_id),
        "sentences": sentences_out,
        "unaligned": [dict(r) for r in unaligned],
    }
