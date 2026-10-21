// M3 acceptance, end to end: audio + manuscript in, Transcribe and compare, and
// the results match the fixtures' injected errors. Speech recognition is the
// ground-truth fake (engine/tests/e2e_app.py); the real model has its own test.
import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { importAudio, importManuscript, step } from "./steps";

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

  // Nothing to check yet: the step says what's missing.
  await step(page, "Check the reading");
  const run = page.getByRole("button", { name: "Transcribe and compare" });
  await expect(run).toBeDisabled();
  await expect(page.getByText("Add recordings first.")).toBeVisible();

  await importAudio(page);
  await importManuscript(page);
  await step(page, "Check the reading");
  await expect(run).toBeEnabled();
  await run.click();
  await expect(page.getByRole("list", { name: "Summary" })).toHaveText(
    /12 read correctly\s*1 read differently\s*0 skipped\s*1 off-script/,
    { timeout: 30_000 },
  );
  const sentences = page.getByRole("list", { name: "Sentences" });
  await expect(sentences.getByRole("listitem").first()).toContainText("I thought those grapes were ripe");
  await expect(page.getByText("Missing: “now”").first()).toBeVisible();
  await expect(page.locator("section.comparison").getByText(/Hold on, let me get some water/)).toBeVisible();

  // The misread line has a clean pickup in take 2, so it counts as clean; its takes show both.
  await page.locator("fieldset.segmented label", { hasText: "Every line" }).click();
  const line = sentences.getByRole("listitem").filter({ hasText: "out of reach." }).first();
  await line.getByText("Every reading (2)").click();
  const takes = line.getByRole("table", { name: "Every reading of this sentence" });
  await expect(takes.getByRole("row")).toHaveCount(3); // header + 2 takes
  await expect(takes).toContainText("Misread: “reach” → “sight.”");
  await expect(takes).toContainText("ch01_take2");

  expect(consoleErrors).toEqual([]);
});

test("audio for part of the book: the rest is 'not recorded yet', and chapters narrow each step", async ({ page }) => {
  // D-059: chapter one recorded, chapter two not yet.
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await page.getByLabel("Name").fill("Half a Book");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await importAudio(page, ["ch01_take1.wav", "ch01_take2.wav"]);
  await importManuscript(page);
  await step(page, "Check the reading");
  await page.getByRole("button", { name: "Transcribe and compare" }).click();
  const summary = page.getByRole("list", { name: "Summary" });
  await expect(summary).toContainText("6 not recorded yet", { timeout: 30_000 });
  await expect(summary).toContainText("0 skipped");
  await expect(page.getByText(/recordings cover only part of the manuscript/)).toBeVisible();

  // No line of chapter two is a mistake to fix.
  await step(page, "Fix mistakes");
  const issues = page.locator("section.issues");
  await expect(issues.getByText("2 to review · 0 fixed · 0 OK as is")).toBeVisible({ timeout: 20_000 });
  await expect(issues.locator("tbody").getByText("Unread line")).toHaveCount(0);

  // Chapters: pick chapter two from the overview; it's waiting for its recordings.
  await step(page, "Overview");
  await page.getByRole("button", { name: "Work on 2. The Tortoise and the Hare" }).click();
  await expect(page.getByLabel("Working on")).toHaveValue(/\d+/);
  await expect(page.getByText("No recording has this chapter in it yet", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Go to Recordings" })).toBeVisible();
  await step(page, "Fix mistakes");
  await expect(issues.getByText("0 to review · 0 fixed · 0 OK as is")).toBeVisible();

  // Chapter one has both issues; the choice survives a reload.
  await page.getByLabel("Working on").selectOption({ label: "1. The Fox and the Grapes · 2 to review" });
  await expect(issues.getByText("2 to review · 0 fixed · 0 OK as is")).toBeVisible();
  await page.reload();
  await expect(page.locator("#step-title")).toHaveText("Fix mistakes", { timeout: 20_000 });
  await expect(page.getByLabel("Working on")).toHaveValue(/\d+/);
  await expect(page.locator(".step-context")).toContainText("1. The Fox and the Grapes");

  expect(consoleErrors).toEqual([]);
});
