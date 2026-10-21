"""Issues (pure): flubbed lines, unread lines, overlong silences. SPEC §6.3-6.4, §6.7.

Detection
- **flub**: a sentence with readings but no clean complete take. The issue
  points at the best take (what the narrator must correct), with its time, page,
  and expected vs heard text. Extra words that *repeat* neighbouring text
  (stutters, restarts) are cleanup (M5), not flubs.
- **missing_line**: a sentence never read in any recording.
- **silence**: a pause longer than the threshold for where it falls: within a
  paragraph, at a paragraph break, or at a chapter break (SPEC §6.3 step 6).
  Leading and trailing room tone of a file is not a pause.

Severity: "high" for real text errors and unread lines; "low" ("check") when
every error looks like the recognizer mishearing a word that was read right
(similar spelling, or low recognition confidence). Every false flag in the M3
benchmark was of that kind; low issues are still listed, just ranked last.

Stable keys (SPEC §4) come from the kind and the sentence's text in context
(its neighbours), not from unit ids or exact times, so re-running checks, or
re-mapping pages, finds the same issue again and keeps the user's decision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from rapidfuzz.fuzz import ratio

from hillaudio.analysis.boundaries import Mask
from hillaudio.text.normalize import normalize

Kind = Literal["flub", "missing_line", "silence"]
Severity = Literal["high", "medium", "low"]
Status = Literal["open", "resolved", "dismissed"]
Change = Literal["new", "still_open", "auto_resolved", "regressed"]
TEXT_ERRORS = frozenset({"missing", "substitution", "transposition", "reordered"})
KINDS: tuple[Kind, ...] = ("flub", "missing_line", "silence")


@dataclass(frozen=True)
class IssueSettings:
    silence_sentence_s: float = 2.0  # SPEC §6.3.6 default suggestion
    silence_paragraph_s: float = 3.0
    silence_chapter_s: float = 5.0
    similar_spelling: float = 75.0  # rapidfuzz ratio: above this, a substitution may be ASR
    low_confidence: float = 0.6  # heard words below this ASR probability may be ASR errors

    def validate(self) -> IssueSettings:
        if not 0.5 <= self.silence_sentence_s <= self.silence_paragraph_s <= self.silence_chapter_s:
            raise ValueError(
                "silence thresholds must satisfy 0.5 <= sentence <= paragraph <= chapter"
            )
        if not 0 <= self.similar_spelling <= 100 or not 0 <= self.low_confidence <= 1:
            raise ValueError("similarity must be 0-100 and confidence 0-1")
        return self

    def threshold(self, boundary: str) -> float:
        return {"chapter": self.silence_chapter_s, "paragraph": self.silence_paragraph_s}.get(
            boundary, self.silence_sentence_s
        )


@dataclass(frozen=True)
class SentenceInfo:
    unit_id: int
    ordinal: int
    text: str
    page_label: str
    paragraph: int  # ids of the containing paragraph and chapter units (or -1)
    chapter: int


@dataclass(frozen=True)
class ReadingInfo:
    sentence_ordinal: int
    source_id: int
    start: float
    end: float
    coverage: float
    score: float
    heard: str
    diffs: tuple[dict[str, Any], ...]  # DiffOut as stored: kind, expected, heard, ...


@dataclass
class IssueDraft:
    kind: Kind
    severity: Severity
    stable_key: str
    source_id: int | None
    start: float | None
    end: float | None
    unit_id: int | None
    page_label: str | None
    sentence_text: str
    expected: str
    heard: str
    detail: dict[str, Any] = field(default_factory=dict)


# -- keys -----------------------------------------------------------------------------


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def sentence_context(sentences: Sequence[SentenceInfo], i: int) -> str:
    """The sentence's normalized text plus its neighbours': identifies it even
    where the same line occurs twice in a book, and survives page re-mapping."""

    def norm(j: int) -> str:
        return " ".join(normalize(sentences[j].text)) if 0 <= j < len(sentences) else ""

    return f"{norm(i - 1)}|{norm(i)}|{norm(i + 1)}"


# -- flubs and unread lines -------------------------------------------------------------


def _complete(r: ReadingInfo) -> bool:
    return r.coverage >= 0.999


def _errors(r: ReadingInfo) -> list[dict[str, Any]]:
    return [
        d
        for d in r.diffs
        if d["kind"] in TEXT_ERRORS or (d["kind"] == "extra" and not d.get("repeat"))
    ]


_SOUND_RULES = [  # order matters; a rough English-sound key, not a full Metaphone
    ("augh", "af"),
    ("ough", "of"),
    ("ph", "f"),
    ("gh", ""),
    ("ck", "k"),
    ("ch", "x"),
    ("sh", "x"),
    ("th", "0"),
    ("qu", "kw"),
    ("x", "ks"),
    ("wr", "r"),
    ("kn", "n"),
]
_SOUND_MAP = str.maketrans("cqzdbvgj", "kkstpfkx")  # merge voiced/unvoiced and spellings


def sound_key(word: str) -> str:
    """Words that sound alike get the same key: "laughed"/"left" -> "lft",
    "trellis"/"trelleys" -> "trls", "air"/"error" -> "ar"."""
    w = "".join(ch for ch in word.lower() if ch.isalpha())
    if not w:
        return ""
    for a, b in _SOUND_RULES:
        w = w.replace(a, b)
    if w.endswith("ed") and len(w) > 3:
        w = w[:-2] + "d"  # "laughed" ends in a "t/d" sound, not "-ed"
    w = w.translate(_SOUND_MAP)
    # Keep a leading vowel as a generic vowel; drop the rest of the vowels.
    head = "a" if w[0] in "aeiouy" else w[0]
    body = "".join(ch for ch in w[1:] if ch not in "aeiouyhw")
    out = head
    for ch in body:
        if ch != out[-1]:
            out += ch
    return out


def likely_misheard(d: dict[str, Any], s: IssueSettings) -> bool:
    """A difference that is probably the recognizer, not the narrator: low
    recognition confidence, or a substitution that sounds or is spelled alike."""
    conf = d.get("confidence")
    if d["kind"] in ("substitution", "extra") and conf is not None and conf < s.low_confidence:
        return True
    if d["kind"] == "substitution":
        exp, heard = normalize(d["expected"]), normalize(d["heard"])
        a, b = "".join(exp), "".join(heard)
        if not (a and b):
            return False
        if ratio(a, b) >= s.similar_spelling:
            return True
        return [sound_key(x) for x in exp] == [sound_key(x) for x in heard]
    return False


def best_take(readings: Sequence[ReadingInfo]) -> ReadingInfo | None:
    full = [r for r in readings if _complete(r)] or list(readings)
    return min(full, key=lambda r: (len(_errors(r)), -r.score)) if full else None


# A run of unread lines at the start or end of a chapter this long or longer is text
# not recorded yet (the audio covers only part of the manuscript), not a skip.
# Shorter runs at the edges, like a forgotten chapter title, are still flagged.
MIN_UNRECORDED_RUN = 3


def unrecorded(sentences: Sequence[SentenceInfo], read: Iterable[int]) -> frozenset[int]:
    """Ordinals of the sentences the recordings don't cover yet, so they aren't
    reported as missing: every line of a chapter nobody has read from, and the
    long unread runs before the first and after the last line read in a chapter.
    Gaps between lines that were read are skips, and stay flagged."""
    done = set(read)
    by_chapter: dict[int, list[int]] = {}
    for s in sentences:
        by_chapter.setdefault(s.chapter, []).append(s.ordinal)
    out: set[int] = set()
    for ords in by_chapter.values():
        hits = [i for i, o in enumerate(ords) if o in done]
        if not hits:
            out.update(ords)
            continue
        head, tail = ords[: hits[0]], ords[hits[-1] + 1 :]
        for run in (head, tail):
            if len(run) >= MIN_UNRECORDED_RUN:
                out.update(run)
    return frozenset(out)


def text_issues(
    sentences: Sequence[SentenceInfo],
    readings: Iterable[ReadingInfo],
    settings: IssueSettings,
    not_recorded: frozenset[int] = frozenset(),
) -> list[IssueDraft]:
    """Flubs and unread lines. Lines in `not_recorded` (see `unrecorded`) aren't
    expected to have a reading, so they're never reported missing."""
    by_sentence: dict[int, list[ReadingInfo]] = {}
    for r in readings:
        by_sentence.setdefault(r.sentence_ordinal, []).append(r)
    out: list[IssueDraft] = []
    for i, s in enumerate(sentences):
        rs = by_sentence.get(s.ordinal, [])
        ctx = sentence_context(sentences, i)
        if not rs and s.ordinal in not_recorded:
            continue
        if not rs:
            out.append(
                IssueDraft(
                    "missing_line",
                    "high",
                    _key("missing_line", ctx),
                    None,
                    None,
                    None,
                    s.unit_id,
                    s.page_label,
                    s.text,
                    s.text,
                    "",
                    {"reason": "not read in any recording"},
                )
            )
            continue
        best = best_take(rs)
        assert best is not None
        errs = _errors(best)
        if _complete(best) and not errs:
            continue  # a clean take exists
        if not _complete(best):
            # Only false starts: the line was begun but never finished.
            errs = [{"kind": "missing", "expected": s.text, "heard": best.heard}]
        severity: Severity = "low" if all(likely_misheard(d, settings) for d in errs) else "high"
        out.append(
            IssueDraft(
                "flub",
                severity,
                _key("flub", ctx),
                best.source_id,
                best.start,
                best.end,
                s.unit_id,
                s.page_label,
                s.text,
                s.text,
                best.heard,
                {
                    "diffs": errs,
                    "takes": len(rs),
                    "complete": _complete(best),
                    "likely_misheard": severity == "low",
                },
            )
        )
    return out


