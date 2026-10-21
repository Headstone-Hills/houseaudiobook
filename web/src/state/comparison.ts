// Pure helpers for the Compare to Text view.

import type { DiffKind, ReadingDiff, SentenceComparison, SentenceReading } from "./engine";

/** Differences that mean the text was read wrongly (not stutters or suspicions). */
export const TEXT_ERRORS: ReadonlySet<DiffKind> = new Set(["missing", "substitution", "transposition", "reordered"]);

export type Status = "clean" | "differences" | "not-read" | "not-recorded";

/** A complete reading (not a false start). */
export function complete(r: SentenceReading): boolean {
  return r.coverage >= 0.999;
}

/** The take to show first: complete, fewest text errors, highest score. */
export function bestReading(s: SentenceComparison): SentenceReading | null {
  const full = s.readings.filter(complete);
  const pool = full.length ? full : s.readings;
  if (!pool.length) return null;
  const errors = (r: SentenceReading) => r.diffs.filter((d) => TEXT_ERRORS.has(d.kind)).length;
  return [...pool].sort((a, b) => errors(a) - errors(b) || b.score - a.score)[0] ?? null;
}

/** clean: some complete take has no text errors; differences: every take has some
 * (or only partial takes exist); not-read: no reading, where the recordings should
 * have one (a skip); not-recorded: text no recording reaches yet (D-059). */
export function status(s: SentenceComparison): Status {
  if (!s.readings.length) return s.recorded === false ? "not-recorded" : "not-read";
  const best = bestReading(s);
  return best && complete(best) && !best.diffs.some((d) => TEXT_ERRORS.has(d.kind)) ? "clean" : "differences";
}

export function pages(s: { page_label: string; page_end_label: string }): string {
  return s.page_label === s.page_end_label ? s.page_label : `${s.page_label}–${s.page_end_label}`;
}

/** 83.5 -> "1:23.5" */
export function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = (seconds - m * 60).toFixed(1).padStart(4, "0");
  return `${m}:${s}`;
}

const LABELS: Record<DiffKind, string> = {
  missing: "Missing",
  extra: "Extra",
  substitution: "Misread",
  transposition: "Swapped",
  reordered: "Reordered",
  mispronunciation: "Unclear",
};

/** One line per difference: "Misread: reach → sight." */
export function describe(d: ReadingDiff): string {
  const label = LABELS[d.kind];
  if (d.kind === "missing") return `${label}: “${d.expected}”`;
  if (d.kind === "extra") return `${label}: “${d.heard}”`;
  if (d.kind === "mispronunciation") return `${label}: “${d.expected}” (low recognition confidence)`;
  if (d.kind === "reordered") return `${label}: “${d.expected || d.heard}”`;
  return `${label}: “${d.expected}” → “${d.heard}”`;
}
