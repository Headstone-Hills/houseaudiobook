// M5 acceptance, end to end: cleanup removes the stutters, false start and
// commentary as EDL regions; they're reviewed, restored, trimmed and undone from
// the keyboard; a long pause is shortened; history survives a reload.
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const FIXTURES = resolve(import.meta.dirname, "../../engine/fixtures");

test("clean up, review removed material, undo and redo", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await page.getByLabel("Name").fill("Cleanup");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await page
    .getByLabel(/import by path/)
    .fill(["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"].map((n) => `${FIXTURES}/${n}`).join("\n"));
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.locator("table.sources tbody tr")).toHaveCount(3, { timeout: 20_000 });
  await page.getByLabel("Or by path").fill(`${FIXTURES}/manuscript.txt`);
  await page.getByRole("button", { name: "Preview" }).click();
  await page.getByRole("button", { name: "Import manuscript" }).click();
  await expect(page.getByText(/13 sentences/).first()).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Transcribe and compare" }).click();

  const removed = page.locator("section.removed");
  await expect(removed.getByText(/4 removed · 0 suggested · 0 restored/)).toBeVisible({ timeout: 30_000 });
  const rows = removed.locator("table tbody tr");
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText("Stutter");
  await expect(rows.nth(1)).toContainText("He did his best");
  await expect(rows.nth(2)).toContainText("Hold on, let me get some water.");

  // Play the join as edited: rendered from the sources.
  await rows.nth(2).focus();
  const rendered = page.waitForResponse((r) => r.url().includes("/api/project/edl/render?"));
  await page.keyboard.press("Space");
  expect((await rendered).status()).toBe(200);

  // Restore the commentary, then undo and redo it from the keyboard.
  await page.keyboard.press("r");
  await expect(removed.getByText(/3 removed · 0 suggested · 1 restored/)).toBeVisible();
  await page.keyboard.press("Control+z");
  await expect(removed.getByText(/4 removed · 0 suggested · 0 restored/)).toBeVisible();
  await page.keyboard.press("Control+Shift+z");
  await expect(removed.getByText(/3 removed · 0 suggested · 1 restored/)).toBeVisible();

  // Bring back 50 ms at the start of the stutter cut (a trim on the clip edge).
  await rows.nth(0).focus();
  const before = await rows.nth(0).locator("td").nth(3).textContent();
  await page.keyboard.press("[");
  await expect(rows.nth(0).locator("td").nth(3)).not.toHaveText(before ?? "");

  // Shorten the paragraph-break pause from the Issues panel, then re-run checks.
  const issues = page.locator("section.issues");
  const pause = issues.locator("table.issue-table tbody tr").filter({ hasText: "paragraph break" });
  await pause.getByRole("button", { name: /Shorten to 1.2 s/ }).click();
  await expect(removed.getByText("Shortened pause")).toBeVisible();
  await issues.getByRole("button", { name: "Re-run checks" }).click();
  await expect(issues.getByText("2 open · 1 resolved · 0 dismissed")).toBeVisible({ timeout: 20_000 });

  // History is saved with the project.
  await page.reload();
  const again = page.locator("section.removed");
  await expect(again.getByText(/4 removed · 0 suggested · 1 restored/)).toBeVisible({ timeout: 20_000 });
  await again.getByText("Edit history").click();
  // Cleanup, takes, restore (undone and redone: one entry), trim, shorten. A
  // re-run that changes nothing adds no entry.
  const history = again.getByRole("list", { name: "Edit history" }).getByRole("listitem");
  await expect(history).toHaveCount(5);
  await expect(history.nth(0)).toContainText("Cleanup: 4 removed");
  await expect(history.nth(1)).toContainText("Takes: 2 lines with retakes");
  await expect(history.nth(4)).toContainText("Shorten pause");

  expect(consoleErrors).toEqual([]);
});
