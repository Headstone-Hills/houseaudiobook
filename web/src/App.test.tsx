import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { App } from "./App";
import { FakeWebSocket } from "./test-setup";
import { FUNCTIONS, SETTINGS, mockApi, project, source } from "./test-utils";

const home = {
  "GET /api/settings": SETTINGS,
  "GET /api/functions": FUNCTIONS,
  "GET /api/health": { status: "ok", version: "9.9.9" },
};

test("projects screen lists recent projects and engine status", async () => {
  mockApi(home);
  render(<App />);
  expect(await screen.findByRole("button", { name: "Old Book" })).toBeInTheDocument();
  expect(await screen.findByText("Engine 9.9.9 ready")).toBeInTheDocument();
  expect(screen.getByLabelText("Location")).toHaveValue(SETTINGS.default_location);
});

test("engine down shows an alert", async () => {
  mockApi({ "GET /api/settings": new Response("", { status: 502 }) });
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Engine unavailable: engine returned 502");
});

test("new project wizard sends the closed function selection", async () => {
  const created: unknown[] = [];
  mockApi({
    ...home,
    "POST /api/projects": (body: unknown) => {
      created.push(body);
      return project({ name: "New Book" });
    },
  });
  render(<App />);
  const fieldset = await screen.findByRole("group", { name: "Automated functions" });
  const box = (name: RegExp) => within(fieldset).getByRole("checkbox", { name });

  // Unchecking Transcribe unchecks everything that needs it.
  fireEvent.click(box(/^Transcribe/));
  expect(box(/^Compare to Text/)).not.toBeChecked();
  expect(box(/^Mark overlong silences/)).toBeChecked();
  // Checking Compare takes brings its prerequisites back.
  fireEvent.click(box(/^Compare takes/));
  expect(box(/^Transcribe/)).toBeChecked();
  expect(box(/^Compare to Text/)).toBeChecked();

  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "New Book" } });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  expect(await screen.findByRole("heading", { name: "New Book" })).toBeInTheDocument();
  expect(created[0]).toEqual({
    location: SETTINGS.default_location,
    name: "New Book",
    functions: ["transcribe", "compare_text", "organize", "silences", "takes"],
  });
});

test("project errors are shown, not swallowed", async () => {
  mockApi({
    ...home,
    "POST /api/projects/open": new Response(JSON.stringify({ detail: "not a HillAudiobook project: /x" }), {
      status: 400,
    }),
  });
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Project folder (.hillproj)"), { target: { value: "/x" } });
  fireEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not a HillAudiobook project: /x");
});

test("project screen: sources, warnings, relink, and refresh when a job finishes", async () => {
  const missing = source({ id: 2, label: "ch02", status: "missing", mode: "reference", path: "/gone.wav" });
  let current = project({
    sources: [source(), missing],
    warnings: ["sources have mixed sample rates (16000 Hz, 48000 Hz); the engine resamples internally"],
  });
  let opened = false;
  mockApi({
    ...home,
    "POST /api/projects/open": () => {
      opened = true;
      return current;
    },
    "GET /api/project": () => (opened ? current : null),
    "GET /api/project/sources/1/peaks": { min: [], max: [], total_buckets: 0, num_frames: 0 },
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Old Book" }));

  const table = await screen.findByRole("table");
  expect(within(table).getByRole("rowheader", { name: "ch01" })).toBeInTheDocument();
  expect(within(table).getAllByText("WAV · 48 kHz · 24-bit · mono")).toHaveLength(2);
  expect(within(table).getAllByText("1:05.0")).toHaveLength(2);
  expect(screen.getByText(/mixed sample rates/)).toBeInTheDocument();
  // Only the missing source offers relinking.
  expect(within(table).getAllByRole("button", { name: "Relink" })).toHaveLength(1);
  expect(screen.getByRole("img", { name: /Waveform of ch01/ })).toBeInTheDocument();

  // A job finishing triggers a refetch of the project.
  current = { ...current, sources: [...current.sources, source({ id: 3, label: "ch03" })] };
  act(() =>
    FakeWebSocket.last?.emit({
      type: "jobs",
      jobs: [{ id: "j1", kind: "import", label: "Importing ch03.wav", status: "done", progress: 1, message: "", result: { files: [] }, error: null }],
    }),
  );
  expect(await screen.findByRole("rowheader", { name: "ch03" })).toBeInTheDocument();
});

test("arrow keys move the selected source", async () => {
  mockApi({
    ...home,
    "POST /api/projects/open": project({ sources: [source(), source({ id: 2, label: "ch02" })] }),
    "GET /api/project/sources/1/peaks": { min: [], max: [], total_buckets: 0, num_frames: 0 },
    "GET /api/project/sources/2/peaks": { min: [], max: [], total_buckets: 0, num_frames: 0 },
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Old Book" }));
  const rows = await screen.findAllByRole("row");
  fireEvent.keyDown(rows[1]!, { key: "ArrowDown" });
  expect(await screen.findByRole("img", { name: /Waveform of ch02/ })).toBeInTheDocument();
  expect(rows[2]).toHaveAttribute("aria-selected", "true");
});

test("import by path posts paths and mode", async () => {
  const calls: unknown[] = [];
  mockApi({
    ...home,
    "POST /api/projects/open": project(),
    "POST /api/project/sources/import": (body: unknown) => {
      calls.push(body);
      return { job_id: "j1" };
    },
  });
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Old Book" }));
  fireEvent.change(await screen.findByLabelText(/import by path/), { target: { value: "/a.wav\n\n /b.mp3 " } });
  fireEvent.click(screen.getByLabelText("Reference in place"));
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  await screen.findByLabelText(/import by path/);
  await act(async () => {});
  expect(calls).toEqual([{ paths: ["/a.wav", "/b.mp3"], mode: "reference" }]);
});

test("a reload continues in the open project", async () => {
  mockApi({ ...home, "GET /api/project": project({ name: "Open Book" }) });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Open Book" })).toBeInTheDocument();
});

test("updates about old jobs don't pull the user off the Projects screen", async () => {
  // Regression: a finished-job update made the app fetch and show the engine's project.
  const fetchMock = mockApi({ ...home, "GET /api/project": null });
  render(<App />);
  await screen.findByRole("button", { name: "Old Book" });
  act(() =>
    FakeWebSocket.last?.emit({
      type: "jobs",
      jobs: [{ id: "old", kind: "import", label: "x", status: "done", progress: 1, message: "", result: null, error: null }],
    }),
  );
  await act(async () => {});
  const projectCalls = fetchMock.mock.calls.filter(([url]) => String(url) === "/api/project");
  expect(projectCalls).toHaveLength(1); // only the initial load
  expect(screen.getByRole("heading", { name: "New project" })).toBeInTheDocument();
});
