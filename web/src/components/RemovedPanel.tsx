import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  api,
  excerptUrl,
  renderUrl,
  type CleanupSettings,
  type Edl,
  type HistoryEntry,
  type Region,
  type RegionKind,
  type RegionStatus,
} from "../state/engine";
import { clock } from "../state/comparison";
import { plural } from "../state/format";

const REGION_LABELS: Record<RegionKind, string> = {
  stutter: "Stutter",
  false_start: "False start",
  commentary: "Commentary",
  pause: "Shortened pause",
  cut: "Cut",
  take: "Other take",
};
const STATUS_LABELS: Record<RegionStatus, string> = {
  removed: "Removed",
  suggested: "Suggested",
  restored: "Restored",
};
const NUDGE_S = 0.05; // one keypress of trimming or rolling

type Filter = RegionStatus | "all";

interface Props {
  revision: number; // bumped when analysis finishes or another panel edits
  onEdited: () => void;
  onError: (msg: string) => void;
}

/** Removed material (SPEC §5, §6.6): every cut the cleanup made or suggested, and
 * the user's own; restore, re-apply, trim or roll each one; undo/redo. */
export function RemovedPanel({ revision, onEdited, onError }: Props) {
  const [edl, setEdl] = useState<Edl | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [editedHere, setEditedHere] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const rows = useRef(new Map<number, HTMLTableRowElement>());
  const fail = (e: unknown) => onError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let cancelled = false;
    api
      .edl()
      .then((d) => !cancelled && setEdl(d))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const apply = (p: Promise<Edl>) =>
    p
      .then((d) => {
        setEdl(d);
        setEditedHere(true);
        onEdited();
      })
      .catch(fail);

  // Undo/redo from anywhere on the page, except while typing.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName ?? "";
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || !(e.metaKey || e.ctrlKey)) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey && edl?.undo) {
        e.preventDefault();
        apply(api.undo());
      } else if (((key === "z" && e.shiftKey) || key === "y") && edl?.redo) {
        e.preventDefault();
        apply(api.redo());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Takes are chosen in the Takes panel: restoring another take here would make
  // the line play twice, so take cuts are only counted (D-045).
  const listed = useMemo(() => (edl?.regions ?? []).filter((r) => r.kind !== "take"), [edl]);
  const otherTakes = (edl?.regions ?? []).filter((r) => r.kind === "take" && r.status === "removed").length;
  const shown = useMemo(() => listed.filter((r) => filter === "all" || r.status === filter), [listed, filter]);
  const current = shown.find((r) => r.id === currentId) ?? null;
  const count = (s: RegionStatus) => listed.filter((r) => r.status === s).length;

  const play = (src: string) => {
    const el = audio.current;
    if (!el) return;
    el.src = src;
    el.play()?.catch(() => undefined);
  };
  // As edited: the timeline around the join. Original: the cut audio in context.
  const playEdited = (r: Region) => r.timeline_at !== null && play(renderUrl(r.timeline_at - 3, r.timeline_at + 3));
  const playOriginal = (r: Region) => play(excerptUrl(r.source_file_id, r.start, r.end));

  const focusRow = (id: number) => {
    setCurrentId(id);
    rows.current.get(id)?.focus();
  };
  const move = (step: number) => {
    if (!shown.length) return;
    const at = current ? shown.indexOf(current) : -1;
    const next = shown[Math.min(shown.length - 1, Math.max(0, at + step))];
    if (next) focusRow(next.id);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const tag = (e.target as HTMLElement).tagName;
    if (["INPUT", "SELECT", "BUTTON"].includes(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
    const r = current;
    const handled = (() => {
      switch (e.key) {
        case "ArrowDown":
        case "j":
          move(1);
          return true;
        case "ArrowUp":
        case "k":
          move(-1);
          return true;
        case " ":
          if (r) playEdited(r);
          return true;
        case "p":
          if (r) playOriginal(r);
          return true;
        case "r":
          if (r) apply(api.setRegionStatus([r.id], "restored"));
          return true;
        case "a":
          if (r) apply(api.setRegionStatus([r.id], "removed"));
          return true;
        // Trim: bring back a little audio at either edge of the cut.
        case "[":
          if (r) apply(api.editRegion(r.id, { start: Math.min(r.end, r.start + NUDGE_S) }));
          return true;
        case "]":
          if (r) apply(api.editRegion(r.id, { end: Math.max(r.start, r.end - NUDGE_S) }));
          return true;
        // Rolling edit: move the cut earlier or later, same length.
        case ",":
          if (r) apply(api.rollRegion(r.id, -NUDGE_S));
          return true;
        case ".":
          if (r) apply(api.rollRegion(r.id, NUDGE_S));
          return true;
        default:
          return false;
      }
    })();
    if (handled) e.preventDefault();
  };

  const loadHistory = () =>
    api
      .history()
      .then((h) => setHistory(h.entries))
      .catch(fail);

  if (!edl) return null;
  const removedS = edl.original_duration - edl.duration;

  return (
    <section aria-labelledby="removed-heading" className="removed">
      <h2 id="removed-heading">Removed material</h2>
      <p aria-live="polite">
        Timeline {clock(edl.duration)} of {clock(edl.original_duration)} recorded · {removedS.toFixed(1)} s removed ·{" "}
        {count("removed")} removed · {count("suggested")} suggested · {count("restored")} restored
        {otherTakes > 0 && ` · ${plural(otherTakes, "other take")} (see Takes)`}
      </p>
      <div className="run-row">
        <button type="button" onClick={() => apply(api.undo())} disabled={!edl.undo}>
          Undo{edl.undo ? `: ${edl.undo}` : ""}
        </button>
        <button type="button" onClick={() => apply(api.redo())} disabled={!edl.redo}>
          Redo{edl.redo ? `: ${edl.redo}` : ""}
        </button>
        <label htmlFor="removed-filter">Show</label>
        <select id="removed-filter" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
          <option value="all">All</option>
          <option value="removed">Removed</option>
          <option value="suggested">Suggested</option>
          <option value="restored">Restored</option>
        </select>
        <audio ref={audio} controls aria-label="Removed material audio" />
      </div>
      {editedHere && (
        <p role="status" className="muted">
          The timeline changed: re-run checks to update the Issues list.
        </p>
      )}
      <p className="muted" id="removed-keys">
        Keys: ↑/↓ or j/k move · Space play as edited · p play what was cut · r restore · a apply · [ ] bring back 50 ms
        at the start/end · , . move the cut 50 ms · ⌘/Ctrl+Z undo · Shift+⌘/Ctrl+Z redo
      </p>

      {shown.length > 0 ? (
        <table className="issue-table" onKeyDown={onKeyDown} aria-describedby="removed-keys">
          <caption className="visually-hidden">Removed material</caption>
          <thead>
            <tr>
              <th scope="col">Type</th>
              <th scope="col">Where</th>
              <th scope="col">What</th>
              <th scope="col">Length</th>
              <th scope="col">Confidence</th>
              <th scope="col">Status</th>
              <th scope="col">Actions</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr
                key={r.id}
                ref={(el) => {
                  if (el) rows.current.set(r.id, el);
                  else rows.current.delete(r.id);
                }}
                tabIndex={r.id === (current?.id ?? shown[0]?.id) ? 0 : -1}
                aria-current={r.id === current?.id ? "true" : undefined}
                className={`st-${r.status}`}
                onFocus={() => setCurrentId(r.id)}
              >
                <td>{REGION_LABELS[r.kind]}</td>
                <td>
                  {r.source_label} {clock(r.start)}
                </td>
                <td>
                  {r.text && <div className="expected">“{r.text}”</div>}
                  <div className="muted">{r.reason}</div>
                </td>
                <td>{r.seconds.toFixed(2)} s</td>
                <td>{r.confidence === null ? "—" : `${Math.round(r.confidence * 100)}%`}</td>
                <td>
                  {STATUS_LABELS[r.status]}
                  {r.decided_by === "auto" ? " (auto)" : ""}
                </td>
                <td className="actions">
                  <button type="button" onClick={() => playEdited(r)} aria-label={`Play as edited at ${clock(r.start)}`}>
                    ▶ Edited
                  </button>
                  <button type="button" onClick={() => playOriginal(r)} aria-label={`Play what was cut at ${clock(r.start)}`}>
                    ▶ Cut
                  </button>
                  {r.status === "removed" ? (
                    <button type="button" onClick={() => apply(api.setRegionStatus([r.id], "restored"))}>
                      Restore
                    </button>
                  ) : (
                    <button type="button" onClick={() => apply(api.setRegionStatus([r.id], "removed"))}>
                      {r.status === "suggested" ? "Apply" : "Remove again"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">
          {listed.length ? "Nothing with this status." : "Nothing removed yet. Run the analysis to clean up."}
        </p>
      )}

      <details onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && loadHistory()}>
        <summary>Edit history</summary>
        <ol className="history" aria-label="Edit history">
          {history.map((h) => (
            <li key={h.seq} className={`hist-${h.state}`}>
              {h.summary} <span className="muted">· {h.origin === "auto" ? "automatic" : "you"}</span>
              {h.state === "undone" && <span className="muted"> · undone (can redo)</span>}
              {h.state === "dropped" && <span className="muted"> · undone</span>}
            </li>
          ))}
        </ol>
        <p className="muted">{plural(history.length, "edit")}. History is saved with the project.</p>
      </details>
      <CleanupForm onError={onError} />
    </section>
  );
}

/** Cleanup settings: which kinds to look for, and the auto-apply threshold. */
function CleanupForm({ onError }: { onError: (msg: string) => void }) {
  const [s, setS] = useState<CleanupSettings | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    api
      .cleanupSettings()
      .then(setS)
      .catch(() => undefined);
  }, []);
  if (!s) return null;
  const toggle = (key: "stutters" | "false_starts" | "commentary", label: string) => (
    <label key={key}>
      <input
        type="checkbox"
        checked={s[key]}
        onChange={(e) => {
          setSaved(false);
          setS({ ...s, [key]: e.target.checked });
        }}
      />
      {label}
    </label>
  );
  return (
    <details className="issue-settings">
      <summary>Cleanup settings</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          api
            .saveCleanupSettings(s)
            .then((r) => {
              setS(r);
              setSaved(true);
            })
            .catch((err: unknown) => onError(err instanceof Error ? err.message : String(err)));
        }}
      >
        {toggle("stutters", "Stutters")}
        {toggle("false_starts", "False starts")}
        {toggle("commentary", "Commentary")}
        <span>
          <label htmlFor="auto-apply">Apply automatically at confidence (%)</label>
          <input
            id="auto-apply"
            type="number"
            min={0}
            max={100}
            step={5}
            value={Math.round(s.auto_apply * 100)}
            onChange={(e) => {
              setSaved(false);
              setS({ ...s, auto_apply: Number(e.target.value) / 100 });
            }}
          />
        </span>
        <button type="submit">Save cleanup settings</button>
        {saved && <span role="status"> Saved. Applies to the next cleanup run.</span>}
      </form>
    </details>
  );
}
