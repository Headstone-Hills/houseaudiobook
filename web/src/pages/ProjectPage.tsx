import { useEffect, useState } from "react";
import { ChapterPicker } from "../components/ChapterPicker";
import { ComparisonPanel } from "../components/ComparisonPanel";
import { ExportPanel, FinalizePanel, GuidePanel } from "../components/FinishPanels";
import { ImportPanel } from "../components/ImportPanel";
import { IssuesPanel } from "../components/IssuesPanel";
import { JobsPanel } from "../components/JobsPanel";
import { ManuscriptPanel } from "../components/ManuscriptPanel";
import { Overview } from "../components/Overview";
import { RemovedPanel } from "../components/RemovedPanel";
import { SourceTable } from "../components/SourceTable";
import { StepNav } from "../components/StepNav";
import { TakesPanel } from "../components/TakesPanel";
import { TimelinePanel } from "../components/TimelinePanel";
import { Waveform } from "../components/Waveform";
import {
  api,
  type Chapter,
  type Comparison,
  type Issue,
  type Job,
  type PageSettings,
  type PeakSettings,
  type ProjectSummary,
  type Region,
  type TakeGroup,
} from "../state/engine";
import { chapterLabel, hasSource, type Scope } from "../state/scope";
import { STEP_KEYS, startStep, stepDef, stepStatuses, type StepKey } from "../state/steps";
import { useStored } from "../state/stored";
import { useUndoShortcuts } from "../state/undo";

interface Props {
  project: ProjectSummary;
  jobs: Job[];
  peakSettings: PeakSettings;
  pageDefaults: PageSettings;
  onChanged: (p: ProjectSummary) => void;
  onClose: () => void;
}

/** A book: a sidebar of steps, one step on screen at a time (D-060), each narrowed
 * to the chapter being worked on when one is chosen (D-059). */
