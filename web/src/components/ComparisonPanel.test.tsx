import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { bestReading, clock, describe as describeDiff, status } from "../state/comparison";
import type { Comparison, SentenceComparison, SentenceReading } from "../state/engine";
import { mockApi } from "../test-utils";
import { ComparisonPanel } from "./ComparisonPanel";

function reading(over: Partial<SentenceReading> = {}): SentenceReading {
  return { source_id: 1, source_label: "ch01_take1", start: 19.56, end: 22.51, coverage: 1, score: 1, heard: "", diffs: [], ...over };
}

const misread = reading({
  score: 12 / 13,
  heard: "But it was all in vain, for they were just out of sight.",
  diffs: [{ kind: "substitution", expected: "reach", heard: "sight.", start: 22.0, end: 22.5 }],
});

function sentence(over: Partial<SentenceComparison> = {}): SentenceComparison {
  return { unit_id: 1, ordinal: 0, text: "A sentence.", page_label: "1", page_end_label: "1", readings: [], best_score: null, ...over };
}

const DATA: Comparison = {
  run_id: 1,
  stale: false,
  sentences: [
    sentence({ unit_id: 1, text: "Chapter One.", readings: [reading()], best_score: 1 }),
    sentence({
      unit_id: 2,
      ordinal: 1,
      text: "But it was all in vain, for they were just out of reach.",
      page_label: "2",
      page_end_label: "2",
      readings: [misread],
      best_score: 12 / 13,
    }),
    sentence({ unit_id: 3, ordinal: 2, text: "Never read.", page_label: "2", page_end_label: "3" }),
  ],
  unaligned: [{ source_file_id: 1, source_label: "ch01_take1", start: 22.96, end: 25.78, text: "Hold on, let me get some water." }],
};

const ASR = {
  installed: true,
  models: [
    { name: "tiny.en", size_mb: 75 },
    { name: "small.en", size_mb: 464 },
  ],
  default: { model: "small.en" },
};

describe("comparison logic", () => {
  test("status, best take, wording", () => {
    const [clean, diff, unread] = DATA.sentences as [SentenceComparison, SentenceComparison, SentenceComparison];
    expect([status(clean), status(diff), status(unread)]).toEqual(["clean", "differences", "not-read"]);
    // A clean pickup beats the misread take even if listed second.
    const withPickup = { ...diff, readings: [misread, reading({ source_label: "ch01_take2" })] };
    expect(bestReading(withPickup)?.source_label).toBe("ch01_take2");
    expect(status(withPickup)).toBe("clean");
    // A false start alone is not a clean read.
    expect(status({ ...clean, readings: [reading({ coverage: 0.3 })] })).toBe("differences");
    expect(describeDiff(misread.diffs[0]!)).toBe("Misread: “reach” → “sight.”");
    expect(describeDiff({ kind: "missing", expected: "now", heard: "", start: null, end: null })).toBe("Missing: “now”");
    expect(clock(83.54)).toBe("1:23.5");
  });
});

test("runs the analysis and shows sentences with differences by default", async () => {
  const runs: unknown[] = [];
  mockApi({
    "GET /api/asr": ASR,
    "GET /api/project/comparison": DATA,
    "POST /api/project/analysis": (body: unknown) => {
      runs.push(body);
      return { job_id: "j1" };
    },
  });
  render(<ComparisonPanel canRun why={null} revision={0} onError={() => {}} />);
  expect(await screen.findByText("1 clean · 1 with differences · 1 not read · 1 off-script passage")).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "Sentences" });
  expect(within(list).getByText(/out of reach\./)).toBeInTheDocument();
  // Shown under the sentence, and again in its (collapsed) table of takes.
  const [inline, inTakes] = screen.getAllByText("Misread: “reach” → “sight.”");
  expect(inline).toBeVisible();
  expect(inTakes).not.toBeVisible();
  fireEvent.click(screen.getAllByText("Show all takes")[0]!);
  expect(inTakes).toBeVisible();
  expect(screen.queryByText("Chapter One.")).not.toBeInTheDocument(); // clean: filtered out
  expect(screen.getByText(/Hold on, let me get some water/)).toBeInTheDocument();
  expect(screen.getByText(/audio never leaves the machine/)).toBeInTheDocument();

  fireEvent.click(screen.getByLabelText("Not read"));
  expect(screen.getByText("Never read.")).toBeInTheDocument();
  expect(screen.getByText(/p\. 2–3/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Speech model"), { target: { value: "tiny.en" } });
  fireEvent.click(screen.getByRole("button", { name: "Transcribe and compare" }));
  await waitFor(() => expect(runs).toEqual([{ stages: null, asr: { model: "tiny.en" } }]));
});

test("explains why it can't run yet", async () => {
  mockApi({ "GET /api/asr": ASR, "GET /api/project/comparison": null });
  render(<ComparisonPanel canRun={false} why="Import the manuscript first." revision={0} onError={() => {}} />);
  expect(await screen.findByText("Import the manuscript first.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Transcribe and compare" })).toBeDisabled();
});

test("out-of-date results ask for a re-run instead of showing", async () => {
  mockApi({ "GET /api/asr": ASR, "GET /api/project/comparison": { ...DATA, stale: true } });
  render(<ComparisonPanel canRun why={null} revision={0} onError={() => {}} />);
  expect(await screen.findByText(/pages changed after this comparison/)).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Sentences" })).not.toBeInTheDocument();
});

test("a model that isn't downloaded yet can't run (regression: no silent download)", async () => {
  mockApi({
    "GET /api/asr": { ...ASR, models: [{ name: "small.en", size_mb: 486, downloaded: false }] },
    "GET /api/project/comparison": null,
  });
  render(<ComparisonPanel canRun why={null} revision={0} onError={() => undefined} />);
  expect(await screen.findByText(/Download the small.en model first \(about 486 MB/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Transcribe and compare" })).toBeDisabled();
});
