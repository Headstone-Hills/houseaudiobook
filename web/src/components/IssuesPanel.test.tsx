import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Issue, IssueList, IssueStatus } from "../state/engine";
import { filterIssues, sortIssues, summary, where } from "../state/issues";
import { mockApi } from "../test-utils";
import { IssuesPanel } from "./IssuesPanel";

function issue(over: Partial<Issue> = {}): Issue {
  return {
    id: 1,
    stable_key: "k1",
    kind: "flub",
    severity: "high",
    status: "open",
    decided_by: null,
    note: "",
    change: "new",
    last_run_id: 5,
    source_file_id: 1,
    source_label: "ch01_take1",
    start: 29.67,
    end: 36.2,
    page_label: "2",
    manuscript_unit_id: 7,
    sentence_text: "I thought those grapes were ripe, he said, but I see now they are quite sour.",
    expected: "I thought those grapes were ripe, he said, but I see now they are quite sour.",
    heard: "I thought those grapes were ripe, he said, but I see they are quite sour.",
    detail: { diffs: [{ kind: "missing", expected: "now", heard: "", start: null, end: null }], complete: true },
    ...over,
  };
}

const FLUB = issue();
const PAUSE = issue({
  id: 2,
  stable_key: "k2",
  kind: "silence",
  severity: "medium",
  start: 12.4,
  end: 15.9,
  page_label: "1",
  manuscript_unit_id: 3,
  expected: "",
  heard: "",
  detail: { seconds: 3.5, boundary: "paragraph", threshold: 3, suggested_s: 1.2 },
});
const UNREAD = issue({
  id: 3,
  stable_key: "k3",
  kind: "missing_line",
  source_file_id: null,
  source_label: null,
  start: null,
  end: null,
  page_label: "3",
  manuscript_unit_id: 9,
  heard: "",
  detail: { reason: "not read in any recording" } as Issue["detail"],
});
const OLD = issue({ id: 4, stable_key: "k4", status: "dismissed", decided_by: "user", last_run_id: 4, manuscript_unit_id: 1 });

const LIST: IssueList = { issues: [FLUB, PAUSE, UNREAD, OLD], last_check_run: 5, stale: false };
const SETTINGS = {
  silence_sentence_s: 2,
  silence_paragraph_s: 3,
  silence_chapter_s: 5,
  similar_spelling: 75,
  low_confidence: 0.6,
};

function setup(list: IssueList = LIST, props: Partial<Parameters<typeof IssuesPanel>[0]> = {}) {
  const state = { issues: list.issues.map((i) => ({ ...i })) };
  const fetch = mockApi({
    "GET /api/project/issues": () => ({ ...list, issues: state.issues }),
    "PATCH /api/project/issues": (body: unknown) => {
      const b = body as { ids: number[]; status?: IssueStatus; note?: string };
      const changed = state.issues
        .filter((i) => b.ids.includes(i.id))
        .map((i) => ({
          ...i,
          ...(b.status ? { status: b.status, decided_by: "user" as const } : {}),
          ...(b.note !== undefined ? { note: b.note } : {}),
        }));
      state.issues = state.issues.map((i) => changed.find((c) => c.id === i.id) ?? i);
      return { issues: changed };
    },
    "GET /api/project/issue-settings": SETTINGS,
    "PUT /api/project/issue-settings": (b: unknown) => ({ ...SETTINGS, ...(b as object) }),
    "POST /api/project/analysis": { job_id: "j1" },
    "POST /api/project/issues/2/shorten": { regions: [] },
  });
  render(<IssuesPanel checks={["flubs", "silences"]} canRun revision={0} onError={() => undefined} {...props} />);
  return fetch;
}

const focus = (el: HTMLElement | undefined) => act(() => el?.focus());

const patches = (fetch: ReturnType<typeof mockApi>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "PATCH")
    .map(([, init]) => JSON.parse(String(init?.body)) as unknown);

