// M3 acceptance, end to end: audio + manuscript in, Transcribe and compare, and
// the results match the fixtures' injected errors. Speech recognition is the
// ground-truth fake (engine/tests/e2e_app.py); the real model has its own test.
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const FIXTURES = resolve(import.meta.dirname, "../../engine/fixtures");

test("compare narration to the manuscript", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await page.getByLabel("Name").fill("Compare");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();

  const run = page.getByRole("button", { name: "Transcribe and compare" });
  await expect(run).toBeDisabled();
  await expect(page.getByText("Import audio first.")).toBeVisible();

  await page
    .getByLabel(/import by path/)
    .fill(["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"].map((n) => `${FIXTURES}/${n}`).join("\n"));
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.locator("table.sources tbody tr")).toHaveCount(3, { timeout: 20_000 });
  await page.getByLabel("Or by path").fill(`${FIXTURES}/manuscript.txt`);
  await page.getByRole("button", { name: "Preview" }).click();
  await page.getByRole("button", { name: "Import manuscript" }).click();
  await expect(page.getByText(/13 sentences/).first()).toBeVisible({ timeout: 20_000 });

  await expect(run).toBeEnabled();
  await run.click();
  await expect(page.getByText("12 clean · 1 with differences · 0 not read · 1 off-script passage")).toBeVisible({
    timeout: 30_000,
  });
  const sentences = page.getByRole("list", { name: "Sentences" });
  await expect(sentences.getByRole("listitem").first()).toContainText("I thought those grapes were ripe");
  await expect(page.getByText("Missing: “now”").first()).toBeVisible();
  await expect(page.locator("section.comparison").getByText(/Hold on, let me get some water/)).toBeVisible();

  // The misread line has a clean pickup in take 2, so it counts as clean; its takes show both.
  await page.getByLabel("All sentences").check();
  const line = sentences.getByRole("listitem").filter({ hasText: "out of reach." }).first();
  await line.getByText("Show all takes").click();
  const takes = line.getByRole("table", { name: "Every reading of this sentence" });
  await expect(takes.getByRole("row")).toHaveCount(3); // header + 2 takes
  await expect(takes).toContainText("Misread: “reach” → “sight.”");
  await expect(takes).toContainText("ch01_take2");

  expect(consoleErrors).toEqual([]);
});
