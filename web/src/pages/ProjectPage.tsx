import { useState } from "react";
import { ImportPanel } from "../components/ImportPanel";
import { JobsPanel } from "../components/JobsPanel";
import { ManuscriptPanel } from "../components/ManuscriptPanel";
import { SourceTable } from "../components/SourceTable";
import { Waveform } from "../components/Waveform";
import type { Job, PageSettings, PeakSettings, ProjectSummary } from "../state/engine";

interface Props {
  project: ProjectSummary;
  jobs: Job[];
  peakSettings: PeakSettings;
  pageDefaults: PageSettings;
  onChanged: (p: ProjectSummary) => void;
  onClose: () => void;
}

export function ProjectPage({ project, jobs, peakSettings, pageDefaults, onChanged, onClose }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const addError = (msg: string) => setErrors((e) => [...e, msg]);

  // Per-file failures inside a finished import job are shown alongside other errors.
  const importErrors = jobs
    .filter((j) => j.kind === "import" && j.status === "done")
    .flatMap((j) => ((j.result as { files?: { name: string; error?: string }[] })?.files ?? []))
    .filter((f) => f.error)
    .map((f) => `${f.name}: ${f.error}`);

  const selected = project.sources.find((s) => s.id === selectedId) ?? project.sources[0];
  const manuscriptRevision = jobs.filter((j) => j.kind === "manuscript" && j.status === "done").length;

  return (
    <main>
      <header className="project-header">
        <h1>{project.name}</h1>
        <span className="muted">{project.path}</span>
        <button type="button" onClick={onClose}>
          Close project
        </button>
      </header>

      {[...project.warnings, ...importErrors, ...errors].length > 0 && (
        <ul role="alert" className="notices">
          {project.warnings.map((w) => (
            <li key={w} className="warning">
              {w}
            </li>
          ))}
          {[...importErrors, ...errors].map((e, i) => (
            <li key={`${i}-${e}`} className="error">
              {e}
            </li>
          ))}
        </ul>
      )}

      <JobsPanel jobs={jobs} />
      <ImportPanel onError={addError} />

      <section aria-label="Sources">
        <h2>Sources</h2>
        <SourceTable
          sources={project.sources}
          selectedId={selected?.id ?? null}
          onSelect={setSelectedId}
          onRelinked={onChanged}
          onError={addError}
        />
        {selected && selected.status !== "missing" && (
          <Waveform source={selected} peakSettings={peakSettings} />
        )}
      </section>

      <ManuscriptPanel
        info={project.manuscript}
        defaults={pageDefaults}
        revision={manuscriptRevision}
        onError={addError}
      />
    </main>
  );
}
