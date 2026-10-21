// Pure helpers for the timeline editor (M7): time <-> pixels, zoom, snapping,
// clip lookup, and the playback schedule that the Web Audio preview follows.

import type { Lanes, TimelineClip, TimelineTrack, TimelineView } from "./engine";

export interface Viewport {
  start: number; // seconds at the left edge
  pps: number; // pixels per second
  width: number; // pixels
}

export const MIN_PPS = 0.5; // a 10 h book fits in ~5 screens
export const MAX_PPS = 2000; // ~8 samples per pixel at 16 kHz

export const toX = (v: Viewport, t: number) => (t - v.start) * v.pps;
export const toT = (v: Viewport, x: number) => v.start + x / v.pps;
export const visibleEnd = (v: Viewport) => v.start + v.width / v.pps;

/** Zoom by `factor` keeping time `anchor` at the same pixel. */
export function zoom(v: Viewport, factor: number, anchor: number): Viewport {
  const pps = Math.min(MAX_PPS, Math.max(MIN_PPS, v.pps * factor));
  const x = toX(v, anchor);
  return { ...v, pps, start: Math.max(0, anchor - x / pps) };
}

/** Scroll so time t is visible (with a margin), as the playhead moves. */
export function follow(v: Viewport, t: number): Viewport {
  const end = visibleEnd(v);
  const margin = (v.width / v.pps) * 0.1;
  if (t < v.start) return { ...v, start: Math.max(0, t - margin) };
  if (t > end - margin) return { ...v, start: Math.max(0, t - (v.width / v.pps) * 0.5) };
  return v;
}

export function fit(duration: number, width: number): Viewport {
  const pps = Math.min(MAX_PPS, Math.max(MIN_PPS, width / Math.max(1, duration)));
  return { start: 0, pps, width };
}

/** Show [start, end] with a little room either side (a chapter, D-059). */
export function fitRange(start: number, end: number, width: number): Viewport {
  const pad = Math.max(0.5, (end - start) * 0.03);
  const a = Math.max(0, start - pad);
  const pps = Math.min(MAX_PPS, Math.max(MIN_PPS, width / Math.max(1, end + pad - a)));
  return { start: a, pps, width };
}

