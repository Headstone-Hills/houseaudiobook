"""Take groups (pure): lines read more than once, ranked. SPEC §6.3 step 7, §6.5.

A *take* is one complete-enough reading of a sentence. Fragments are false
starts (cleanup, D-040), and readings already cut from the timeline by cleanup
are not takes. A sentence with two or more takes is a group; its takes are
ranked and the best is suggested as preferred.

Ranking, most important first (D-044):
1. complete before partial;
2. fewer real text errors (ones that don't look like the recognizer mishearing);
3. fewer likely-misheard differences;
4. fewer extra words (non-repeat additions);
5. loudness within 3 dB of the narration's median (SPEC: "loudness consistency");
6. higher alignment score;
7. the later take: narrators re-read a line because the earlier one was wrong.
The suggestion is *confident* when it beats the runner-up on 1-3; otherwise the
takes read equally well and the user should listen.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from hillaudio.analysis.boundaries import Mask
from hillaudio.analysis.cleanup import FALSE_START_MAX_COVERAGE
from hillaudio.analysis.issues import (
    TEXT_ERRORS,
    IssueSettings,
    ReadingInfo,
    SentenceInfo,
    likely_misheard,
    sentence_context,
)

LOUDNESS_TOLERANCE_DB = 3.0
COMPLETE = 0.999


@dataclass(frozen=True)
class Level:
    """A recording's 10 ms energy envelope, for loudness."""

    db: npt.NDArray[np.float32]
    speech: Mask
    fps: float


@dataclass
class Take:
    stable_key: str
    source_id: int
    start: float
    end: float
    span_end: float  # where the next speech starts: what choosing/cutting it covers
    coverage: float
    score: float
    hard_errors: int
    soft_errors: int
    extras: int
    loudness_db: float | None
    heard: str
    diffs: tuple[dict[str, Any], ...]
    order: tuple[int, float]  # recording order, then time


@dataclass
class TakeGroup:
    stable_key: str
    unit_id: int
    ordinal: int
    text: str
    page_label: str
    takes: list[Take]  # ranked, best first
    confident: bool
    reasons: list[str] = field(default_factory=list)  # why the best was suggested


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def loudness(level: Level | None, start: float, end: float) -> float | None:
    """Mean level of the speech frames in [start, end), in dB."""
    if level is None:
        return None
    a, b = max(0, int(start * level.fps)), min(len(level.db), int(end * level.fps) + 1)
    frames = level.db[a:b][level.speech[a:b]]
    return float(np.mean(frames)) if len(frames) else None


def _cut_fraction(r: ReadingInfo, cut: Sequence[tuple[float, float]]) -> float:
    span = r.end - r.start
    if span <= 0:
        return 0.0
    removed = sum(max(0.0, min(r.end, b) - max(r.start, a)) for a, b in cut)
    return removed / span


def _span_end(
    r: ReadingInfo, mine: Sequence[ReadingInfo], onsets: Sequence[float], duration: float
) -> float:
    later = [x.start for x in mine if x.start >= r.end - 0.01 and x is not r]
    later += [t for t in onsets if t >= r.end]
    return min(later, default=duration)


def rank_key(t: Take, median_db: float | None) -> tuple[Any, ...]:
    dev = 0.0
    if median_db is not None and t.loudness_db is not None:
        dev = max(0.0, abs(t.loudness_db - median_db) - LOUDNESS_TOLERANCE_DB)
    return (
        t.coverage < COMPLETE,
        t.hard_errors,
        t.soft_errors,
        t.extras,
        round(dev),
        -round(t.score, 4),
        (-t.order[0], -t.order[1]),
    )


