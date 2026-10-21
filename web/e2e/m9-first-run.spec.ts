// M9 acceptance (web mode): the first-run model download is explicit, shows its
// size and source, reports progress, and makes the model usable. The e2e engine
// serves fake weights from a local stand-in for Hugging Face (tests/fakehf.py).
import { expect, test } from "@playwright/test";

test("download a speech model from the Speech models list", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
  await page.request.post("/api/project/close");
  await page.goto("/");

  const banner = page.getByRole("complementary", { name: "Speech models" });
  // small.en (the default) is installed in the e2e engine: no first-run prompt.
  await expect(banner.getByText(/needs a model/)).toHaveCount(0);
  await banner.getByText("Speech models").click();
  const tiny = banner.locator("li").filter({ hasText: "tiny.en" });
  await expect(tiny).toContainText("MIT");
  await tiny.getByRole("button", { name: "Download tiny.en" }).click();
  await expect(tiny.getByLabel("Downloading tiny.en")).toBeVisible();
  await expect(tiny).toContainText("downloaded", { timeout: 30_000 });
  const models = (await (await page.request.get("/api/models")).json()) as { models: { name: string; installed: boolean }[] };
  expect(models.models.find((m) => m.name === "tiny.en")?.installed).toBe(true);

  expect(consoleErrors).toEqual([]);
});
