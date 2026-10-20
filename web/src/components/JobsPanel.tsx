import { api, type Job } from "../state/engine";

/** Background work with real progress; anything running can be cancelled (SPEC §7). */
export function JobsPanel({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) return null;
  return (
    <section aria-label="Background jobs" className="jobs">
      <h2>Jobs</h2>
      <ul>
        {jobs.map((j) => (
          <li key={j.id} className={`job job-${j.status}`}>
            <span className="job-label">{j.label}</span>
            <progress value={j.progress} max={1} aria-label={`${j.label} progress`} />
            <span className="job-message" aria-live="polite">
              {j.status === "failed" ? `failed: ${j.error ?? ""}` : j.status === "running" ? j.message : j.status}
            </span>
            {(j.status === "running" || j.status === "queued") && (
              <button type="button" onClick={() => void api.cancelJob(j.id)}>
                Cancel
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
