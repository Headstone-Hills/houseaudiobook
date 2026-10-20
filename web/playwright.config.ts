import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

// Each run gets a throwaway app home and project location, so e2e never touches
// the developer's real recent-projects list or projects.
const runDir = mkdtempSync(join(tmpdir(), "hillaudio-e2e-"));
process.env.E2E_PROJECTS = join(runDir, "projects");

export default defineConfig({
  testDir: "e2e",
  // One engine holds one open project at a time (D-015), so tests must not run in parallel.
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:5173",
    // Use a preinstalled Chromium when one is provided (e.g. a dev container);
    // otherwise Playwright's own (CI runs `npx playwright install chromium`).
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
  webServer: [
    {
      // The real engine, without auto-reload.
      command: "../engine/.venv/bin/python -m uvicorn hillaudio.api.app:create_app --factory --host 127.0.0.1 --port 8765",
      cwd: "../engine",
      env: { HILLAUDIO_HOME: join(runDir, "home") },
      url: "http://127.0.0.1:8765/api/health",
      reuseExistingServer: false,
    },
    { command: "npm run dev", url: "http://127.0.0.1:5173", reuseExistingServer: false },
  ],
});
