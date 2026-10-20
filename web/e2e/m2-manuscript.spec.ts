// M2 acceptance, end to end: upload a PDF manuscript, see its page mapping,
// skip the front matter, import, and browse sentences with their pages.
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const FIXTURES = resolve(import.meta.dirname, "../../engine/fixtures");

test("import a PDF manuscript and map its pages", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  // The engine is shared by all tests and keeps its open project; start clean.
  await page.request.post("/api/project/close");
  await page.goto("/");
  await page.getByLabel("Name").fill("Two Fables");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "Two Fables" })).toBeVisible();

  await page.getByTestId("manuscript-picker").setInputFiles(`${FIXTURES}/manuscript.pdf`);
  await expect(page.getByText("the PDF's pages")).toBeVisible();
  const pages = page.getByRole("table", { name: "Where each page starts" });
  await expect(pages.getByRole("row").nth(1)).toContainText("i");
  await expect(pages.getByRole("row").nth(1)).toContainText("Two Fables from Aesop");

  await page.getByLabel("Skip front matter").check();
  await expect(page.locator(".preview-sentences li").first()).toContainText(
    "p. 1 Chapter One. The Fox and the Grapes.",
  );
  await page.getByRole("button", { name: "Import manuscript" }).click();

  await expect(page.getByText(/manuscript\.pdf.*3 pages · 2 chapters · 13 sentences/)).toBeVisible({
    timeout: 20_000,
  });
  const units = page.locator(".unit-table tbody tr");
  await expect(units).toHaveCount(13);
  await expect(units.nth(4)).toContainText("But it was all in vain");
  await expect(units.nth(4).locator(".page-cell")).toHaveText("2");

  // Re-map: estimate pages from word count instead.
  await page.getByRole("button", { name: "Change page settings…" }).click();
  await page.getByLabel("Take page numbers from").selectOption("estimate");
  await expect(page.getByText("an estimate from word count")).toBeVisible();
  await page.getByRole("button", { name: "Apply page settings" }).click();
  await expect(page.getByText(/about 1 page \(estimated\)/)).toBeVisible({ timeout: 20_000 });
  await expect(units.nth(4).locator(".page-cell")).toHaveText("approx. 1");

  expect(consoleErrors).toEqual([]);
});
