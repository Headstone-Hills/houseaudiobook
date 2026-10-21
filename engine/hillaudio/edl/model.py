"""The edit decision list (pure): the narration track as sources minus removed regions.

SPEC §5: the timeline is a list of clips referencing (source, in, out), and
automated removals are *removed regions* that can be restored one by one or by
dragging a clip edge outward. In M5 there is one track, the narration, and every
edit is a removal (cleanup, pause shortening, the user's own cuts). So the state
is just the regions; clips and their timeline positions are derived:

    clips = for each source in order: [0, num_frames) minus its removed regions

Deriving positions makes every edit ripple by construction, and keeps each edit
small (one region row), which makes undo cheap (D-038). Times are integer
samples: source positions at the source's rate, timeline positions at the
timeline rate, so nothing drifts over a long book (SPEC §8).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

RegionStatus = Literal["removed", "restored", "suggested"]


@dataclass(frozen=True)
class SourceInfo:
    id: int
    sample_rate: int
    num_frames: int


@dataclass(frozen=True)
class Region:
    id: int
    source_id: int
    start: int  # samples, at the source's rate
    end: int
    status: RegionStatus

    @property
    def active(self) -> bool:
        return self.status == "removed" and self.end > self.start


@dataclass(frozen=True)
class Clip:
    source_id: int
    source_in: int  # samples at the source rate, [in, out)
    source_out: int
    timeline_start: int  # samples at the timeline rate
    timeline_end: int


def timeline_rate(sources: Sequence[SourceInfo]) -> int:
    """The highest source rate (no source is downsampled for preview)."""
    return max((s.sample_rate for s in sources), default=48000)


def merged(regions: Iterable[Region], source_id: int) -> list[tuple[int, int]]:
    """A source's active removed spans, sorted and merged."""
    spans = sorted((r.start, r.end) for r in regions if r.source_id == source_id and r.active)
    out: list[tuple[int, int]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


@dataclass(frozen=True)
class Segment:
    """A stretch of one recording, in timeline order (M6, D-043)."""

    source_id: int
    source_in: int
    source_out: int


def default_segments(sources: Sequence[SourceInfo]) -> list[Segment]:
    """Each recording once, in order: the timeline before anything is moved."""
    return [Segment(s.id, 0, s.num_frames) for s in sources]


def normalize(segs: Sequence[Segment]) -> list[Segment]:
    """Drop empty segments and merge neighbours that continue the same recording,
    so moving a piece away and back leaves exactly the segments it started with."""
    out: list[Segment] = []
    for s in segs:
        if s.source_out <= s.source_in:
            continue
        if out and out[-1].source_id == s.source_id and out[-1].source_out == s.source_in:
            out[-1] = Segment(s.source_id, out[-1].source_in, s.source_out)
        else:
            out.append(s)
    return out


def _without(segs: Sequence[Segment], source_id: int, a: int, b: int) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        if s.source_id != source_id or s.source_out <= a or s.source_in >= b:
            out.append(s)
            continue
        out.append(Segment(source_id, s.source_in, max(s.source_in, a)))
        out.append(Segment(source_id, min(s.source_out, b), s.source_out))
    return [s for s in out if s.source_out > s.source_in]


def place(
    segs: Sequence[Segment],
    source_id: int,
    a: int,
    b: int,
    before: tuple[int, int] | None = None,
) -> list[Segment]:
    """Move [a, b) of a recording so it plays just before `before` = (source,
    sample); None puts it back where it naturally belongs in its own recording."""
    rest = _without(segs, source_id, a, b)
    piece = Segment(source_id, a, b)
    at: int | None = None
    if before is not None:
        bs, x = before
        for i, s in enumerate(rest):
            if s.source_id == bs and s.source_in <= x < s.source_out:
                head, tail = Segment(bs, s.source_in, x), Segment(bs, x, s.source_out)
                rest[i : i + 1] = [head, tail]
                at = i + 1
                break
    if at is None:
        # Home: after the piece of its recording that ends where it starts, or
        # before the one that starts where it ends, or else by source position.
        mine = [(i, s) for i, s in enumerate(rest) if s.source_id == source_id]
        at = next((i + 1 for i, s in mine if s.source_out == a), None)
        if at is None:
            at = next((i for i, s in mine if s.source_in == b), None)
        if at is None:
            earlier = [i for i, s in mine if s.source_out <= a]
            later = [i for i, s in mine if s.source_in >= b]
            at = earlier[-1] + 1 if earlier else (later[0] if later else len(rest))
    rest.insert(at, piece)
    return normalize(rest)


def clips(
    sources: Sequence[SourceInfo],
    regions: Sequence[Region],
    rate: int,
    segments: Sequence[Segment] | None = None,
) -> list[Clip]:
    """The timeline: segments in order (default: each recording once), each minus
    the removed regions that fall inside it."""
    by_id = {s.id: s for s in sources}
    segs = list(segments) if segments is not None else default_segments(sources)
    cuts = {s.id: merged(regions, s.id) for s in sources}
    out: list[Clip] = []
    # Exact cumulative timeline time, so rounding never accumulates across clips.
    t = Fraction(0)
    for seg in segs:
        src = by_id.get(seg.source_id)
        if src is None:
            continue
        pos, end = max(0, seg.source_in), min(src.num_frames, seg.source_out)
        spans: list[tuple[int, int]] = []
        for a, b in cuts[src.id]:
            if b <= pos or a >= end:
                continue
            if a > pos:
                spans.append((pos, a))
            pos = max(pos, b)
        if pos < end:
            spans.append((pos, end))
        for a, b in spans:
            start = round(t * rate)
            t += Fraction(b - a, src.sample_rate)
            out.append(Clip(src.id, a, b, start, round(t * rate)))
    return out


def duration(cl: Sequence[Clip]) -> int:
    return cl[-1].timeline_end if cl else 0


def to_timeline(
    cl: Sequence[Clip], sources: dict[int, SourceInfo], rate: int, source_id: int, sample: int
) -> int | None:
    """Where a source sample plays on the timeline (None: it was cut)."""
    for c in cl:
        if c.source_id == source_id and c.source_in <= sample < c.source_out:
            sr = sources[source_id].sample_rate
            return c.timeline_start + round(Fraction(sample - c.source_in, sr) * rate)
    return None


def to_source(
    cl: Sequence[Clip], sources: dict[int, SourceInfo], rate: int, t: int
) -> tuple[int, int] | None:
    """Which source sample plays at timeline sample t."""
    for c in cl:
        if c.timeline_start <= t < c.timeline_end:
            sr = sources[c.source_id].sample_rate
            return c.source_id, c.source_in + int(Fraction(t - c.timeline_start, rate) * sr)
    return None


def kept_seconds(regions: Sequence[Region], source_id: int, sr: int, a: float, b: float) -> float:
    """How much of [a, b) seconds of a source is still on the timeline."""
    lo, hi = round(a * sr), round(b * sr)
    cut = sum(max(0, min(hi, y) - max(lo, x)) for x, y in merged(regions, source_id))
    return (hi - lo - cut) / sr
