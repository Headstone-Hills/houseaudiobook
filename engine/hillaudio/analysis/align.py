"""Align a transcript to the manuscript at word level (pure). SPEC §6.3 step 3.

Narration is not a clean read of the text: narrators restart, retake lines,
skip, misread and talk off-script. So instead of one global alignment:

1. **Anchors**: positions where a run of `K` transcript tokens equals a run of
   manuscript tokens that is rare in the manuscript.
2. **Chaining**: anchors are chained by dynamic programming. Continuing along the
   diagonal costs one per word of drift (an extra or missing word, as in edit
   distance); any other move (a restart back to an earlier line, a skip ahead, a
   new chapter) is a *jump* with a fixed penalty. So up to 3 extra words (a
   stutter, "the the") stay inside a sentence as an "extra" difference, while a
   longer insertion (a false start, commentary) or a skipped sentence becomes a
   jump. Each jump starts a new **run**: transcript read continuously from one place.
3. **Fallback**: transcript stretches no run covers, but that closely resemble a
   short sentence (a two-word pickup can't make a 3-token anchor), become runs too.
4. **Word alignment** inside each run is a small edit-distance DP between
   consecutive anchors, so cost stays linear in practice even for whole books.
   Run edges then extend to the sentence boundaries (allowing mismatches), so a
   misread last word still belongs to its sentence, but never across a long pause.
5. **Readings**: each manuscript sentence touched by a run gets one reading of
   that run, with coverage, a score, and classified differences.

Transcript tokens outside every run are *unaligned*: commentary, false starts
too short to anchor, or noise. Cleanup (M5) works from these and the readings.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz.distance import Levenshtein
from rapidfuzz.fuzz import ratio

from hillaudio.text.normalize import tokens_equal

K = 3  # anchor length in tokens
MAX_OCCURRENCES = 4  # anchors from k-grams this common are ambiguous; skip them
LOOKBACK = 64  # anchors considered as predecessors in chaining
JUMP_PENALTY = 3.5  # cost of a chain jump, in tokens (drift costs 1 per token)
START_PENALTY = 4.0  # cost of starting a chain
LONG_PAUSE_S = 0.35  # edge extension never crosses a pause this long
FALLBACK_MIN_RATIO = 85.0  # rapidfuzz ratio (0-100) for the short-sentence fallback
FALLBACK_MIN_COVER = 0.6  # the stretch must be at least this share of the sentence
LOW_CONFIDENCE = 0.5  # matched words below this ASR probability: mispronunciation?

OpKind = Literal["match", "sub", "ins", "del"]
DiffKind = Literal[
    "missing", "extra", "substitution", "transposition", "reordered", "mispronunciation"
]


@dataclass(frozen=True)
class Op:
    kind: OpKind
    m: int | None  # manuscript token index (None for an insertion)
    t: int | None  # transcript token index (None for a deletion)


@dataclass
class Run:
    t_start: int
    t_end: int  # exclusive
    m_start: int
    m_end: int  # exclusive
    ops: list[Op] = field(default_factory=list)


@dataclass(frozen=True)
class Diff:
    kind: DiffKind
    m_start: int | None  # manuscript token range (None: nothing expected here)
    m_end: int | None
    t_start: int | None  # transcript token range (None: nothing heard)
    t_end: int | None


@dataclass
class Reading:
    sentence: int  # index into the sentence list given to `readings`
    run: int  # index into FileAlignment.runs
    m_start: int  # manuscript tokens of the sentence this reading covers
    m_end: int
    t_start: int | None  # transcript tokens of the reading (None if nothing was heard)
    t_end: int | None
    core_t_start: int | None  # the same without extra words at the edges (stutters)
    core_t_end: int | None
    coverage: float  # share of the sentence's tokens this reading covers
    matches: int
    subs: int
    dels: int
    ins: int
    diffs: list[Diff]

    @property
    def score(self) -> float:
        """Word accuracy over the whole sentence: 1.0 is a perfect complete read."""
        total = self.matches + self.subs + self.dels + self.ins
        sentence_len = (self.m_end - self.m_start) / max(self.coverage, 1e-9)
        return self.matches / max(total, sentence_len, 1)


@dataclass
class FileAlignment:
    runs: list[Run]
    unaligned: list[tuple[int, int]]  # transcript token ranges in no run


# -- 1-2: anchors and chaining ------------------------------------------------------


def _anchors(m_toks: Sequence[str], t_toks: Sequence[str]) -> list[tuple[int, int]]:
    index: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for i in range(len(m_toks) - K + 1):
        index[tuple(m_toks[i : i + K])].append(i)
    out: list[tuple[int, int]] = []
    for t in range(len(t_toks) - K + 1):
        positions = index.get(tuple(t_toks[t : t + K]))
        if positions and len(positions) <= MAX_OCCURRENCES:
            out.extend((t, m) for m in positions)
    return out


def _chain(anchors: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Best-scoring anchor chain, split into runs at jumps."""
    if not anchors:
        return []
    anchors.sort()
    score = [0.0] * len(anchors)
    back: list[int | None] = [None] * len(anchors)
    jump = [False] * len(anchors)
    for a, (ta, ma) in enumerate(anchors):
        best, best_b, best_jump = float(K) - START_PENALTY, None, True
        for b in range(max(0, a - LOOKBACK), a):
            tb, mb = anchors[b]
            if tb >= ta:
                continue
            dt, dm = ta - tb, ma - mb
            # Continue: gain the new tokens, pay one per token of drift. Jump: fixed.
            cont = min(K, dt, dm) - abs(dm - dt) if dm > 0 else float("-inf")
            jmp = K - JUMP_PENALTY
            gain, is_jump = (cont, False) if cont >= jmp else (jmp, True)
            if score[b] + gain > best:
                best, best_b, best_jump = score[b] + gain, b, is_jump
        score[a], back[a], jump[a] = best, best_b, best_jump

    end: int | None = max(range(len(anchors)), key=lambda i: score[i])
    chain: list[int] = []
    while end is not None:
        chain.append(end)
        end = back[end]
    chain.reverse()

    runs: list[list[tuple[int, int]]] = []
    for idx in chain:
        if jump[idx] or not runs:
            runs.append([])
        runs[-1].append(anchors[idx])
    # A "run" of one anchor whose neighbours both jump away is likely a spurious
    # match of a common phrase; drop it rather than invent a restart.
    return [r for r in runs if len(r) > 1 or len(runs) == 1]