/** "1:02:03.4" or "2:03.4". */
export function timecode(t: number): string {
  const s = Math.max(0, t);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

// -- snapping -------------------------------------------------------------------------------

export type SnapKind = "clip" | "word" | "sentence" | "silence" | "marker";

/** Times the tools snap to (SPEC §6.6: words, sentences, silences, markers),
 * plus clip edges, sorted and de-duplicated. */
export function snapPoints(view: TimelineView, lanes: Lanes | null, kinds?: Set<SnapKind>): number[] {
  const on = (k: SnapKind) => !kinds || kinds.has(k);
  const pts: number[] = [0, view.duration];
  if (on("clip")) view.clips.forEach((c) => pts.push(c.timeline_start, c.timeline_end));
  if (on("marker")) {
    view.markers.forEach((m) => m.t !== null && pts.push(m.t));
    view.issues.forEach((i) => pts.push(i.t));
  }
  if (lanes) {
    if (on("word")) lanes.words.forEach((w) => pts.push(w.t0, w.t1));
    if (on("sentence")) lanes.sentences.forEach((s) => pts.push(s.t0, s.t1));
    // A cut is best in the middle of a pause; its edges are where speech starts/ends.
    if (on("silence")) lanes.pauses.forEach((p) => pts.push(p.t0, (p.t0 + p.t1) / 2, p.t1));
  }
  pts.sort((a, b) => a - b);
  return pts.filter((t, i) => i === 0 || t - pts[i - 1]! > 1e-4);
}

/** The next snap point after t (dir 1) or before it (dir -1). */
export function nextSnap(points: number[], t: number, dir: 1 | -1): number {
  if (dir > 0) return points.find((p) => p > t + 1e-4) ?? t;
  for (let i = points.length - 1; i >= 0; i--) if (points[i]! < t - 1e-4) return points[i]!;
  return t;
}

/** Snap t to the nearest point within `tolerance` seconds (else t). */
export function snapTo(points: number[], t: number, tolerance: number): number {
  let best = t;
  let dist = tolerance;
  for (const p of points) {
    const d = Math.abs(p - t);
    if (d <= dist) {
      best = p;
      dist = d;
    }
  }
  return best;
}

// -- clips -------------------------------------------------------------------------------------

export const clipKey = (c: TimelineClip) =>
  c.ref.clip_id != null ? `c${c.ref.clip_id}` : `n${c.ref.source_id}:${c.ref.source_in}`;

export function clipAt(clips: TimelineClip[], t: number, trackId: number): TimelineClip | null {
  return clips.find((c) => c.track_id === trackId && c.timeline_start <= t && t < c.timeline_end) ?? null;
}

export function trackClips(clips: TimelineClip[], trackId: number): TimelineClip[] {
  return clips.filter((c) => c.track_id === trackId).sort((a, b) => a.timeline_start - b.timeline_start);
}

/** With any track soloed, only soloed tracks play (SPEC §6.6). */
export function audible(c: TimelineClip, tracks: TimelineTrack[]): boolean {
  const tr = tracks.find((t) => t.id === c.track_id);
  if (c.muted || tr?.mute) return false;
  return tracks.some((t) => t.solo) ? Boolean(tr?.solo) : true;
}

// -- playback schedule ---------------------------------------------------------------------------

export interface PlayEvent {
  key: string; // cache key for the audio chunk
  sourceId: number;
  sourceStart: number; // seconds in the recording
  duration: number; // seconds
  when: number; // timeline seconds when it starts
  gain: number[][]; // [timeline seconds, linear gain] breakpoints, ramped linearly
  pan: number;
}

const CHUNK_S = 10;

const db = (g: number) => 10 ** (g / 20);

/** The gain envelope of a clip (clip gain x track gain x fades), as
 * breakpoints for [t0, t1): the same linear fades the server render uses. */
export function envelope(c: TimelineClip, track: TimelineTrack | undefined, t0: number, t1: number): number[][] {
  const g = db(c.gain_db + (track?.gain_db ?? 0));
  const at = (t: number) => {
    let v = g;
    if (c.fade_in > 0 && t < c.timeline_start + c.fade_in) v *= (t - c.timeline_start) / c.fade_in;
    if (c.fade_out > 0 && t > c.timeline_end - c.fade_out) v *= (c.timeline_end - t) / c.fade_out;
    return Math.max(0, v);
  };
  const ts = [t0, t1, c.timeline_start + c.fade_in, c.timeline_end - c.fade_out].filter((t) => t >= t0 && t <= t1);
  return [...new Set(ts)].sort((a, b) => a - b).map((t) => [t, at(t)]);
}

/** What to play in [from, until): audible clips cut into chunks of at most
 * 10 s (fetched as excerpts and decoded ahead of the playhead). */
export function schedule(view: TimelineView, from: number, until: number): PlayEvent[] {
  const out: PlayEvent[] = [];
  for (const c of view.clips) {
    if (!audible(c, view.tracks) || c.timeline_end <= from || c.timeline_start >= until) continue;
    const track = view.tracks.find((t) => t.id === c.track_id);
    // Chunks on a fixed grid of the clip, so re-scheduling finds the same chunks.
    const first = Math.max(0, Math.floor((from - c.timeline_start) / CHUNK_S));
    for (let k = first; c.timeline_start + k * CHUNK_S < Math.min(until, c.timeline_end); k++) {
      const t0 = c.timeline_start + k * CHUNK_S;
      const t1 = Math.min(c.timeline_end, t0 + CHUNK_S);
      const offset = t0 - c.timeline_start;
      out.push({
        key: `${c.source_id}:${(c.source_in + offset).toFixed(4)}:${(t1 - t0).toFixed(4)}`,
        sourceId: c.source_id,
        sourceStart: c.source_in + offset,
        duration: t1 - t0,
        when: t0,
        gain: envelope(c, track, t0, t1),
        pan: track?.pan ?? 0,
      });
    }
  }
  return out.sort((a, b) => a.when - b.when);
}
