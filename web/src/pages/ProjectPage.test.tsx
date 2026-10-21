import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Chapter, Issue, ManuscriptInfo, ProjectSummary } from "../state/engine";
import { SETTINGS, mockApi, project, source } from "../test-utils";
import { ProjectPage } from "./ProjectPage";

const BOOK: ProjectSummary = project({
  sources: [source()],
  manuscript: { original_name: "book.txt", counts: { chapter: 2, paragraph: 4, sentence: 13 } } as unknown as ManuscriptInfo,
});

function chapter(over: Partial<Chapter>): Chapter {
  return {
    id: 10,
    number: 1,
    title: "Chapter One. The Fox and the Grapes.",
    page_label: "1",
    status: "recorded",
    sentences: 7,
    read: 7,
    not_recorded: 0,
    sentence_ids: [1, 2, 3],
    spans: [{ source_id: 1, start: 0, end: 34 }],
    timeline: { start: 0, end: 30 },
    open_issues: 1,
    ...over,
  };
}
const CHAPTERS = [
  chapter({}),
  chapter({
    id: 20,
    number: 2,
    title: "Chapter Two. The Tortoise and the Hare.",
    page_label: "3",
    status: "not_recorded",
    read: 0,
    not_recorded: 6,
    sentence_ids: [8, 9],
    spans: [],
    timeline: null,
    open_issues: 0,
  }),
];
const ISSUE = {
  id: 1,
  stable_key: "k",
  kind: "flub",
  severity: "high",
  status: "open",
  decided_by: null,
  note: "",
  change: "new",
  last_run_id: 1,
  source_file_id: 1,
  source_label: "ch01_take1",
  start: 29.67,
  end: 33.6,
  page_label: "2",
  manuscript_unit_id: 2,
  sentence_text: "I thought those grapes were ripe.",
  expected: "I thought those grapes were ripe.",
  heard: "I thought grapes were ripe.",
  detail: { diffs: [{ kind: "missing", expected: "those", heard: "", start: null, end: null }], complete: true },
} as Issue;

function setup() {
  const fetch = mockApi({
    "GET /api/project/chapters": CHAPTERS,
    "GET /api/project/comparison": { run_id: 1, stale: false, sentences: [], unaligned: [] },
    "GET /api/project/issues": { issues: [ISSUE], last_check_run: 1, stale: false },
    "GET /api/project/edl": { regions: [], clips: [], duration: 30, original_duration: 34, rate: 16000, undo: null, redo: null },
    "GET /api/project/takes": { groups: [], undo: null, redo: null },
    "GET /api/project/issue-settings": {},
    "GET /api/asr": { installed: true, models: [], default: { model: "small.en" } },
    "GET /api/project/sources/1/peaks": { min: [], max: [], total_buckets: 0, num_frames: 0 },
    "POST /api/project/edl/undo": { regions: [] },
    "POST /api/project/edl/redo": { regions: [] },
  });
  const props = {
    project: BOOK,
    jobs: [],
    peakSettings: SETTINGS.peaks,
    pageDefaults: SETTINGS.page_defaults,
    onChanged: () => undefined,
    onClose: () => undefined,
  };
  return Object.assign(fetch, { ...render(<ProjectPage {...props} />), props });
}

const nav = () => screen.getByRole("navigation", { name: "Steps" });
const stepButton = (name: RegExp) => within(nav()).getByRole("button", { name });
const title = () => screen.getByRole("heading", { level: 2, name: (_, el) => el.id === "step-title" });

test("one step at a time, with a sidebar and a Next button", async () => {
  setup();
  // A book with recordings and text opens on the overview, which says what's next.
  expect(title()).toHaveTextContent("Overview");
  const next = await screen.findByRole("region", { name: "Next" });
  await waitFor(() => expect(next).toHaveTextContent("Fix mistakes: 1 to review"));
  fireEvent.click(screen.getByRole("button", { name: "Go to Fix mistakes" }));
  expect(title()).toHaveTextContent("Fix mistakes");
  expect(stepButton(/^Fix mistakes/)).toHaveAttribute("aria-current", "step");
  expect(await screen.findByText("I thought grapes were ripe.", { exact: false })).toBeInTheDocument();
  // Other steps' panels aren't on screen.
  expect(screen.queryByRole("table", { name: "Sources" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Next: Clean up →" }));
  expect(title()).toHaveTextContent("Clean up");
  fireEvent.click(stepButton(/^Recordings/));
  expect(title()).toHaveTextContent("Recordings");
  expect(await screen.findByRole("rowheader", { name: "ch01" })).toBeInTheDocument();
});

test("the step is remembered for the project", async () => {
  const { unmount, props } = setup();
  fireEvent.click(stepButton(/^Export/));
  expect(title()).toHaveTextContent("Export");
  unmount();
  render(<ProjectPage {...props} />);
  expect(title()).toHaveTextContent("Export");
});

test("picking a chapter narrows every step to it (D-059)", async () => {
  setup();
  const picker = await screen.findByLabelText("Working on");
  expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual([
    "The whole book",
    "1. The Fox and the Grapes · 1 to review",
    "2. The Tortoise and the Hare (not recorded yet)",
  ]);
  // The overview lists the chapters to pick from as well.
  fireEvent.click(await screen.findByRole("button", { name: "Work on 2. The Tortoise and the Hare" }));
  expect(picker).toHaveValue("20");
  expect(await screen.findByText(/No recording has this chapter in it yet/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Go to Recordings" })).toBeInTheDocument();
  // Its issues: none (the flub is in chapter one).
  fireEvent.click(stepButton(/^Fix mistakes/));
  expect(screen.getByText(/2\. The Tortoise and the Hare · Step 4 of 8/i)).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("No mistakes or long pauses found.")).toBeInTheDocument());
  fireEvent.change(picker, { target: { value: "10" } });
  expect(await screen.findByText("I thought grapes were ripe.", { exact: false })).toBeInTheDocument();
  expect(within(stepButton(/^Fix mistakes/)).getByText("1")).toBeInTheDocument();
});

test("undo and redo shortcuts work on every step, not just Clean up", async () => {
  const fetch = setup();
  fireEvent.click(stepButton(/^Choose takes/));
  fireEvent.keyDown(window, { key: "z", ctrlKey: true });
  fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: true });
  await waitFor(() =>
    expect(
      fetch.mock.calls.filter(([, init]) => init?.method === "POST").map(([u]) => String(u)),
    ).toEqual(["/api/project/edl/undo", "/api/project/edl/redo"]),
  );
  // Typing in a field is left alone.
  fireEvent.click(stepButton(/^Fix mistakes/));
  fireEvent.keyDown(await screen.findByLabelText("Sort by"), { key: "z", ctrlKey: true });
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2);
});
