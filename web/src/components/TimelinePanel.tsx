import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import {
  api,
  excerptUrl,
  renderUrl,
  type ClipRef,
  type Lanes,
  type PeakSettings,
  type Source,
  type TimelineClip,
  type TimelineView,
} from "../state/engine";
import { Player } from "../state/player";
import {
  clipAt,
  clipKey,
  fit,
  fitRange,
  follow,
  nextSnap,
  snapPoints,
  snapTo,
  timecode,
  toT,
  toX,
  trackClips,
  visibleEnd,
  zoom,
  type Viewport,
} from "../state/timeline";

const RULER_H = 22;
const MARKER_H = 16;
const TRACK_H = 64;
const CUT_KIND: Record<string, string> = {
  stutter: "a stutter (cleanup)",
  false_start: "a false start (cleanup)",
  commentary: "off-script speech (cleanup)",
  pause: "a shortened pause",
  cut: "your cut",
  take: "another take",
};
const REMOVED_H = 22; // the cuts lane: a ✂ tab per cut, to click
const WORDS_H = 22;
const EDGE_PX = 6;
const FADE_PX = 10;
const NUDGE_S = 0.05;
const MAX_LANES_S = 900;
const PEAK_CHUNK = 8192;

interface Props {
  sources: Source[];
  peakSettings: PeakSettings;
  revision: number;
  /** A chapter's place on the timeline: shown first, and what Fit fits (D-059). */
  focus?: { start: number; end: number } | null;
  onEdited: () => void;
  onError: (msg: string) => void;
}

type Drag =
  | { kind: "move"; clip: TimelineClip; dx: number; trackId: number; t: number }
  | { kind: "trim"; clip: TimelineClip; edge: "start" | "end"; t: number }
  | { kind: "fade"; clip: TimelineClip; edge: "in" | "out"; t: number }
  | { kind: "select"; t0: number; t: number };

/** Multitrack timeline editor (SPEC §6.6). Keyboard first: every tool has a key
 * (see the key list under the toolbar); the mouse does the same by dragging. */
