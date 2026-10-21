import { useEffect, useRef, useState } from "react";
import {
  api,
  exportFileUrl,
  finalizePreviewUrl,
  renderUrl,
  type ExportFormat,
  type ExportReport,
  type FinalizeSettings,
  type FinalizeView,
  type GuideRow,
  type Job,
  type Preflight,
  type SplitMode,
} from "../state/engine";
import { plural } from "../state/format";
import { chapterLabel, type Scope } from "../state/scope";

const SPLITS: [SplitMode, string][] = [
  ["single", "One file"],
  ["chapters", "One file per chapter"],
  ["markers", "One file per marker section"],
];

interface Common {
  jobs: Job[];
  onError: (msg: string) => void;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The job with this id, as the job feed last reported it. */
function useJob(id: string | null, jobs: Job[]): Job | null {
  return id ? (jobs.find((j) => j.id === id) ?? null) : null;
}

function Report({ report, caption }: { report: ExportReport; caption: string }) {
  return (
    <table className="report">
      <caption>
        {caption}: {report.pass ? "passes" : "does not pass"}
      </caption>
      <thead>
        <tr>
          <th scope="col">File</th>
          {report.files[0]?.checks.map((c) => (
            <th scope="col" key={c.name}>
              {c.name} <span className="muted">({c.limit})</span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {report.files.map((f) => (
          <tr key={f.title}>
            <th scope="row">
              {f.file ? (
                <a href={exportFileUrl(f.file)} download>
                  {f.file.split("/").pop()}
                </a>
              ) : (
                f.title
              )}
            </th>
            {f.checks.map((c) => (
              <td key={c.name} className={c.pass ? "pass" : "fail"}>
                {typeof c.value === "boolean" ? (c.value ? "yes" : "no") : c.value} {c.pass ? "✓" : "✗ fails"}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// -- Finalize -----------------------------------------------------------------------------------

/** The master-bus chain (SPEC §6.8): settings, A/B preview, and a levels report. */
export function FinalizePanel({ jobs, onError }: Common) {
  const [view, setView] = useState<FinalizeView | null>(null);
  const [draft, setDraft] = useState<FinalizeSettings | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [ab, setAb] = useState({ start: 0, end: 20 });
  const audio = useRef<HTMLAudioElement>(null);
  const job = useJob(jobId, jobs);

  useEffect(() => {
    api
      .finalize()
      .then((v) => {
        setView(v);
        setDraft(v.settings);
      })
      .catch(() => undefined);
  }, [job?.status]);

  if (!view || !draft) return null;
  const preset = view.presets[draft.preset];
  const report = job?.status === "done" ? (job.result as ExportReport) : view.last_analysis;

  const save = (s: Partial<FinalizeSettings>) => {
    const next = { ...draft, ...s };
    setDraft(next);
    api
      .saveFinalize(s)
      .then((v) => setView(v))
      .catch((e: unknown) => {
        onError(message(e));
        setDraft(view.settings);
      });
  };
  const play = (url: string) => {
    if (!audio.current) return;
    audio.current.src = url;
    audio.current.play()?.catch(() => undefined);
  };
  const num = (key: keyof FinalizeSettings, label: string, step = 0.5, min?: number, max?: number) => (
    <label>
      {label}
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={draft[key] as number}
        onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })}
        onBlur={(e) => save({ [key]: Number(e.target.value) })}
      />
    </label>
  );
  const check = (key: keyof FinalizeSettings, label: string) => (
    <label>
      <input type="checkbox" checked={draft[key] as boolean} onChange={(e) => save({ [key]: e.target.checked })} />
      {label}
    </label>
  );

  return (
    <section aria-labelledby="finalize-heading" className="finalize">
      <h2 id="finalize-heading">Finalize</h2>
      <p className="muted">
        A chain on the master bus, applied when you export. Nothing is changed in the recordings.
      </p>
      <div className="run-row">
        <label>
          Preset
          <select value={draft.preset} onChange={(e) => save({ preset: e.target.value })}>
            {Object.values(view.presets).map((p) => (
              <option key={p.key} value={p.key}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        {preset && (
          <span className="muted">
            RMS {preset.rms_min_db} to {preset.rms_max_db} dB · peak ≤ {preset.peak_max_db} dB · noise floor ≤{" "}
            {preset.noise_floor_max_db} dB · room tone {preset.head_s} s / {preset.tail_s} s
          </span>
        )}
      </div>
      {preset?.note && <p className="muted">{preset.note}</p>}
      <fieldset className="chain">
        <legend>Chain</legend>
        <div>
          {check("noise_reduction", "Noise reduction")}
          {num("nr_strength", "Strength (0–1)", 0.05, 0, 1)}
          {num("nr_reduction_db", "Max reduction (dB)", 1, 1, 40)}
          <span className="muted">
            Room tone:{" "}
            {draft.noise_region
              ? `recording ${draft.noise_region.source_id}, ${draft.noise_region.start.toFixed(1)}–${draft.noise_region.end.toFixed(1)} s`
              : "the quietest second, found automatically"}
          </span>
        </div>
        <div>
          {check("gate", "Gate between phrases")}
          {num("gate_threshold_db", "Threshold (dB)", 1, -90, -20)}
          {num("gate_range_db", "Range (dB)", 1, 1, 40)}
        </div>
        <div>
          {check("auto_level", "Auto-level (clip riding + gentle compression)")}
          {num("compressor_threshold_db", "Threshold (dB)", 1, -60, 0)}
          {num("compressor_ratio", "Ratio", 0.5, 1, 10)}
        </div>
        <div>
          {check("normalize", "Normalize")}
          {num("target_rms_db", "Target RMS (dB)", 0.5, -40, -6)}
          {check("limiter", "Limiter")}
          {num("ceiling_db", "Ceiling (dB)", 0.1, -20, 0)}
        </div>
      </fieldset>
      <div className="run-row">
        <label>
          A/B from (s)
          <input type="number" min={0} value={ab.start} onChange={(e) => setAb({ ...ab, start: Number(e.target.value) })} />
        </label>
        <label>
          to (s)
          <input type="number" min={0} value={ab.end} onChange={(e) => setAb({ ...ab, end: Number(e.target.value) })} />
        </label>
        <button type="button" onClick={() => play(renderUrl(ab.start, ab.end))}>
          A: as edited
        </button>
        <button type="button" onClick={() => play(finalizePreviewUrl(ab.start, ab.end))}>
          B: finalized
        </button>
        <audio ref={audio} controls aria-label="Finalize preview audio" />
      </div>
      <div className="run-row">
        <button
          type="button"
          onClick={() =>
            api
              .analyze("single")
              .then((r) => setJobId(r.job_id))
              .catch((e: unknown) => onError(message(e)))
          }
          disabled={job?.status === "running" || job?.status === "queued"}
        >
          Analyze levels
        </button>
        {job && job.status !== "done" && (
          <span role="status">
            {job.status === "failed" ? `Failed: ${job.error}` : `${job.message || job.status} ${Math.round(job.progress * 100)}%`}
          </span>
        )}
      </div>
      {report && <Report report={report} caption="Levels after finalize" />}
    </section>
  );
}

// -- Export ------------------------------------------------------------------------------------

/** Export (SPEC §6.9): pre-flight first, then the files, with a report on each. */
/** `chapter`: a chapter workspace exports just that chapter (D-059). */
export function ExportPanel({ jobs, chapter = null, onError }: Common & { chapter?: Scope }) {
  const [chosen, setChosen] = useState<ExportFormat | null>(null);
  const [split, setSplit] = useState<SplitMode>("single");
  const [kbps, setKbps] = useState(192);
  const [pf, setPf] = useState<Preflight | null>(null);
  const [past, setPast] = useState<ExportReport[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId, jobs);
  // Until the user picks a format, a book with lossy recordings defaults to WAV:
  // MP3 would re-encode them, which is never done (D-058).
  const lossy = pf?.lossy_sources ?? [];
  const format: ExportFormat = chosen ?? (lossy.length > 0 ? "wav24" : "mp3");

  const chapterId = chapter?.id ?? null;
  useEffect(() => {
    api
      .preflight(format, split, chapterId)
      .then(setPf)
      .catch(() => undefined);
  }, [format, split, chapterId, job?.status]);
  useEffect(() => {
    api
      .exports()
      .then(setPast)
      .catch(() => undefined);
  }, [job?.status]);

  const issues = pf ? Object.entries(pf.open_issues) : [];
  const busy = job?.status === "running" || job?.status === "queued";

  return (
    <section aria-labelledby="export-heading" className="export">
      <h2 id="export-heading">Export</h2>
      <div className="run-row">
        <label>
          Format
          <select
            value={format}
            onChange={(e) => setChosen(e.target.value as ExportFormat)}
          >
            <option value="mp3">MP3 (CBR)</option>
            <option value="wav24">WAV 24-bit</option>
            <option value="wav16">WAV 16-bit</option>
          </select>
        </label>
        {format === "mp3" && (
          <label>
            Bitrate (kbps)
            <select value={kbps} onChange={(e) => setKbps(Number(e.target.value))}>
              {[192, 256, 320].map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
        )}
        {chapter ? (
          <span className="muted">One file: {chapterLabel(chapter)}</span>
        ) : (
          <label>
            Files
            <select value={split} onChange={(e) => setSplit(e.target.value as SplitMode)}>
              {SPLITS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {pf && (
        <div className="preflight" aria-label="Pre-flight summary">
          <h3>Before you export</h3>
          <ul>
            {lossy.length > 0 && format !== "mp3" && (
              <li>
                Some recordings are lossy ({lossy.join(", ")}), so this exports as WAV: MP3 would re-encode them,
                and lossy to lossy is never done. Encode the WAV to MP3 elsewhere if your distributor needs MP3.
              </li>
            )}
            <li>
              {issues.length
                ? `Unresolved issues: ${issues.map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ")}`
                : "No unresolved issues."}
            </li>
            <li>
              {plural(pf.outputs.length, "file")}: {pf.outputs.map((o) => `${o.title} (${(o.seconds / 60).toFixed(1)} min)`).join("; ")}
            </li>
            <li>
              {pf.last_analysis
                ? `Last levels check: ${pf.last_analysis.pass ? "passes" : "does not pass"} (Finalize → Analyze to refresh)`
                : "Levels not analyzed yet (Finalize → Analyze levels)."}
            </li>
            {pf.warnings.map((w) => (
              <li key={w} className="warning">
                {w}
              </li>
            ))}
            {pf.problems.map((p) => (
              <li key={p} className="error" role="alert">
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="run-row">
        <button
          type="button"
          className="primary"
          disabled={busy || !pf || pf.problems.length > 0}
          onClick={() =>
            api
              .exportAudio(format, split, format === "mp3" ? kbps : null, chapterId)
              .then((r) => setJobId(r.job_id))
              .catch((e: unknown) => onError(message(e)))
          }
        >
          {chapter ? "Export this chapter" : "Export"}
        </button>
        {job && job.status !== "done" && (
          <span role="status">
            {job.status === "failed" ? `Failed: ${job.error}` : `${job.message || job.status} ${Math.round(job.progress * 100)}%`}
          </span>
        )}
      </div>
      {job?.status === "done" && <Report report={job.result as ExportReport} caption="Exported" />}
      {past.length > 0 && (
        <details>
          <summary>Earlier exports ({past.length})</summary>
          {past.map((r) => (
            <Report key={r.created} report={r} caption={`${r.created} · ${r.format.toUpperCase()}`} />
          ))}
        </details>
      )}
    </section>
  );
}

// -- Guide -------------------------------------------------------------------------------------

/** The narrator corrections guide (SPEC §6.9): preview, then CSV / Markdown / PDF. */
export function GuidePanel({ revision, chapter = null }: { revision: number; chapter?: Scope }) {
  const [reference, setReference] = useState<"original" | "master">("original");
  const [sort, setSort] = useState<"page" | "time">("page");
  const [narrator, setNarrator] = useState("");
  const [silences, setSilences] = useState(false);
  const [resolved, setResolved] = useState(false);
  const [rows, setRows] = useState<GuideRow[]>([]);
  const chapterId = chapter?.id ?? null;
  const query = (): URLSearchParams => {
    const params = new URLSearchParams({
      reference,
      sort,
      include_silences: String(silences),
      include_resolved: String(resolved),
    });
    if (chapterId !== null) params.set("chapter", String(chapterId));
    return params;
  };
  const q = query();
  const qs = q.toString();

  useEffect(() => {
    api
      .guideRows(qs)
      .then((r) => setRows(r.rows))
      .catch(() => undefined);
  }, [qs, revision]);

  const file = (format: string) => `/api/project/guide?${q.toString()}&format=${format}&narrator=${encodeURIComponent(narrator)}`;

  return (
    <section aria-labelledby="guide-heading" className="guide">
      <h2 id="guide-heading">Corrections guide</h2>
      <div className="run-row">
        <label>
          Narrator
          <input value={narrator} onChange={(e) => setNarrator(e.target.value)} />
        </label>
        <label>
          Times refer to
          <select value={reference} onChange={(e) => setReference(e.target.value as "original" | "master")}>
            <option value="original">the original recordings</option>
            <option value="master">the edited master</option>
          </select>
        </label>
        <label>
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as "page" | "time")}>
            <option value="page">by page</option>
            <option value="time">by time</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={silences} onChange={(e) => setSilences(e.target.checked)} /> Long pauses
        </label>
        <label>
          <input type="checkbox" checked={resolved} onChange={(e) => setResolved(e.target.checked)} /> Resolved too
        </label>
      </div>
      <p>
        {plural(rows.length, "correction")} ·{" "}
        <a href={file("pdf")} download>
          PDF
        </a>{" "}
        ·{" "}
        <a href={file("csv")} download>
          CSV
        </a>{" "}
        ·{" "}
        <a href={file("md")} download>
          Markdown
        </a>
      </p>
      {rows.length > 0 && (
        <table className="issue-table">
          <caption className="visually-hidden">Corrections</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Page</th>
              <th scope="col">Time</th>
              <th scope="col">Recording</th>
              <th scope="col">Type</th>
              <th scope="col">Expected</th>
              <th scope="col">Heard</th>
              <th scope="col">Note</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.n}>
                <td>{r.n}</td>
                <td>{r.page}</td>
                <td>{r.time}</td>
                <td>{r.recording}</td>
                <td>{r.type}</td>
                <td>{r.expected}</td>
                <td>{r.heard}</td>
                <td>{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
