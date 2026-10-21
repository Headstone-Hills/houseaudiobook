import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { Job, SpeechModel } from "../state/engine";
import { mockApi } from "../test-utils";
import { ModelsBanner } from "./ModelsBanner";

const small: SpeechModel = { name: "small.en", repo: "Systran/faster-whisper-small.en", revision: "d1d751a", license: "MIT", size_mb: 486, installed: false, job_id: null };
const tiny: SpeechModel = { ...small, name: "tiny.en", size_mb: 76, installed: true };

describe("ModelsBanner", () => {
  test("first run: explains the download and starts it only when asked", async () => {
    const fetch = mockApi({
      "GET /api/models": { dir: "/m", source: "https://huggingface.co", models: [tiny, small] },
      "POST /api/models/small.en/download": { job_id: "m1" },
    });
    const { rerender } = render(<ModelsBanner jobs={[]} defaultModel="small.en" />);
    expect(await screen.findByText(/The recommended small.en model is 486 MB, downloaded once from huggingface.co/)).toBeInTheDocument();
    expect(screen.getByText(/never leave this computer/)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([, i]) => i?.method === "POST")).toBe(false); // nothing yet
    fireEvent.click(screen.getAllByRole("button", { name: "Download small.en" })[0]!);
    await waitFor(() => expect(fetch.mock.calls.some(([u]) => String(u) === "/api/models/small.en/download")).toBe(true));
    const running: Job = { id: "m1", kind: "model", label: "Download small.en", status: "running", progress: 0.25, message: "small.en: 122 of 486 MB", result: null, error: null };
    rerender(<ModelsBanner jobs={[running]} defaultModel="small.en" />);
    expect(screen.getAllByLabelText("Downloading small.en")[0]).toHaveAttribute("value", "0.25");
    expect(screen.getAllByText(/122 of 486 MB/)[0]).toBeInTheDocument();
  });

  test("nothing to say when the model is there; failures are shown", async () => {
    mockApi({ "GET /api/models": { dir: "/m", source: "https://huggingface.co", models: [tiny, { ...small, installed: true }] } });
    const failed: Job = { id: "m2", kind: "model", label: "Download small.en", status: "failed", progress: 0.1, message: "", result: null, error: "the download of small.en was interrupted; it resumes where it stopped when you try again" };
    render(<ModelsBanner jobs={[failed]} defaultModel="small.en" />);
    expect(await screen.findByText("Speech models")).toBeInTheDocument();
    expect(screen.queryByText(/needs a model/)).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("resumes where it stopped");
  });
});
