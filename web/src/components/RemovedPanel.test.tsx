import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { Edl, Region } from "../state/engine";
import { mockApi } from "../test-utils";
import { RemovedPanel } from "./RemovedPanel";

function region(over: Partial<Region> = {}): Region {
  return {
    id: 1,
    source_file_id: 1,
    source_label: "ch01_take1",
    kind: "stutter",
    status: "removed",
    origin: "auto",
    decided_by: "auto",
    confidence: 0.9,
    reason: "repeated “The”",
    text: "The",
    start: 8.19,
    end: 8.62,
    seconds: 0.43,
    timeline_at: 8.19,
    issue_id: null,
    ...over,
  };
}

const STUTTER = region();
const COMMENT = region({
  id: 2,
  kind: "commentary",
  text: "Hold on, let me get some water.",
  reason: "off-script speech (sounds like a note to the room)",
  start: 22.96,
  end: 25.78,
  seconds: 2.82,
  timeline_at: 20.9,
});
const SUGGESTED = region({ id: 3, kind: "commentary", status: "suggested", confidence: 0.4, text: "and then", start: 30, end: 31, seconds: 1 });

function edl(regions: Region[], over: Partial<Edl> = {}): Edl {
  const removed = regions.filter((r) => r.status === "removed").reduce((a, r) => a + r.seconds, 0);
  return { rate: 16000, duration: 66.6 - removed, original_duration: 66.6, clips: [], regions, undo: "Cleanup: 2 removed, 1 suggested", redo: null, ...over };
}

const SETTINGS = { auto_apply: 0.7, stutters: true, false_starts: true, commentary: true };

function setup() {
  let state = edl([STUTTER, COMMENT, SUGGESTED]);
  const onEdited = vi.fn();
  const fetch = mockApi({
    "GET /api/project/edl": () => state,
    "POST /api/project/edl/regions/status": (body: unknown) => {
      const b = body as { ids: number[]; status: Region["status"] };
      state = edl(
        state.regions.map((r) => (b.ids.includes(r.id) ? { ...r, status: b.status, decided_by: "user" as const } : r)),
        { undo: b.status === "restored" ? "Restore commentary" : "Remove commentary" },
      );
      return state;
    },
    "PATCH /api/project/edl/regions/1": (body: unknown) => {
      const b = body as { start?: number; end?: number };
      state = edl(state.regions.map((r) => (r.id === 1 ? { ...r, ...b, seconds: (b.end ?? r.end) - (b.start ?? r.start) } : r)));
      return state;
    },
    "POST /api/project/edl/regions/1/roll": () => state,
    "POST /api/project/edl/undo": () => {
      state = edl(state.regions, { undo: null, redo: "Restore commentary" });
      return state;
    },
    "POST /api/project/edl/redo": () => state,
    "GET /api/project/edl/history": {
      entries: [
        { seq: 3, op_type: "cleanup_apply", origin: "auto", summary: "Cleanup: 2 removed, 1 suggested", created_at: "", state: "done" },
        { seq: 9, op_type: "region_status", origin: "user", summary: "Restore commentary", created_at: "", state: "undone" },
      ],
      undo: "Cleanup",
      redo: "Restore commentary",
    },
    "GET /api/project/cleanup-settings": SETTINGS,
    "PUT /api/project/cleanup-settings": (b: unknown) => ({ ...SETTINGS, ...(b as object) }),
  });
  render(<RemovedPanel revision={0} onEdited={onEdited} onError={() => undefined} />);
  return { fetch, onEdited };
}

const calls = (fetch: ReturnType<typeof mockApi>, method: string) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([u, init]) => [String(u), init?.body ? (JSON.parse(String(init.body)) as unknown) : null]);

const focus = (el: HTMLElement | undefined) => act(() => el?.focus());

describe("RemovedPanel", () => {
  test("summarizes the timeline and lists what was removed or suggested", async () => {
    setup();
    expect(await screen.findByText(/Timeline 1:03\.\d of 1:06\.6 recorded · 3\.\d s removed · 2 removed · 1 suggested · 0 restored/)).toBeInTheDocument();
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Stutter");
    expect(rows[0]).toHaveTextContent("ch01_take1 0:08.2");
    expect(rows[1]).toHaveTextContent("“Hold on, let me get some water.”");
    expect(rows[2]).toHaveTextContent("Suggested");
    expect(within(rows[2]!).getByRole("button", { name: "Apply" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "suggested" } });
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  });

  test("keyboard: restore, apply, trim, roll, play", async () => {
    const { fetch, onEdited } = setup();
    const table = await screen.findByRole("table");
    const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
    focus(rows()[0]);
    fireEvent.keyDown(table, { key: "j" });
    expect(rows()[1]).toHaveFocus();
    fireEvent.keyDown(table, { key: "r" });
    await waitFor(() =>
      expect(calls(fetch, "POST")).toContainEqual(["/api/project/edl/regions/status", { ids: [2], status: "restored" }]),
    );
    await waitFor(() => expect(rows()[1]).toHaveTextContent("Restored"));
    expect(onEdited).toHaveBeenCalled();
    expect(screen.getByText("The timeline changed: re-run checks to update the Issues list.")).toBeInTheDocument();
    fireEvent.keyDown(table, { key: "a" });
    await waitFor(() =>
      expect(calls(fetch, "POST")).toContainEqual(["/api/project/edl/regions/status", { ids: [2], status: "removed" }]),
    );

    focus(rows()[0]);
    fireEvent.keyDown(table, { key: "[" });
    await waitFor(() => expect(calls(fetch, "PATCH")[0]?.[1]).toEqual({ start: expect.closeTo(8.24, 5) as number }));
    fireEvent.keyDown(table, { key: "." });
    await waitFor(() => expect(calls(fetch, "POST")).toContainEqual(["/api/project/edl/regions/1/roll", { delta: 0.05 }]));

    fireEvent.keyDown(table, { key: " " });
    const audio = screen.getByLabelText("Removed material audio") as HTMLAudioElement;
    expect(audio.src).toContain("/api/project/edl/render?start=5.190&end=11.190");
    fireEvent.keyDown(table, { key: "p" });
    expect(audio.src).toContain("/api/project/sources/1/excerpt?start=");
  });

  test("undo and redo, by button and by shortcut; history", async () => {
    const { fetch } = setup();
    const undo = await screen.findByRole("button", { name: "Undo: Cleanup: 2 removed, 1 suggested" });
    fireEvent.click(undo);
    expect(await screen.findByRole("button", { name: "Redo: Restore commentary" })).toBeEnabled();
    fireEvent.keyDown(window, { key: "z", ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(calls(fetch, "POST").map(([u]) => u)).toContain("/api/project/edl/redo"));

    fireEvent.click(screen.getByText("Edit history"));
    const list = await screen.findByRole("list", { name: "Edit history" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(2));
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent("undone (can redo)");
  });

  test("cleanup settings", async () => {
    const { fetch } = setup();
    fireEvent.click(await screen.findByText("Cleanup settings"));
    fireEvent.click(screen.getByLabelText("Commentary"));
    fireEvent.change(screen.getByLabelText("Apply automatically at confidence (%)"), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: "Save cleanup settings" }));
    expect(await screen.findByText(/Saved/)).toBeInTheDocument();
    expect(calls(fetch, "PUT")[0]?.[1]).toMatchObject({ commentary: false, auto_apply: 0.9 });
  });
});
