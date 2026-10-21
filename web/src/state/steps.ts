// The workflow as steps (D-060): one screen each, in the order the work is done,
// each with a status so the sidebar shows where things stand and what's next.

import type { Comparison, Issue, ProjectSummary, Region, TakeGroup } from "./engine";
import { hasAudio, hasItem, hasSource, hasUnit, type Scope } from "./scope";
import { status as lineStatus } from "./comparison";

export type StepKey =
  | "overview"
  | "recordings"
  | "manuscript"
  | "compare"
  | "issues"
  | "cleanup"
  | "takes"
  | "timeline"
  | "export";

export interface StepDef {
  key: StepKey;
  title: string;
  /** What the step is for, in one sentence, shown under its title. */
  blurb: string;
}

export const STEPS: StepDef[] = [
  { key: "overview", title: "Overview", blurb: "Where things stand, and what to do next." },
  {
    key: "recordings",
    title: "Recordings",
    blurb: "Add the narration: any number of WAV or MP3 files. The originals are never changed.",
  },
  {
    key: "manuscript",
    title: "Manuscript",
    blurb: "Add the book's text, so the narration can be checked against it line by line.",
  },
  {
    key: "compare",
    title: "Check the reading",
    blurb: "Transcribe the narration on this computer and compare it with the manuscript.",
  },
  {
    key: "issues",
    title: "Fix mistakes",
    blurb: "Lines read wrong or skipped, and pauses that run long. Listen to each, then mark it fixed or OK.",
  },
  {
    key: "cleanup",
    title: "Clean up",
    blurb: "Stutters, false starts and off-script remarks that were cut, or are suggested for cutting.",
  },
  { key: "takes", title: "Choose takes", blurb: "Lines read more than once: pick the reading to keep." },
  { key: "timeline", title: "Edit", blurb: "The edited narration on a timeline, for fine-tuning by hand." },
  {
    key: "export",
    title: "Export",
    blurb: "Even out the levels, export the finished audio, and make a corrections list for the narrator.",
  },
];

export const STEP_KEYS = STEPS.map((s) => s.key);

export function stepDef(key: StepKey): StepDef {
  return STEPS.find((s) => s.key === key) ?? STEPS[0]!;
}

/** done: nothing left here. todo: the next thing to do. attention: something to
 * review (badge says how many). blocked: needs an earlier step. idle: optional. */
export type StepState = "done" | "todo" | "attention" | "blocked" | "idle";

export interface StepStatus {
  state: StepState;
  badge?: number;
  note: string; // a short reason, for the sidebar's tooltip and the overview
}

/** What the steps' statuses are worked out from; null = not loaded yet. */
export interface Progress {
  project: ProjectSummary;
  scope: Scope;
  comparison: Comparison | null;
  issues: Issue[] | null;
  regions: Region[] | null;
  takes: TakeGroup[] | null;
}

export function stepStatuses(p: Progress): Record<StepKey, StepStatus> {
  const { project, scope } = p;
  const sources = project.sources.filter((s) => hasSource(scope, s.id));
  const anyAudio = project.sources.length > 0;
  const compared = p.comparison !== null;
  const ready = anyAudio && project.manuscript !== null;

  const recordings: StepStatus = sources.length
    ? { state: "done", note: `${sources.length} recording${sources.length === 1 ? "" : "s"}` }
    : scope && anyAudio && compared
      ? { state: "todo", note: "No recording of this chapter yet" }
      : { state: anyAudio ? "done" : "todo", note: anyAudio ? "Recordings added" : "Add the narration" };

  const manuscript: StepStatus = project.manuscript
    ? { state: "done", note: project.manuscript.original_name }
    : { state: "todo", note: "Add the book's text" };

  let compare: StepStatus;
  if (!ready) compare = { state: "blocked", note: "Needs recordings and the manuscript" };
  else if (!compared) compare = { state: "todo", note: "Not checked yet" };
  else if (p.comparison?.stale) compare = { state: "attention", note: "The manuscript changed: check again" };
  else {
    const lines = p.comparison!.sentences.filter((s) => hasUnit(scope, s.unit_id));
    const notRecorded = lines.filter((s) => lineStatus(s) === "not-recorded").length;
    compare = {
      state: "done",
      note: notRecorded ? `${notRecorded} line${notRecorded === 1 ? "" : "s"} not recorded yet` : "Checked",
    };
  }

  const open = (p.issues ?? []).filter(
    (i) =>
      i.status === "open" &&
      hasItem(scope, { unitId: i.manuscript_unit_id, sourceId: i.source_file_id, start: i.start, end: i.end }),
  ).length;
  const issues: StepStatus = !compared && !p.issues?.length
    ? { state: "blocked", note: "Check the reading first" }
    : open
      ? { state: "attention", badge: open, note: `${open} to review` }
      : { state: "done", note: "Nothing to review" };

  const suggested = (p.regions ?? []).filter(
    (r) => r.kind !== "take" && r.status === "suggested" && hasAudio(scope, r.source_file_id, r.start, r.end),
  ).length;
  const cleanup: StepStatus = !compared
    ? { state: "blocked", note: "Check the reading first" }
    : suggested
      ? { state: "attention", badge: suggested, note: `${suggested} suggested cut${suggested === 1 ? "" : "s"}` }
      : { state: "done", note: "Nothing suggested" };

  const toChoose = (p.takes ?? []).filter(
    (g) =>
      g.state === "suggested" &&
      !g.confident &&
      hasItem(scope, {
        unitId: g.manuscript_unit_id,
        sourceId: g.takes[0]?.source_file_id ?? null,
        start: g.takes[0]?.start ?? null,
      }),
  ).length;
  const takes: StepStatus = !compared
    ? { state: "blocked", note: "Check the reading first" }
    : toChoose
      ? { state: "attention", badge: toChoose, note: `${toChoose} to listen to` }
      : { state: "done", note: "All chosen" };

  return {
    overview: { state: "idle", note: "" },
    recordings,
    manuscript,
    compare,
    issues,
    cleanup,
    takes,
    timeline: anyAudio ? { state: "idle", note: "Optional fine-tuning" } : { state: "blocked", note: "Add recordings first" },
    export: anyAudio ? { state: "idle", note: "When you're done" } : { state: "blocked", note: "Add recordings first" },
  };
}

/** The step to suggest next: the first that's waiting on the user. */
export function nextUp(statuses: Record<StepKey, StepStatus>): StepKey | null {
  for (const key of STEP_KEYS) {
    if (key === "overview") continue;
    const s = statuses[key];
    if (s.state === "todo" || s.state === "attention") return key;
  }
  return null;
}

/** Where a project opens: where the work is, for a project with nothing in it yet
 * that's adding recordings. */
export function startStep(project: ProjectSummary): StepKey {
  if (!project.sources.length) return "recordings";
  if (!project.manuscript) return "manuscript";
  return "overview";
}