# -- 4: word alignment inside a run ---------------------------------------------------


def _dp(
    m_toks: Sequence[str], t_toks: Sequence[str], ms: int, me: int, ts: int, te: int
) -> list[Op]:
    """Edit-distance alignment of t_toks[ts:te] to m_toks[ms:me], with backtrace."""
    n, k = me - ms, te - ts
    if n == 0:
        return [Op("ins", None, ts + j) for j in range(k)]
    if k == 0:
        return [Op("del", ms + i, None) for i in range(n)]
    cost = [[0] * (k + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, k + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        mi = m_toks[ms + i - 1]
        row, prev = cost[i], cost[i - 1]
        for j in range(1, k + 1):
            same = tokens_equal(mi, t_toks[ts + j - 1])
            row[j] = min(prev[j - 1] + (0 if same else 1), prev[j] + 1, row[j - 1] + 1)
    # Backtrace. On equal cost prefer, in order: a match, a missing word, an extra
    # word, and only then a substitution, so two unrelated words are never paired
    # just because it costs the same (e.g. across a skipped sentence).
    ops: list[Op] = []
    i, j = n, k
    while i or j:
        here = cost[i][j]
        if (
            i
            and j
            and tokens_equal(m_toks[ms + i - 1], t_toks[ts + j - 1])
            and here == cost[i - 1][j - 1]
        ):
            ops.append(Op("match", ms + i - 1, ts + j - 1))
            i, j = i - 1, j - 1
        elif i and here == cost[i - 1][j] + 1:
            ops.append(Op("del", ms + i - 1, None))
            i -= 1
        elif j and here == cost[i][j - 1] + 1:
            ops.append(Op("ins", None, ts + j - 1))
            j -= 1
        else:
            ops.append(Op("sub", ms + i - 1, ts + j - 1))
            i, j = i - 1, j - 1
    ops.reverse()
    return ops


def _align_run(m_toks: Sequence[str], t_toks: Sequence[str], anchors: list[tuple[int, int]]) -> Run:
    t0, m0 = anchors[0]
    t_last, m_last = anchors[-1]
    run = Run(t0, t_last + K, m0, m_last + K)
    ct, cm = t0, m0
    for t, m in anchors:
        if t < ct or m < cm:
            continue  # overlaps tokens already aligned
        run.ops += _dp(m_toks, t_toks, cm, m, ct, t)
        run.ops.append(Op("match", m, t))
        ct, cm = t + 1, m + 1
    run.ops += _dp(m_toks, t_toks, cm, run.m_end, ct, run.t_end)
    return run


def _semi_global(m_seg: Sequence[str], t_seg: Sequence[str]) -> tuple[int, list[Op]]:
    """Align all of m_seg to a *prefix* of t_seg (trailing transcript is free).
    Returns (how many t tokens were used, ops with segment-relative indices)."""
    best_j = 0
    best_ops = [Op("del", i, None) for i in range(len(m_seg))]
    best_cost = len(m_seg)
    for j in range(1, len(t_seg) + 1):
        ops = _dp(m_seg, t_seg, 0, len(m_seg), 0, j)
        cost = sum(op.kind != "match" for op in ops)
        # On a tie, use the heard words: within a sentence and before any long
        # pause, "sight" for "reach" is a misread, not a missing word plus noise.
        if cost <= best_cost:
            best_j, best_cost, best_ops = j, cost, ops
    return best_j, best_ops


def _extend(
    run: Run,
    m_toks: Sequence[str],
    t_toks: Sequence[str],
    t_lo: int,
    t_hi: int,
    sentences: Sequence[tuple[int, int]],
    pauses: Sequence[float] | None,
    right: bool = True,
) -> None:
    """Grow a run's edges to the boundaries of the sentences it ends inside.

    Mismatches are allowed, so a misread or skipped last word stays with its
    sentence; the extension never reaches across a long pause or into another run.
    """
    import bisect

    starts = [s for s, _ in sentences]

    def long_pause(i: int) -> bool:
        return pauses is not None and i < len(pauses) and pauses[i] >= LONG_PAUSE_S

    # Right edge.
    s = bisect.bisect_right(starts, run.m_end - 1) - 1
    if right and s >= 0 and run.m_end < sentences[s][1]:
        need = sentences[s][1] - run.m_end
        hi = run.t_end
        while hi < min(t_hi, run.t_end + need + 2) and not long_pause(hi):
            hi += 1
        used, ops = _semi_global(m_toks[run.m_end : sentences[s][1]], t_toks[run.t_end : hi])
        run.ops += [
            Op(
                o.kind,
                None if o.m is None else run.m_end + o.m,
                None if o.t is None else run.t_end + o.t,
            )
            for o in ops
        ]
        run.t_end += used
        run.m_end = sentences[s][1]

    # Left edge (the same, mirrored: align reversed sequences).
    s = bisect.bisect_right(starts, run.m_start) - 1
    if s >= 0 and run.m_start > sentences[s][0]:
        need = run.m_start - sentences[s][0]
        lo = run.t_start
        # The pause *before* token lo-1 ends at lo-1; don't cross a long one.
        while lo > max(t_lo, run.t_start - need - 2) and not long_pause(lo):
            lo -= 1
        m_seg = list(reversed(m_toks[sentences[s][0] : run.m_start]))
        t_seg = list(reversed(t_toks[lo : run.t_start]))
        used, ops = _semi_global(m_seg, t_seg)
        mapped = [
            Op(
                o.kind,
                None if o.m is None else run.m_start - 1 - o.m,
                None if o.t is None else run.t_start - 1 - o.t,
            )
            for o in ops
        ]
        run.ops = list(reversed(mapped)) + run.ops
        run.t_start -= used
        run.m_start = sentences[s][0]


def _sentence_end(m: int, sentences: Sequence[tuple[int, int]]) -> int:
    """End (exclusive) of the sentence containing manuscript token m."""
    import bisect

    s = bisect.bisect_right([a for a, _ in sentences], m) - 1
    return sentences[s][1] if s >= 0 else m + 1


def _trim_overhang(run: Run, nxt: Run, sentences: Sequence[tuple[int, int]]) -> None:
    """Give back a few tokens a run took from the next sentence across a retake.

    An anchor can match across a take boundary: "...a nap. | The hare..." (the
    retake) against "...a nap. The tortoise..." (the text) pulls the retake's
    "The" into the next sentence. When the next run starts earlier in the text
    (a restart) and this run only reaches K or fewer tokens into its last
    sentence, those tokens belong to the next run.
    """
    import bisect

    if nxt.m_start >= run.m_end:
        return  # no restart
    starts = [s for s, _ in sentences]
    s = bisect.bisect_right(starts, run.m_end - 1) - 1
    if s < 0 or sentences[s][0] <= run.m_start:
        return
    boundary = sentences[s][0]
    tail = [op for op in run.ops if op.m is not None and op.m >= boundary]
    if not tail or len(tail) > K or run.m_end >= sentences[s][1]:
        return
    keep: list[Op] = []
    for op in run.ops:
        if op.m is not None and op.m >= boundary:
            break
        keep.append(op)
    # Trailing insertions after the last kept match go back too.
    while keep and keep[-1].kind == "ins":
        keep.pop()
    run.ops = keep
    run.m_end = boundary
    t_idx = [op.t for op in keep if op.t is not None]
    run.t_end = max(t_idx) + 1 if t_idx else run.t_start


# -- 3: short-sentence fallback for unaligned stretches -------------------------------


def _fallback(
    m_toks: Sequence[str],
    t_toks: Sequence[str],
    gap: tuple[int, int],
    sentences: Sequence[tuple[int, int]],
) -> Run | None:
    ts, te = gap
    heard = " ".join(t_toks[ts:te])
    best: tuple[float, int, int] | None = None
    for ms, me in sentences:
        if me - ms > 2 * (te - ts) + 2 or (te - ts) < FALLBACK_MIN_COVER * (me - ms):
            continue
        r = ratio(heard, " ".join(m_toks[ms:me]))
        if r >= FALLBACK_MIN_RATIO and (best is None or r > best[0]):
            best = (r, ms, me)
    if best is None:
        return None
    _, ms, me = best
    return Run(ts, te, ms, me, _dp(m_toks, t_toks, ms, me, ts, te))


def align(
    m_toks: Sequence[str],
    t_toks: Sequence[str],
    sentences: Sequence[tuple[int, int]] = (),
    pauses: Sequence[float] | None = None,
) -> FileAlignment:
    """Align one transcript.

    `sentences`: manuscript token ranges (for edge extension and the fallback).
    `pauses`: seconds of silence before each transcript token, if known.
    """
    chains = _chain(_anchors(m_toks, t_toks))
    runs = [_align_run(m_toks, t_toks, c) for c in chains]
    runs.sort(key=lambda r: r.t_start)
    if sentences:
        for i in range(len(runs) - 1):
            _trim_overhang(runs[i], runs[i + 1], sentences)
        for i, run in enumerate(runs):
            t_lo = runs[i - 1].t_end if i else 0
            nxt = runs[i + 1] if i + 1 < len(runs) else None
            t_hi = nxt.t_start if nxt else len(t_toks)
            # Before a restart that re-reads the rest of this sentence, leave this
            # run partial: it is a false start, not a reading with missing words.
            restart = nxt is not None and nxt.m_start < _sentence_end(run.m_end - 1, sentences)
            _extend(run, m_toks, t_toks, t_lo, t_hi, sentences, pauses, right=not restart)

    def gaps(rs: list[Run]) -> list[tuple[int, int]]:
        out, pos = [], 0
        for r in rs:
            if r.t_start > pos:
                out.append((pos, r.t_start))
            pos = max(pos, r.t_end)
        if pos < len(t_toks):
            out.append((pos, len(t_toks)))
        return out

    if sentences:
        for gap in gaps(runs):
            fb = _fallback(m_toks, t_toks, gap, sentences)
            if fb is not None:
                runs.append(fb)
        runs.sort(key=lambda r: r.t_start)
    return FileAlignment(runs, gaps(runs))


# -- 5: readings per sentence -----------------------------------------------------------


def _classify(block: list[Op], m_toks: Sequence[str], t_toks: Sequence[str]) -> DiffKind:
    kinds = {op.kind for op in block}
    if kinds == {"del"}:
        return "missing"
    if kinds == {"ins"}:
        return "extra"
    ms = [m_toks[op.m] for op in block if op.m is not None]
    ts = [t_toks[op.t] for op in block if op.t is not None]
    if len(ms) == len(ts) == 2 and ms == ts[::-1]:
        return "transposition"
    return "substitution"


def _diffs(
    ops: list[Op], m_toks: Sequence[str], t_toks: Sequence[str], probs: Sequence[float] | None
) -> list[Diff]:
    out: list[Diff] = []
    block: list[Op] = []

    def flush() -> None:
        if not block:
            return
        m_idx = [op.m for op in block if op.m is not None]
        t_idx = [op.t for op in block if op.t is not None]
        out.append(
            Diff(
                _classify(block, m_toks, t_toks),
                min(m_idx) if m_idx else None,
                max(m_idx) + 1 if m_idx else None,
                min(t_idx) if t_idx else None,
                max(t_idx) + 1 if t_idx else None,
            )
        )
        block.clear()

    def swap_at(i: int) -> bool:
        """ops[i:i+3] is "extra X, match Y, missing X" (or mirrored): Y and X swapped."""
        if i + 2 >= len(ops):
            return False
        a, mid, b = ops[i], ops[i + 1], ops[i + 2]
        if mid.kind != "match" or {a.kind, b.kind} != {"ins", "del"}:
            return False
        x_t = (
            t_toks[a.t]
            if a.kind == "ins" and a.t is not None
            else (t_toks[b.t] if b.t is not None else None)
        )
        x_m = (
            m_toks[b.m]
            if b.kind == "del" and b.m is not None
            else (m_toks[a.m] if a.m is not None else None)
        )
        return x_t is not None and x_m is not None and tokens_equal(x_m, x_t)

    i = 0
    while i < len(ops):
        if swap_at(i):
            flush()
            trio = ops[i : i + 3]
            m_idx = [op.m for op in trio if op.m is not None]
            t_idx = [op.t for op in trio if op.t is not None]
            out.append(
                Diff("transposition", min(m_idx), max(m_idx) + 1, min(t_idx), max(t_idx) + 1)
            )
            i += 3
            continue
        op = ops[i]
        i += 1
        if op.kind == "match":
            flush()
            if probs is not None and op.t is not None and probs[op.t] < LOW_CONFIDENCE:
                out.append(Diff("mispronunciation", op.m, (op.m or 0) + 1, op.t, op.t + 1))
        else:
            block.append(op)
    flush()

    # A missing stretch whose words show up as an extra stretch elsewhere in the
    # same reading was said out of order.
    missing = [d for d in out if d.kind == "missing" and d.m_start is not None]
    extra = [d for d in out if d.kind == "extra" and d.t_start is not None]
    for d in missing:
        want = Counter(m_toks[d.m_start : d.m_end])
        for e in extra:
            if len(want) and Counter(t_toks[e.t_start : e.t_end]) == want:
                i, j = out.index(d), out.index(e)
                out[i] = Diff("reordered", d.m_start, d.m_end, None, None)
                out[j] = Diff("reordered", None, None, e.t_start, e.t_end)
                extra.remove(e)
                break
    return out


def readings(
    fa: FileAlignment,
    m_toks: Sequence[str],
    t_toks: Sequence[str],
    sentences: Sequence[tuple[int, int]],
    probs: Sequence[float] | None = None,
) -> list[Reading]:
    """Split each run's ops by sentence. `sentences`: manuscript token ranges, sorted.

    Extra words *between* two sentences go to the next sentence only if they repeat
    its opening (a stutter or restart: "The. The vine..."). Otherwise they belong to
    neither sentence ("sorry", "okay, where was I") and are added to
    `fa.unaligned`, so a clean reading isn't marked down for off-script talk.
    """
    import bisect

    starts = [s for s, _ in sentences]

    def sentence_of(m: int) -> int:
        s = bisect.bisect_right(starts, m) - 1
        return s if s >= 0 and m < sentences[s][1] else -1

    def repeats_start(ins: list[Op], s: int) -> bool:
        heard = [t_toks[o.t] for o in ins if o.t is not None]
        want = m_toks[sentences[s][0] : sentences[s][0] + len(heard)]
        return (
            bool(heard)
            and len(want) == len(heard)
            and all(tokens_equal(a, b) for a, b in zip(want, heard, strict=True))
        )

    def to_unaligned(ins: list[Op]) -> None:
        t_idx = [o.t for o in ins if o.t is not None]
        if t_idx:
            fa.unaligned.append((min(t_idx), max(t_idx) + 1))

    out: list[Reading] = []
    for r_idx, run in enumerate(fa.runs):
        groups: dict[int, list[Op]] = defaultdict(list)
        pending: list[Op] = []  # insertions not yet assigned
        current: int | None = None
        for op in run.ops:
            if op.m is None:
                pending.append(op)
                continue
            s = sentence_of(op.m)
            if s < 0:
                continue  # manuscript text outside any sentence (e.g. skipped)
            if pending:
                if s == current:
                    groups[s] += pending  # inside a sentence: an extra word in it
                elif repeats_start(pending, s):
                    groups[s] += pending
                else:
                    to_unaligned(pending)
                pending = []
            groups[s].append(op)
            current = s
        if pending:
            to_unaligned(pending)  # after the run's last sentence

        for s, ops in sorted(groups.items()):
            ms_, me_ = sentences[s]
            m_idx = [op.m for op in ops if op.m is not None]
            t_idx = [op.t for op in ops if op.t is not None]
            core = [op.t for op in ops if op.t is not None and op.kind != "ins"]
            counts = Counter(op.kind for op in ops)
            out.append(
                Reading(
                    sentence=s,
                    run=r_idx,
                    m_start=min(m_idx),
                    m_end=max(m_idx) + 1,
                    t_start=min(t_idx) if t_idx else None,
                    t_end=max(t_idx) + 1 if t_idx else None,
                    core_t_start=min(core) if core else None,
                    core_t_end=max(core) + 1 if core else None,
                    coverage=len(set(m_idx)) / max(me_ - ms_, 1),
                    matches=counts["match"],
                    subs=counts["sub"],
                    dels=counts["del"],
                    ins=counts["ins"],
                    diffs=_diffs(ops, m_toks, t_toks, probs),
                )
            )
    fa.unaligned.sort()
    return out


def word_error_rate(ref: Sequence[str], hyp: Sequence[str]) -> float:
    """Token-level WER (plain equality; use for reports, not alignment)."""
    return Levenshtein.distance(list(ref), list(hyp)) / max(len(ref), 1)
