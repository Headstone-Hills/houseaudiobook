import { describe, expect, test, vi } from "vitest";
import type { TimelineClip, TimelineTrack, TimelineView } from "./engine";
import { Player } from "./player";
import { audible, clipAt, envelope, fit, follow, nextSnap, schedule, snapPoints, snapTo, timecode, toT, toX, zoom } from "./timeline";

function clip(over: Partial<TimelineClip> = {}): TimelineClip {
  return {
    ref: { source_id: 1, source_in: 0 },
    track_id: 1,
    source_id: 1,
    source_label: "ch01",
    source_in: 0,
    source_out: 30,
    timeline_start: 0,
    timeline_end: 30,
    gain_db: 0,
    fade_in: 0,
    fade_out: 0,
    muted: false,
    ...over,
  };
}

const NARR: TimelineTrack = { id: 1, name: "Narration", kind: "narration", gain_db: 0, pan: 0, mute: false, solo: false };
const ROOM: TimelineTrack = { id: 2, name: "Room", kind: "audio", gain_db: -6, pan: -0.5, mute: false, solo: false };

function view(over: Partial<TimelineView> = {}): TimelineView {
  return {
    rate: 16000,
    duration: 50,
    channels: 1,
    tracks: [NARR, ROOM],
    clips: [
      clip(),
      clip({ ref: { source_id: 2, source_in: 0 }, source_id: 2, source_label: "ch02", source_in: 5, source_out: 25, timeline_start: 30, timeline_end: 50 }),
      clip({ ref: { clip_id: 7 }, track_id: 2, source_id: 3, source_in: 0, source_out: 4, timeline_start: 10, timeline_end: 14, fade_in: 1 }),
    ],
    markers: [{ id: 1, label: "m", color: "blue", t: 12.5 }],
    issues: [{ id: 3, kind: "silence", severity: "medium", t: 21, color: "purple", label: "" }],
    removed: [],
    undo: null,
    redo: null,
    ...over,
  };
}

describe("viewport", () => {
  test("mapping, zoom around an anchor, follow, fit, timecode", () => {
    const v = { start: 10, pps: 100, width: 1000 };
    expect(toX(v, 12)).toBe(200);
    expect(toT(v, 200)).toBe(12);
    const z = zoom(v, 2, 12);
    expect(z.pps).toBe(200);
    expect(toX(z, 12)).toBeCloseTo(200); // the anchor stays put
    expect(follow(v, 25).start).toBeGreaterThan(10);
    expect(follow(v, 15)).toBe(v);
    expect(fit(100, 1000).pps).toBe(10);
    expect(timecode(3725.25)).toBe("1:02:05.3");
    expect(timecode(83.5)).toBe("1:23.5");
  });
});

describe("snapping", () => {
  const lanes = {
    words: [{ text: "The", t0: 1.0, t1: 1.3 }],
    sentences: [{ ordinal: 0, t0: 1.0, t1: 4.0 }],
    pauses: [{ t0: 4.0, t1: 6.0 }],
  };
  test("points include clip edges, words, sentences, pause middles, markers, issues", () => {
    const pts = snapPoints(view(), lanes);
    for (const t of [0, 1.0, 1.3, 4.0, 5.0, 6.0, 10, 12.5, 14, 21, 30, 50]) expect(pts).toContain(t);
    expect(pts).toEqual([...pts].sort((a, b) => a - b));
    expect(nextSnap(pts, 4.0, 1)).toBe(5.0);
    expect(nextSnap(pts, 4.0, -1)).toBe(1.3);
    expect(snapTo(pts, 4.96, 0.1)).toBe(5.0);
    expect(snapTo(pts, 7.5, 0.1)).toBe(7.5);
    expect(snapPoints(view(), lanes, new Set(["silence"]))).not.toContain(1.3);
  });
});

