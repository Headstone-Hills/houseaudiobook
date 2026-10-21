import { describe, expect, test } from "vitest";
import type { Chapter, Comparison, Issue, Region, TakeGroup } from "./engine";
import { chapterLabel, chapterName, chapterProgress, hasAudio, hasItem, hasSource, hasUnit } from "./scope";
import { nextUp, startStep, stepStatuses } from "./steps";
import { project, source } from "../test-utils";

function chapter(over: Partial<Chapter> = {}): Chapter {
  return {
    id: 10,
    number: 1,
    title: "Chapter One. The Fox and the Grapes.",
    page_label: "1",
    status: "recorded",
    sentences: 3,
    read: 3,
    not_recorded: 0,
    sentence_ids: [1, 2, 3],
    spans: [{ source_id: 1, start: 0, end: 30 }],
    timeline: { start: 0, end: 29 },
    open_issues: 1,
    ...over,
  };
}

describe("chapter scope", () => {
  const ch = chapter();
  test("the whole book has everything", () => {
    expect(hasUnit(null, null)).toBe(true);
    expect(hasAudio(null, null, null)).toBe(true);
    expect(hasSource(null, 7)).toBe(true);
  });
  test("a chapter has its lines and its part of each recording", () => {
    expect([hasUnit(ch, 2), hasUnit(ch, 4), hasUnit(ch, null)]).toEqual([true, false, false]);
    expect(hasAudio(ch, 1, 29.5, 31)).toBe(true); // overlaps the end
    expect(hasAudio(ch, 1, 30, 31)).toBe(false);
    expect(hasAudio(ch, 2, 5, 6)).toBe(false); // another recording
    expect([hasSource(ch, 1), hasSource(ch, 2)]).toEqual([true, false]);
    // A line decides; with none, the place in the recording does.
    expect(hasItem(ch, { unitId: 9, sourceId: 1, start: 5 })).toBe(false);
    expect(hasItem(ch, { unitId: null, sourceId: 1, start: 5 })).toBe(true);
  });
  test("names and progress", () => {
    expect(chapterName(ch)).toBe("The Fox and the Grapes");
    expect(chapterLabel(ch)).toBe("1. The Fox and the Grapes");
    expect(chapterLabel(chapter({ number: 0, title: "Opening pages" }))).toBe("Opening pages");
    expect(chapterName(chapter({ title: "Prologue" }))).toBe("Prologue");
    expect(chapterProgress(ch)).toBe("Recorded · 1 to review");
    expect(chapterProgress(chapter({ status: "partial", read: 2, open_issues: 0 }))).toBe(
      "2 of 3 lines recorded · nothing to review",
    );
    expect(chapterProgress(chapter({ status: "not_recorded" }))).toBe("Not recorded yet");
  });
});

const COMPARED: Comparison = { run_id: 1, stale: false, sentences: [], unaligned: [] };
const withText = project({
  sources: [source()],
  manuscript: { original_name: "book.txt" } as unknown as NonNullable<ReturnType<typeof project>["manuscript"]>,
});
const issue = (over: Partial<Issue>): Issue => ({ status: "open", manuscript_unit_id: 2, source_file_id: 1, start: 1, end: 2, ...over }) as Issue;

describe("step statuses", () => {
  test("a new project starts by adding recordings, then the text", () => {
    const empty = project();
    expect(startStep(empty)).toBe("recordings");
    expect(startStep(project({ sources: [source()] }))).toBe("manuscript");
    expect(startStep(withText)).toBe("overview");
    const st = stepStatuses({ project: empty, scope: null, comparison: null, issues: null, regions: null, takes: null });
    expect(st.recordings.state).toBe("todo");
    expect(st.compare).toEqual({ state: "blocked", note: "Needs recordings and the manuscript" });
    expect(nextUp(st)).toBe("recordings");
  });

  test("after checking: counts what's left to review, in the chapter when one is chosen", () => {
    const issues = [issue({ id: 1 }), issue({ id: 2, manuscript_unit_id: 8 }), issue({ id: 3, status: "resolved" })];
    const regions = [
      { kind: "stutter", status: "suggested", source_file_id: 1, start: 40, end: 41 },
      { kind: "take", status: "suggested", source_file_id: 1, start: 4, end: 5 },
    ] as Region[];
    const takes = [{ state: "suggested", confident: false, manuscript_unit_id: 3, takes: [] }] as unknown as TakeGroup[];
    const progress = { project: withText, comparison: COMPARED, issues, regions, takes };
    const book = stepStatuses({ ...progress, scope: null });
    expect([book.issues.badge, book.cleanup.badge, book.takes.badge]).toEqual([2, 1, 1]);
    expect(nextUp(book)).toBe("issues");
    const one = stepStatuses({ ...progress, scope: chapter() });
    expect([one.issues.badge, one.cleanup.state, one.takes.badge]).toEqual([1, "done", 1]);
  });

  test("a chapter nobody has recorded asks for its recordings", () => {
    const st = stepStatuses({
      project: withText,
      scope: chapter({ status: "not_recorded", spans: [], read: 0 }),
      comparison: COMPARED,
      issues: [],
      regions: [],
      takes: [],
    });
    expect(st.recordings).toEqual({ state: "todo", note: "No recording of this chapter yet" });
    expect(nextUp(st)).toBe("recordings");
  });
});
