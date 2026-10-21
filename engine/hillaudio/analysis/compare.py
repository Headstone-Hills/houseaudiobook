"""Compare to Text (pure): transcript words + manuscript sentences -> readings.

Glue over analysis/align.py that speaks in sentences, words and seconds rather
than token indices, and attaches readable text to every difference:

    expected: "for they were just out of reach"   heard: "for they were just out of sight"

Times come from the transcript's word timestamps, snapped to the audio when a
speech mask is given (analysis/boundaries.py, D-008/D-028).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from hillaudio.analysis.align import Diff, Reading, align, readings
from hillaudio.analysis.boundaries import Mask, snap_end, snap_start
from hillaudio.text.normalize import tokenize


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    prob: float = 1.0


@dataclass(frozen=True)
class Sentence:
    key: int  # caller's id (e.g. manuscript_unit.id)
    text: str


@dataclass(frozen=True)
class DiffOut:
    kind: str
    expected: str  # manuscript text ("" when nothing was expected, e.g. extra words)
    heard: str  # transcript text ("" when nothing was heard, e.g. missing words)
    start: float | None  # when it happened in the recording (None for pure omissions)
    end: float | None
    repeat: bool = False  # extra words repeating adjacent text (a stutter or restart)
    confidence: float | None = None  # mean ASR probability of the heard words


@dataclass
class SentenceReading:
    sentence_key: int
    start: float
    end: float
    coverage: float
    score: float
    matches: int
    subs: int
    dels: int
    ins: int
    heard: str
    diffs: list[DiffOut] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.coverage >= 0.999


@dataclass(frozen=True)
class Unaligned:
    start: float
    end: float
    text: str


@dataclass
class Comparison:
    readings: list[SentenceReading]
    unaligned: list[Unaligned]


@dataclass(frozen=True)
class _MTok:
    norm: str
    sentence: int  # index into the sentence list
    start: int  # char span within that sentence's text
    end: int


def manuscript_tokens(sentences: Sequence[Sentence]) -> tuple[list[_MTok], list[tuple[int, int]]]:
    toks: list[_MTok] = []
    ranges: list[tuple[int, int]] = []
    for i, s in enumerate(sentences):
        a = len(toks)
        toks += [_MTok(t.norm, i, t.start, t.end) for t in tokenize(s.text)]
        ranges.append((a, len(toks)))
    return toks, ranges


def transcript_tokens(words: Sequence[Word]) -> tuple[list[str], list[int]]:
    """Tokens and the word each came from. Punctuation-only words ("-") yield none."""
    toks: list[str] = []
    word_of: list[int] = []
    for i, w in enumerate(words):
        for t in tokenize(w.text):
            toks.append(t.norm)
            word_of.append(i)
    return toks, word_of


def _span_text(text: str, toks: list[_MTok], a: int, b: int) -> str:
    return text[toks[a].start : toks[b - 1].end] if b > a else ""


def compare(
    sentences: Sequence[Sentence],
    words: Sequence[Word],
    mask: Mask | None = None,
    fps: float = 100.0,
) -> Comparison:
    m_toks, ranges = manuscript_tokens(sentences)
    t_norm, word_of = transcript_tokens(words)
    m_norm = [t.norm for t in m_toks]
    # Pause before each token: the gap since the previous word ended (0 within a word).
    pauses = [
        0.0
        if i == 0 or word_of[i] == word_of[i - 1]
        else max(0.0, words[word_of[i]].start - words[word_of[i - 1]].end)
        for i in range(len(word_of))
    ]
    fa = align(m_norm, t_norm, ranges, pauses)
    rs = readings(fa, m_norm, t_norm, ranges, [words[w].prob for w in word_of])

    def words_text(ta: int, tb: int) -> str:
        idx = sorted({word_of[t] for t in range(ta, tb)})
        return " ".join(words[i].text.strip() for i in idx)

    def times(ta: int, tb: int) -> tuple[float, float]:
        w0, w1 = word_of[ta], word_of[tb - 1]
        start, end = words[w0].start, words[w1].end
        if mask is not None:
            # Neighbouring words bound the snap, so a boundary never moves into
            # audio the transcript gives to another word.
            lo = words[w0 - 1].start if w0 > 0 else None
            hi = words[w1 + 1].end if w1 + 1 < len(words) else None
            start = snap_start(mask, fps, start, lower=lo, upper=words[w0].end)
            end = snap_end(mask, fps, end, lower=words[w1].start, upper=hi)
            if end <= start:
                start, end = words[w0].start, words[w1].end
        return start, end

    def diff_out(d: Diff, text: str) -> DiffOut:
        expected = ""
        if d.m_start is not None and d.m_end is not None:
            expected = _span_text(text, m_toks, d.m_start, d.m_end)
        heard, start, end, conf = "", None, None, None
        if d.t_start is not None and d.t_end is not None:
            heard = words_text(d.t_start, d.t_end)
            start, end = times(d.t_start, d.t_end)
            idx = sorted({word_of[t] for t in range(d.t_start, d.t_end)})
            conf = sum(words[i].prob for i in idx) / len(idx)
        return DiffOut(d.kind, expected, heard, start, end, d.repeat, conf)

    out: list[SentenceReading] = []
    for r in rs:
        if r.t_start is None or r.t_end is None:
            continue  # nothing heard (cannot happen for a run, but be safe)
        sent = sentences[r.sentence]
        # The reading spans the sentence proper; a stutter or other extra words at
        # its edges are diffs with their own times, not part of the take.
        a = r.core_t_start if r.core_t_start is not None else r.t_start
        b = r.core_t_end if r.core_t_end is not None else r.t_end
        start, end = times(a, b)
        out.append(
            SentenceReading(
                sentence_key=sent.key,
                start=start,
                end=end,
                coverage=r.coverage,
                score=r.score,
                matches=r.matches,
                subs=r.subs,
                dels=r.dels,
                ins=r.ins,
                heard=words_text(r.t_start, r.t_end),
                diffs=[diff_out(d, sent.text) for d in r.diffs],
            )
        )
    unaligned = []
    for ta, tb in fa.unaligned:
        start, end = times(ta, tb)
        unaligned.append(Unaligned(start, end, words_text(ta, tb)))
    return Comparison(out, unaligned)


__all__ = [
    "Comparison",
    "DiffOut",
    "Reading",
    "Sentence",
    "SentenceReading",
    "Unaligned",
    "Word",
    "compare",
]
