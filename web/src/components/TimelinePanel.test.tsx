import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { TimelineView } from "../state/engine";
import { SETTINGS, mockApi, source } from "../test-utils";
import { TimelinePanel } from "./TimelinePanel";

const VIEW: TimelineView = {
  rate: 16000,
  duration: 40,
  channels: 1,
  tracks: [
    { id: 1, name: "Narration", kind: "narration", gain_db: 0, pan: 0, mute: false, solo: false },
    { id: 2, name: "Room tone", kind: "audio", gain_db: 0, pan: 0, mute: false, solo: false },
  ],
  clips: [
    { ref: { source_id: 1, source_in: 0 }, track_id: 1, source_id: 1, source_label: "ch01", source_in: 0, source_out: 25, timeline_start: 0, timeline_end: 25, gain_db: 0, fade_in: 0, fade_out: 0, muted: false },
    { ref: { source_id: 2, source_in: 0 }, track_id: 1, source_id: 2, source_label: "ch02", source_in: 0, source_out: 15, timeline_start: 25, timeline_end: 40, gain_db: 0, fade_in: 0, fade_out: 0, muted: false },
  ],
  markers: [],
  issues: [],
  removed: [{ id: 5, kind: "stutter", status: "removed", t: 8.2, seconds: 0.4, text: "The", source_id: 1, start: 8.2, end: 8.6 }],
  undo: null,
  redo: null,
};
const LANES = { words: [{ text: "Chapter", t0: 0.5, t1: 1.0 }], sentences: [], pauses: [{ t0: 3, t1: 4 }] };

function setup() {
  const fetch = mockApi({
    "GET /api/project/timeline": VIEW,
    "GET /api/project/timeline/lanes": LANES,
    "GET /api/project/sources/1/peaks": { min: [], max: [], total_buckets: 0 },
    "GET /api/project/sources/2/peaks": { min: [], max: [], total_buckets: 0 },
    "POST /api/project/timeline/split": VIEW,
    "POST /api/project/timeline/ripple-delete": VIEW,
    "POST /api/project/timeline/trim": VIEW,
    "POST /api/project/timeline/roll": VIEW,
    "POST /api/project/timeline/move": VIEW,
    "POST /api/project/timeline/clip": VIEW,
    "POST /api/project/timeline/markers": VIEW,
    "POST /api/project/timeline/tracks": VIEW,
    "PATCH /api/project/timeline/tracks/1": VIEW,
    "POST /api/project/edl/regions/status": { regions: [] },
  });
  render(
    <TimelinePanel
      sources={[source({ id: 1 }), source({ id: 2, label: "ch02" })]}
      peakSettings={SETTINGS.peaks}
      revision={0}
      onEdited={() => undefined}
      onError={() => undefined}
    />,
  );
  return fetch;
}

const sent = (fetch: ReturnType<typeof mockApi>, method = "POST") =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([u, init]) => [String(u).replace("/api/project/", ""), JSON.parse(String(init?.body ?? "null")) as unknown]);

const press = (el: HTMLElement, key: string, opts: Partial<KeyboardEventInit> = {}) => fireEvent.keyDown(el, { key, ...opts });

