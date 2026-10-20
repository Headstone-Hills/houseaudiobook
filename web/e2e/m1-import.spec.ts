// M1 acceptance, end to end in a real browser against the real engine:
// create a project, import by upload and by path, see sources and a waveform.
// This caught two bugs the unit tests could not (see docs/DECISIONS.md D-016).
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const FIXTURES = resolve(import.meta.dirname, "../../engine/fixtures");

test("create project, import, inspect sources", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.goto("/");
  await expect(page.getByText(/Engine .* ready/)).toBeVisible();
  await page.getByLabel("Name").fill("Fox and Grapes");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { name: "Fox and Grapes" })).toBeVisible();

  // Upload two files (same path as drag-and-drop); order must follow the pick order.
  await page.getByTestId("file-picker").setInputFiles([`${FIXTURES}/ch01_take1.wav`, `${FIXTURES}/ch01_take2.wav`]);
  // Import by path, referenced in place.
  await page.getByLabel(/import by path/).fill(`${FIXTURES}/ch02_take1.wav`);
  await page.getByLabel("Reference in place").check();
  await page.getByRole("button", { name: "Import", exact: true }).click();

  const labels = page.locator("table.sources tbody th");
  await expect(labels).toHaveText(["ch01_take1", "ch01_take2", "ch02_take1"], { timeout: 30_000 });
  const rows = page.locator("table.sources tbody tr");
  await expect(rows.nth(0)).toContainText("Copied");
  await expect(rows.nth(2)).toContainText("Referenced");
  await expect(rows.nth(2)).toContainText("OK");

  // Keyboard selection drives the waveform, which actually draws.
  await page.getByRole("rowheader", { name: "ch01_take1" }).click();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("img", { name: /Waveform of ch02_take1/ })).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(() => {
        const c = document.querySelector("canvas")!;
        const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
        let inked = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i]! > 0) inked++;
        return inked / (c.width * c.height);
      }),
    )
    .toBeGreaterThan(0.05);

  expect(consoleErrors).toEqual([]);
});
