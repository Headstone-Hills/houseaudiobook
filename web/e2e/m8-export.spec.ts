// M8 acceptance, end to end: finalize to the ACX preset with a pass/fail report,
// export MP3 per chapter (measured from the written files), and the narrator
// corrections guide as PDF, CSV and Markdown.
import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { checkedBook, step } from "./steps";

test("finalize, export and the corrections guide", async ({ page }) => {
  test.setTimeout(120_000);
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await checkedBook(page, "Export", projects);
  await step(page, "Fix mistakes");
  await expect(page.locator("section.issues").getByText("3 to review · 0 fixed · 0 OK as is")).toBeVisible({
    timeout: 30_000,
  });
  await step(page, "Export");

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
