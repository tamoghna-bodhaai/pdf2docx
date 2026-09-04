import { expect, test, type Page } from "@playwright/test";

const config = {
  provider: "Mathpix", mathpix_key_configured: true, mathpix_formats: [], max_pages: 0,
  max_upload_mb: 50, batch_max_files: 10, batch_workers: 3, mathpix_page_rate: 0.0015,
  remote_delete: true, improve_mathpix: false, history_limit: 100, local_tools_available: true,
  accepted_image_types: ["image/jpeg", "image/png", "image/webp"], image_max_files: 30,
  image_max_pixels: 40_000_000, image_max_upload_mb: 50,
};

async function mockWorkspace(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/config") return route.fulfill({ json: { signup_open: true } });
    if (path === "/api/auth/me") return route.fulfill({ json: { id: "visual-user", email: "reader@example.com" } });
    if (path === "/api/config") return route.fulfill({ json: config });
    if (path === "/api/history") return route.fulfill({ json: { jobs: [], total_cost: 0, count: 0 } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
}

test.describe("visual contracts", () => {
  test.describe.configure({ mode: "serial" });
  for (const width of [375, 768, 1280]) {
    for (const theme of ["light", "dark"] as const) {
      test(`login and empty workspace at ${width}px in ${theme} mode`, async ({ page }) => {
        await mockWorkspace(page);
        await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
        await page.addInitScript((selected) => localStorage.setItem("pdf2docx-theme", selected), theme);

        await page.goto("/login");
        await expect(page.getByRole("tab", { name: "Sign up" })).toBeEnabled();
        await expect(page).toHaveScreenshot(`login-${width}-${theme}.png`, { animations: "disabled", fullPage: true });

        await page.goto("/?tool=images-to-pdf");
        await expect(page.getByRole("heading", { name: "Turn images into one PDF." })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Recent files" })).toBeVisible();
        await expect(page).toHaveScreenshot(`workspace-${width}-${theme}.png`, { animations: "disabled", fullPage: true });
      });
    }
  }
});
