// Realtime preview from the EDL (SPEC §9 M7): Web Audio plays the placed clips
// straight from excerpts of the original recordings, with the clip and track
// gain, fades and pan applied by audio nodes. Nothing is rendered in advance,
// so an edit is heard the next time playback starts. The join crossfades of the
// server render (10 ms) are left out; "Render preview" plays the exact mix.

import { excerptUrl, type TimelineView } from "./engine";
import { schedule, type PlayEvent } from "./timeline";

const LOOKAHEAD_S = 8; // audio fetched and scheduled ahead of the playhead
const START_DELAY_S = 0.15;

export type Fetcher = (ctx: BaseAudioContext, ev: PlayEvent) => Promise<AudioBuffer>;

const fetchExcerpt: Fetcher = async (ctx, ev) => {
  const r = await fetch(excerptUrl(ev.sourceId, ev.sourceStart, ev.sourceStart + ev.duration, 0));
  if (!r.ok) throw new Error(`audio unavailable (${r.status})`);
  return ctx.decodeAudioData(await r.arrayBuffer());
};

export class Player {
  private ctx: AudioContext | null = null;
  private buffers = new Map<string, Promise<AudioBuffer>>();
  private nodes: AudioScheduledSourceNode[] = [];
  private scheduled = new Set<string>();
  private timer: ReturnType<typeof setInterval> | null = null;
  private ctxAtStart = 0;
  private tAtStart = 0;
  playing = false;
  private current: TimelineView | null = null;

  constructor(
    private fetcher: Fetcher = fetchExcerpt,
    private onError: (msg: string) => void = () => undefined,
    private makeContext: () => AudioContext = () => new AudioContext(),
  ) {}

  /** The timeline to play (the latest view after each edit). */
  setView(view: TimelineView | null): void {
    this.current = view;
  }

  /** Timeline seconds now playing (or where playback stopped). */
  position(): number {
    if (!this.playing || !this.ctx) return this.tAtStart;
    return Math.max(this.tAtStart, this.tAtStart + this.ctx.currentTime - this.ctxAtStart);
  }

  async play(from: number): Promise<void> {
    this.stop();
    this.ctx ??= this.makeContext();
    if (this.ctx.state === "suspended") await this.ctx.resume();
    this.tAtStart = from;
    this.ctxAtStart = this.ctx.currentTime + START_DELAY_S;
    this.playing = true;
    this.fill();
    this.timer = setInterval(() => this.fill(), 500);
  }

  stop(): void {
    if (this.playing) this.tAtStart = this.position();
    this.playing = false;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    for (const n of this.nodes) {
      try {
        n.stop();
      } catch {
        // already stopped
      }
    }
    this.nodes = [];
    this.scheduled.clear();
  }

  /** Drop decoded audio (e.g. after a project change). */
  forget(): void {
    this.buffers.clear();
  }

  private fill(): void {
    const view = this.current;
    const ctx = this.ctx;
    if (!view || !ctx || !this.playing) return;
    const now = this.position();
    if (now >= view.duration) {
      this.stop();
      return;
    }
    for (const ev of schedule(view, now, now + LOOKAHEAD_S)) {
      const id = `${ev.key}@${ev.when.toFixed(4)}`;
      if (this.scheduled.has(id) || ev.when + ev.duration <= now) continue;
      this.scheduled.add(id);
      let buf = this.buffers.get(ev.key);
      if (!buf) {
        buf = this.fetcher(ctx, ev);
        this.buffers.set(ev.key, buf);
        buf.catch((e: unknown) => {
          this.buffers.delete(ev.key);
          this.onError(e instanceof Error ? e.message : String(e));
        });
      }
      const started = this.ctxAtStart;
      buf
        .then((b) => {
          if (this.playing && started === this.ctxAtStart) this.start(ctx, b, ev);
        })
        .catch(() => undefined);
    }
  }

  private start(ctx: AudioContext, buffer: AudioBuffer, ev: PlayEvent): void {
    const toCtx = (t: number) => this.ctxAtStart + (t - this.tAtStart);
    const at = toCtx(ev.when);
    // A chunk that began before the playhead (or arrived late) joins in part-way:
    // it starts when playback does (or now) with the matching offset into it.
    const begin = Math.max(at, this.ctxAtStart, ctx.currentTime);
    const late = begin - at;
    if (late >= ev.duration) return;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const gain = ctx.createGain();
    const [first, ...rest] = ev.gain;
    gain.gain.setValueAtTime(first?.[1] ?? 1, begin);
    for (const [t, v] of rest) gain.gain.linearRampToValueAtTime(v!, toCtx(t!));
    src.connect(gain);
    if (ev.pan) {
      // The server's balance law (D-047), not StereoPanner's equal-power law:
      // unity at centre; panning fades one side and leaves the other as it is.
      const merger = ctx.createChannelMerger(2);
      const sides = [Math.min(1, 1 - ev.pan), Math.min(1, 1 + ev.pan)];
      const splitter = buffer.numberOfChannels > 1 ? ctx.createChannelSplitter(2) : null;
      if (splitter) gain.connect(splitter);
      sides.forEach((level, ch) => {
        const g = ctx.createGain();
        g.gain.value = level;
        if (splitter) splitter.connect(g, ch);
        else gain.connect(g); // mono: the same signal feeds both sides
        g.connect(merger, 0, ch);
      });
      merger.connect(ctx.destination);
    } else {
      gain.connect(ctx.destination);
    }
    src.start(begin, late, ev.duration - late);
    this.nodes.push(src);
    src.onended = () => {
      this.nodes = this.nodes.filter((n) => n !== src);
    };
  }
}