describe("TimelinePanel", () => {
  test("keyboard tools send the right edits", async () => {
    const fetch = setup();
    const app = await screen.findByRole("application", { name: /Timeline/ });
    act(() => app.focus());

    press(app, "ArrowRight", { shiftKey: true }); // 10 s
    expect(screen.getByLabelText("Playhead")).toHaveTextContent("0:10.0 / 0:40.0");
    press(app, "s");
    await waitFor(() => expect(sent(fetch)).toContainEqual(["timeline/split", { t: 10, track_id: 1 }]));

    // Selection with [ and ], then ripple delete.
    press(app, "[", { code: "BracketLeft" });
    press(app, "ArrowRight");
    press(app, "ArrowRight");
    press(app, "]", { code: "BracketRight" });
    expect(screen.getByRole("status")).toHaveTextContent("selection 0:10.0–0:12.0");
    press(app, "Delete");
    await waitFor(() => expect(sent(fetch)).toContainEqual(["timeline/ripple-delete", { start: 10, end: 12 }]));

    // Snap: the next point after 12 s is the end of the first clip (25 s).
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).includes("/timeline/lanes"))).toBe(true));
    press(app, "ArrowRight", { altKey: true });
    expect(screen.getByLabelText("Playhead")).toHaveTextContent("0:25.0");

    // Clip tools on the clip under the playhead (ch02 at 25 s).
    press(app, "+");
    await waitFor(() =>
      expect(sent(fetch)).toContainEqual(["timeline/clip", { ref: { source_id: 2, source_in: 0 }, gain_db: 1 }]),
    );
    press(app, "ArrowRight");
    press(app, "f");
    await waitFor(() =>
      expect(sent(fetch)).toContainEqual(["timeline/clip", { ref: { source_id: 2, source_in: 0 }, fade_in: 1 }]),
    );
    press(app, "{", { code: "BracketLeft", shiftKey: true });
    await waitFor(() =>
      expect(sent(fetch)).toContainEqual(["timeline/trim", { ref: { source_id: 2, source_in: 0 }, edge: "start", t: 26 }]),
    );
    press(app, ",");
    await waitFor(() => expect(sent(fetch)).toContainEqual(["timeline/roll", { t: 26, delta: -0.05 }]));
    press(app, "ArrowDown", { altKey: true }); // move the clip to the next track
    await waitFor(() =>
      expect(sent(fetch)).toContainEqual(["timeline/move", { ref: { source_id: 2, source_in: 0 }, t: 25, track_id: 2 }]),
    );
    press(app, "m");
    await waitFor(() => expect(sent(fetch).some(([u]) => u === "timeline/markers")).toBe(true));
    press(app, "t");
    await waitFor(() => expect(sent(fetch)).toContainEqual(["timeline/tracks", { name: "Track 3" }]));
    press(app, "U", { shiftKey: true });
    await waitFor(() => expect(sent(fetch, "PATCH")).toContainEqual(["timeline/tracks/1", { mute: true }]));
  });

  test("Tab selects clips and opens the inspector; Shift+R restores a nearby cut", async () => {
    const fetch = setup();
    const app = await screen.findByRole("application", { name: /Timeline/ });
    act(() => app.focus());
    press(app, "Tab");
    expect(await screen.findByText(/Clip: ch01 0:00.0–0:25.0 at 0:00.0/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Gain (dB)"), { target: { value: "-3" } });
    await waitFor(() =>
      expect(sent(fetch)).toContainEqual(["timeline/clip", { ref: { source_id: 1, source_in: 0 }, gain_db: -3 }]),
    );
    press(app, "Tab");
    expect(screen.getByLabelText("Playhead")).toHaveTextContent("0:25.0");
    press(app, "Home");
    press(app, "ArrowRight", { shiftKey: true });
    press(app, "ArrowLeft");
    press(app, "ArrowLeft"); // 8 s: the stutter cut sits at 8.2 s
    press(app, "R", { shiftKey: true });
    await waitFor(() => expect(sent(fetch)).toContainEqual(["edl/regions/status", { ids: [5], status: "restored" }]));
  });

  test("zoom keys and the track header", async () => {
    const fetch = setup();
    const app = await screen.findByRole("application", { name: /Timeline/ });
    act(() => app.focus());
    press(app, "z");
    press(app, "0");
    fireEvent.click(screen.getAllByRole("button", { name: "S" })[0]!);
    await waitFor(() => expect(sent(fetch, "PATCH")).toContainEqual(["timeline/tracks/1", { solo: true }]));
  });

  test("quick repeated edits build on each other (regression)", async () => {
    let gain = 0;
    const fetch = mockApi({
      "GET /api/project/timeline": VIEW,
      "GET /api/project/timeline/lanes": LANES,
      "GET /api/project/sources/1/peaks": { min: [], max: [], total_buckets: 0 },
      "GET /api/project/sources/2/peaks": { min: [], max: [], total_buckets: 0 },
      "POST /api/project/timeline/clip": (b: unknown) => {
        gain = (b as { gain_db: number }).gain_db;
        return { ...VIEW, clips: VIEW.clips.map((c, i) => (i === 0 ? { ...c, gain_db: gain } : c)) };
      },
    });
    render(<TimelinePanel sources={[source({ id: 1 })]} peakSettings={SETTINGS.peaks} revision={0} onEdited={() => undefined} onError={() => undefined} />);
    const app = await screen.findByRole("application", { name: /Timeline/ });
    act(() => app.focus());
    press(app, "+");
    press(app, "+");
    press(app, "+");
    await waitFor(() => expect(sent(fetch).map(([, b]) => (b as { gain_db: number }).gain_db)).toEqual([1, 2, 3]));
  });

  test("Alt+Down right after adding a track moves the clip there (regression)", async () => {
    const one = { ...VIEW, tracks: VIEW.tracks.slice(0, 1) };
    let added: () => void = () => undefined;
    const fetch = mockApi({
      "GET /api/project/timeline": one,
      "GET /api/project/timeline/lanes": LANES,
      "GET /api/project/sources/1/peaks": { min: [], max: [], total_buckets: 0 },
      "GET /api/project/sources/2/peaks": { min: [], max: [], total_buckets: 0 },
      // The new track arrives only after Alt+Down has been pressed.
      "POST /api/project/timeline/tracks": () => new Promise((r) => (added = () => r(VIEW))),
      "POST /api/project/timeline/move": VIEW,
    });
    render(<TimelinePanel sources={[source({ id: 1 })]} peakSettings={SETTINGS.peaks} revision={0} onEdited={() => undefined} onError={() => undefined} />);
    const app = await screen.findByRole("application", { name: /Timeline/ });
    act(() => app.focus());
    press(app, "t");
    press(app, "ArrowDown", { altKey: true });
    await waitFor(() => expect(sent(fetch).map(([u]) => u)).toEqual(["timeline/tracks"]));
    act(() => added());
    await waitFor(() => expect(sent(fetch)).toContainEqual(["timeline/move", { ref: VIEW.clips[0]!.ref, t: 0, track_id: 2 }]));
  });
});
