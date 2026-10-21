import { useEffect, useRef, useState } from "react";
import { api, excerptUrl, type EditCheck, type LostLine } from "../state/engine";
import { clock } from "../state/comparison";
import { plural } from "../state/format";
import { hasUnit, type Scope } from "../state/scope";

/** What took the audio away, in words. */
const BY: Record<string, string> = {
  stutter: "stutter cleanup",
  false_start: "false-start cleanup",
  commentary: "off-script cleanup",
  pause: "a shortened pause",
  cut: "a cut",
  take: "choosing another take",
  muted: "a muted clip or track",
};

interface Props {
  checks: string[]; // the project's checking stages, for Re-run checks
  revision: number; // bumped by edits and finished analyses
  scope?: Scope;
  onEdited: () => void;
  onTakes: () => void; // go to Choose takes
  onError: (msg: string) => void;
}

/** Did the edit lose anything (D-061)? Every line read in full is checked against
 * the timeline as it is now: removed or clipped lines are listed with what cut
 * them, to restore or fix. Re-run checks also refreshes Fix mistakes. */
export function EditCheckPanel({ checks, revision, scope = null, onEdited, onTakes, onError }: Props) {
  const [data, setData] = useState<EditCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const fail = (e: unknown) => onError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let cancelled = false;
    api
      .editCheck()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setBusy(false);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const rerun = () => {
    setBusy(true);
    api.runAnalysis(checks, null).catch((e: unknown) => {
      setBusy(false);
      fail(e);
    });
  };

  const restore = (l: LostLine) => {
    const ids = l.removals.filter((r) => r.id !== null && r.kind !== "take").map((r) => r.id as number);
    if (!ids.length) return;
    api
      .setRegionStatus(ids, "restored")
      .then(() => onEdited())
      .catch(fail);
  };

  const play = (l: LostLine) => {
    const el = audio.current;
    if (!el) return;
    el.src = excerptUrl(l.source_id, l.start, l.end, 0.5);
    el.play()?.catch(() => undefined);
  };

  const lines = (data?.lines ?? []).filter((l) => hasUnit(scope, l.unit_id));

  return (
    <section aria-labelledby="edit-check-heading" className="edit-check card">
      <h2 id="edit-check-heading">Check the edit</h2>
      <div className="run-row">
        <button type="button" onClick={rerun} disabled={busy || !checks.length || data?.run_id == null}>
          {busy ? "Checking…" : "Re-run checks"}
        </button>
        <span aria-live="polite" className={lines.length ? "error" : "muted"}>
          {!data || data.run_id === null
            ? "Check the reading first: the edit is checked against it."
            : lines.length
              ? `${plural(lines.length, "line")} lost in the edit`
              : `Nothing lost: all ${plural(data.checked, "line")} read in full still play${scope ? " (whole book)" : ""}.`}
        </span>
        <audio ref={audio} controls aria-label="Lost line audio" />
      </div>
      <p className="muted">
        Re-run checks after editing to update Fix mistakes too. This list updates by itself after every edit.
      </p>
      {lines.length > 0 && (
        <ul className="lost-lines" aria-label="Lines lost in the edit">
          {lines.map((l) => {
            const kinds = [...new Set(l.removals.map((r) => r.kind))];
            const restorable = l.removals.some((r) => r.id !== null && r.kind !== "take");
            return (
              <li key={l.unit_id} className={`lost lost-${l.status}`}>
                <div className="issue-head">
                  <span className={`chip ${l.status === "removed" ? "chip-flub" : "chip-silence"}`}>
                    {l.status === "removed" ? "Removed" : "Clipped"}
                  </span>
                  <span className="muted">
                    p. {l.page_label} · {clock(l.start)}
                  </span>
                </div>
                <div className="expected">{l.text}</div>
                <div className="muted">
                  Missing: {l.missing.join(" ")}
                  {kinds.length > 0 && ` · by ${kinds.map((k) => BY[k] ?? k).join(", ")}`}
                </div>
                <div className="actions">
                  <button type="button" onClick={() => play(l)} aria-label={`Play the original of p. ${l.page_label}`}>
                    ▶ Original
                  </button>
                  {restorable && (
                    <button type="button" className="primary" onClick={() => restore(l)}>
                      Restore
                    </button>
                  )}
                  {kinds.includes("take") && (
                    <button type="button" onClick={onTakes}>
                      Go to Choose takes
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
