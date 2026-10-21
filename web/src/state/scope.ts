// Chapter workspaces (D-059): which items belong to the chapter being worked on.
// The scope is the chapter, or null for the whole book (everything is in it).

import type { Chapter, ChapterStatus } from "./engine";

export type Scope = Chapter | null;

/** A manuscript line (sentence unit id) in the scope. */
export function hasUnit(scope: Scope, unitId: number | null | undefined): boolean {
  if (!scope) return true;
  return unitId !== null && unitId !== undefined && unitIds(scope).has(unitId);
}

/** A stretch of a recording that overlaps the chapter's part of that recording. */
export function hasAudio(scope: Scope, sourceId: number | null, start: number | null, end?: number | null): boolean {
  if (!scope) return true;
  if (sourceId === null || start === null) return false;
  const stop = end ?? start;
  return scope.spans.some((s) => s.source_id === sourceId && start < s.end && stop > s.start);
}

/** A recording with any of the chapter in it. */
export function hasSource(scope: Scope, sourceId: number): boolean {
  return !scope || scope.spans.some((s) => s.source_id === sourceId);
}

/** An item tied to a line if it has one, else to a place in a recording (a pause
 * between lines that matched no text). */
export function hasItem(
  scope: Scope,
  item: { unitId: number | null; sourceId: number | null; start: number | null; end?: number | null },
): boolean {
  if (!scope) return true;
  return item.unitId !== null ? hasUnit(scope, item.unitId) : hasAudio(scope, item.sourceId, item.start, item.end);
}

const idSets = new WeakMap<Chapter, Set<number>>();
function unitIds(ch: Chapter): Set<number> {
  let s = idSets.get(ch);
  if (!s) {
    s = new Set(ch.sentence_ids);
    idSets.set(ch, s);
  }
  return s;
}

/** "Chapter Two. The Tortoise and the Hare." -> "The Tortoise and the Hare" when
 * the heading starts with its own number; the picker shows the number already. */
export function chapterName(ch: Chapter): string {
  const t = ch.title.replace(/[.\s]+$/, "");
  const m = /^(?:chapter|part|book)\s+[\w-]+[.:\s-]+(.+)$/i.exec(t);
  return m?.[1] ?? t;
}

export function chapterLabel(ch: Chapter): string {
  return ch.number === 0 ? chapterName(ch) : `${ch.number}. ${chapterName(ch)}`;
}

export const CHAPTER_STATUS: Record<ChapterStatus, string> = {
  not_compared: "Not checked yet",
  not_recorded: "Not recorded yet",
  partial: "Partly recorded",
  recorded: "Recorded",
};

/** One line on how far a chapter is: what's recorded, what's left to review. */
export function chapterProgress(ch: Chapter): string {
  if (ch.status === "not_compared") return CHAPTER_STATUS.not_compared;
  if (ch.status === "not_recorded") return CHAPTER_STATUS.not_recorded;
  const part = ch.status === "partial" ? `${ch.read} of ${ch.sentences} lines recorded` : "Recorded";
  const review = ch.open_issues ? `${ch.open_issues} to review` : "nothing to review";
  return `${part} · ${review}`;
}