export function ProjectPage({ project, jobs, peakSettings, pageDefaults, onChanged, onClose }: Props) {
  const [stored, setStep] = useStored<StepKey>(`hillaudio:step:${project.path}`, startStep(project));
  const step: StepKey = STEP_KEYS.includes(stored) ? stored : "overview";
  const [chapterId, setChapterId] = useStored<number | null>(`hillaudio:chapter:${project.path}`, null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [edlRevision, setEdlRevision] = useState(0);
  const [reviewRevision, setReviewRevision] = useState(0);
  const edited = () => setEdlRevision((n) => n + 1);
  const reviewed = () => setReviewRevision((n) => n + 1);
  const addError = (msg: string) => setErrors((e) => [...e, msg]);
  // Undo and redo work on every step (the edit history is the book's, not a
  // panel's); "nothing to undo" is not an error worth showing.
  useUndoShortcuts(
    () => void api.undo().then(edited, () => undefined),
    () => void api.redo().then(edited, () => undefined),
  );

  // Per-file failures inside a finished import job are shown alongside other errors.
  const importErrors = jobs
    .filter((j) => j.kind === "import" && j.status === "done")
    .flatMap((j) => ((j.result as { files?: { name: string; error?: string }[] })?.files ?? []))
    .filter((f) => f.error)
    .map((f) => `${f.name}: ${f.error}`);

  const manuscriptRevision = jobs.filter((j) => j.kind === "manuscript" && j.status === "done").length;
  const analysisRevision = jobs.filter((j) => j.kind === "analysis" && j.status === "done").length;
  const modelRevision = jobs.filter((j) => j.kind === "model" && j.status === "done").length;
  const dataRevision = analysisRevision + manuscriptRevision;
  const analysisBlocker = !project.sources.length
    ? "Add recordings first."
    : !project.manuscript
      ? "Add the manuscript first."
      : !project.functions.includes("transcribe")
        ? "Transcribing is turned off for this book."
        : null;

  // Chapters, and what the step statuses are worked out from. Reloaded when work
  // finishes or an edit or review changes the counts.
  const [progress, setProgress] = useState<{
    comparison: Comparison | null;
    issues: Issue[] | null;
    regions: Region[] | null;
    takes: TakeGroup[] | null;
  }>({ comparison: null, issues: null, regions: null, takes: null });
  const progressKey = `${dataRevision}:${edlRevision}:${reviewRevision}:${project.sources.length}:${project.manuscript?.mapped_at ?? ""}`;
  useEffect(() => {
    let cancelled = false;
    const none = <T,>() => null as T | null;
    Promise.all([
      api.chapters().catch(() => [] as Chapter[]),
      api.comparison().catch(none<Comparison>),
      api.issues().then((d) => d.issues, none<Issue[]>),
      api.edl().then((d) => d.regions, none<Region[]>),
      api.takes().then((d) => d.groups, none<TakeGroup[]>),
    ])
      .then(([ch, comparison, issues, regions, takes]) => {
        if (cancelled) return;
        setChapters(ch);
        setProgress({ comparison, issues, regions, takes });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [progressKey]);

  const scope: Scope = chapters.find((c) => c.id === chapterId) ?? null;
  const statuses = stepStatuses({ project, scope, ...progress });
  const at = STEP_KEYS.indexOf(step);
  const prev = at > 0 ? STEP_KEYS[at - 1] : undefined;
  const next = at < STEP_KEYS.length - 1 ? STEP_KEYS[at + 1] : undefined;

  const go = (key: StepKey) => {
    setStep(key);
    document.getElementById("step-title")?.focus();
  };

  const sources = showAll ? project.sources : project.sources.filter((s) => hasSource(scope, s.id));
  const selected = sources.find((s) => s.id === selectedId) ?? sources[0];
  const notices = [...project.warnings, ...importErrors, ...errors];

  const body = (() => {
    switch (step) {
      case "overview":
        return (
          <Overview
            scope={scope}
            chapters={chapters}
            sources={project.sources}
            statuses={statuses}
            onStep={go}
            onChapter={setChapterId}
          />
        );
      case "recordings":
        return (
          <>
            <section aria-labelledby="sources-heading" className="card">
              <h2 id="sources-heading">
                {scope && !showAll ? `Recordings with ${chapterLabel(scope)} in them` : "Recordings"}
              </h2>
              {scope && (
                <p className="muted">
                  {sources.length || showAll ? "" : "No recording has this chapter in it yet. "}
                  <button type="button" className="link" onClick={() => setShowAll((v) => !v)}>
                    {showAll ? "Show only this chapter's recordings" : `Show all ${project.sources.length} recordings`}
                  </button>
                </p>
              )}
              <SourceTable
                sources={sources}
                selectedId={selected?.id ?? null}
                onSelect={setSelectedId}
                onRelinked={onChanged}
                onError={addError}
              />
              {selected && selected.status !== "missing" && <Waveform source={selected} peakSettings={peakSettings} />}
            </section>
            <ImportPanel onError={addError} />
          </>
        );
      case "manuscript":
        return (
          <>
            {scope && (
              <p className="note">The manuscript is the whole book&apos;s: chapters are found from its headings.</p>
            )}
            <ManuscriptPanel
              info={project.manuscript}
              defaults={pageDefaults}
              revision={manuscriptRevision}
              onError={addError}
            />
          </>
        );
      case "compare":
        return (
          <ComparisonPanel
            canRun={analysisBlocker === null}
            why={analysisBlocker}
            revision={dataRevision}
            modelRevision={modelRevision}
            scope={scope}
            onError={addError}
          />
        );
      case "issues":
        return (
          <IssuesPanel
            checks={project.functions.filter((f) => ["cleanup", "takes", "flubs", "silences"].includes(f))}
            canRun={project.sources.length > 0}
            revision={dataRevision}
            scope={scope}
            onEdited={edited}
            onDecided={reviewed}
            onError={addError}
          />
        );
      case "cleanup":
        return (
          <RemovedPanel
            revision={dataRevision + edlRevision}
            scope={scope}
            undoKeys={false}
            onEdited={edited}
            onError={addError}
          />
        );
      case "takes":
        return (
          <TakesPanel
            sources={project.sources}
            revision={dataRevision + edlRevision}
            scope={scope}
            onEdited={edited}
            onError={addError}
          />
        );
      case "timeline":
        return project.sources.length ? (
          <TimelinePanel
            sources={project.sources}
            peakSettings={peakSettings}
            revision={dataRevision + edlRevision + project.sources.length}
            focus={scope?.timeline ?? null}
            onEdited={edited}
            onError={addError}
          />
        ) : (
          <p className="empty">Add recordings first: the timeline is built from them.</p>
        );
      case "export":
        return project.sources.length ? (
          <>
            <FinalizePanel jobs={jobs} onError={addError} />
            <ExportPanel jobs={jobs} chapter={scope} onError={addError} />
            <GuidePanel revision={dataRevision + edlRevision} chapter={scope} />
          </>
        ) : (
          <p className="empty">Add recordings first.</p>
        );
    }
  })();

  // Steps that are one panel show the step title only; their own heading would repeat it.
  const solo = ["compare", "issues", "cleanup", "takes", "timeline", "manuscript"].includes(step);

  return (
    <div className="project">
      <header className="topbar">
        <span className="brand" aria-hidden="true">
          HillAudiobook
        </span>
        <h1 title={project.path}>{project.name}</h1>
        <ChapterPicker chapters={chapters} value={scope ? scope.id : null} onChange={setChapterId} />
        <button type="button" className="ghost" onClick={onClose}>
          Close project
        </button>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <StepNav current={step} statuses={statuses} onSelect={go} />
          <JobsPanel jobs={jobs} />
        </aside>
        <main className="step" aria-labelledby="step-title">
          {notices.length > 0 && (
            <ul role="alert" className="notices">
              {project.warnings.map((w) => (
                <li key={w} className="warning">
                  {w}
                </li>
              ))}
              {[...importErrors, ...errors].map((e, i) => (
                <li key={`${i}-${e}`} className="error">
                  {e}{" "}
                  {i >= importErrors.length && (
                    <button
                      type="button"
                      className="link"
                      onClick={() => setErrors((all) => all.filter((_, k) => k !== i - importErrors.length))}
                    >
                      Dismiss
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="step-head">
            <p className="step-context">
              {scope ? chapterLabel(scope) : "Whole book"}
              {step !== "overview" && ` · Step ${at} of ${STEP_KEYS.length - 1}`}
            </p>
            <h2 id="step-title" tabIndex={-1}>
              {stepDef(step).title}
            </h2>
            <p className="step-blurb">{stepDef(step).blurb}</p>
            {step !== "overview" && statuses[step].state === "blocked" && (
              <p className="note">{statuses[step].note}.</p>
            )}
          </div>
          <div className={`step-body${solo ? " solo" : ""}`}>{body}</div>
          <footer className="step-footer">
            {prev ? (
              <button type="button" onClick={() => go(prev)}>
                ← {stepDef(prev).title}
              </button>
            ) : (
              <span />
            )}
            {next && (
              <button type="button" className="primary" onClick={() => go(next)}>
                Next: {stepDef(next).title} →
              </button>
            )}
          </footer>
        </main>
      </div>
    </div>
  );
}
