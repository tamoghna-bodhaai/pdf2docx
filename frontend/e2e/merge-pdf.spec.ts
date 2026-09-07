import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

const pdf = execFileSync("python3", ["-c", "import fitz,sys; d=fitz.open(); d.new_page().insert_text((72,72),'Merge source'); sys.stdout.buffer.write(d.tobytes())"]);

test("merge, pointer order, reload and regenerate in both themes", async ({ page, context }, testInfo) => {
  const signup = await page.request.post("/api/auth/signup", { data: { email: `merge-${testInfo.project.name}-${Date.now()}@example.com`, password: "a good password", invite_code: "playwright-invite" } });
  expect(signup.ok()).toBeTruthy();
  await page.goto("/?tool=merge-pdf&panel=setup");
  await page.locator('input[type="file"]').setInputFiles([{ name: "first.pdf", mimeType: "application/pdf", buffer: pdf }, { name: "second.pdf", mimeType: "application/pdf", buffer: pdf }]);
  await expect(page).toHaveURL(/job=/);
  const first = page.getByRole("button", { name: /Reorder first/ });
  const second = page.getByRole("button", { name: /Reorder second/ });
  const start = (await first.boundingBox())!;
  const end = (await second.boundingBox())!;
  // Cancel a completed visual move before committing the next gesture.
  if (testInfo.project.name === "desktop") {
    await page.mouse.move(start.x + 15, start.y + 15); await page.mouse.down();
    await page.mouse.move(end.x + 15, end.y + 15, { steps: 5 });
    await expect(first).toHaveAccessibleName(/position 2/);
    await page.keyboard.press("Escape"); await page.mouse.up();
    await expect(first).toHaveAccessibleName(/position 1/);
  }
  if (testInfo.project.name === "mobile") {
    const session = await context.newCDPSession(page);
    await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: start.x + 15, y: start.y + 15 }] });
    await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: end.x + 15, y: end.y + 15 }] });
    await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  } else {
    await page.mouse.move(start.x + 15, start.y + 15); await page.mouse.down();
    await page.mouse.move(end.x + 15, end.y + 15, { steps: 5 }); await page.mouse.up();
  }
  await expect(first).toHaveAccessibleName(/position 2/);
  await expect(first).toBeEnabled();
  await page.reload();
  await expect(first).toHaveAccessibleName(/position 2/);
  async function openSettings() {
    const button = page.getByRole("button", { name: "Settings & export", exact: true });
    if (await button.isVisible()) await button.click();
  }
  await openSettings();
  await page.getByLabel("Tool settings").getByRole("button", { name: "Merge PDF", exact: true }).click();
  const download = page.getByRole("link", { name: "Download merged PDF" });
  await expect(download).toBeVisible();
  const pending = page.waitForEvent("download"); await download.click();
  expect((await pending).suggestedFilename()).toBe("first-merged.pdf");
  const close = page.getByRole("button", { name: "Close", exact: true });
  if (await close.isVisible()) await close.click();
  await first.press("ArrowUp");
  await expect(first).toBeEnabled();
  await openSettings();
  await expect(page.getByRole("link", { name: "Download previous result" })).toBeVisible();
  await page.getByLabel("Tool settings").getByRole("button", { name: "Merge PDF", exact: true }).click();
  await expect(download).toBeVisible();
  if (await close.isVisible()) await close.click();
  await page.locator('input[type="file"]').setInputFiles({ name: "third.pdf", mimeType: "application/pdf", buffer: pdf });
  await expect(page.getByRole("button", { name: /Reorder third/ })).toBeVisible();
  await page.getByLabel("Actions for second.pdf, position 2").click();
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(second).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: /Reorder third.*position 2/ })).toBeVisible();
  await openSettings();
  await page.getByLabel("Tool settings").getByRole("button", { name: "Merge PDF", exact: true }).click();
  await expect(download).toBeVisible();
  if (await close.isVisible()) await close.click();
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"]) {
      await page.evaluate(value => document.documentElement.setAttribute("data-theme", value), theme);
      await expect(page.getByRole("list", { name: "PDF source order" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
      await page.evaluate(() => { window.scrollTo(0, 0); document.querySelector(".workbench-canvas")?.scrollTo(0, 0); });
      await page.screenshot({ animations: "disabled", path: testInfo.outputPath(`merge-${width}-${theme}.png`), fullPage: true });
    }
  }
});