def groups(
    sentences: Sequence[SentenceInfo],
    readings: Sequence[ReadingInfo],
    *,
    source_keys: Mapping[int, str],
    source_order: Mapping[int, int],
    durations: Mapping[int, float],
    onsets: Mapping[int, Sequence[float]],
    cut: Mapping[int, Sequence[tuple[float, float]]],
    levels: Mapping[int, Level] | None = None,
    settings: IssueSettings | None = None,
    pickups: frozenset[int] = frozenset(),
) -> list[TakeGroup]:
    """Take groups in manuscript order.

    `onsets`: each recording's word start times (for where a take's span ends);
    `cut`: spans already removed by cleanup (not takes themselves); `pickups`:
    pickup recordings (D-062). A line read once, in a pickup, is a group of one:
    the take still has to be placed where its line goes.
    """
    s = settings or IssueSettings()
    levels = levels or {}
    by_source: dict[int, list[ReadingInfo]] = {}
    for r in readings:
        by_source.setdefault(r.source_id, []).append(r)
    for rs in by_source.values():
        rs.sort(key=lambda r: r.start)

    all_db = [
        x
        for r in readings
        if r.coverage > FALSE_START_MAX_COVERAGE
        and (x := loudness(levels.get(r.source_id), r.start, r.end)) is not None
    ]
    median_db = float(np.median(all_db)) if all_db else None

    by_sentence: dict[int, list[ReadingInfo]] = {}
    for r in readings:
        if r.coverage <= FALSE_START_MAX_COVERAGE:
            continue  # a fragment: a false start, not a take
        if _cut_fraction(r, cut.get(r.source_id, ())) >= 0.5:
            continue  # cleanup already took it out
        by_sentence.setdefault(r.sentence_ordinal, []).append(r)

    out: list[TakeGroup] = []
    for i, sent in enumerate(sentences):
        rs = by_sentence.get(sent.ordinal, [])
        if len(rs) < 2 and not (rs and rs[0].source_id in pickups):
            continue
        gkey = _key("take_group", sentence_context(sentences, i))
        seen: dict[int, int] = {}
        takes: list[Take] = []
        for r in sorted(rs, key=lambda r: (source_order.get(r.source_id, 0), r.start)):
            n = seen.get(r.source_id, 0)
            seen[r.source_id] = n + 1
            errs = [d for d in r.diffs if d["kind"] in TEXT_ERRORS]
            soft = sum(1 for d in errs if likely_misheard(d, s))
            extras = sum(1 for d in r.diffs if d["kind"] == "extra" and not d.get("repeat"))
            takes.append(
                Take(
                    _key(gkey, source_keys.get(r.source_id, str(r.source_id)), str(n)),
                    r.source_id,
                    r.start,
                    r.end,
                    _span_end(
                        r,
                        by_source.get(r.source_id, []),
                        onsets.get(r.source_id, ()),
                        durations.get(r.source_id, r.end),
                    ),
                    r.coverage,
                    r.score,
                    len(errs) - soft,
                    soft,
                    extras,
                    loudness(levels.get(r.source_id), r.start, r.end),
                    r.heard,
                    r.diffs,
                    (source_order.get(r.source_id, 0), r.start),
                )
            )
        ranked = sorted(takes, key=lambda t: rank_key(t, median_db))
        if len(ranked) == 1:
            out.append(
                TakeGroup(
                    gkey,
                    sent.unit_id,
                    sent.ordinal,
                    sent.text,
                    sent.page_label,
                    ranked,
                    confident=True,
                    reasons=["the only take"],
                )
            )
            continue
        best, second = ranked[0], ranked[1]
        kb, ks = rank_key(best, median_db), rank_key(second, median_db)
        out.append(
            TakeGroup(
                gkey,
                sent.unit_id,
                sent.ordinal,
                sent.text,
                sent.page_label,
                ranked,
                confident=kb[:3] < ks[:3],
                reasons=_reasons(kb, ks),
            )
        )
    return out


def _reasons(best: tuple[Any, ...], second: tuple[Any, ...]) -> list[str]:
    names = [
        "complete",
        "fewer text errors",
        "fewer likely mishearings",
        "fewer extra words",
        "more even loudness",
        "higher match score",
        "the later take",
    ]
    for name, a, b in zip(names, best, second, strict=True):
        if a != b:
            return [name]
    return ["the takes are equal"]
