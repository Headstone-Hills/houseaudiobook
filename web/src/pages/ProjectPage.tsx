import { useState } from "react";
import { ImportPanel } from "../components/ImportPanel";
import { JobsPanel } from "../components/JobsPanel";
import { ComparisonPanel } from "../components/ComparisonPanel";
import { IssuesPanel } from "../components/IssuesPanel";
import { ManuscriptPanel } from "../components/ManuscriptPanel";
import { RemovedPanel } from "../components/RemovedPanel";
import { TakesPanel } from "../components/TakesPanel";
import { TimelinePanel } from "../components/TimelinePanel";
import { ExportPanel, FinalizePanel, GuidePanel } from "../components/FinishPanels";
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
  const [edlRevision, setEdlRevision] = useState(0);
  const edited = () => setEdlRevision((n) => n + 1);
  const addError = (msg: string) => setErrors((e) => [...e, msg]);

  // Per-file failures inside a finished import job are shown alongside other errors.
  const importErrors = jobs
    .filter((j) => j.kind === "import" && j.status === "done")
    .flatMap((j) => ((j.result as { files?: { name: string; error?: string }[] })?.files ?? []))
    .filter((f) => f.error)
    .map((f) => `${f.name}: ${f.error}`);

  const selected = project.sources.find((s) => s.id === selectedId) ?? project.sources[0];
  const manuscriptRevision = jobs.filter((j) => j.kind === "manuscript" && j.status === "done").length;
  const analysisRevision = jobs.filter((j) => j.kind === "analysis" && j.status === "done").length;
  const modelRevision = jobs.filter((j) => j.kind === "model" && j.status === "done").length;
  const analysisBlocker = !project.sources.length
    ? "Import audio first."
    : !project.manuscript
      ? "Import the manuscript first."
      : !project.functions.includes("transcribe")
        ? "Transcribe is turned off for this project."
        : null;

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

      <ComparisonPanel
        canRun={analysisBlocker === null}
        why={analysisBlocker}
        revision={analysisRevision + manuscriptRevision}
        modelRevision={modelRevision}
        onError={addError}
      />

      <IssuesPanel
        checks={project.functions.filter((f) => ["cleanup", "takes", "flubs", "silences"].includes(f))}
        canRun={project.sources.length > 0}
        revision={analysisRevision + manuscriptRevision}
        onEdited={edited}
        onError={addError}
      />

      <TakesPanel
        sources={project.sources}
        revision={analysisRevision + manuscriptRevision + edlRevision}
        onEdited={edited}
        onError={addError}
      />

      <RemovedPanel revision={analysisRevision + manuscriptRevision + edlRevision} onEdited={edited} onError={addError} />

      {project.sources.length > 0 && (
        <TimelinePanel
          sources={project.sources}
          peakSettings={peakSettings}
          revision={analysisRevision + manuscriptRevision + edlRevision + project.sources.length}
          onEdited={edited}
          onError={addError}
        />
      )}

      {project.sources.length > 0 && (
        <>
          <FinalizePanel jobs={jobs} onError={addError} />
          <ExportPanel jobs={jobs} onError={addError} />
          <GuidePanel revision={analysisRevision + manuscriptRevision + edlRevision} />
        </>
      )}
    </main>
  );
}
