import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { ExportReport, FinalizeView, Job, Preflight } from "../state/engine";
import { mockApi } from "../test-utils";
import { ExportPanel, FinalizePanel, GuidePanel } from "./FinishPanels";

const SETTINGS = {
  preset: "acx",
  noise_reduction: true,
  nr_strength: 0.5,
  nr_reduction_db: 12,
  noise_region: null,
  gate: false,
  gate_threshold_db: -55,
  gate_range_db: 10,
  auto_level: true,
  compressor_threshold_db: -24,
  compressor_ratio: 2,
  normalize: true,
  target_rms_db: -20,
  limiter: true,
  ceiling_db: -3.5,
};
const ACX = {
  key: "acx",
  name: "ACX / Audible",
  rms_min_db: -23,
  rms_max_db: -18,
  peak_max_db: -3,
  noise_floor_max_db: -60,
  mp3_min_kbps: 192,
  mp3_sample_rate: 44100,
  head_s: 1,
  tail_s: 3,
  max_minutes: 120,
  note: "Verify on acx.com: they can change.",
};
const REPORT: ExportReport = {
  created: "20261021-120000",
  format: "mp3",
  split: "single",
  pass: true,
  files: [
    {
      title: "Book",
      file: "exports/20261021-120000/Book.mp3",
      seconds: 70,
      levels: { peak_db: -3.6, rms_db: -20.1, noise_floor_db: -71, digital_silence: false },
      normalize_db: 5.2,
      pass: true,
      checks: [
        { name: "RMS", value: -20.1, limit: "-23 to -18 dB", pass: true },
        { name: "Noise floor", value: -71, limit: "<= -60 dB RMS", pass: true },
      ],
    },
  ],
};
const VIEW: FinalizeView = { settings: SETTINGS, presets: { acx: ACX }, last_analysis: null };

const job = (over: Partial<Job>): Job => ({ id: "j1", kind: "finalize", label: "", status: "running", progress: 0.4, message: "Book: measuring", result: null, error: null, ...over });

const calls = (fetch: ReturnType<typeof mockApi>, method: string) =>
  fetch.mock.calls.filter(([, i]) => i?.method === method).map(([u, i]) => [String(u), JSON.parse(String(i?.body ?? "null")) as unknown]);

describe("FinalizePanel", () => {
  test("settings save as they change; analyze shows progress then the report", async () => {
    const fetch = mockApi({
      "GET /api/project/finalize": VIEW,
      "PUT /api/project/finalize": (b: unknown) => ({ ...VIEW, settings: { ...SETTINGS, ...(b as object) } }),
      "POST /api/project/finalize/analyze": { job_id: "j1" },
    });
    const { rerender } = render(<FinalizePanel jobs={[]} onError={() => undefined} />);
    expect(await screen.findByText(/RMS -23 to -18 dB · peak ≤ -3 dB/)).toBeInTheDocument();
    expect(screen.getByText(/Verify on acx.com/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Gate between phrases"));
    await waitFor(() => expect(calls(fetch, "PUT")).toContainEqual(["/api/project/finalize", { gate: true }]));
    const strength = screen.getByLabelText("Strength (0–1)");
    fireEvent.change(strength, { target: { value: "0.8" } });
    fireEvent.blur(strength);
    await waitFor(() => expect(calls(fetch, "PUT")).toContainEqual(["/api/project/finalize", { nr_strength: 0.8 }]));

    fireEvent.click(screen.getByRole("button", { name: "Analyze levels" }));
    await waitFor(() => expect(calls(fetch, "POST")).toContainEqual(["/api/project/finalize/analyze", { split: "single" }]));
    rerender(<FinalizePanel jobs={[job({})]} onError={() => undefined} />);
    expect(await screen.findByText("Book: measuring 40%")).toBeInTheDocument();
    rerender(<FinalizePanel jobs={[job({ status: "done", progress: 1, result: REPORT })]} onError={() => undefined} />);
    const table = await screen.findByRole("table", { name: /Levels after finalize: passes/ });
    expect(within(table).getByText(/-20.1 ✓/)).toBeInTheDocument();
  });

  test("A/B plays the edited timeline and the finalized version", async () => {
    mockApi({ "GET /api/project/finalize": VIEW });
    render(<FinalizePanel jobs={[]} onError={() => undefined} />);
    fireEvent.click(await screen.findByRole("button", { name: "A: as edited" }));
    const audio = screen.getByLabelText("Finalize preview audio") as HTMLAudioElement;
    expect(audio.src).toContain("/api/project/edl/render?start=0.000&end=20.000");
    fireEvent.click(screen.getByRole("button", { name: "B: finalized" }));
    expect(audio.src).toContain("/api/project/finalize/preview?start=0.000&end=20.000");
  });
});

describe("ExportPanel", () => {
  const PF: Preflight = {
    open_issues: { flub: 1, silence: 2 },
    lossy_sources: [],
    missing_sources: [],
    outputs: [{ title: "Book", seconds: 70 }],
    problems: [],
    warnings: [],
    last_analysis: REPORT,
  };

  test("pre-flight, export, report with a download link", async () => {
    const fetch = mockApi({
      "GET /api/project/export/preflight": PF,
      "GET /api/project/exports": [],
      "POST /api/project/export": { job_id: "e1" },
    });
    const { rerender } = render(<ExportPanel jobs={[]} onError={() => undefined} />);
    expect(await screen.findByText("Unresolved issues: 1 flub, 2 silence")).toBeInTheDocument();
    expect(screen.getByText("1 file: Book (1.2 min)")).toBeInTheDocument();
    expect(screen.getByText(/Last levels check: passes/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Bitrate (kbps)"), { target: { value: "256" } });
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    await waitFor(() => expect(calls(fetch, "POST")).toContainEqual(["/api/project/export", { format: "mp3", split: "single", kbps: 256 }]));
    rerender(<ExportPanel jobs={[job({ id: "e1", kind: "export", status: "done", result: REPORT })]} onError={() => undefined} />);
    const link = await screen.findByRole("link", { name: "Book.mp3" });
    expect(link).toHaveAttribute("href", "/api/project/exports/20261021-120000/Book.mp3");
  });

  test("a blocking problem disables export", async () => {
    mockApi({
      "GET /api/project/export/preflight": { ...PF, lossy_sources: ["pickup"], problems: ["MP3 export would re-encode lossy recordings (pickup): lossy to lossy is never done."] },
      "GET /api/project/exports": [],
    });
    render(<ExportPanel jobs={[]} onError={() => undefined} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("lossy to lossy is never done");
    expect(screen.getByRole("button", { name: "Export" })).toBeDisabled();
  });
});

describe("GuidePanel", () => {
  test("rows, options and download links", async () => {
    const fetch = mockApi({
      "GET /api/project/guide/rows": {
        header: {},
        rows: [{ n: 1, page: "2", time: "0:29.7", recording: "ch01_take1", type: "Flub", expected: "I see now", heard: "I see", note: "", status: "open" }],
      },
    });
    render(<GuidePanel revision={0} />);
    expect(await screen.findByText("1 correction ·", { exact: false })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "0:29.7" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Times refer to"), { target: { value: "master" } });
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u).includes("reference=master"))).toBe(true));
    fireEvent.change(screen.getByLabelText("Narrator"), { target: { value: "Sam" } });
    const pdf = screen.getByRole("link", { name: "PDF" });
    expect(pdf.getAttribute("href")).toContain("format=pdf");
    expect(pdf.getAttribute("href")).toContain("reference=master");
    expect(pdf.getAttribute("href")).toContain("narrator=Sam");
  });
});
