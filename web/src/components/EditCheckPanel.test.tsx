import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { EditCheck, LostLine } from "../state/engine";
import { mockApi } from "../test-utils";
import { EditCheckPanel } from "./EditCheckPanel";

function lost(over: Partial<LostLine>): LostLine {
  return {
    unit_id: 3,
    ordinal: 3,
    page_label: "1",
    text: "He did his best to reach them by jumping as high as he could.",
    status: "clipped",
    source_id: 1,
    start: 12.4,
    end: 15.9,
    missing: ["reach", "them"],
    removals: [{ id: 9, kind: "cut", start: 14.0, end: 14.8 }],
    ...over,
  };
}

const REPORT: EditCheck = {
  run_id: 1,
  stale: false,
  checked: 13,
  lines: [lost({}), lost({ unit_id: 5, status: "removed", missing: ["Take", "two"], removals: [{ id: 4, kind: "take", start: 0, end: 3 }] })],
};

const calls = (fetch: ReturnType<typeof mockApi>, method: string) =>
  fetch.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([u, init]) => [String(u), JSON.parse(String(init?.body)) as unknown]);

test("lists lines the edit lost, with what cut them; restore and re-run", async () => {
  let edited = 0;
  let takes = 0;
  const fetch = mockApi({
    "GET /api/project/edit-check": REPORT,
    "POST /api/project/edl/regions/status": { regions: [] },
    "POST /api/project/analysis": { job_id: "j1" },
  });
  render(
    <EditCheckPanel
      checks={["flubs", "silences"]}
      revision={0}
      onEdited={() => edited++}
      onTakes={() => takes++}
      onError={() => undefined}
    />,
  );
  expect(await screen.findByText("2 lines lost in the edit")).toBeInTheDocument();
  const [clipped, removed] = within(screen.getByRole("list", { name: "Lines lost in the edit" })).getAllByRole("listitem");
  expect(clipped).toHaveTextContent("Clipped");
  expect(clipped).toHaveTextContent("Missing: reach them · by a cut");
  expect(removed).toHaveTextContent("by choosing another take");
  // A take is changed in Choose takes, not restored here.
  expect(within(removed!).queryByRole("button", { name: "Restore" })).toBeNull();
  fireEvent.click(within(removed!).getByRole("button", { name: "Go to Choose takes" }));
  expect(takes).toBe(1);

  fireEvent.click(within(clipped!).getByRole("button", { name: "Restore" }));
  await waitFor(() => expect(calls(fetch, "POST")).toContainEqual(["/api/project/edl/regions/status", { ids: [9], status: "restored" }]));
  await waitFor(() => expect(edited).toBe(1));

  fireEvent.click(within(clipped!).getByRole("button", { name: "Play the original of p. 1" }));
  expect((screen.getByLabelText("Lost line audio") as HTMLAudioElement).src).toContain("/api/project/sources/1/excerpt?start=12.400&end=15.900&context=0.5");

  fireEvent.click(screen.getByRole("button", { name: "Re-run checks" }));
  await waitFor(() => expect(calls(fetch, "POST")).toContainEqual(["/api/project/analysis", { stages: ["flubs", "silences"], asr: {} }]));
  expect(screen.getByRole("button", { name: "Checking…" })).toBeDisabled();
});

test("nothing lost; and before any check", async () => {
  mockApi({ "GET /api/project/edit-check": { ...REPORT, lines: [] } });
  const { unmount } = render(<EditCheckPanel checks={["flubs"]} revision={0} onEdited={() => undefined} onTakes={() => undefined} onError={() => undefined} />);
  expect(await screen.findByText("Nothing lost: all 13 lines read in full still play.")).toBeInTheDocument();
  unmount();
  mockApi({ "GET /api/project/edit-check": { run_id: null, stale: false, checked: 0, lines: [] } });
  render(<EditCheckPanel checks={["flubs"]} revision={0} onEdited={() => undefined} onTakes={() => undefined} onError={() => undefined} />);
  expect(await screen.findByText(/Check the reading first/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Re-run checks" })).toBeDisabled();
});
