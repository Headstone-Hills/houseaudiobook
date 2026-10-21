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
from dataclasses import dataclass, replace
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


NARRATION = 1  # the narration track's id (always present)


@dataclass(frozen=True)
class Clip:
    """A placed clip: what plays where. Narration clips are derived from segments
    minus removed regions; other tracks' clips are stored as they are (M7)."""

    source_id: int
    source_in: int  # samples at the source rate, [in, out)
    source_out: int
    timeline_start: int  # samples at the timeline rate
    timeline_end: int
    track_id: int = NARRATION
    gain_db: float = 0.0
    fade_in: int = 0  # timeline samples
    fade_out: int = 0
    muted: bool = False
    clip_id: int | None = None  # a free clip's id (other tracks)
    segment: int | None = None  # the narration segment it comes from (index)


@dataclass(frozen=True)
class Track:
    id: int
    name: str
    kind: Literal["narration", "audio"] = "audio"
    gain_db: float = 0.0
    pan: float = 0.0  # -1 left .. +1 right
    mute: bool = False
    solo: bool = False


@dataclass(frozen=True)
class FreeClip:
    """A clip on a track other than the narration: at a fixed timeline position,
    not rippled by narration edits (as in Audacity)."""

    id: int
    track_id: int
    source_id: int
    source_in: int  # source samples
    source_out: int
    timeline_start: int  # timeline samples
    gain_db: float = 0.0
    fade_in: int = 0  # source samples
    fade_out: int = 0
    muted: bool = False


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
    """A stretch of one recording, in timeline order (M6, D-043), with the clip
    attributes of what it plays (M7, D-046). Fades are in source samples and
    apply at the start of its first clip and the end of its last."""

    source_id: int
    source_in: int
    source_out: int
    gain_db: float = 0.0
    fade_in: int = 0
    fade_out: int = 0
    muted: bool = False
    keep: bool = False  # a split: never merge with the next segment

    def with_span(self, a: int, b: int, *, first: bool, last: bool) -> Segment:
        """A piece of this segment: fades stay only on the outer pieces."""
        return replace(
            self,
            source_in=a,
            source_out=b,
            fade_in=self.fade_in if first else 0,
            fade_out=self.fade_out if last else 0,
            keep=self.keep if last else False,
        )


def default_segments(sources: Sequence[SourceInfo]) -> list[Segment]:
    """Each recording once, in order: the timeline before anything is moved."""
    return [Segment(s.id, 0, s.num_frames) for s in sources]


def _joins(a: Segment, b: Segment) -> bool:
    return (
        a.source_id == b.source_id
        and a.source_out == b.source_in
        and not a.keep
        and a.gain_db == b.gain_db
        and a.muted == b.muted
        and a.fade_out == 0
        and b.fade_in == 0
    )


def normalize(segs: Sequence[Segment]) -> list[Segment]:
    """Drop empty segments and merge neighbours that continue the same recording
    with the same attributes (and no split between), so moving a piece away and
    back leaves exactly the segments it started with."""
    out: list[Segment] = []
    for s in segs:
        if s.source_out <= s.source_in:
            continue
        if out and _joins(out[-1], s):
            out[-1] = replace(s, source_in=out[-1].source_in, fade_in=out[-1].fade_in)
        else:
            out.append(s)
    return out


def _without(segs: Sequence[Segment], source_id: int, a: int, b: int) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        if s.source_id != source_id or s.source_out <= a or s.source_in >= b:
            out.append(s)
            continue
        out.append(s.with_span(s.source_in, max(s.source_in, a), first=True, last=False))
        out.append(s.with_span(min(s.source_out, b), s.source_out, first=False, last=True))
    return [s for s in out if s.source_out > s.source_in]


def _covering(segs: Sequence[Segment], source_id: int, x: int) -> int | None:
    return next(
        (
            i
            for i, s in enumerate(segs)
            if s.source_id == source_id and s.source_in <= x < s.source_out
        ),
        None,
    )


def split(segs: Sequence[Segment], source_id: int, x: int) -> list[Segment]:
    """Split the segment playing sample x of a recording into two clips that stay
    separate (so each can be moved, faded or gained on its own)."""
    out = list(segs)
    i = _covering(out, source_id, x)
    if i is None or out[i].source_in == x:
        if i is not None and i > 0:
            out[i - 1] = replace(out[i - 1], keep=True)  # a split on an existing edge
        return out
    s = out[i]
    left = s.with_span(s.source_in, x, first=True, last=False)
    out[i : i + 1] = [
        replace(left, keep=True),
        s.with_span(x, s.source_out, first=False, last=True),
    ]
    return out


