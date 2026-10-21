import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { Job, PickupReport } from "../state/engine";
import { mockApi, source } from "../test-utils";
import { PickupsPanel } from "./PickupsPanel";

const REPORT: PickupReport[] = [
  {
    source_id: 3,
    label: "pickups_ch1",
    duration: 12,
    checked: true,
    lines: [
      { take_id: 1, group_id: 7, unit_id: 4, page_label: "2", text: "Out of reach.", start: 0.5, end: 3.4, status: "placed", replaces: "flub" },
      { take_id: 2, group_id: 8, unit_id: 9, page_label: "3", text: "A skipped line.", start: 4, end: 6, status: "placed", replaces: "missing" },
      { take_id: 3, group_id: 9, unit_id: 10, page_label: "3", text: "Read twice.", start: 7, end: 9, status: "choose", replaces: "flub" },
    ],
    unmatched: [{ start: 10, end: 11, text: "pickup for page two" }],
  },
];

const GROUP = {
  id: 9,
  manuscript_unit_id: 10,
  sentence_text: "Read twice.",
  page_label: "3",
  state: "suggested",
  confident: false,
  reason: "the takes are equal",
  previous_take_id: null,
  takes: [],
};

test("lists what each pickup did, and asks for a choice where two takes are good", async () => {
  mockApi({
    "GET /api/project/pickups": REPORT,
    "GET /api/project/takes": { groups: [GROUP, { ...GROUP, id: 1 }], undo: null, redo: null },
  });
  render(<PickupsPanel sources={[source()]} jobs={[]} revision={0} onEdited={() => undefined} onError={() => undefined} />);
  const list = await screen.findByRole("list", { name: "Lines in pickups_ch1" });
  const items = within(list).getAllByRole("listitem");
  expect(items.map((li) => li.querySelector(".chip")?.textContent)).toEqual([
    "In place of the flub",
    "Fills a skipped line",
    "Listen and choose",
    "Matches no line",
  ]);
  expect(screen.getByText("1 pickup file · 2 lines in place · 1 to choose")).toBeInTheDocument();
  // Only the line to choose is offered for choosing.
  expect(await screen.findByRole("group", { name: "Line 1 of 1" })).toHaveTextContent("Read twice.");
});

test("added files come in as pickups, and the reading is checked when they're in", async () => {
  const calls: unknown[] = [];
  const fetch = mockApi({
    "GET /api/project/pickups": [],
    "POST /api/project/sources/import": (b: unknown) => {
      calls.push(b);
      return { job_id: "imp1" };
    },
    "GET /api/asr": { installed: true, models: [], default: { model: "small.en" } },
    "POST /api/project/analysis": { job_id: "an1" },
  });
  const props = { sources: [source()], revision: 0, onEdited: () => undefined, onError: () => undefined };
  const { rerender } = render(<PickupsPanel {...props} jobs={[]} />);
  fireEvent.click(screen.getByText("Or add by path"));
  fireEvent.change(screen.getByLabelText("Pickup files (one per line)"), { target: { value: "/rec/pickups.wav" } });
  fireEvent.click(screen.getByRole("button", { name: "Add pickups" }));
  await waitFor(() => expect(calls).toEqual([{ paths: ["/rec/pickups.wav"], mode: "copy", role: "pickup" }]));
  expect(await screen.findByText("Importing pickups…")).toBeInTheDocument();
  const job = (status: Job["status"]): Job => ({ id: "imp1", kind: "import", label: "", status, progress: 1, message: "", result: null, error: null });
  rerender(<PickupsPanel {...props} jobs={[job("running")]} />);
  expect(fetch.mock.calls.some(([u]) => String(u) === "/api/project/analysis")).toBe(false);
  rerender(<PickupsPanel {...props} jobs={[job("done")]} />);
  await waitFor(() => {
    const post = fetch.mock.calls.find(([u]) => String(u) === "/api/project/analysis");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ stages: null, asr: { model: "small.en" } });
  });
});
