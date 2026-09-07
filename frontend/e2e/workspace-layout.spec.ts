import { images } from "./image-fixtures";
import { expect, test, type Page } from "@playwright/test";
import { config, job } from "../test/fixtures";



async function mock(page: Page) {
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/config") return route.fulfill({json: config});
    if (path === "/api/auth/me") return route.fulfill({json: {id: "reader", email: "reader@example.com"}});
    if (path === "/api/history") return route.fulfill({json: {jobs: [], count: 0, total_cost: 0}});
    if (path === "/api/jobs/job-1") return route.fulfill({json: job({pages: 8})});
    if (/\/page\/\d+\.png$/.test(path)) return route.fulfill({body: Buffer.from(images[1], "base64"), contentType: "image/jpeg"});
    return route.fulfill({status: 404, json: {detail: "Not found"}});
  });
}

async function navigate(page: Page, label: string) {
  const trigger = page.getByRole("button", {name: "Navigation", exact: true});
  if (await trigger.isVisible()) await trigger.click();
  await page.getByRole("navigation", {name: "Workspace navigation"}).getByRole("button", {name: label, exact: true}).click();
}

for (const width of [375, 768, 1024, 1280, 1920]) {
  test(`image containment and draft navigation at ${width}px`, async ({page}) => {
    await mock(page);
    await page.setViewportSize({width, height: 600});
    await page.goto("/?tool=images-to-pdf");
    await page.locator('input[type="file"]').setInputFiles(images.map((buffer, index) => ({name: `research-scan-${index + 1}-with-a-similar-long-filename.jpg`, mimeType: "image/jpeg", buffer: Buffer.from(buffer, "base64")})));
    const previews = page.getByRole("img", {name: /^Preview of/});
    await expect(previews).toHaveCount(4);
    await expect.poll(() => previews.evaluateAll(nodes => nodes.every(node => (node as HTMLImageElement).naturalWidth > 0))).toBe(true);
    for (const preview of await previews.all()) {
      const geometry = await preview.evaluate(node => {
        const image = node.getBoundingClientRect(); const paper = node.parentElement!.getBoundingClientRect(); const area = node.parentElement!.parentElement!.getBoundingClientRect();
        const name = node.closest("li")!.querySelector("strong")!.getBoundingClientRect();
        return {contained: image.left >= paper.left && image.right <= paper.right + 1 && image.top >= paper.top && image.bottom <= paper.bottom + 1, bounded: paper.left >= area.left - 1 && paper.right <= area.right + 1 && paper.top >= area.top - 1 && paper.bottom <= area.bottom + 1, separate: name.top >= area.bottom, ratio: paper.width / paper.height};
      });
      expect(geometry.contained).toBe(true); expect(geometry.bounded).toBe(true); expect(geometry.separate).toBe(true);
    }
    const ratios = await previews.evaluateAll(nodes => nodes.map(node => {const r=node.parentElement!.getBoundingClientRect(); return r.width/r.height;}));
    expect(ratios[0]).toBeCloseTo(595.28 / 841.89, 2);
    expect(ratios[1]).toBeCloseTo(841.89 / 595.28, 2);
    expect(ratios[2]).toBeCloseTo(595.28 / 841.89, 2);
    expect(ratios[3]).toBeCloseTo(595.28 / 841.89, 2);
    await navigate(page, "History");
    await expect(page.getByText("No completed PDF to DOCX jobs")).toBeVisible();
    await navigate(page, "Conversions");
    await navigate(page, "Uploads");
    await expect(previews).toHaveCount(4);
    const trigger = page.getByRole("button", {name: "Settings & export"});
    if (width < 1024) {
      await trigger.click();
      const dialog = page.getByRole("dialog", {name: "Tool settings"});
      await expect(dialog).toBeVisible();
      await page.getByRole("button", {name: /Create PDF/}).focus();
      await page.keyboard.press("Tab");
      await expect(dialog.getByRole("button", {name: "Close", exact: true})).toBeFocused();
      await page.keyboard.press("Escape");
      await expect(trigger).toBeFocused();
      await trigger.click();
    }
    const action = page.getByRole("button", {name: /Create PDF/});
    await expect(action).toBeInViewport();
    // Browser zoom reduces the available CSS viewport; emulate 125% reflow.
    await page.setViewportSize({width: Math.round(width / 1.25), height: 480});
    if (width >= 1024 && width / 1.25 < 1024) await trigger.click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(action).toBeInViewport();
  });
}