# -- overlong silences ---------------------------------------------------------------------


def pauses(
    mask: Mask, fps: float, cut: Sequence[tuple[float, float]] = ()
) -> list[tuple[float, float, float]]:
    """Silent stretches between speech: (start, end, length) in seconds, leading
    and trailing silence excluded.

    `cut`: spans removed from the timeline. Pauses are measured as they play
    (cut audio left out), so cutting a stutter's gap or shortening a pause
    changes them, while start/end stay positions in the recording.
    """
    n = len(mask)
    keep = np.ones(n, dtype=bool)
    for a, b in cut:
        keep[max(0, round(a * fps)) : max(0, round(b * fps))] = False
    idx = np.flatnonzero(keep)
    speech = np.flatnonzero(mask[keep])
    if len(speech) == 0:
        return []
    gaps = np.flatnonzero(np.diff(speech) > 1)
    return [
        (
            (idx[speech[g]] + 1) / fps,
            idx[speech[g + 1]] / fps,
            (speech[g + 1] - speech[g] - 1) / fps,
        )
        for g in gaps
    ]


def _boundary(prev: SentenceInfo | None, nxt: SentenceInfo | None) -> str:
    if prev is None or nxt is None:
        return "unknown"  # not between two aligned sentences
    if prev.chapter != nxt.chapter:
        return "chapter"
    if prev.paragraph != nxt.paragraph:
        return "paragraph"
    return "sentence"


