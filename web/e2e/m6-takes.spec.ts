// M6 acceptance, end to end: lines read twice are grouped, the better take is
// suggested and applied (a pickup moves into place), and choosing another take
// from the keyboard updates the EDL, one click (or B) away from going back.
import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { checkedBook, step } from "./steps";

type Clip = { source_id: number; source_in: number };

test("compare takes and choose from the keyboard", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await checkedBook(page, "Takes", projects);
  await step(page, "Choose takes");

  const panel = page.locator("section.takes-panel");
  await expect(panel.getByText("2 lines read more than once · 1 to review · 0 confirmed")).toBeVisible({
    timeout: 30_000,
  });
  const box = panel.getByRole("group", { name: "Line 1 of 2" });
  await expect(box).toContainText("out of reach.");
  await expect(box.locator("li.take").nth(1)).toContainText("Take 2 · Preferred (suggested) · ch01_take2");

  const pickupMoved = async () => {
    const edl = (await (await page.request.get("/api/project/edl")).json()) as { clips: Clip[] };
    return edl.clips.findIndex((c) => c.source_id === 2 && c.source_in > 0.4) === 3;
  };
  expect(await pickupMoved()).toBe(true); // right after ch01_take1's first three clips

  // Play take 1, switch to take 2 (A/B), then choose take 1 with the "1" key.
  await box.focus();
  const excerpt = page.waitForResponse((r) => r.url().includes("/excerpt?") && r.url().includes("context=0"));
  await page.keyboard.press("Space");
  expect((await excerpt).status()).toBe(200);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("1");
  await expect(box.locator("li.take").nth(0)).toContainText("Take 1 · Preferred");
  await expect(box.getByText("Confirmed.")).toBeVisible();
  expect(await pickupMoved()).toBe(false); // the pickup went home (and is cut)

  // B: back to the previous choice; then undo it with Ctrl+Z.
  await page.keyboard.press("b");
  await expect(box.locator("li.take").nth(1)).toContainText("Take 2 · Preferred");
  expect(await pickupMoved()).toBe(true);
  await page.keyboard.press("Control+z");
  await expect(box.locator("li.take").nth(0)).toContainText("Take 1 · Preferred");

  // Next line: two clean reads; confirm the suggestion. Survives a reload.
  await page.keyboard.press("n");
  const second = panel.getByRole("group", { name: "Line 2 of 2" });
  await expect(second).toContainText("Suggested (the later take): listen and confirm.");
  await page.keyboard.press("c");
  await expect(second.getByText("Confirmed.")).toBeVisible();
  await page.reload();
  await expect(page.locator("section.takes-panel").getByText(/0 to review · 2 confirmed/)).toBeVisible({
    timeout: 20_000,
  });

  expect(consoleErrors).toEqual([]);
});