def set_attrs(
    segs: Sequence[Segment], source_id: int, a: int, b: int, **attrs: object
) -> list[Segment]:
    """Set clip attributes on [a, b) of a recording (splitting at a and b)."""
    out = split(split(segs, source_id, a), source_id, b)
    for i, s in enumerate(out):
        if s.source_id == source_id and s.source_in >= a and s.source_out <= b:
            out[i] = replace(s, **attrs)  # type: ignore[arg-type]
    return out


def place(
    segs: Sequence[Segment],
    source_id: int,
    a: int,
    b: int,
    before: tuple[int, int] | None = None,
) -> list[Segment]:
    """Move [a, b) of a recording so it plays just before `before` = (source,
    sample); None puts it back where it naturally belongs in its own recording."""
    home = _covering(segs, source_id, a)
    attrs = segs[home] if home is not None else None
    rest = _without(segs, source_id, a, b)
    # The moved piece keeps the gain and mute of where it came from (not fades).
    piece = Segment(
        source_id,
        a,
        b,
        attrs.gain_db if attrs else 0.0,
        muted=attrs.muted if attrs else False,
    )
    at: int | None = None
    if before is not None:
        bs, x = before
        for i, s in enumerate(rest):
            if s.source_id == bs and s.source_in <= x < s.source_out:
                head = s.with_span(s.source_in, x, first=True, last=False)
                tail = s.with_span(x, s.source_out, first=False, last=True)
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
    for index, seg in enumerate(segs):
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
        for k, (a, b) in enumerate(spans):
            start = round(t * rate)
            t += Fraction(b - a, src.sample_rate)
            fi = seg.fade_in if k == 0 else 0
            fo = seg.fade_out if k == len(spans) - 1 else 0
            out.append(
                Clip(
                    src.id,
                    a,
                    b,
                    start,
                    round(t * rate),
                    NARRATION,
                    seg.gain_db,
                    round(Fraction(fi * rate, src.sample_rate)),
                    round(Fraction(fo * rate, src.sample_rate)),
                    seg.muted,
                    None,
                    index,
                )
            )
    return out


def free_clips(sources: Sequence[SourceInfo], frees: Sequence[FreeClip], rate: int) -> list[Clip]:
    by_id = {s.id: s for s in sources}
    out = []
    for f in frees:
        src = by_id.get(f.source_id)
        if src is None or f.source_out <= f.source_in:
            continue
        n = round(Fraction(f.source_out - f.source_in, src.sample_rate) * rate)
        out.append(
            Clip(
                f.source_id,
                f.source_in,
                f.source_out,
                f.timeline_start,
                f.timeline_start + n,
                f.track_id,
                f.gain_db,
                round(Fraction(f.fade_in * rate, src.sample_rate)),
                round(Fraction(f.fade_out * rate, src.sample_rate)),
                f.muted,
                f.id,
            )
        )
    return sorted(out, key=lambda c: (c.track_id, c.timeline_start))


def layout(
    sources: Sequence[SourceInfo],
    regions: Sequence[Region],
    rate: int,
    segments: Sequence[Segment] | None = None,
    frees: Sequence[FreeClip] = (),
) -> list[Clip]:
    """Every placed clip on every track: the narration first, then the others.
    The server render and the browser preview both play exactly this."""
    return clips(sources, regions, rate, segments) + free_clips(sources, frees, rate)


def audible(c: Clip, tracks: dict[int, Track]) -> bool:
    """Mute and solo (SPEC §6.6): with any track soloed, only soloed tracks play."""
    tr = tracks.get(c.track_id)
    if c.muted or (tr is not None and tr.mute):
        return False
    if any(t.solo for t in tracks.values()):
        return tr is not None and tr.solo
    return True


def duration(cl: Sequence[Clip]) -> int:
    return max((c.timeline_end for c in cl), default=0)


def narration(cl: Sequence[Clip]) -> list[Clip]:
    return [c for c in cl if c.track_id == NARRATION]


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