def silence_issues(
    source_id: int,
    source_key: str,
    mask: Mask,
    fps: float,
    sentences: Sequence[SentenceInfo],
    readings: Sequence[ReadingInfo],
    settings: IssueSettings,
    cut: Sequence[tuple[float, float]] = (),
) -> list[IssueDraft]:
    """Overlong pauses in one source. `readings`: this source's, for context;
    `cut`: this source's spans removed from the timeline (seconds)."""
    by_ord = {s.ordinal: (i, s) for i, s in enumerate(sentences)}
    mine = sorted((r for r in readings if r.source_id == source_id), key=lambda r: r.start)
    slack = 0.25  # readings' snapped edges vs the mask's edges
    out: list[IssueDraft] = []
    for a, b, length in pauses(mask, fps, cut):
        before = [r for r in mine if r.end <= a + slack]
        # The first line read after the pause begins: the same line whether or not
        # what followed was cut since (a pickup took its place).
        after = [r for r in mine if r.start >= a + slack]
        prev = by_ord[before[-1].sentence_ordinal] if before else None
        nxt = by_ord[after[0].sentence_ordinal] if after else None
        boundary = _boundary(prev[1] if prev else None, nxt[1] if nxt else None)
        limit = settings.threshold(boundary)
        if length <= limit:
            continue
        if prev or nxt:
            ctx = "|".join(sentence_context(sentences, x[0]) if x else "" for x in (prev, nxt))
        else:
            ctx = f"t{round(a / 5)}"  # no alignment: a coarse position is the best key
        # The issue's page and sentence: the line before the pause, else the one after.
        anchor = prev or nxt
        near = anchor[1] if anchor else None
        out.append(
            IssueDraft(
                "silence",
                "medium",
                _key("silence", source_key, ctx),
                source_id,
                a,
                b,
                near.unit_id if near else None,
                near.page_label if near else None,
                near.text if near else "",
                "",
                "",
                {
                    "seconds": round(length, 2),
                    "boundary": boundary,
                    "threshold": limit,
                    # What "shorten" will do in M5: a natural pause for this boundary.
                    "suggested_s": {"chapter": 2.0, "paragraph": 1.2}.get(boundary, 0.6),
                },
            )
        )
    return out