describe("issue logic", () => {
  test("sort by page puts unlocated issues last; filter by status", () => {
    const noLine = issue({ id: 9, manuscript_unit_id: null, start: 1 });
    expect(sortIssues([UNREAD, noLine, FLUB, PAUSE], "page").map((i) => i.id)).toEqual([2, 1, 3, 9]);
    expect(sortIssues([UNREAD, FLUB, PAUSE], "time").map((i) => i.id)).toEqual([2, 1, 3]);
    expect(sortIssues([PAUSE, UNREAD, FLUB], "severity").map((i) => i.id)).toEqual([1, 3, 2]);
    expect(filterIssues(LIST.issues, { kind: "all", status: "open", severity: "all" })).toHaveLength(3);
    expect(filterIssues(LIST.issues, { kind: "silence", status: "all", severity: "all" })).toEqual([PAUSE]);
  });

  test("wording", () => {
    expect(summary(FLUB)).toBe("Missing: “now”");
    expect(summary(PAUSE)).toBe("3.5 s pause at a paragraph break (limit 3 s)");
    expect(summary(UNREAD)).toBe("Not read in any recording");
    expect(summary(issue({ detail: { complete: false } }))).toBe("Only false starts: never read to the end");
    expect(where(FLUB)).toBe("ch01_take1 0:29.7");
    expect(where(UNREAD)).toBe("not in any recording");
  });
});

