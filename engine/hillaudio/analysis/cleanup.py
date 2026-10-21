"""Cleanup candidates (pure): stutters, false starts, commentary. SPEC §6.3 step 4.

Each candidate is a span of one source to remove from the timeline, with a reason,
a confidence and a stable key. Nothing here edits anything: the EDL store applies
candidates at or above the confidence threshold as `auto` removed regions, and
lists the rest as *suggested* (SPEC: "Low-confidence candidates go to the action
list as suggested rather than being auto-applied").

Cut edges are speech onsets: a cut runs from the onset of the unwanted speech to
the onset of the speech that follows it. The pause before the unwanted speech
stays, the pause after it goes, so the join sounds like one natural pause.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from hillaudio.analysis.boundaries import Mask, snap_start
from hillaudio.analysis.issues import ReadingInfo, SentenceInfo, sentence_context
from hillaudio.text.normalize import normalize

CutKind = Literal["stutter", "false_start", "commentary"]
COMPLETE = 0.999
# A restart this soon after a partial reading is a false start, not a later retake.
RESTART_WINDOW_S = 6.0
# A false start is a fragment. A reading that covers more of the line is a take
# (perhaps with a few misheard words at its end): choosing between takes is M6's,
# and cutting one here would remove real narration.
FALSE_START_MAX_COVERAGE = 0.6

# Phrases narrators use when they stop or restart (SPEC: "keyword hints").
HINTS = (
    "sorry",
    "let me",
    "again",
    "hold on",
    "hang on",
    "one more time",
    "from the top",
    "pick up",
    "pickup",
    "take",
    "okay",
    "ok",
    "um",
    "uh",
    "oops",
    "start over",
    "wait",
    "scratch that",
)


@dataclass(frozen=True)
class CleanupSettings:
    auto_apply: float = 0.7  # candidates at or above this confidence are applied
    stutters: bool = True
    false_starts: bool = True
    commentary: bool = True

    def validate(self) -> CleanupSettings:
        if not 0 <= self.auto_apply <= 1:
            raise ValueError("the auto-apply confidence must be between 0 and 1")
        return self


@dataclass(frozen=True)
class TWord:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Gap:
    """Speech that matched no sentence (from Compare to Text)."""

    start: float
    end: float
    text: str


@dataclass
class Cut:
    kind: CutKind
    source_id: int
    start: float
    end: float
    confidence: float
    reason: str
    text: str  # what is removed (as heard)
    stable_key: str
    sentence_ordinal: int | None = None
    detail: dict[str, object] = field(default_factory=dict)


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _onset(mask: Mask | None, fps: float, t: float, lower: float | None = None) -> float:
    return snap_start(mask, fps, t, lower=lower) if mask is not None and len(mask) else t


def _next_word(words: Sequence[TWord], t: float) -> TWord | None:
    # Words are sorted by start; the first starting at or after t (10 ms slack).
    return next((w for w in words if w.start >= t - 0.01), None)


def _paused(mask: Mask | None, fps: float, a: float, b: float, min_s: float = 0.2) -> bool:
    """Is there a silence of at least min_s between a and b?"""
    if mask is None or not len(mask):
        return b - a >= min_s
    i, j = max(0, int(a * fps)), min(len(mask), round(b * fps) + 1)
    run = best = 0
    for v in mask[i:j]:
        run = 0 if v else run + 1
        best = max(best, run)
    return best >= min_s * fps


def hint_score(text: str) -> int:
    t = " ".join(normalize(text))
    return sum(1 for h in HINTS if re.search(rf"\b{re.escape(h)}\b", t))


# -- with a manuscript (from Compare to Text) ---------------------------------------------


def cuts(
    source_id: int,
    source_key: str,
    sentences: Sequence[SentenceInfo],
    readings: Sequence[ReadingInfo],
    gaps: Sequence[Gap],
    words: Sequence[TWord],
    mask: Mask | None,
    fps: float,
    settings: CleanupSettings,
) -> list[Cut]:
    """Cleanup candidates in one source. `readings`, `gaps`, `words`: this source's."""
    by_ord = {s.ordinal: i for i, s in enumerate(sentences)}
    mine = sorted((r for r in readings if r.source_id == source_id), key=lambda r: r.start)
    words = sorted(words, key=lambda w: w.start)
    out: list[Cut] = []
    seen: dict[str, int] = {}

    def keyed(*parts: str) -> str:
        # Identical candidates (the same stutter twice in one sentence) get an index.
        base = _key(*parts)
        n = seen.get(base, 0)
        seen[base] = n + 1
        return base if n == 0 else _key(base, str(n))

    def ctx(r: ReadingInfo) -> str:
        i = by_ord.get(r.sentence_ordinal)
        return sentence_context(sentences, i) if i is not None else str(r.sentence_ordinal)

    if settings.stutters:
        for r in mine:
            for d in r.diffs:
                if d["kind"] != "extra" or not d.get("repeat") or d.get("start") is None:
                    continue
                start = _onset(mask, fps, float(d["start"]))
                nxt = _next_word(words, float(d["end"]))
                if nxt is None:
                    continue
                end = _onset(mask, fps, nxt.start, lower=start)
                if end <= start:
                    continue
                out.append(
                    Cut(
                        "stutter",
                        source_id,
                        start,
                        end,
                        0.9,
                        f"repeated “{d['heard']}”",
                        str(d["heard"]),
                        keyed("stutter", source_key, ctx(r), " ".join(normalize(d["heard"]))),
                        r.sentence_ordinal,
                    )
                )

    if settings.false_starts:
        for i, r in enumerate(mine):
            if r.coverage > FALSE_START_MAX_COVERAGE:
                continue
            later = mine[i + 1] if i + 1 < len(mine) else None
            # The very next reading restarts the same sentence, soon after.
            if (
                later is None
                or later.sentence_ordinal != r.sentence_ordinal
                or later.start - r.end > RESTART_WINDOW_S
            ):
                continue
            out.append(
                Cut(
                    "false_start",
                    source_id,
                    r.start,
                    later.start,
                    0.9 if later.coverage >= COMPLETE else 0.75,
                    "restarted the line",
                    r.heard,
                    keyed("false_start", source_key, ctx(r), " ".join(normalize(r.heard))),
                    r.sentence_ordinal,
                )
            )

    if settings.commentary:
        for g in gaps:
            prev = next((r for r in reversed(mine) if r.end <= g.start + 0.05), None)
            if prev is not None and not _paused(mask, fps, prev.end, g.start):
                # Speech running straight on from a line is that line's end, misheard
                # (narrators pause before talking to the room): never cut it.
                continue
            nxt_reading = next((r for r in mine if r.start >= g.end - 0.05), None)
            nxt = _next_word(words, g.end)
            if nxt_reading is not None:
                end = nxt_reading.start
            elif nxt is not None:
                end = _onset(mask, fps, nxt.start, lower=g.start)
            else:
                end = g.end  # nothing follows: leave the file's tail as it was
            hints = hint_score(g.text)
            n_words = len(normalize(g.text))
            # Hints say "this is the narrator talking to the room". Without them, a
            # short aside is likely chatter; a long passage may be text the
            # manuscript lacks (or an alignment failure), so only suggest it.
            confidence = 0.9 if hints else (0.6 if n_words <= 6 else 0.4)
            out.append(
                Cut(
                    "commentary",
                    source_id,
                    _onset(mask, fps, g.start),
                    end,
                    confidence,
                    "off-script speech" + (" (sounds like a note to the room)" if hints else ""),
                    g.text,
                    keyed("commentary", source_key, " ".join(normalize(g.text))),
                    None,
                    {"words": n_words, "hints": hints},
                )
            )
    return _no_overlaps(out)