# -- re-run reconciliation -------------------------------------------------------------------


@dataclass(frozen=True)
class Existing:
    stable_key: str
    kind: str
    status: Status
    decided_by: str | None  # "user", "auto", or None (never decided)


@dataclass(frozen=True)
class Update:
    stable_key: str
    status: Status
    change: Change
    decided_by: str | None
    draft: IssueDraft | None  # new details (None: issue not found this run)


def reconcile(
    existing: Sequence[Existing], drafts: Sequence[IssueDraft], kinds_checked: Iterable[str]
) -> list[Update]:
    """What a re-run does to each issue (SPEC §6.7).

    - found, not seen before: new, open;
    - found, was open: still open (details refreshed);
    - found, the user dismissed or resolved it: kept as the user decided (SPEC §4);
    - found, had been auto-resolved by an earlier run: regressed, reopened;
    - not found, was open, and its kind was checked this run: auto-resolved.
    Issues of kinds not checked this run are left alone.
    """
    checked = set(kinds_checked)
    by_key = {e.stable_key: e for e in existing}
    seen: set[str] = set()
    out: list[Update] = []
    for d in drafts:
        if d.stable_key in seen:
            continue  # two detections with one key: keep the first
        seen.add(d.stable_key)
        e = by_key.get(d.stable_key)
        if e is None:
            out.append(Update(d.stable_key, "open", "new", None, d))
        elif e.status == "resolved" and e.decided_by == "auto":
            out.append(Update(d.stable_key, "open", "regressed", None, d))
        elif e.status != "open":
            # The user's decision (dismissed, or resolved by hand) is kept.
            out.append(Update(d.stable_key, e.status, "still_open", e.decided_by, d))
        else:
            out.append(Update(d.stable_key, "open", "still_open", e.decided_by, d))
    for e in existing:
        if e.stable_key not in seen and e.kind in checked and e.status == "open":
            out.append(Update(e.stable_key, "resolved", "auto_resolved", "auto", None))
    return out