describe("clips and the schedule", () => {
  test("clipAt and solo/mute", () => {
    const v = view();
    expect(clipAt(v.clips, 31, 1)?.source_label).toBe("ch02");
    expect(clipAt(v.clips, 31, 2)).toBeNull();
    expect(audible(v.clips[2]!, v.tracks)).toBe(true);
    expect(audible(v.clips[2]!, [NARR, { ...ROOM, mute: true }])).toBe(false);
    expect(audible(v.clips[0]!, [NARR, { ...ROOM, solo: true }])).toBe(false);
    expect(audible(clip({ muted: true }), v.tracks)).toBe(false);
  });

  test("envelope: clip x track gain, linear fades", () => {
    const c = clip({ gain_db: -6, fade_in: 2, fade_out: 4, timeline_end: 30 });
    const env = envelope(c, { ...NARR, gain_db: 6 }, 0, 30);
    expect(env.map(([t]) => t)).toEqual([0, 2, 26, 30]);
    expect(env.map(([, g]) => Number(g!.toFixed(6)))).toEqual([0, 1, 1, 0]);
    expect(envelope(clip({ gain_db: -20 }), NARR, 5, 8)).toEqual([
      [5, 0.1],
      [8, 0.1],
    ]);
  });

  test("schedule: audible clips in 10 s chunks, with track gain and pan", () => {
    const ev = schedule(view(), 8, 33);
    expect(ev.map((e) => [e.sourceId, e.when, e.sourceStart, e.duration])).toEqual([
      [1, 0, 0, 10],
      [1, 10, 10, 10],
      [3, 10, 0, 4],
      [1, 20, 20, 10],
      [2, 30, 5, 10],
    ]);
    const room = ev.find((e) => e.sourceId === 3)!;
    expect(room.pan).toBe(-0.5);
    expect(room.gain[0]).toEqual([10, 0]); // fading in
    expect(room.gain.at(-1)![1]).toBeCloseTo(10 ** (-6 / 20));
    // Stable chunk keys: re-scheduling a moment later finds the same chunks.
    expect(schedule(view(), 9, 33).map((e) => e.key)).toEqual(ev.map((e) => e.key));
  });
});

// -- the Web Audio player, with a fake context ------------------------------------------------------

class FakeParam {
  calls: unknown[][] = [];
  value = 1;
  setValueAtTime(...a: unknown[]) {
    this.calls.push(["set", ...a]);
  }
  linearRampToValueAtTime(...a: unknown[]) {
    this.calls.push(["ramp", ...a]);
  }
}
class FakeNode {
  connected: unknown[] = [];
  connect(n: unknown) {
    this.connected.push(n);
    return n;
  }
}
class FakeSource extends FakeNode {
  buffer: unknown = null;
  started: number[] = [];
  onended: (() => void) | null = null;
  start(...a: number[]) {
    this.started = a;
  }
  stop() {}
}
class FakeContext {
  currentTime = 0;
  state = "running";
  destination = new FakeNode();
  sources: FakeSource[] = [];
  gains: { gain: FakeParam }[] = [];
  resume() {
    return Promise.resolve();
  }
  createBufferSource() {
    const s = new FakeSource();
    this.sources.push(s);
    return s;
  }
  createGain() {
    const g = Object.assign(new FakeNode(), { gain: new FakeParam() });
    this.gains.push(g);
    return g;
  }
  createChannelMerger() {
    return new FakeNode();
  }
  createChannelSplitter() {
    return new FakeNode();
  }
}

describe("Player", () => {
  test("schedules the placed clips from fetched excerpts, at the right times", async () => {
    const ctx = new FakeContext();
    const fetched: string[] = [];
    const player = new Player(
      (_c, ev) => {
        fetched.push(`${ev.sourceId}@${ev.sourceStart}+${ev.duration}`);
        return Promise.resolve({ numberOfChannels: 1 } as AudioBuffer);
      },
      () => undefined,
      () => ctx as unknown as AudioContext,
    );
    player.setView(view());
    vi.useFakeTimers();
    await player.play(8);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetched).toEqual(["1@0+10", "1@10+10", "3@0+4"]); // 8 s ahead
    const [first] = ctx.sources;
    // Starts 0.15 s from now, 8 s into the first chunk.
    expect(first!.started.map((x) => Number(x.toFixed(6)))).toEqual([0.15, 8, 2]);
    expect(player.playing).toBe(true);
    ctx.currentTime = 1.15;
    expect(player.position()).toBeCloseTo(9);
    player.stop();
    expect(player.playing).toBe(false);
    expect(player.position()).toBeCloseTo(9);
    vi.useRealTimers();
  });
});
