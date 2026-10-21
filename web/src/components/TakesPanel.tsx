import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, excerptUrl, type Source, type Take, type TakesResponse } from "../state/engine";
import { TEXT_ERRORS, clock, describe } from "../state/comparison";
import { plural } from "../state/format";

const STATUS_LABELS = { preferred: "Preferred", alternate: "Alternate", rejected: "Rejected" } as const;

interface Props {
  sources: Source[];
  revision: number; // analysis finished, or an edit elsewhere (undo) changed things
  onEdited: () => void;
  onError: (msg: string) => void;
}

/** Take comparison (SPEC §6.5): one group per line read more than once. Keyboard
 * first: 1-9 choose, Space play, ←/→ switch take (A/B, in sync), L loop,
 * N / Shift+N next/previous line, C confirm, X reject, B back. */
export function TakesPanel({ sources, revision, onEdited, onError }: Props) {
  const [data, setData] = useState<TakesResponse | null>(null);
  const [groupIdx, setGroupIdx] = useState(0);
  const [cursor, setCursor] = useState(0); // the take being auditioned, by index
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const fail = (e: unknown) => onError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let cancelled = false;
    api
      .takes()
      .then((d) => !cancelled && setData(d))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const groups = data?.groups ?? [];
  const group = groups[Math.min(groupIdx, groups.length - 1)];
  // Takes in the order they were recorded: "take 1" is the first reading.
  const takes = group ? [...group.takes].sort(byRecording) : [];
  const current = takes[Math.min(cursor, takes.length - 1)];

  const apply = (p: Promise<TakesResponse>) =>
    p
      .then((d) => {
        setData(d);
        onEdited();
      })
      .catch(fail);

  /** Play a take; when switching takes mid-play, carry on at the same point (A/B). */
  const play = (t: Take | undefined, keepPosition = false) => {
    const el = audio.current;
    if (!el || !t) return;
    const at = keepPosition ? el.currentTime : 0;
    el.loop = loop;
    el.src = excerptUrl(t.source_file_id, t.start, t.end, 0);
    el.onloadedmetadata = () => {
      el.currentTime = Math.min(at, Number.isFinite(el.duration) ? el.duration : at);
    };
    el.play()?.catch(() => undefined);
    setPlaying(true);
  };

  const switchTo = (i: number) => {
    const n = Math.max(0, Math.min(takes.length - 1, i));
    setCursor(n);
    if (playing) play(takes[n], true);
  };

  const moveGroup = (step: number) => {
    if (!groups.length) return;
    setGroupIdx((g) => Math.max(0, Math.min(groups.length - 1, g + step)));
    setCursor(0);
    audio.current?.pause();
    setPlaying(false);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const tag = (e.target as HTMLElement).tagName;
    if (["INPUT", "SELECT", "BUTTON", "TEXTAREA"].includes(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!group) return;
    const handled = (() => {
      if (/^[1-9]$/.test(e.key)) {
        const t = takes[Number(e.key) - 1];
        if (t) {
          setCursor(Number(e.key) - 1);
          apply(api.chooseTake(group.id, t.id));
        }
        return true;
      }
      switch (e.key) {
        case " ":
          if (playing && !audio.current?.paused) {
            audio.current?.pause();
            setPlaying(false);
          } else play(current);
          return true;
        case "ArrowRight":
          switchTo(cursor + 1);
          return true;
        case "ArrowLeft":
          switchTo(cursor - 1);
          return true;
        case "l":
        case "L":
          setLoop((v) => {
            if (audio.current) audio.current.loop = !v;
            return !v;
          });
          return true;
        case "n":
        case "j":
          moveGroup(1);
          return true;
        case "N":
        case "k":
          moveGroup(-1);
          return true;
        case "c":
          apply(api.confirmTakes([group.id]));
          return true;
        case "x":
          if (current && current.status !== "preferred") apply(api.markTake(current.id, "rejected"));
          return true;
        case "b":
          if (group.previous_take_id !== null) apply(api.takeBack(group.id));
          return true;
        default:
          return false;
      }
    })();
    if (handled) e.preventDefault();
  };

  if (!data) return null;
  const review = groups.filter((g) => g.state === "suggested" && !g.confident).length;
  const confirmed = groups.filter((g) => g.state === "confirmed").length;

  return (
    <section aria-labelledby="takes-heading" className="takes-panel">
      <h2 id="takes-heading">Takes</h2>
      <p aria-live="polite">
        {groups.length
          ? `${plural(groups.length, "line")} read more than once · ${review} to review · ${confirmed} confirmed`
          : "No line was read more than once."}
      </p>
      {group && (
        <div
          className="take-group"
          tabIndex={0}
          role="group"
          aria-label={`Line ${groupIdx + 1} of ${groups.length}`}
          aria-describedby="takes-keys"
          onKeyDown={onKeyDown}
        >
          <p className="muted" id="takes-keys">
            Keys: 1–9 choose · Space play · ←/→ switch take (keeps your place) · L loop{loop ? " (on)" : ""} · N /
            Shift+N next/previous line · C confirm · X reject · B back to the previous choice
          </p>
          <div className="run-row">
            <button type="button" onClick={() => moveGroup(-1)} disabled={groupIdx === 0}>
              Previous line
            </button>
            <span>
              Line {groupIdx + 1} of {groups.length}
            </span>
            <button type="button" onClick={() => moveGroup(1)} disabled={groupIdx >= groups.length - 1}>
              Next line
            </button>
            <audio
              ref={audio}
              controls
              aria-label="Take audio"
              onEnded={() => setPlaying(false)}
              onPause={() => setPlaying(false)}
            />
          </div>
          <p className="cmp-line">
            <span className="page-tag">p. {group.page_label ?? "—"}</span> {group.sentence_text}
          </p>
          <p className="muted">
            {group.state === "confirmed"
              ? "Confirmed."
              : group.confident
                ? `Suggested: ${group.reason}.`
                : `Suggested (${group.reason}): listen and confirm.`}{" "}
            {group.state !== "confirmed" && (
              <button type="button" onClick={() => apply(api.confirmTakes([group.id]))}>
                Confirm
              </button>
            )}{" "}
            {group.previous_take_id !== null && (
              <button type="button" onClick={() => apply(api.takeBack(group.id))}>
                Back to the previous choice
              </button>
            )}
          </p>
          <ol className="take-list" aria-label="Takes of this line">
            {takes.map((t, i) => (
              <li
                key={t.id}
                className={`take take-${t.status}${i === cursor ? " take-cursor" : ""}`}
                aria-current={i === cursor ? "true" : undefined}
              >
                <div className="take-head">
                  <strong>Take {i + 1}</strong> · {STATUS_LABELS[t.status]}
                  {t.decided_by === "auto" && t.status === "preferred" ? " (suggested)" : ""} · {t.source_label}{" "}
                  {clock(t.start)} · {Math.round(t.score * 100)}%
                  {t.loudness_db !== null ? ` · ${t.loudness_db.toFixed(1)} dB` : ""}
                </div>
                <TakeWave take={t} source={sources.find((s) => s.id === t.source_file_id)} />
                <p className="heard">{t.heard}</p>
                <ul className="diffs">
                  {t.diffs
                    .filter((d) => TEXT_ERRORS.has(d.kind) || (d.kind === "extra" && !d.repeat))
                    .map((d, j) => (
                      <li key={j}>{describe(d)}</li>
                    ))}
                </ul>
                <div className="actions">
                  <button type="button" onClick={() => play(t)} aria-label={`Play take ${i + 1}`}>
                    ▶ Play
                  </button>
                  {t.status !== "preferred" && (
                    <button type="button" onClick={() => apply(api.chooseTake(group.id, t.id))}>
                      Use take {i + 1}
                    </button>
                  )}
                  {t.status === "alternate" && (
                    <button type="button" onClick={() => apply(api.markTake(t.id, "rejected"))}>
                      Reject
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

function byRecording(a: Take, b: Take): number {
  return a.source_file_id - b.source_file_id || a.start - b.start;
}

/** A small waveform of the take, from the cached peaks. */
function TakeWave({ take, source }: { take: Take; source: Source | undefined }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!source) return;
    const base = 512; // level 0 bucket size (engine peaks settings)
    const first = Math.floor((take.start * source.sample_rate) / base);
    const count = Math.max(1, Math.ceil(((take.end - take.start) * source.sample_rate) / base));
    let cancelled = false;
    api
      .peaks(source.id, 0, first, Math.min(count, 4096))
      .then((p) => {
        const c = canvas.current;
        const ctx = c?.getContext?.("2d");
        if (cancelled || !c || !ctx) return;
        const w = c.width;
        const h = c.height;
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = getComputedStyle(c).color || "#555";
        const n = p.max.length;
        for (let x = 0; x < w; x++) {
          const i = Math.floor((x / w) * n);
          const hi = p.max[i] ?? 0;
          const lo = p.min[i] ?? 0;
          ctx.fillRect(x, ((1 - hi) * h) / 2, 1, Math.max(1, ((hi - lo) * h) / 2));
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [take.start, take.end, source]);
  return <canvas ref={canvas} className="take-wave" width={480} height={40} aria-hidden="true" />;
}
