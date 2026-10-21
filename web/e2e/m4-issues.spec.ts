// M4 acceptance, end to end: flubs and long pauses are flagged with time and page,
// reviewed from the keyboard, played with context, and decisions survive a re-run.
import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { checkedBook, step } from "./steps";

test("review issues from the keyboard and re-run checks", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await checkedBook(page, "Issues", projects);
  await step(page, "Fix mistakes");

  // The fixtures' ground truth: the skipped "now" (p. 2) and two long pauses.
  const issues = page.locator("section.issues");
  await expect(issues.getByText("3 to review · 0 fixed · 0 OK as is")).toBeVisible({ timeout: 30_000 });
  const rows = issues.locator("table.issue-table tbody tr");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("Long pause");
  await expect(rows.nth(0)).toContainText("at a paragraph break");
  await expect(rows.nth(1)).toContainText("Flub");
  await expect(rows.nth(1)).toContainText("Missing: “now”");
  await expect(rows.nth(1).locator("td").nth(2)).toHaveText("p. 2");
  await expect(rows.nth(1)).toContainText("ch01_take1 0:29.");
  await expect(rows.nth(2)).toContainText("at a sentence break");
  // The sidebar counts them too.
  const badge = page.getByRole("button", { name: /^Fix mistakes/ }).locator(".step-badge");
  await expect(badge).toHaveText("3");

  // Keyboard: move to the flub, play it (excerpt with context), add a note, dismiss.
  await rows.nth(0).focus();
  await page.keyboard.press("j");
  await expect(rows.nth(1)).toBeFocused();
  const excerpt = page.waitForResponse((r) => r.url().includes("/excerpt?"));
  await page.keyboard.press("Space");
  const resp = await excerpt;
  expect(resp.status()).toBe(200);
  expect(resp.headers()["content-type"]).toBe("audio/wav");
  await page.keyboard.press("n");
  await page.getByLabel("Note").fill("intentional");
  await page.keyboard.press("Enter");
  await expect(rows.nth(1)).toContainText("intentional");
  await page.keyboard.press("d");
  await expect(issues.getByText("2 to review · 0 fixed · 1 OK as is")).toBeVisible();

  // Resolve the rest in bulk.
  await rows.nth(0).focus();
  await page.keyboard.press("x");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("x");
  await page.keyboard.press("r");
  await expect(issues.getByText("0 to review · 2 fixed · 1 OK as is")).toBeVisible();
  await expect(badge).toHaveCount(0);

  // Re-run: the same issues, decisions kept (also after reloading the page).
  await issues.getByRole("button", { name: "Re-run checks" }).click();
  await expect(page.getByText("Flag flubs + Flag silences").first()).toBeVisible();
  await page.reload();
  await expect(page.locator("section.issues").getByText("0 to review · 2 fixed · 1 OK as is")).toBeVisible({
    timeout: 20_000,
  });
  await page.locator("section.issues fieldset.segmented label", { hasText: /^All$/ }).click();
  const all = page.locator("section.issues table.issue-table tbody tr");
  await expect(all).toHaveCount(3);
  await expect(all.filter({ hasText: "Flub" })).toContainText("OK as is");
  await expect(all.filter({ hasText: "Flub" })).toContainText("intentional");

  expect(consoleErrors).toEqual([]);
});
