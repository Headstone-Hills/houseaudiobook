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


def clips(sources: Sequence[SourceInfo], regions: Sequence[Region], rate: int) -> list[Clip]:
    out: list[Clip] = []
    # Exact cumulative timeline time, so rounding never accumulates across clips.
    t = Fraction(0)
    for s in sources:
        cuts = [(max(0, a), min(s.num_frames, b)) for a, b in merged(regions, s.id)]
        pos = 0
        spans: list[tuple[int, int]] = []
        for a, b in cuts:
            if a > pos:
                spans.append((pos, a))
            pos = max(pos, b)
        if pos < s.num_frames:
            spans.append((pos, s.num_frames))
        for a, b in spans:
            start = round(t * rate)
            t += Fraction(b - a, s.sample_rate)
            out.append(Clip(s.id, a, b, start, round(t * rate)))
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
