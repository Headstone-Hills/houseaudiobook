import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { App } from "./App";

afterEach(() => vi.unstubAllGlobals());

test("shows engine version when the engine is up", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "ok", version: "9.9.9" }))),
  );
  render(<App />);
  expect(await screen.findByText("Engine 9.9.9 ready")).toBeInTheDocument();
});

test("reports when the engine is down", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 502 })));
  render(<App />);
  expect(await screen.findByText(/Engine unavailable: .*502/)).toBeInTheDocument();
});