describe("IssuesPanel", () => {
  test("lists open issues in page order with expected vs heard and change badges", async () => {
    setup();
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.querySelector(".chip")?.textContent)).toEqual(["Long pause", "Flub", "Unread line"]);
    expect(rows.map((r) => within(r).getAllByRole("cell")[2]?.textContent)).toEqual(["p. 1", "p. 2", "p. 3"]);
    expect(rows[1]).toHaveTextContent("Heard: I thought those grapes were ripe, he said, but I see they are quite sour.");
    expect(rows[1]).toHaveTextContent("New");
    expect(screen.getByText("3 to review · 0 fixed · 1 OK as is")).toBeInTheDocument();
    // The dismissed one is a filter away.
    fireEvent.click(screen.getByLabelText(/^OK as is/));
    const only = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(only).toHaveLength(1);
    expect(only[0]).not.toHaveTextContent("New"); // last touched by an earlier check
  });

  test("keyboard review: move, resolve, dismiss, note, select and bulk", async () => {
    const fetch = setup();
    const table = await screen.findByRole("table");
    const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
    focus(rows()[0]);
    fireEvent.keyDown(table, { key: "j" });
    expect(rows()[1]).toHaveFocus();
    fireEvent.keyDown(table, { key: "r" });
    await waitFor(() => expect(patches(fetch)).toEqual([{ ids: [1], status: "resolved" }]));
    // Resolved drops out of the open filter.
    await waitFor(() => expect(rows()).toHaveLength(2));

    focus(rows()[0]);
    fireEvent.keyDown(table, { key: "n" });
    const note = screen.getByLabelText("Note");
    fireEvent.change(note, { target: { value: "room noise" } });
    fireEvent.keyDown(note, { key: "Enter" });
    await waitFor(() => expect(patches(fetch)).toContainEqual({ ids: [2], note: "room noise" }));
    expect(await screen.findByText("Note: room noise")).toBeInTheDocument();

    focus(rows()[0]);
    fireEvent.keyDown(table, { key: "x" });
    fireEvent.keyDown(table, { key: "ArrowDown" });
    fireEvent.keyDown(table, { key: "x" });
    expect(screen.getByRole("button", { name: "Dismiss 2 selected" })).toBeEnabled();
    fireEvent.keyDown(table, { key: "d" });
    await waitFor(() => expect(patches(fetch)).toContainEqual({ ids: [2, 3], status: "dismissed" }));
    await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());

    fireEvent.click(screen.getByLabelText(/^All$/));
    focus(within(screen.getByRole("table")).getAllByRole("row")[1]);
    fireEvent.keyDown(screen.getByRole("table"), { key: "o" });
    await waitFor(() => expect(patches(fetch).at(-1)).toMatchObject({ status: "open" }));
  });

  test("space plays the excerpt with context", async () => {
    setup();
    const table = await screen.findByRole("table");
    focus(within(table).getAllByRole("row")[2]); // the flub
    fireEvent.keyDown(table, { key: " " });
    const audio = screen.getByLabelText("Issue audio") as HTMLAudioElement;
    expect(audio.src).toContain("/api/project/sources/1/excerpt?start=29.670&end=36.200");
    expect(screen.getByRole("button", { name: "Play ch01_take1 0:29.7" })).toHaveTextContent("■");
    // Unread lines have nothing to play.
    expect(within(within(table).getAllByRole("row")[3]!).queryByRole("button", { name: /^Play/ })).toBeNull();
  });

  test("each issue has its own Fixed and OK-as-is buttons, and reports decisions", async () => {
    let decided = 0;
    const fetch = setup(LIST, { onDecided: () => decided++ });
    const table = await screen.findByRole("table");
    const flub = within(table).getAllByRole("row")[2]!;
    fireEvent.click(within(flub).getByRole("button", { name: "Fixed" }));
    await waitFor(() => expect(patches(fetch)).toEqual([{ ids: [1], status: "resolved" }]));
    await waitFor(() => expect(decided).toBe(1));
    expect(screen.getByText("2 to review · 1 fixed · 1 OK as is")).toBeInTheDocument();
    // Bulk buttons only once something is ticked.
    expect(screen.queryByRole("button", { name: /selected$/ })).toBeNull();
    fireEvent.click(screen.getByLabelText("Select Long pause at ch01_take1 0:12.4"));
    expect(screen.getByRole("button", { name: "Fixed 1 selected" })).toBeEnabled();
  });

  test("in a chapter, only that chapter's issues (D-059)", async () => {
    const chapter = {
      id: 50,
      number: 1,
      title: "Chapter One.",
      page_label: "1",
      status: "recorded" as const,
      sentences: 4,
      read: 4,
      not_recorded: 0,
      sentence_ids: [1, 3, 7],
      spans: [{ source_id: 1, start: 0, end: 40 }],
      timeline: null,
      open_issues: 2,
    };
    setup(LIST, { scope: chapter });
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    // The unread line (unit 9) is in another chapter.
    expect(rows.map((r) => r.querySelector(".chip")?.textContent)).toEqual(["Long pause", "Flub"]);
    expect(screen.getByText("2 to review · 0 fixed · 1 OK as is")).toBeInTheDocument();
  });

  test("re-run checks and thresholds", async () => {
    const fetch = setup();
    fireEvent.click(await screen.findByRole("button", { name: "Re-run checks" }));
    await waitFor(() =>
      expect(fetch.mock.calls.some(([u, init]) => String(u) === "/api/project/analysis" && init?.method === "POST")).toBe(true),
    );
    const call = fetch.mock.calls.find(([u]) => String(u) === "/api/project/analysis");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ stages: ["flubs", "silences"], asr: {} });

    fireEvent.click(screen.getByText("Long-pause thresholds"));
    fireEvent.change(screen.getByLabelText("At a paragraph break (seconds)"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Save thresholds" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({ silence_paragraph_s: 4 });
  });

  test("s shortens a long pause", async () => {
    const fetch = setup();
    const table = await screen.findByRole("table");
    focus(within(table).getAllByRole("row")[1]); // the pause
    fireEvent.keyDown(table, { key: "s" });
    expect(await screen.findByText("Shortened to 1.2 s. Re-run checks to update.")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([u]) => String(u) === "/api/project/issues/2/shorten")).toBe(true);
    // Not for other kinds.
    focus(within(table).getAllByRole("row")[2]);
    fireEvent.keyDown(table, { key: "s" });
    expect(fetch.mock.calls.filter(([u]) => String(u).includes("/shorten"))).toHaveLength(1);
  });
});
