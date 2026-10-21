// Moving around a project, which shows one step at a time (D-060), and the setup
// most specs share.
import { resolve } from "node:path";
import { expect, type Page } from "@playwright/test";

export const FIXTURES = resolve(import.meta.dirname, "../../engine/fixtures");
export const AUDIO = ["ch01_take1.wav", "ch01_take2.wav", "ch02_take1.wav"];

/** Open a step from the sidebar, by its title. */
export async function step(page: Page, title: string) {
  await page
    .getByRole("navigation", { name: "Steps" })
    .getByRole("button", { name: new RegExp(`^${title}`) })
    .click();
  await expect(page.locator("#step-title")).toHaveText(title);
}

export async function importAudio(page: Page, names: string[] = AUDIO) {
  await step(page, "Recordings");
  await page.getByLabel(/import by path/).fill(names.map((n) => `${FIXTURES}/${n}`).join("\n"));
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.locator("table.sources tbody tr")).toHaveCount(names.length, { timeout: 20_000 });
}

export async function importManuscript(page: Page) {
  await step(page, "Manuscript");
  await page.getByLabel("Or by path").fill(`${FIXTURES}/manuscript.txt`);
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await page.getByRole("button", { name: "Import manuscript" }).click();
  await expect(page.getByText(/13 sentences/).first()).toBeVisible({ timeout: 20_000 });
}

/** Transcribe and compare (with the project's checks). Returns without waiting. */
export async function checkReading(page: Page) {
  await step(page, "Check the reading");
  await page.getByRole("button", { name: "Transcribe and compare" }).click();
}

/** A new project with the three fixture recordings and the manuscript, checked. */
export async function checkedBook(page: Page, name: string, projects: string) {
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await importAudio(page);
  await importManuscript(page);
  await checkReading(page);
}
