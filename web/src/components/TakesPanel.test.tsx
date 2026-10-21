import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { Take, TakeGroup, TakesResponse } from "../state/engine";
import { mockApi, source } from "../test-utils";
import { TakesPanel } from "./TakesPanel";

function take(over: Partial<Take> = {}): Take {
  return {
    id: 1,
    take_group_id: 1,
    source_file_id: 1,
    source_label: "ch01_take1",
    start: 19.56,
    end: 22.51,
    span_end: 22.96,
    coverage: 1,
    score: 12 / 13,
    hard_errors: 1,
    soft_errors: 0,
    extras: 0,
    loudness_db: -21.3,
    rank: 1,
    status: "alternate",
    decided_by: "auto",
    heard: "But it was all in vain, for they were just out of sight.",
    diffs: [{ kind: "substitution", expected: "reach", heard: "sight.", start: 22, end: 22.5 }],
    ...over,
  };
}

const MISREAD = take();
const PICKUP = take({
  id: 2,
  source_file_id: 2,
  source_label: "ch01_take2",
  start: 0.5,
  end: 3.4,
  span_end: 3.9,
  score: 1,
  hard_errors: 0,
  rank: 0,
  status: "preferred",
  heard: "But it was all in vain, for they were just out of reach.",
  diffs: [],
});

function group(over: Partial<TakeGroup> = {}): TakeGroup {
  return {
    id: 10,
    manuscript_unit_id: 5,
    sentence_text: "But it was all in vain, for they were just out of reach.",
    page_label: "2",
    state: "suggested",
    confident: true,
    reason: "fewer text errors",
    previous_take_id: null,
    takes: [PICKUP, MISREAD], // ranked
    ...over,
  };
}

const S10 = group({
  id: 11,
  manuscript_unit_id: 9,
  sentence_text: "The hare soon ran far ahead, and lay down to take a nap.",
  page_label: "3",
  confident: false,
  reason: "the later take",
  takes: [
    take({ id: 4, take_group_id: 11, source_file_id: 3, source_label: "ch02_take1", start: 16.05, end: 19.3, status: "preferred", score: 1, hard_errors: 0, diffs: [] }),
    take({ id: 3, take_group_id: 11, source_file_id: 3, source_label: "ch02_take1", start: 11.9, end: 15.15, score: 1, hard_errors: 0, diffs: [] }),
  ],
});

function setup() {
  let state: TakesResponse = { groups: [group(), S10], undo: "Takes: 2 lines with retakes", redo: null };
  const onEdited = vi.fn();
  const fetch = mockApi({
    "GET /api/project/takes": () => state,
    "POST /api/project/takes/10/choose": (body: unknown) => {
      const id = (body as { take_id: number }).take_id;
      const g = state.groups[0]!;
      const prev = g.takes.find((t) => t.status === "preferred")!.id;
      state = {
        ...state,
        groups: [
          { ...g, state: "confirmed", previous_take_id: prev, takes: g.takes.map((t) => ({ ...t, status: t.id === id ? "preferred" : "alternate", decided_by: "user" })) },
          state.groups[1]!,
        ],
      };
      return state;
    },
    "POST /api/project/takes/10/back": () => state,
    "POST /api/project/takes/confirm": () => {
      state = { ...state, groups: state.groups.map((g) => ({ ...g, state: "confirmed" })) };
      return state;
    },
    "POST /api/project/takes/take/3/mark": () => state,
    "GET /api/project/sources/1/peaks": { min: [], max: [] },
    "GET /api/project/sources/2/peaks": { min: [], max: [] },
    "GET /api/project/sources/3/peaks": { min: [], max: [] },
  });
  render(
    <TakesPanel
      sources={[source({ id: 1 }), source({ id: 2, label: "ch01_take2" }), source({ id: 3, label: "ch02_take1" })]}
      revision={0}
      onEdited={onEdited}
      onError={() => undefined}
    />,
  );
  return { fetch, onEdited };
}

const posts = (fetch: ReturnType<typeof mockApi>) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([u, init]) => [String(u), init?.body ? (JSON.parse(String(init.body)) as unknown) : null]);

describe("TakesPanel", () => {
  test("shows each take in recording order with its differences", async () => {
    setup();
    expect(await screen.findByText("2 lines read more than once · 1 to review · 0 confirmed")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Takes of this line" });
    const items = within(list).getAllByRole("listitem").filter((li) => li.classList.contains("take"));
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Take 1 · Alternate · ch01_take1 0:19.6 · 92% · -21.3 dB");
    expect(items[0]).toHaveTextContent("Misread: “reach” → “sight.”");
    expect(items[1]).toHaveTextContent("Take 2 · Preferred (suggested) · ch01_take2");
    expect(screen.getByText(/Suggested: fewer text errors\./)).toBeInTheDocument();
  });

  test("keyboard: choose, A/B switch, loop, next line, confirm, reject, back", async () => {
    const { fetch, onEdited } = setup();
    await screen.findByText(/2 lines read more than once/);
    const box = screen.getByRole("group", { name: "Line 1 of 2" });
    act(() => box.focus());

    fireEvent.keyDown(box, { key: " " });
    const audio = screen.getByLabelText("Take audio") as HTMLAudioElement;
    expect(audio.src).toContain("/api/project/sources/1/excerpt?start=19.560&end=22.510&context=0");
    fireEvent.keyDown(box, { key: "ArrowRight" });
    expect(audio.src).toContain("/api/project/sources/2/excerpt?start=0.500");
    fireEvent.keyDown(box, { key: "l" });
    expect(screen.getByText(/L loop \(on\)/)).toBeInTheDocument();

    fireEvent.keyDown(box, { key: "1" });
    await waitFor(() => expect(posts(fetch)).toContainEqual(["/api/project/takes/10/choose", { take_id: 1 }]));
    expect(onEdited).toHaveBeenCalled();
    expect(await screen.findByText("Confirmed.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back to the previous choice" })).toBeInTheDocument();
    fireEvent.keyDown(box, { key: "b" });
    await waitFor(() => expect(posts(fetch).map(([u]) => u)).toContain("/api/project/takes/10/back"));

    fireEvent.keyDown(box, { key: "n" });
    const next = await screen.findByRole("group", { name: "Line 2 of 2" });
    expect(next).toHaveTextContent("The hare soon ran far ahead");
    expect(next).toHaveTextContent("Suggested (the later take): listen and confirm.");
    fireEvent.keyDown(next, { key: "x" }); // take 1 of this line is the alternate
    await waitFor(() => expect(posts(fetch)).toContainEqual(["/api/project/takes/take/3/mark", { status: "rejected" }]));
    fireEvent.keyDown(next, { key: "c" });
    await waitFor(() => expect(posts(fetch)).toContainEqual(["/api/project/takes/confirm", { ids: [11] }]));
    fireEvent.keyDown(next, { key: "N" });
    expect(await screen.findByRole("group", { name: "Line 1 of 2" })).toBeInTheDocument();
  });

  test("nothing to compare", async () => {
    mockApi({ "GET /api/project/takes": { groups: [], undo: null, redo: null } });
    render(<TakesPanel sources={[]} revision={0} onEdited={() => undefined} onError={() => undefined} />);
    expect(await screen.findByText("No line was read more than once.")).toBeInTheDocument();
  });
});