test("invalid groups never request invalid pages and selecting a group opens its editor", async ({page}) => {
  await mock(page);
  await page.setViewportSize({width: 375, height: 600});
  const requested: number[] = [];
  page.on("request", request => {const match = request.url().match(/\/page\/(\d+)\.png/); if (match) requested.push(Number(match[1]));});
  await page.goto("/?tool=split-pdf&job=job-1");
  await page.getByRole("button", {name: /Range 1 · Pages/}).click();
  await expect(page.getByLabel("Start page", {exact: true})).toBeFocused();
  await page.getByLabel("End page", {exact: true}).fill("999999999");
  await page.keyboard.press("Escape");
  await expect(page.getByText("Enter a valid range to preview this output.")).toBeVisible();
  expect(requested.every(page => page >= 1 && page <= 8)).toBe(true);
});

for (const width of [375, 768, 1024, 1280, 1920]) {
  test(`split previews stay compact when ranges change at ${width}px`, async ({page}) => {
    await mock(page);
    await page.setViewportSize({width, height: 600});
    await page.goto("/?tool=split-pdf&job=job-1");
    const group = page.getByRole("button", {name: /Range 1 · Pages/}).locator("..");
    await expect(group).toBeVisible();
    const before = (await group.boundingBox())!;
    expect(before.width).toBeLessThanOrEqual(540);
    const preview = group.locator("figure > div").first();
    expect((await preview.boundingBox())!.height).toBeLessThanOrEqual(334);

    const toolsBox = (await page.getByRole("navigation", {name: "Document tools"}).boundingBox())!;
    if (width >= 640 && width < 1280) {
      const navigationBox = (await page.getByRole("button", {name: "Navigation", exact: true}).boundingBox())!;
      expect(toolsBox.x - navigationBox.x - navigationBox.width).toBeCloseTo(16, 0);
    } else if (width >= 1280) {
      const heading = (await page.getByRole("heading", {name: "Split PDF", exact: true}).boundingBox())!;
      expect(toolsBox.x + 16).toBeCloseTo(heading.x, 0);
    } else {
      expect(toolsBox.x - (await page.locator(".topbar").boundingBox())!.x).toBeLessThanOrEqual(16);
    }

    const settings = page.getByRole("button", {name: "Settings & export"});
    if (width < 1024) await settings.click();
    await page.getByRole("button", {name: "Add range", exact: true}).click();
    await page.getByLabel("Range 2 start page", {exact: true}).fill("4");
    await page.getByLabel("Range 2 end page", {exact: true}).fill("8");
    if (width < 1024) await page.keyboard.press("Escape");
    expect((await group.boundingBox())!.width).toBeCloseTo(before.width, 0);

    if (width < 1024) await settings.click();
    await page.getByLabel("Actions for range 2").click();
    await page.getByRole("button", {name: "Remove", exact: true}).click();
    if (width < 1024) await page.keyboard.press("Escape");
    expect((await group.boundingBox())!.width).toBeCloseTo(before.width, 0);
    expect((await preview.boundingBox())!.height).toBeLessThanOrEqual(334);

    if (width < 1024) await settings.click();
    await page.getByLabel("End page", {exact: true}).fill("1");
    if (width < 1024) await page.keyboard.press("Escape");
    await expect(group.locator("figure")).toHaveCount(1);
    expect((await preview.boundingBox())!.height).toBeLessThanOrEqual(334);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (width < 1024) await settings.click();
    await expect(page.locator('button[type="submit"]', {hasText: "Split PDF"})).toBeInViewport();
  });
}
