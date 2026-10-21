// M8 acceptance, end to end: finalize to the ACX preset with a pass/fail report,
// export MP3 per chapter (measured from the written files), and the narrator
// corrections guide as PDF, CSV and Markdown.
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const FIXTURES = resolve(import.meta.dirname, "../../engine/fixtures");

test("finalize, export and the corrections guide", async ({ page }) => {
  test.setTimeout(120_000);
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await page.getByLabel("Name").fill("Export");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await page
    .getByLabel(/import by path/)
    .fill(["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"].map((n) => `${FIXTURES}/${n}`).join("\n"));
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.locator("table.sources tbody tr")).toHaveCount(3, { timeout: 20_000 });
  await page.getByLabel("Or by path").fill(`${FIXTURES}/manuscript.txt`);
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await page.getByRole("button", { name: "Import manuscript" }).click();
  await expect(page.getByText(/13 sentences/).first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Transcribe and compare" }).click();
  await expect(page.locator("section.issues").getByText("3 open · 0 resolved · 0 dismissed")).toBeVisible({ timeout: 30_000 });

  // Finalize: analyze against ACX.
  const fin = page.locator("section.finalize");
  await expect(fin.getByText(/RMS -23 to -18 dB, peak -3 dB, noise floor -60 dB RMS/)).toBeVisible();
  await fin.getByRole("button", { name: "Analyze levels" }).click();
  await expect(fin.getByRole("table", { name: /Levels after finalize: passes/ })).toBeVisible({ timeout: 60_000 });

  // Export: MP3, one file per chapter.
  const exp = page.locator("section.export");
  await exp.getByLabel("Files").selectOption("chapters");
  await expect(exp.getByText(/2 files: Chapter One/)).toBeVisible();
  await expect(exp.getByText("Last levels check: passes", { exact: false })).toBeVisible();
  await exp.getByRole("button", { name: "Export" }).click();
  const report = exp.getByRole("table", { name: /Exported: passes/ });
  await expect(report).toBeVisible({ timeout: 60_000 });
  const links = report.getByRole("link");
  await expect(links).toHaveCount(2);
  const href = await links.first().getAttribute("href");
  const file = await page.request.get(href!);
  expect(file.status()).toBe(200);
  expect((await file.body()).subarray(0, 3).toString("latin1")).toMatch(/^(ID3|\xff\xfb)/);

  // The guide: the skipped "now" on page 2.
  const guide = page.locator("section.guide");
  await expect(guide.getByRole("cell", { name: "Flub" })).toBeVisible();
  await guide.getByLabel("Narrator").fill("Sam Reader");
  for (const name of ["PDF", "CSV", "Markdown"]) {
    const r = await page.request.get((await guide.getByRole("link", { name }).getAttribute("href"))!);
    expect(r.status()).toBe(200);
  }
  const csv = await (await page.request.get((await guide.getByRole("link", { name: "CSV" }).getAttribute("href"))!)).text();
  expect(csv).toContain("# Narrator: Sam Reader");
  expect(csv).toContain("Flub");

  expect(consoleErrors).toEqual([]);
});