# -- without a manuscript ---------------------------------------------------------------------


def repeat_cuts(
    source_id: int,
    source_key: str,
    words: Sequence[TWord],
    mask: Mask | None,
    fps: float,
    max_len: int = 3,
) -> list[Cut]:
    """Immediate repeats in the transcript alone: "the the", "in the- in the"."""
    words = sorted(words, key=lambda w: w.start)
    toks = [" ".join(normalize(w.text)) for w in words]
    out: list[Cut] = []
    seen: dict[str, int] = {}
    i = 0
    while i < len(words):
        found = 0
        for n in range(max_len, 0, -1):
            a, b = toks[i : i + n], toks[i + n : i + 2 * n]
            if len(b) == n and all(a) and a == b:
                found = n
                break
        if not found:
            i += 1
            continue
        start = _onset(mask, fps, words[i].start)
        end = _onset(mask, fps, words[i + found].start, lower=start)
        text = " ".join(w.text for w in words[i : i + found])
        context = " ".join(toks[max(0, i - 3) : i + 2 * found + 3])
        base = _key("stutter", source_key, context)
        k = seen.get(base, 0)
        seen[base] = k + 1
        if end > start:
            out.append(
                Cut(
                    "stutter",
                    source_id,
                    start,
                    end,
                    0.75,  # no manuscript to confirm the repeat isn't intended
                    f"repeated “{text}”",
                    text,
                    base if k == 0 else _key(base, str(k)),
                )
            )
        i += found
    return out


def _no_overlaps(cs: list[Cut]) -> list[Cut]:
    """Keep candidates disjoint: of two overlapping cuts keep the more confident
    (then the earlier); a cut must never take speech another cut keeps."""
    out: list[Cut] = []
    for c in sorted(cs, key=lambda c: (-c.confidence, c.start)):
        if all(c.end <= o.start or c.start >= o.end for o in out):
            out.append(c)
    return sorted(out, key=lambda c: c.start)
