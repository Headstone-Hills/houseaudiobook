// M7 acceptance, end to end: the multitrack timeline, edited from the keyboard
// (split, select + ripple delete, trim, gain, fades, tracks, markers, snapping),
// with the realtime preview playing straight from the EDL in the browser.
import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { importAudio, step } from "./steps";

type View = {
  duration: number;
  tracks: { id: number; name: string; mute: boolean }[];
  clips: { track_id: number; source_label: string; timeline_start: number; gain_db: number; fade_in: number }[];
  markers: { t: number | null }[];
};

test("edit on the timeline and preview from the EDL", async ({ page }) => {
  const projects = process.env.E2E_PROJECTS!;
  mkdirSync(projects, { recursive: true });
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  page.on("pageerror", (e) => consoleErrors.push(String(e)));

  await page.request.post("/api/project/close");
  await page.goto("/");
  await page.getByLabel("Name").fill("Timeline");
  await page.getByLabel("Location").fill(projects);
  await page.getByRole("button", { name: "Create project" }).click();
  await importAudio(page);
  await step(page, "Edit");

  const get = async () => (await (await page.request.get("/api/project/timeline")).json()) as View;
  const panel = page.locator("section.timeline");
  const app = panel.getByRole("application", { name: /Timeline/ });
  await expect(app).toBeVisible();
  const before = await get();
  expect(before.clips.map((c) => c.source_label)).toEqual(["ch01_take1", "ch01_take2", "ch02_take1"]);

  // Split at 10 s; select 10-12 s and ripple-delete it.
  await app.focus();
  await page.keyboard.press("Shift+ArrowRight");
  await expect(panel.getByLabel("Playhead")).toContainText("0:10.0");
  await page.keyboard.press("s");
  await expect.poll(async () => (await get()).clips.length).toBe(4);
  await page.keyboard.press("BracketLeft");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("BracketRight");
  await page.keyboard.press("Delete");
  await expect.poll(async () => (await get()).duration).toBeCloseTo(before.duration - 2, 2);

  // Clip gain and a fade on the clip under the playhead; a marker; a new track.
  await page.keyboard.press("+");
  await page.keyboard.press("+");
  await expect.poll(async () => (await get()).clips.find((c) => c.timeline_start === 10)?.gain_db).toBe(2);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("f");
  // The playhead is at 13 s (12 s after the delete, then one step): a 3 s fade.
  await expect.poll(async () => (await get()).clips.find((c) => c.timeline_start === 10)?.fade_in).toBeCloseTo(3, 3);
  await page.keyboard.press("m");
  await expect.poll(async () => (await get()).markers.length).toBe(1);
  await page.keyboard.press("t");
  await expect.poll(async () => (await get()).tracks.length).toBe(2);

  // Move the clip under the playhead to the new track (Alt+Down), then undo it.
  await page.keyboard.press("Alt+ArrowDown");
  await expect.poll(async () => (await get()).clips.filter((c) => c.track_id !== 1).length).toBe(1);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await get()).clips.filter((c) => c.track_id !== 1).length).toBe(0);

  // Realtime preview: Space plays from the EDL, fetching excerpts (no render).
  await page.keyboard.press("Home");
  const excerpt = page.waitForResponse((r) => r.url().includes("/excerpt?") && r.url().includes("context=0"));
  await page.keyboard.press("Space");
  expect((await excerpt).status()).toBe(200);
  await expect(panel.getByLabel("Playhead")).not.toContainText("0:00.0 /", { timeout: 10_000 });
  await page.keyboard.press("Space");
  await expect(panel.getByRole("button", { name: "Play", exact: true })).toBeVisible();

  // Render preview of the server mix (exact), via P.
  const rendered = page.waitForResponse((r) => r.url().includes("/api/project/edl/render?"));
  await page.keyboard.press("p");
  expect((await rendered).status()).toBe(200);

  // The mouse: drag the first narration clip to the right (a ripple move).
  const canvas = panel.locator("canvas");
  await canvas.scrollIntoViewIfNeeded(); // the mouse works in viewport coordinates
  const box = (await canvas.boundingBox())!;
  const y = box.y + 22 + 16 + 32; // ruler, markers, then the narration lane
  const moved = page.waitForResponse((r) => r.url().includes("/api/project/timeline/move"));
  await page.mouse.move(box.x + 60, y);
  await page.mouse.down();
  await page.mouse.move(box.x + 300, y, { steps: 5 });
  await page.mouse.up();
  expect((await moved).status()).toBe(200);
  await panel.screenshot({ path: test.info().outputPath("timeline.png") });

  expect(consoleErrors).toEqual([]);
});
