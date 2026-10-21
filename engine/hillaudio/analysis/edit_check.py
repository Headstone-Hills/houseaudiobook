"""Lines the edit lost (pure, D-061): after cleanup, take choices and timeline
edits, is every line that was read in full still heard?

A line is checked by its words. Of each complete reading, the words that
belong to the line (the transcript words in it, less the extras: a stutter's
repeat, an aside) must still play on the timeline. The line is fine if one
reading is heard whole. Otherwise the reading heard most is reported: "removed"
when less than half its words play, else "clipped", with the words that don't
(or are cut short).

Lines never read in full are the Issues list's business (skips, false starts),
not the edit's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from hillaudio.analysis.issues import ReadingInfo, SentenceInfo
from hillaudio.text.normalize import normalize

# A word plays if at least this much of it is on the timeline. Words are measured
# within the reading's span, whose edges are snapped to the audio, so what's cut
# of a word there is speech: a quarter of it gone is an audible clip.
WORD_HEARD = 0.75
COMPLETE = 0.999


@dataclass(frozen=True)
class Word:
    text: str
    start: float  # seconds in the recording
    end: float


@dataclass(frozen=True)
class Removal:
    """Something that takes audio off the timeline: a removed edit region, or a
    muted clip or track (id None)."""

    id: int | None
    kind: str  # an edit region kind (stutter, take, cut, ...) or "muted"
    start: float  # seconds in the recording
    end: float


@dataclass
class Lost:
    unit_id: int
    ordinal: int
    page_label: str
    text: str
    status: Literal["removed", "clipped"]
    source_id: int
    start: float  # the reading checked, in its recording
    end: float
    missing: list[str]  # the line's words that don't play, in order
    removals: list[dict[str, Any]] = field(default_factory=list)


def heard_fraction(w: Word, heard: Sequence[tuple[float, float]]) -> float:
    """How much of a word plays. A word with no duration (the recognizer gives
    some) plays if its moment does."""
    if w.end <= w.start:
        return 1.0 if any(a <= w.start < b for a, b in heard) else 0.0
    on = sum(max(0.0, min(b, w.end) - max(a, w.start)) for a, b in heard)
    return on / (w.end - w.start)


def _norm(w: Word) -> str:
    return " ".join(normalize(w.text))


def _in(w: Word, a: float, b: float) -> bool:
    mid = (w.start + w.end) / 2
    return a - 0.02 <= mid <= b + 0.02


def _line_words(r: ReadingInfo, words: Sequence[Word]) -> tuple[list[Word], list[Word]]:
    """The reading's transcript words, the line's own and the extras, cut to the
    reading's span. The span's edges are snapped to the audio; the recognizer's
    word times can stray past them, and a word just outside belongs to the next
    or previous utterance. So only words the reading heard count (in order), and
    each only within the span."""
    extras = [
        (float(d["start"]), float(d["end"]))
        for d in r.diffs
        if d.get("kind") == "extra" and d.get("start") is not None and d.get("end") is not None
    ]
    said = [t for t in normalize(r.heard) if t]
    mine: list[Word] = []
    i = 0
    for w in words:
        if w.end < r.start - 0.05 or w.start > r.end + 0.05:
            continue
        toks = normalize(w.text)
        if toks and said[i : i + len(toks)] == toks:
            i += len(toks)
            mine.append(Word(w.text, max(w.start, r.start), min(w.end, r.end)))
    extra = [w for w in mine if any(_in(w, a, b) for a, b in extras)]
    return [w for w in mine if w not in extra], extra


def check(
    sentences: Sequence[SentenceInfo],
    readings: Sequence[ReadingInfo],
    words: Mapping[int, Sequence[Word]],
    heard: Mapping[int, Sequence[tuple[float, float]]],
    removals: Mapping[int, Sequence[Removal]],
) -> tuple[int, list[Lost]]:
    """(lines checked, lines the edit lost). `words`, `heard` (the stretches that
    play) and `removals`: per recording, in seconds."""
    by_ord: dict[int, list[ReadingInfo]] = {}
    for r in readings:
        if r.coverage >= COMPLETE:
            by_ord.setdefault(r.sentence_ordinal, []).append(r)
    checked = 0
    out: list[Lost] = []
    for s in sentences:
        best: tuple[float, float, ReadingInfo, list[Word]] | None = None
        for r in by_ord.get(s.ordinal, []):
            line, extra = _line_words(r, words.get(r.source_id, []))
            if not line:
                continue
            spans = heard.get(r.source_id, [])
            missing = [w for w in line if heard_fraction(w, spans) < WORD_HEARD]
            # A stutter cut may take the first of two "the"s and keep the repeat:
            # the word is still heard.
            kept_extras = [_norm(w) for w in extra if heard_fraction(w, spans) >= WORD_HEARD]
            for w in list(missing):
                if _norm(w) in kept_extras:
                    kept_extras.remove(_norm(w))
                    missing.remove(w)
            frac = 1 - len(missing) / len(line)
            if best is None or (frac, r.score) > (best[0], best[1]):
                best = (frac, r.score, r, missing)
        if best is None:
            continue
        checked += 1
        frac, _, r, missing = best
        if not missing:
            continue
        lost = [(w.start, w.end) for w in missing]
        why = [
            {"id": m.id, "kind": m.kind, "start": m.start, "end": m.end}
            for m in removals.get(r.source_id, [])
            if any(m.start <= b and a <= m.end for a, b in lost)
        ]
        out.append(
            Lost(
                s.unit_id,
                s.ordinal,
                s.page_label,
                s.text,
                "removed" if frac < 0.5 else "clipped",
                r.source_id,
                r.start,
                r.end,
                [w.text for w in missing],
                why,
            )
        )
    return checked, out