export function TimelinePanel({ sources, peakSettings, revision, focus = null, onEdited, onError }: Props) {
  const [view, setView] = useState<TimelineView | null>(null);
  const [lanes, setLanes] = useState<Lanes | null>(null);
  const [vp, setVp] = useState<Viewport>({ start: 0, pps: 20, width: 1000 });
  const [playhead, setPlayhead] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [trackId, setTrackId] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [cutId, setCutId] = useState<number | null>(null); // the cut being adjusted
  const [snap, setSnap] = useState(true);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [status, setStatus] = useState("");
  const [peaksTick, setPeaksTick] = useState(0);
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [player] = useState(() => new Player(undefined, onError));
  useEffect(() => player.setView(view), [player, view]);
  const peaks = useRef(new Map<string, Promise<{ min: number[]; max: number[] }> | { min: number[]; max: number[] }>());
  const fail = useCallback((e: unknown) => onError(e instanceof Error ? e.message : String(e)), [onError]);

  // -- data ---------------------------------------------------------------------------------

  // The first load fits the chapter (or the book); later loads keep the view.
  const focusRef = useRef(focus);
  useEffect(() => {
    focusRef.current = focus;
  }, [focus]);
  useEffect(() => {
    let cancelled = false;
    api
      .timeline()
      .then((v) => {
        if (cancelled) return;
        setView(v);
        const f = focusRef.current;
        setVp((old) =>
          old.start === 0 && old.pps === 20
            ? f
              ? fitRange(f.start, f.end, old.width)
              : fit(v.duration, old.width)
            : old,
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision]);

  // Another chapter chosen: go to it (adjusting state as the prop changes, not in an effect).
  const focusKey = focus ? `${focus.start}:${focus.end}` : "";
  const [seenFocus, setSeenFocus] = useState(focusKey);
  if (focusKey !== seenFocus) {
    setSeenFocus(focusKey);
    if (focus) {
      setVp((v) => fitRange(focus.start, focus.end, v.width));
      setPlayhead(focus.start);
    }
  }
  const fitAll = (v: Viewport) =>
    focus ? fitRange(focus.start, focus.end, v.width) : fit(view?.duration ?? 0, v.width);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setVp((v) => ({ ...v, width: Math.max(300, el.clientWidth - 2) })));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // The transcript and snap lanes for what's on screen.
  const laneWindow = useMemo(() => {
    const a = Math.max(0, vp.start - 5);
    return [a, Math.min(a + MAX_LANES_S, visibleEnd(vp) + 5)] as const;
  }, [vp]);
  useEffect(() => {
    if (!view) return;
    let cancelled = false;
    const id = setTimeout(() => {
      api
        .lanes(laneWindow[0], laneWindow[1])
        .then((l) => !cancelled && setLanes(l))
        .catch(() => undefined);
    }, 120);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
  }, [view, laneWindow]);

  const points = useMemo(() => (view ? snapPoints(view, lanes) : []), [view, lanes]);
  const clips = useMemo(() => view?.clips ?? [], [view]);
  const tracks = useMemo(() => view?.tracks ?? [], [view]);
  const current = clips.find((c) => clipKey(c) === selected) ?? null;
  // Cuts on the timeline, in order: where audio was taken out (a join).
  const cuts = useMemo(
    () =>
      (view?.removed ?? [])
        .filter((r) => r.t !== null && r.status === "removed")
        .sort((a, b) => a.t! - b.t!),
    [view],
  );
  const cut = cuts.find((r) => r.id === cutId) ?? null;

  // Edits run one after another, each built from the timeline as the previous
  // one left it: pressing + twice quickly gives +2 dB, not +1 twice.
  const latest = useRef<TimelineView | null>(null);
  useEffect(() => {
    latest.current = view;
  }, [view]);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const edit = (make: (v: TimelineView) => Promise<TimelineView> | null, what?: string) => {
    queue.current = queue.current.then(async () => {
      const v = latest.current;
      const p = v ? make(v) : null;
      if (!p) return;
      try {
        const next = await p;
        latest.current = next;
        setView(next);
        if (what) setStatus(what);
        onEdited();
      } catch (e) {
        fail(e);
      }
    });
  };

  // -- playback -----------------------------------------------------------------------------

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const pl = player;
      const t = pl.position();
      setPlayhead(t);
      setVp((v) => follow(v, t));
      if (!pl.playing) setPlaying(false);
      else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, player]);

  useEffect(() => () => player.stop(), [player]);

  const togglePlay = () => {
    const pl = player;
    if (pl.playing) {
      pl.stop();
      setPlaying(false);
      setPlayhead(pl.position());
    } else {
      pl.play(playhead)
        .then(() => setPlaying(true))
        .catch(fail);
    }
  };

  const seek = (t: number) => {
    const d = view?.duration ?? 0;
    const x = Math.max(0, Math.min(d, t));
    setPlayhead(x);
    setVp((v) => follow(v, x));
    if (player.playing) player.play(x).catch(fail);
  };

  // -- tools ----------------------------------------------------------------------------------

  const ref = (c: TimelineClip): ClipRef => c.ref;
  const selectClip = (c: TimelineClip | null) => {
    setSelected(c ? clipKey(c) : null);
    if (c) {
      setTrackId(c.track_id);
      setStatus(`${c.source_label} ${timecode(c.timeline_start)}–${timecode(c.timeline_end)}`);
    }
  };
  const clipUnderPlayhead = () => current ?? clipAt(clips, playhead, trackId);
  /** The same clip in a newer view: the selected one, else the one at the playhead. */
  const clipIn = (v: TimelineView) =>
    (selected ? v.clips.find((c) => clipKey(c) === selected) : undefined) ?? clipAt(v.clips, playhead, trackId);

  const tools = {
    split: () => edit(() => api.tl("split", { t: playhead, track_id: trackId }), "Split"),
    deleteSel: () => {
      if (sel && sel[1] > sel[0]) {
        const [a, b] = sel;
        edit(() => api.tl("ripple-delete", { start: a, end: b }), "Deleted selection");
        setSel(null);
      } else if (current?.ref.clip_id != null) {
        const id = current.ref.clip_id;
        edit(() => api.tlDelete(`clips/${id}`), "Deleted clip");
        setSelected(null);
      } else setStatus("Select a range ([ and ]) or a clip on another track to delete");
    },
    trim: (edge: "start" | "end") =>
      edit((v) => {
        const c = clipIn(v);
        return c ? api.tl("trim", { ref: ref(c), edge, t: playhead }) : null;
      }, `Trimmed ${edge}`),
    // A rolling edit on the chosen cut, or the nearest one (within 1 s): no need
    // to land the playhead exactly on the join.
    roll: (dir: number) => {
      const r = cut ?? nearestCut(1);
      if (!r) return setStatus("Choose a cut first: click a ✂ in the cuts lane, or press C");
      if (r.kind === "take") return setStatus("That cut is another take of a line: change it in Choose takes");
      setCutId(r.id);
      edit(() => api.rollRegion(r.id, dir * NUDGE_S).then(() => api.timeline()), dir < 0 ? "Moved the cut earlier" : "Moved the cut later");
    },
    /** Move one edge of the chosen cut: `by` seconds in the recording (+ later). */
    cutEdge: (edge: "start" | "end", by: number) => {
      if (!cut || cut.kind === "take") return;
      const bounds = edge === "start" ? { start: Math.max(0, cut.start + by) } : { end: Math.max(cut.start, cut.end + by) };
      edit(() => api.editRegion(cut.id, bounds).then(() => api.timeline()), `Cut ${edge} moved`);
    },
    restoreCut: () => {
      if (!cut || cut.kind === "take") return;
      const id = cut.id;
      setCutId(null);
      edit(() => api.setRegionStatus([id], "restored").then(() => api.timeline()), `Restored ${cut.kind.replace("_", " ")}`);
    },
    goCut: (dir: number) => {
      const next =
        dir > 0 ? cuts.find((r) => r.t! > playhead + 0.01) : [...cuts].reverse().find((r) => r.t! < playhead - 0.01);
      if (next) selectCut(next);
      else setStatus(dir > 0 ? "No more cuts after the playhead" : "No cuts before the playhead");
    },
    // To the track above or below, worked out from the queued view: right after
    // "add track" the new track may not have rendered yet (regression).
    moveTrack: (dir: number) =>
      edit((v) => {
        const c = clipIn(v);
        const to = c && v.tracks[v.tracks.findIndex((t) => t.id === c.track_id) + dir];
        return c && to ? api.tl("move", { ref: ref(c), t: c.timeline_start, track_id: to.id }) : null;
      }, "Moved clip"),
    moveTo: (c: TimelineClip, t: number, track: number) =>
      edit(() => api.tl("move", { ref: ref(c), t: Math.max(0, t), track_id: track }), "Moved clip"),
    gain: (delta: number) =>
      edit((v) => {
        const c = clipIn(v);
        return c ? api.tl("clip", { ref: ref(c), gain_db: Math.round((c.gain_db + delta) * 10) / 10 }) : null;
      }, "Clip gain"),
    fade: (which: "in" | "out") =>
      edit((v) => {
        const c = clipIn(v);
        if (!c) return null;
        const len = which === "in" ? playhead - c.timeline_start : c.timeline_end - playhead;
        return api.tl("clip", { ref: ref(c), [which === "in" ? "fade_in" : "fade_out"]: Math.max(0, len) });
      }, `Fade ${which}`),
    muteClip: () =>
      edit((v) => {
        const c = clipIn(v);
        return c ? api.tl("clip", { ref: ref(c), muted: !c.muted }) : null;
      }, "Clip mute"),
    marker: () => edit(() => api.tl("markers", { t: playhead, label: `Marker ${timecode(playhead)}` }), "Marker added"),
    addTrack: () => edit((v) => api.tl("tracks", { name: `Track ${v.tracks.length + 1}` }), "Track added"),
    track: (attr: "mute" | "solo") =>
      edit((v) => {
        const tr = v.tracks.find((t) => t.id === trackId);
        return tr ? api.tlPatch(`tracks/${tr.id}`, { [attr]: !tr[attr] }) : null;
      }, `Track ${attr}`),
    restoreNear: () => {
      const r = (view?.removed ?? [])
        .filter((x) => x.t !== null && x.status === "removed" && x.kind !== "take")
        .sort((a, b) => Math.abs(a.t! - playhead) - Math.abs(b.t! - playhead))[0];
      if (!r || Math.abs(r.t! - playhead) > 1) return setStatus("No removed material within 1 s of the playhead");
      edit(
        () => api.setRegionStatus([r.id], "restored").then(() => api.timeline()),
        `Restored ${r.kind.replace("_", " ")}`,
      );
    },
    renderPreview: () => {
      const [a, b] = sel && sel[1] > sel[0] ? sel : [playhead, playhead + 20];
      const el = new Audio(renderUrl(a, Math.min(b, a + 60)));
      el.play()?.catch(() => undefined);
      setStatus(`Rendering ${timecode(a)}–${timecode(b)} from the sources`);
    },
  };

  const playAcross = (t: number) => {
    const el = new Audio(renderUrl(t - 3, t + 3));
    el.play()?.catch(() => undefined);
    setStatus(`Playing 3 s either side of the cut at ${timecode(t)}`);
  };
  const playCut = (r: { source_id: number; start: number; end: number }) => {
    const el = new Audio(excerptUrl(r.source_id, r.start, r.end, 0.5));
    el.play()?.catch(() => undefined);
  };
  function nearestCut(within: number) {
    const r = cuts
      .filter((x) => x.kind !== "take")
      .sort((a, b) => Math.abs(a.t! - playhead) - Math.abs(b.t! - playhead))[0];
    return r && Math.abs(r.t! - playhead) <= within ? r : null;
  }
  function selectCut(r: (typeof cuts)[number]) {
    setCutId(r.id);
    seek(r.t!);
    setStatus(`Cut at ${timecode(r.t!)}: ${r.seconds.toFixed(2)} s taken out`);
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const tag = (e.target as HTMLElement).tagName;
    if (["INPUT", "SELECT", "TEXTAREA", "BUTTON"].includes(tag) || e.metaKey || e.ctrlKey) return;
    const k = e.key;
    const code = e.code;
    const step = e.shiftKey ? 10 : 1;
    const handled = (() => {
      if (k === " ") return togglePlay(), true;
      if (e.altKey && e.shiftKey && (k === "ArrowLeft" || k === "ArrowRight")) {
        const c = clipUnderPlayhead();
        if (!c) return true;
        const t = nextSnap(points, c.timeline_start, k === "ArrowLeft" ? -1 : 1);
        return tools.moveTo(c, t, c.track_id), true;
      }
      if (e.altKey && (k === "ArrowUp" || k === "ArrowDown")) return tools.moveTrack(k === "ArrowUp" ? -1 : 1), true;
      if (e.altKey && (k === "ArrowLeft" || k === "ArrowRight")) return seek(nextSnap(points, playhead, k === "ArrowLeft" ? -1 : 1)), true;
      switch (k) {
        case "ArrowLeft":
          return seek(playhead - step), true;
        case "ArrowRight":
          return seek(playhead + step), true;
        case "ArrowUp":
        case "ArrowDown": {
          const i = tracks.findIndex((t) => t.id === trackId);
          const to = tracks[Math.max(0, Math.min(tracks.length - 1, i + (k === "ArrowUp" ? -1 : 1)))];
          if (to) {
            setTrackId(to.id);
            setSelected(null);
            setStatus(`Track: ${to.name}`);
          }
          return true;
        }
        case "Home":
          return seek(0), true;
        case "End":
          return seek(view?.duration ?? 0), true;
        case "Tab": {
          const list = trackClips(clips, trackId);
          if (!list.length) return true;
          const i = current ? list.findIndex((c) => clipKey(c) === clipKey(current)) : -1;
          const next = list[(i + (e.shiftKey ? -1 : 1) + list.length) % list.length]!;
          selectClip(next);
          seek(next.timeline_start);
          return true;
        }
        case "Escape":
          setSel(null);
          setSelected(null);
          return true;
        case "Delete":
        case "Backspace":
          return tools.deleteSel(), true;
        case "s":
          return tools.split(), true;
        case "S":
          return tools.track("solo"), true;
        case "+":
        case "=":
          return tools.gain(1), true;
        case "-":
        case "_":
          return tools.gain(-1), true;
        case "f":
          return tools.fade("in"), true;
        case "g":
          return tools.fade("out"), true;
        case "u":
          return tools.muteClip(), true;
        case "U":
          return tools.track("mute"), true;
        case "m":
          return tools.marker(), true;
        case "t":
          return tools.addTrack(), true;
        case ",":
          return tools.roll(-1), true;
        case ".":
          return tools.roll(1), true;
        case "c":
          return tools.goCut(1), true;
        case "C":
          return tools.goCut(-1), true;
        case "R":
          return tools.restoreNear(), true;
        case "p":
          return tools.renderPreview(), true;
        case "z":
          return setVp((v) => zoom(v, 2, playhead)), true;
        case "Z":
          return setVp((v) => zoom(v, 0.5, playhead)), true;
        case "0":
          return view && setVp(fitAll), true;
        case "n":
          return setSnap((s) => !s), true;
      }
      // [ and ] (by key position, so Shift works on any layout): selection or trim.
      if (code === "BracketLeft" || code === "BracketRight") {
        const edge = code === "BracketLeft" ? "start" : "end";
        if (e.shiftKey) tools.trim(edge);
        else {
          setSel((s) => {
            const [a, b] = s ?? [playhead, playhead];
            const next: [number, number] = edge === "start" ? [playhead, Math.max(playhead, b)] : [Math.min(a, playhead), playhead];
            setStatus(`Selection ${timecode(next[0])}–${timecode(next[1])}`);
            return next;
          });
        }
        return true;
      }
      return false;
    })();
    if (handled) e.preventDefault();
  };

  // -- layout and drawing ------------------------------------------------------------------------

  const rows = useMemo(() => {
    const out: { kind: "ruler" | "markers" | "track" | "removed" | "words"; y: number; h: number; trackId?: number }[] = [];
    let y = 0;
    const push = (kind: (typeof out)[number]["kind"], h: number, id?: number) => {
      out.push({ kind, y, h, trackId: id });
      y += h;
    };
    push("ruler", RULER_H);
    push("markers", MARKER_H);
    tracks.forEach((t) => {
      push("track", TRACK_H, t.id);
      if (t.kind === "narration") {
        push("removed", REMOVED_H);
        push("words", WORDS_H);
      }
    });
    return { rows: out, height: y };
  }, [tracks]);

  const sourceById = useMemo(() => new Map(sources.map((s) => [s.id, s])), [sources]);

  /** Peaks for a source at a level, fetched in chunks as they come into view. */
  const peakRange = useCallback(
    (src: Source, level: number, b0: number, b1: number) => {
      const out: { min: number[]; max: number[]; first: number } = { min: [], max: [], first: b0 };
      for (let c = Math.floor(b0 / PEAK_CHUNK); c <= Math.floor(b1 / PEAK_CHUNK); c++) {
        const key = `${src.id}:${level}:${c}`;
        const got = peaks.current.get(key);
        if (!got) {
          const p = api.peaks(src.id, level, c * PEAK_CHUNK, PEAK_CHUNK).then((r) => {
            const v = { min: r.min, max: r.max };
            peaks.current.set(key, v);
            setPeaksTick((n) => n + 1);
            return v;
          });
          p.catch(() => peaks.current.delete(key));
          peaks.current.set(key, p);
        }
        const v = peaks.current.get(key);
        if (!v || v instanceof Promise) return null;
        const lo = Math.max(b0, c * PEAK_CHUNK);
        const hi = Math.min(b1, (c + 1) * PEAK_CHUNK);
        out.min.push(...v.min.slice(lo - c * PEAK_CHUNK, hi - c * PEAK_CHUNK));
        out.max.push(...v.max.slice(lo - c * PEAK_CHUNK, hi - c * PEAK_CHUNK));
      }
      return out;
    },
    [],
  );

  useEffect(() => {
    const c = canvas.current;
    const ctx = c?.getContext?.("2d");
    if (!c || !ctx || !view) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = vp.width * dpr;
    c.height = rows.height * dpr;
    c.style.width = `${vp.width}px`;
    c.style.height = `${rows.height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const css = getComputedStyle(c);
    const color = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
    const fg = color("--fg", "#222");
    const muted = color("--muted", "#888");
    const accent = color("--accent", "#2d5bd0");
    const wave = color("--wave", "#3b82f6");
    ctx.clearRect(0, 0, vp.width, rows.height);
    ctx.font = "11px system-ui, sans-serif";
    const end = visibleEnd(vp);

    for (const row of rows.rows) {
      if (row.kind === "ruler") {
        ctx.fillStyle = muted;
        const stepS = [0.1, 0.5, 1, 5, 10, 30, 60, 300, 600, 1800, 3600].find((s) => s * vp.pps >= 70) ?? 3600;
        for (let t = Math.floor(vp.start / stepS) * stepS; t < end; t += stepS) {
          const x = toX(vp, t);
          ctx.fillRect(x, row.y + row.h - 6, 1, 6);
          ctx.fillText(timecode(t), x + 3, row.y + 12);
        }
      } else if (row.kind === "markers") {
        for (const m of view.markers) {
          if (m.t === null) continue;
          ctx.fillStyle = m.color;
          ctx.fillRect(toX(vp, m.t) - 1, row.y, 3, row.h);
        }
        for (const i of view.issues) {
          ctx.fillStyle = i.color;
          const x = toX(vp, i.t);
          ctx.beginPath();
          ctx.moveTo(x - 5, row.y + 2);
          ctx.lineTo(x + 5, row.y + 2);
          ctx.lineTo(x, row.y + row.h - 2);
          ctx.fill();
        }
      } else if (row.kind === "track") {
        const tr = tracks.find((t) => t.id === row.trackId);
        if (row.trackId === trackId) {
          ctx.fillStyle = "rgba(59,130,246,0.06)";
          ctx.fillRect(0, row.y, vp.width, row.h);
        }
        for (const cl of clips) {
          if (cl.track_id !== row.trackId || cl.timeline_end < vp.start || cl.timeline_start > end) continue;
          const x0 = toX(vp, cl.timeline_start);
          const x1 = toX(vp, cl.timeline_end);
          const isSel = clipKey(cl) === selected;
          ctx.fillStyle = isSel ? "rgba(59,130,246,0.18)" : "rgba(127,127,127,0.10)";
          ctx.fillRect(x0, row.y + 2, x1 - x0, row.h - 4);
          ctx.strokeStyle = isSel ? wave : muted;
          ctx.strokeRect(x0 + 0.5, row.y + 2.5, Math.max(1, x1 - x0 - 1), row.h - 5);
          // Waveform from the peaks, at the level that fits this zoom.
          const src = sourceById.get(cl.source_id);
          const silent = cl.muted || tr?.mute;
          if (src) {
            const fpx = src.sample_rate / vp.pps; // frames per pixel
            let level = 0;
            while (level + 1 < peakSettings.levels && peakSettings.base * peakSettings.factor ** (level + 1) <= fpx) level++;
            const fpb = peakSettings.base * peakSettings.factor ** level;
            const xa = Math.max(0, Math.floor(x0));
            const xb = Math.min(vp.width, Math.ceil(x1));
            const s0 = cl.source_in + (toT(vp, xa) - cl.timeline_start);
            const s1 = cl.source_in + (toT(vp, xb) - cl.timeline_start);
            const b0 = Math.max(0, Math.floor((s0 * src.sample_rate) / fpb));
            const b1 = Math.max(b0 + 1, Math.ceil((s1 * src.sample_rate) / fpb));
            const pk = peakRange(src, level, b0, b1);
            if (pk && pk.max.length) {
              const g = 10 ** (cl.gain_db / 20);
              const mid = row.y + row.h / 2;
              const amp = (row.h - 8) / 2;
              ctx.fillStyle = silent ? muted : wave;
              for (let x = xa; x < xb; x++) {
                const t = toT(vp, x);
                let fade = 1;
                if (cl.fade_in > 0 && t < cl.timeline_start + cl.fade_in) fade = (t - cl.timeline_start) / cl.fade_in;
                if (cl.fade_out > 0 && t > cl.timeline_end - cl.fade_out) fade = Math.min(fade, (cl.timeline_end - t) / cl.fade_out);
                const s = cl.source_in + (t - cl.timeline_start);
                const i = Math.floor((s * src.sample_rate) / fpb) - b0;
                const j = Math.max(i + 1, Math.floor(((s + 1 / vp.pps) * src.sample_rate) / fpb) - b0);
                let hi = 0;
                let lo = 0;
                for (let k = Math.max(0, i); k < Math.min(pk.max.length, j); k++) {
                  hi = Math.max(hi, pk.max[k]!);
                  lo = Math.min(lo, pk.min[k]!);
                }
                const f = Math.min(1, (g * Math.max(0, fade)) / 32767);
                const top = mid - hi * f * amp;
                ctx.fillRect(x, top, 1, Math.max(1, (hi - lo) * f * amp));
              }
            }
          }
          // Fade handles: a line from the corner to where the fade ends.
          ctx.strokeStyle = fg;
          if (cl.fade_in > 0) {
            ctx.beginPath();
            ctx.moveTo(x0, row.y + row.h - 3);
            ctx.lineTo(toX(vp, cl.timeline_start + cl.fade_in), row.y + 3);
            ctx.stroke();
          }
          if (cl.fade_out > 0) {
            ctx.beginPath();
            ctx.moveTo(toX(vp, cl.timeline_end - cl.fade_out), row.y + 3);
            ctx.lineTo(x1, row.y + row.h - 3);
            ctx.stroke();
          }
          if (x1 - x0 > 60) {
            ctx.fillStyle = fg;
            const label = `${cl.source_label}${cl.gain_db ? ` ${cl.gain_db > 0 ? "+" : ""}${cl.gain_db} dB` : ""}${cl.muted ? " (muted)" : ""}`;
            ctx.fillText(label, Math.max(x0, 0) + 4, row.y + 13);
          }
        }
      } else if (row.kind === "removed") {
        // Each cut is a ✂ tab here and a line down through the narration: it takes
        // no time on the timeline, so it's drawn as a mark, not a stretch.
        ctx.font = "12px system-ui, sans-serif";
        for (const r of view.removed) {
          if (r.t === null || (r.kind === "take" && r.status !== "removed")) continue;
          const x = toX(vp, r.t);
          if (x < -20 || x > vp.width + 20) continue;
          const on = r.id === cutId;
          if (r.status !== "removed") {
            ctx.fillStyle = "rgba(127,127,127,0.35)"; // suggested or kept: not taken out
            ctx.fillRect(x - 2, row.y + 4, 4, row.h - 8);
            continue;
          }
          ctx.fillStyle = on ? accent : "rgba(220,38,38,0.85)";
          ctx.fillRect(x - 8, row.y + 2, 16, row.h - 4);
          ctx.fillStyle = "#fff";
          ctx.fillText("✂", x - 6, row.y + row.h - 7);
          ctx.fillStyle = on ? accent : "rgba(220,38,38,0.6)";
          ctx.fillRect(x - (on ? 1 : 0.5), row.y - TRACK_H, on ? 2 : 1, TRACK_H);
          if (vp.pps >= 30) {
            ctx.fillStyle = fg;
            ctx.fillText(`${r.seconds.toFixed(1)} s`, x + 10, row.y + row.h - 7);
          }
        }
        ctx.font = "11px system-ui, sans-serif";
      } else if (row.kind === "words" && lanes && vp.pps >= 15) {
        ctx.fillStyle = fg;
        let lastX = -Infinity;
        for (const w of lanes.words) {
          const x = toX(vp, w.t0);
          if (x < -50 || x > vp.width) continue;
          if (x < lastX + 4) continue;
          ctx.fillText(w.text, x, row.y + 15);
          lastX = x + ctx.measureText(w.text).width;
        }
      }
    }

    // Selection and playhead over everything.
    if (sel || drag?.kind === "select") {
      const [a, b] = drag?.kind === "select" ? [Math.min(drag.t0, drag.t), Math.max(drag.t0, drag.t)] : sel!;
      ctx.fillStyle = "rgba(59,130,246,0.15)";
      ctx.fillRect(toX(vp, a), 0, (b - a) * vp.pps, rows.height);
    }
    if (drag && drag.kind !== "select") {
      ctx.fillStyle = wave;
      ctx.fillRect(toX(vp, drag.t) - 1, 0, 2, rows.height);
    }
    ctx.fillStyle = "#dc2626";
    ctx.fillRect(toX(vp, playhead) - 0.5, 0, 1.5, rows.height);
  }, [view, lanes, vp, rows, playhead, sel, selected, trackId, drag, peakRange, sourceById, peakSettings, peaksTick, clips, tracks, cutId]);

  // -- mouse -------------------------------------------------------------------------------------

  const hit = (e: PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const row = rows.rows.find((rw) => y >= rw.y && y < rw.y + rw.h);
    return { x, y, t: toT(vp, x), row };
  };
  const snapT = (t: number) => (snap ? snapTo(points, t, 8 / vp.pps) : t);

  const onPointerDown = (e: PointerEvent<HTMLCanvasElement>) => {
    box.current?.focus();
    const { x, y, t, row } = hit(e);
    if (!row || row.kind === "ruler" || row.kind === "markers" || row.kind === "words") {
      if (row?.kind === "words" && lanes) {
        const w = lanes.words.find((w) => w.t0 <= t && t < w.t1);
        if (w) return seek(w.t0);
      }
      setDrag({ kind: "select", t0: snapT(t), t: snapT(t) });
      e.currentTarget.setPointerCapture?.(e.pointerId);
      return;
    }
    if (row.kind === "removed") {
      const hitCut = cuts
        .map((r) => ({ r, d: Math.abs(toX(vp, r.t!) - x) }))
        .filter((h) => h.d <= 10)
        .sort((a, b) => a.d - b.d)[0];
      if (hitCut) selectCut(hitCut.r);
      else {
        setCutId(null);
        seek(t);
      }
      return;
    }
    setTrackId(row.trackId!);
    const c = clipAt(clips, t, row.trackId!);
    selectClip(c);
    if (!c) return seek(snapT(t));
    const x0 = toX(vp, c.timeline_start);
    const x1 = toX(vp, c.timeline_end);
    const top = y - row.y < FADE_PX;
    if (top && x - x0 < FADE_PX * 2) setDrag({ kind: "fade", clip: c, edge: "in", t });
    else if (top && x1 - x < FADE_PX * 2) setDrag({ kind: "fade", clip: c, edge: "out", t });
    else if (x - x0 < EDGE_PX) setDrag({ kind: "trim", clip: c, edge: "start", t: c.timeline_start });
    else if (x1 - x < EDGE_PX) setDrag({ kind: "trim", clip: c, edge: "end", t: c.timeline_end });
    else {
      setDrag({ kind: "move", clip: c, dx: t - c.timeline_start, trackId: c.track_id, t: c.timeline_start });
      seek(t);
    }
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: PointerEvent<HTMLCanvasElement>) => {
    if (!drag) return;
    const { t, row } = hit(e);
    if (drag.kind === "move") setDrag({ ...drag, t: snapT(t - drag.dx), trackId: row?.trackId ?? drag.trackId });
    else setDrag({ ...drag, t: snapT(t) });
  };

  const onPointerUp = () => {
    const d = drag;
    setDrag(null);
    if (!d) return;
    if (d.kind === "select") {
      const a = Math.min(d.t0, d.t);
      const b = Math.max(d.t0, d.t);
      if (b - a > 0.01) {
        setSel([a, b]);
        setStatus(`Selection ${timecode(a)}–${timecode(b)}`);
      } else {
        setSel(null);
        seek(a);
      }
    } else if (d.kind === "move") {
      if (Math.abs(d.t - d.clip.timeline_start) > 1e-3 || d.trackId !== d.clip.track_id) tools.moveTo(d.clip, d.t, d.trackId);
    } else if (d.kind === "trim") {
      edit(() => api.tl("trim", { ref: d.clip.ref, edge: d.edge, t: d.t }), `Trimmed ${d.edge}`);
    } else {
      const len = d.edge === "in" ? d.t - d.clip.timeline_start : d.clip.timeline_end - d.t;
      edit(() => api.tl("clip", { ref: d.clip.ref, [d.edge === "in" ? "fade_in" : "fade_out"]: Math.max(0, len) }), `Fade ${d.edge}`);
    }
  };

  if (!view) return null;
  const tracksOther = tracks.filter((t) => t.kind !== "narration");

  return (
    <section aria-labelledby="timeline-heading" className="timeline">
      <h2 id="timeline-heading">Timeline</h2>
      <div className="run-row">
        <button type="button" onClick={togglePlay}>
          {playing ? "Pause" : "Play"}
        </button>
        <span className="timecode" aria-label="Playhead">
          {timecode(playhead)} / {timecode(view.duration)}
        </span>
        <button type="button" onClick={() => setVp((v) => zoom(v, 2, playhead))}>
          Zoom in
        </button>
        <button type="button" onClick={() => setVp((v) => zoom(v, 0.5, playhead))}>
          Zoom out
        </button>
        <button type="button" onClick={() => setVp(fitAll)}>
          {focus ? "Fit chapter" : "Fit"}
        </button>
        {focus && (
          <button type="button" onClick={() => setVp((v) => fit(view.duration, v.width))}>
            Whole book
          </button>
        )}
        <label>
          <input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} /> Snap
        </label>
        <button type="button" onClick={() => tools.goCut(-1)} title="C with Shift">
          ◀ Previous cut
        </button>
        <button type="button" onClick={() => tools.goCut(1)} title="C">
          Next cut ▶
        </button>
        <button type="button" onClick={tools.split}>
          Split
        </button>
        <button type="button" onClick={tools.deleteSel}>
          {sel ? "Cut selection" : "Delete"}
        </button>
        <button type="button" onClick={tools.marker}>
          Add marker
        </button>
        <button type="button" onClick={tools.addTrack}>
          Add track
        </button>
        <button type="button" onClick={tools.renderPreview}>
          Render preview
        </button>
      </div>
      <details className="keys">
        <summary>Keyboard shortcuts</summary>
        <p className="muted" id="timeline-keys">
          Space play · ←/→ 1 s (Shift 10 s) · Alt+←/→ snap · Home/End · ↑/↓ track · Tab clip · [ ] selection · Delete
          ripple delete · S split · Shift+[ ] trim to playhead · C / Shift+C next/previous cut · , . move the cut 50 ms · Alt+Shift+←/→ move clip · Alt+↑/↓ move to
          track · +/− gain · F/G fade in/out · U mute clip · Shift+U/Shift+S mute/solo track · M marker · T add track ·
          Shift+R restore cut · P render preview · Z/Shift+Z zoom · 0 fit · N snap on/off · ⌘/Ctrl+Z undo
        </p>
      </details>
      <p role="status" aria-live="polite" className="muted">
        {status}
        {sel ? ` · selection ${timecode(sel[0])}–${timecode(sel[1])}` : ""}
      </p>
      <p className="timeline-help">
        <strong>To cut:</strong> drag across the waveform to select, then <em>Cut selection</em>.{" "}
        <strong>To adjust a cut:</strong> click its ✂ in the cuts lane under the narration (or press C for the next one).
      </p>
      {sel && sel[1] > sel[0] && (
        <div className="edit-bar" role="group" aria-label="Selection">
          <span>
            Selected {timecode(sel[0])}–{timecode(sel[1])} ({(sel[1] - sel[0]).toFixed(2)} s)
          </span>
          <button type="button" onClick={tools.renderPreview}>
            ▶ Play selection
          </button>
          <button type="button" className="primary" onClick={tools.deleteSel}>
            ✂ Cut selection
          </button>
          <button type="button" className="link" onClick={() => setSel(null)}>
            Clear
          </button>
        </div>
      )}
      {cut && (
        <div className="edit-bar cut-bar" role="group" aria-label="Cut">
          <div className="cut-what">
            <strong>✂ Cut at {timecode(cut.t!)}</strong>: {cut.seconds.toFixed(2)} s taken out
            {cut.text ? ` (“${cut.text}”)` : ""} · {CUT_KIND[cut.kind] ?? cut.kind}
            <button type="button" className="link" onClick={() => setCutId(null)}>
              Close
            </button>
          </div>
          <div className="cut-row">
            <button type="button" onClick={() => playAcross(cut.t!)}>
              ▶ Listen across the cut
            </button>
            <button type="button" onClick={() => playCut(cut)}>
              ▶ Hear what was cut
            </button>
            {cut.kind !== "take" && (
              <button type="button" onClick={tools.restoreCut}>
                Restore (put it back)
              </button>
            )}
          </div>
          {cut.kind === "take" ? (
            <p className="muted">This is another take of a line: change which take plays in Choose takes.</p>
          ) : (
            <>
              <div className="cut-row">
                <span className="cut-label">Move the cut (rolling edit: same length, 50 ms a click)</span>
                <button type="button" onClick={() => tools.roll(-1)}>
                  ◀ Earlier
                </button>
                <button type="button" onClick={() => tools.roll(1)}>
                  Later ▶
                </button>
              </div>
              <div className="cut-row">
                <span className="cut-label">Where it starts</span>
                <button type="button" onClick={() => tools.cutEdge("start", -NUDGE_S)} aria-label="Start the cut 50 ms earlier">
                  ◀ Cut more
                </button>
                <button type="button" onClick={() => tools.cutEdge("start", NUDGE_S)} aria-label="Start the cut 50 ms later">
                  Keep more ▶
                </button>
                <span className="cut-label">Where it ends</span>
                <button type="button" onClick={() => tools.cutEdge("end", -NUDGE_S)} aria-label="End the cut 50 ms earlier">
                  ◀ Keep more
                </button>
                <button type="button" onClick={() => tools.cutEdge("end", NUDGE_S)} aria-label="End the cut 50 ms later">
                  Cut more ▶
                </button>
              </div>
            </>
          )}
        </div>
      )}
      <div className="timeline-body">
        <div className="track-heads" style={{ paddingTop: RULER_H + MARKER_H }}>
          {tracks.map((tr) => (
            <div
              key={tr.id}
              className={`track-head${tr.id === trackId ? " current" : ""}`}
              style={{ height: tr.kind === "narration" ? TRACK_H + REMOVED_H + WORDS_H : TRACK_H }}
            >
              <input
                aria-label={`Track name ${tr.name}`}
                defaultValue={tr.name}
                onBlur={(e) => e.target.value !== tr.name && edit(() => api.tlPatch(`tracks/${tr.id}`, { name: e.target.value }))}
              />
              <button type="button" aria-pressed={tr.mute} onClick={() => edit(() => api.tlPatch(`tracks/${tr.id}`, { mute: !tr.mute }))}>
                M
              </button>
              <button type="button" aria-pressed={tr.solo} onClick={() => edit(() => api.tlPatch(`tracks/${tr.id}`, { solo: !tr.solo }))}>
                S
              </button>
              <label>
                Gain
                <input
                  type="number"
                  step={0.5}
                  aria-label={`${tr.name} gain (dB)`}
                  value={tr.gain_db}
                  onChange={(e) => edit(() => api.tlPatch(`tracks/${tr.id}`, { gain_db: Number(e.target.value) }))}
                />
              </label>
              <label>
                Pan
                <input
                  type="range"
                  min={-1}
                  max={1}
                  step={0.1}
                  aria-label={`${tr.name} pan`}
                  value={tr.pan}
                  onChange={(e) => edit(() => api.tlPatch(`tracks/${tr.id}`, { pan: Number(e.target.value) }))}
                />
              </label>
            </div>
          ))}
        </div>
        <div
          ref={box}
          className="timeline-canvas"
          tabIndex={0}
          role="application"
          aria-label="Timeline: use the keys listed above"
          aria-describedby="timeline-keys"
          onKeyDown={onKeyDown}
          onWheel={(e) => {
            if (e.ctrlKey || e.metaKey) setVp((v) => zoom(v, e.deltaY < 0 ? 1.25 : 0.8, toT(v, e.nativeEvent.offsetX)));
            else setVp((v) => ({ ...v, start: Math.max(0, v.start + (e.deltaX || e.deltaY) / v.pps) }));
          }}
        >
          <canvas
            ref={canvas}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            aria-hidden="true"
          />
        </div>
      </div>
      {current && (
        <ClipInspector
          clip={current}
          tracks={tracks}
          onChange={(attrs) => edit(() => api.tl("clip", { ref: current.ref, ...attrs }), "Clip updated")}
          onMove={(to) => tools.moveTo(current, current.timeline_start, to)}
        />
      )}
      {tracksOther.length > 0 && (
        <AddRecording
          sources={sources}
          tracks={tracksOther}
          onAdd={(track, source) => edit(() => api.tl(`tracks/${track}/add-source`, { source_id: source, t: playhead }), "Recording added")}
        />
      )}
    </section>
  );
}

function ClipInspector({
  clip,
  tracks,
  onChange,
  onMove,
}: {
  clip: TimelineClip;
  tracks: TimelineView["tracks"];
  onChange: (attrs: Record<string, number | boolean>) => void;
  onMove: (track: number) => void;
}) {
  return (
    <fieldset className="inspector">
      <legend>
        Clip: {clip.source_label} {timecode(clip.source_in)}–{timecode(clip.source_out)} at {timecode(clip.timeline_start)}
      </legend>
      <label>
        Gain (dB)
        <input type="number" step={0.5} value={clip.gain_db} onChange={(e) => onChange({ gain_db: Number(e.target.value) })} />
      </label>
      <label>
        Fade in (s)
        <input type="number" min={0} step={0.05} value={Number(clip.fade_in.toFixed(3))} onChange={(e) => onChange({ fade_in: Number(e.target.value) })} />
      </label>
      <label>
        Fade out (s)
        <input type="number" min={0} step={0.05} value={Number(clip.fade_out.toFixed(3))} onChange={(e) => onChange({ fade_out: Number(e.target.value) })} />
      </label>
      <label>
        <input type="checkbox" checked={clip.muted} onChange={(e) => onChange({ muted: e.target.checked })} /> Muted
      </label>
      <label>
        Track
        <select value={clip.track_id} onChange={(e) => onMove(Number(e.target.value))}>
          {tracks.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}

function AddRecording({
  sources,
  tracks,
  onAdd,
}: {
  sources: Source[];
  tracks: TimelineView["tracks"];
  onAdd: (track: number, source: number) => void;
}) {
  const [track, setTrack] = useState(tracks[0]?.id ?? 0);
  const [source, setSource] = useState(sources[0]?.id ?? 0);
  return (
    <div className="run-row">
      <label>
        Add recording
        <select value={source} onChange={(e) => setSource(Number(e.target.value))}>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        to track
        <select value={track} onChange={(e) => setTrack(Number(e.target.value))}>
          {tracks.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <button type="button" onClick={() => onAdd(track, source)} disabled={!track || !source}>
        Add at playhead
      </button>
    </div>
  );
}
