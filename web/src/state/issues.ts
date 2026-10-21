// Pure helpers for the Issues panel (SPEC §6.4).

import type { Issue, IssueKind, IssueSeverity, IssueStatus } from "./engine";
import { clock, describe } from "./comparison";

export type SortKey = "page" | "time" | "severity" | "type";

export interface IssueFilter {
  kind: IssueKind | "all";
  status: IssueStatus | "all";
  severity: IssueSeverity | "all";
}

export const KIND_LABELS: Record<IssueKind, string> = {
  flub: "Flub",
  missing_line: "Unread line",
  silence: "Long pause",
};

export const SEVERITY_LABELS: Record<IssueSeverity, string> = {
  high: "High",
  medium: "Medium",
  low: "Check", // probably the recognizer, not the narrator
};

const SEVERITY_RANK: Record<IssueSeverity, number> = { high: 0, medium: 1, low: 2 };
const KIND_RANK: Record<IssueKind, number> = { flub: 0, missing_line: 1, silence: 2 };

export function filterIssues(items: Issue[], f: IssueFilter): Issue[] {
  return items.filter(
    (i) =>
      (f.kind === "all" || i.kind === f.kind) &&
      (f.status === "all" || i.status === f.status) &&
      (f.severity === "all" || i.severity === f.severity),
  );
}

// Unit ids follow manuscript order, so they order issues by page; issues with no
// line (a pause in audio that matched no text) go last.
const byPage = (a: Issue, b: Issue) =>
  (a.manuscript_unit_id ?? Infinity) - (b.manuscript_unit_id ?? Infinity) || byTime(a, b);
const byTime = (a: Issue, b: Issue) =>
  (a.source_file_id ?? Infinity) - (b.source_file_id ?? Infinity) ||
  (a.start ?? Infinity) - (b.start ?? Infinity) ||
  a.id - b.id;

export function sortIssues(items: Issue[], key: SortKey): Issue[] {
  const cmp: Record<SortKey, (a: Issue, b: Issue) => number> = {
    page: byPage,
    time: byTime,
    severity: (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || byPage(a, b),
    type: (a, b) => KIND_RANK[a.kind] - KIND_RANK[b.kind] || byPage(a, b),
  };
  return [...items].sort(cmp[key]);
}

/** "ch01_take1 0:29.7", or "not in any recording". */
export function where(i: Issue): string {
  if (i.source_file_id === null || i.start === null) return "not in any recording";
  return `${i.source_label ?? "?"} ${clock(i.start)}`;
}

/** What's wrong, in one line. */
export function summary(i: Issue): string {
  if (i.kind === "silence") {
    const d = i.detail;
    const at = d.boundary && d.boundary !== "unknown" ? ` at a ${d.boundary} break` : "";
    return `${(d.seconds ?? 0).toFixed(1)} s pause${at} (limit ${d.threshold ?? "?"} s)`;
  }
  if (i.kind === "missing_line") return "Not read in any recording";
  if (i.detail.complete === false) return "Only false starts: never read to the end";
  return (i.detail.diffs ?? []).map(describe).join("; ");
}

export function counts(items: Issue[]): Record<IssueStatus, number> {
  const out = { open: 0, resolved: 0, dismissed: 0 };
  items.forEach((i) => out[i.status]++);
  return out;
}
