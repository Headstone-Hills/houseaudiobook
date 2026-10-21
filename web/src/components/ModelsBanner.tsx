import { useEffect, useState } from "react";
import { api, type Job, type SpeechModel } from "../state/engine";

interface Props {
  jobs: Job[];
  defaultModel: string;
}

/** First run (M9): what speech recognition needs, how big it is and where it
 * comes from, downloaded only when the user asks (D-055). */
export function ModelsBanner({ jobs, defaultModel }: Props) {
  const [models, setModels] = useState<SpeechModel[] | null>(null);
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);
  const modelJobs = jobs.filter((j) => j.kind === "model");
  const finished = modelJobs.filter((j) => j.status === "done" || j.status === "failed").length;

  useEffect(() => {
    api
      .models()
      .then((r) => {
        setModels(r.models);
        setSource(r.source);
      })
      .catch(() => undefined);
  }, [finished]);

  if (!models) return null;
  const jobFor = (m: SpeechModel) =>
    modelJobs.find((j) => j.label === `Download ${m.name}` && (j.status === "running" || j.status === "queued"));
  const failed = modelJobs.filter((j) => j.status === "failed").at(-1);
  const wanted = models.find((m) => m.name === defaultModel);
  const download = (m: SpeechModel) => {
    setError(null);
    api.downloadModel(m.name).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  const row = (m: SpeechModel) => {
    const j = jobFor(m);
    return (
      <li key={m.name}>
        <strong>{m.name}</strong> · {m.size_mb} MB · {m.license} ·{" "}
        {m.installed ? (
          <>
            downloaded{" "}
            <button
              type="button"
              onClick={() => {
                if (window.confirm(`Remove the ${m.name} model (${m.size_mb} MB)? You can download it again.`)) {
                  api
                    .removeModel(m.name)
                    .then(() => api.models())
                    .then((r) => setModels(r.models))
                    .catch(() => undefined);
                }
              }}
            >
              Remove
            </button>
          </>
        ) : j ? (
          <span role="status">
            <progress max={1} value={j.progress} aria-label={`Downloading ${m.name}`} /> {j.message}{" "}
            <button type="button" onClick={() => api.cancelJob(j.id).catch(() => undefined)}>
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" onClick={() => download(m)}>
            Download {m.name}
          </button>
        )}
      </li>
    );
  };

  return (
    <aside className="models-banner" aria-label="Speech models">
      {wanted && !wanted.installed && (
        <p>
          <strong>Speech recognition needs a model.</strong> The recommended {wanted.name} model is {wanted.size_mb} MB,
          downloaded once from {source.replace(/^https?:\/\//, "")} ({wanted.license} license). Your audio and text never
          leave this computer.
        </p>
      )}
      {wanted && !wanted.installed && <ul className="models">{row(wanted)}</ul>}
      {(failed?.error || error) && (
        <p role="alert" className="error">
          {error ?? failed?.error}
        </p>
      )}
      <details>
        <summary>Speech models</summary>
        <ul className="models">{models.map(row)}</ul>
        <p className="muted">
          Pinned versions from {source}; every file is checked before it is used. An interrupted download resumes.
        </p>
      </details>
    </aside>
  );
}
