import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, excerptUrl, uploadFile, type Job, type PickupReport, type Source } from "../state/engine";
import { plural } from "../state/format";
import { hasUnit, type Scope } from "../state/scope";
import { TakesPanel } from "./TakesPanel";

const ACCEPT = ".wav,.wave,.mp3,audio/wav,audio/mpeg";

type Line = PickupReport["lines"][number];

function what(l: Line): string {
  if (l.status === "choose") return "Listen and choose";
  if (l.status === "not_used") return "Not used: another take is better";
  return l.replaces === "flub" ? "In place of the flub" : l.replaces === "missing" ? "Fills a skipped line" : "In place (re-read)";
}

interface Props {
  sources: Source[];
  jobs: Job[];
  revision: number; // bumped when analysis finishes or an edit happens
  scope?: Scope;
  onEdited: () => void;
  onError: (msg: string) => void;
}

/** Pickups (D-062): add files of re-recorded lines; the app transcribes them,
 * finds each line, puts the best reading where the flubbed one was, and lists
 * what each pickup did. Lines with more than one good take wait for a choice. */
export function PickupsPanel({ sources, jobs, revision, scope = null, onEdited, onError }: Props) {
  const picker = useRef<HTMLInputElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [report, setReport] = useState<PickupReport[]>([]);
  const [uploading, setUploading] = useState<Record<string, number>>({});
  const [paths, setPaths] = useState("");
  // Import jobs started here: once they're all done, the analysis runs by itself.
  const [pending, setPending] = useState<string[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const fail = (e: unknown) => onError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let cancelled = false;
    api
      .pickups()
      .then((r) => !cancelled && setReport(r))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision, pending.length]);

  // When this panel's imports finish, check the reading (transcribe the pickups,
  // compare, place the takes, re-check the flubs).
  const finished =
    pending.length > 0 &&
    pending.every((id) => {
      const j = jobs.find((x) => x.id === id);
      return j !== undefined && j.status !== "running" && j.status !== "queued";
    });
  const startedFor = useRef("");
  const pendingKey = pending.join(",");
  useEffect(() => {
    if (!finished || startedFor.current === pendingKey) return;
    startedFor.current = pendingKey;
    api
      .asrInfo()
      .then((info) => api.runAnalysis(null, info.default.model))
      .then(() => {
        setPending([]);
        setAnalyzing(true);
      })
      .catch((e: unknown) => {
        setPending([]);
        onError(e instanceof Error ? e.message : String(e));
      });
  }, [finished, pendingKey, onError]);
  const running = jobs.some((j) => j.kind === "analysis" && (j.status === "running" || j.status === "queued"));
  const busy = Object.keys(uploading).length > 0 || pending.length > 0 || (analyzing && running);

  const upload = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      try {
        const id = await uploadFile(file, (f) => setUploading((u) => ({ ...u, [file.name]: f })), "pickup");
        setPending((p) => [...p, id]);
      } catch (e) {
        onError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setUploading((u) => {
          const rest = { ...u };
          delete rest[file.name];
          return rest;
        });
      }
    }
  };

  const submitPaths = (e: FormEvent) => {
    e.preventDefault();
    const list = paths.split("\n").map((p) => p.trim()).filter(Boolean);
    if (!list.length) return;
    api
      .importPaths(list, "copy", "pickup")
      .then((r) => {
        setPaths("");
        setPending((p) => [...p, r.job_id]);
      })
      .catch(fail);
  };

  const play = (sourceId: number, start: number, end: number) => {
    const el = audio.current;
    if (!el) return;
    el.src = excerptUrl(sourceId, start, end, 0);
    el.play()?.catch(() => undefined);
  };

  const shown = report.map((r) => ({ ...r, lines: r.lines.filter((l) => hasUnit(scope, l.unit_id)) }));
  const lines = shown.flatMap((r) => r.lines);
  const toChoose = [...new Set(lines.filter((l) => l.status === "choose").map((l) => l.group_id))];
  const placed = lines.filter((l) => l.status === "placed").length;

  return (
    <section aria-labelledby="pickups-heading" className="pickups card">
      <h2 id="pickups-heading">Pickups</h2>
      <p className="muted">
        Re-recorded some lines? Add the pickup files. Each is transcribed to find which line it is, and the best reading
        goes in place of the flubbed one. Nothing else in a pickup file plays.
      </p>
      <div className="run-row">
        <button type="button" className="primary" onClick={() => picker.current?.click()} disabled={busy}>
          Add pickup files…
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          data-testid="pickup-picker"
          onChange={(e) => {
            if (e.target.files) void upload(e.target.files);
            e.target.value = "";
          }}
        />
        <span aria-live="polite" className="muted">
          {Object.keys(uploading).length
            ? `Uploading ${Object.keys(uploading).join(", ")}…`
            : pending.length
              ? "Importing pickups…"
              : analyzing && running
                ? "Transcribing the pickups and putting them in place…"
                : report.length
                  ? `${plural(report.length, "pickup file")} · ${plural(placed, "line")} in place${toChoose.length ? ` · ${toChoose.length} to choose` : ""}`
                  : ""}
        </span>
        <audio ref={audio} controls aria-label="Pickup audio" />
      </div>
      <details className="path-import">
        <summary>Or add by path</summary>
        <form onSubmit={submitPaths}>
          <label htmlFor="pickup-paths">Pickup files (one per line)</label>
          <textarea id="pickup-paths" rows={2} value={paths} onChange={(e) => setPaths(e.target.value)} />
          <button type="submit" disabled={busy}>
            Add pickups
          </button>
        </form>
      </details>

      {shown.map((r) => (
        <div key={r.source_id} className="pickup-file">
          <h3>{r.label}</h3>
          {!r.checked ? (
            <p className="muted">Not checked yet.</p>
          ) : r.lines.length === 0 && r.unmatched.length === 0 ? (
            <p className="muted">No line found in it.</p>
          ) : (
            <ul className="pickup-lines" aria-label={`Lines in ${r.label}`}>
              {r.lines.map((l) => (
                <li key={l.take_id} className={`pickup-${l.status}`}>
                  <span className={`chip chip-pickup-${l.status}`}>{what(l)}</span>{" "}
                  <span className="page-tag">p. {l.page_label ?? "—"}</span> {l.text}{" "}
                  <button type="button" className="link" onClick={() => play(r.source_id, l.start, l.end)}>
                    ▶ Play
                  </button>
                </li>
              ))}
              {r.unmatched.map((u) => (
                <li key={u.start} className="pickup-unmatched">
                  <span className="chip">Matches no line</span> <span className="muted">“{u.text}”</span>{" "}
                  <button type="button" className="link" onClick={() => play(r.source_id, u.start, u.end)}>
                    ▶ Play
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}

      {toChoose.length > 0 && (
        <div className="pickup-choose">
          <h3>Choose between takes</h3>
          <p className="muted">
            {plural(toChoose.length, "line")} {toChoose.length === 1 ? "has" : "have"} more than one good take. Listen and
            pick the one to keep.
          </p>
          <TakesPanel
            sources={sources}
            revision={revision}
            scope={scope}
            groupIds={toChoose}
            onEdited={onEdited}
            onError={onError}
          />
        </div>
      )}
    </section>
  );
}
