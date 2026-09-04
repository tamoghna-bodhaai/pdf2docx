import { expect, test, type Page } from "@playwright/test";

const config = {
  provider: "Mathpix", mathpix_key_configured: true,
  mathpix_formats: [{ ext: "docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", note: "", requestable: true, always: false }],
  max_pages: 0, max_upload_mb: 50, batch_max_files: 10, batch_workers: 3,
  mathpix_page_rate: 0.0015, remote_delete: true, improve_mathpix: false,
  history_limit: 100, local_tools_available: true,
  accepted_image_types: ["image/jpeg", "image/png", "image/webp"], image_max_files: 30,
  image_max_pixels: 40_000_000, image_max_upload_mb: 50,
};

const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAgAAAALCAIAAADN+VtyAAAAFUlEQVR4nGP8//8/AzbAhFV0JEgAADzeAxN4UWjoAAAAAElFTkSuQmCC", "base64");

function visualJob(id: string, filename: string, status: "ready" | "done") {
  return {
    id, filename, kind: "pdf_to_docx", source_filenames: [filename], output_filename: "",
    output_pages: 0, page_range: null, page_orientation: "auto", page_ranges: [], merge_ranges: false, artifacts: [], batch_id: "visual-batch", pages: 12,
    layout: "mathpix", requested_formats: ["docx"], multi_column: false,
    diagnostics: [], status, done: status === "done" ? 12 : 0, total: 12, error: null,
    size_bytes: 2_400_000, cost: status === "done" ? 0.018 : 0,
    cost_known: status === "done", created_at: "2026-09-04T12:00:00Z",
    started_at: status === "done" ? "2026-09-04T12:00:01Z" : null,
    finished_at: status === "done" ? "2026-09-04T12:00:05Z" : null,
    has_docx: status === "done", has_md: status === "done", has_source: true,
    has_pdf: false, has_rebuilt: false, mathpix_formats: [], has_detection: false,
    has_package: status === "done",
  };
}

function splitVisualJob() {
  return {
    ...visualJob("visual-split", "quarterly-archive-with-a-deliberately-long-source-name.pdf", "ready"),
    kind: "split_pdf", source_filenames: ["quarterly-archive-with-a-deliberately-long-source-name.pdf"],
    pages: 18, requested_formats: [], batch_id: "", cost: 0, page_ranges: [
      { start: 1, end: 4 }, { start: 4, end: 7 }, { start: 8, end: 10 },
      { start: 2, end: 3 }, { start: 11, end: 14 }, { start: 15, end: 18 },
    ],
  };
}

async function mockWorkspace(page: Page) {
  const state: { uploaded: boolean; status: "ready" | "done" } = { uploaded: false, status: "ready" };
  const jobs = () => [
    visualJob("visual-1", "research-notes.pdf", state.status),
    visualJob("visual-2", "appendix.pdf", state.status),
  ];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/config") return route.fulfill({ json: { signup_open: true } });
    if (path === "/api/auth/me") return route.fulfill({ json: { id: "visual-user", email: "reader@example.com" } });
    if (path === "/api/config") return route.fulfill({ json: config });
    if (path === "/api/history") return route.fulfill({ json: { jobs: state.uploaded ? jobs() : [], total_cost: 0, count: state.uploaded ? 2 : 0 } });
    if (path === "/api/tools/split-pdf") return route.fulfill({ json: splitVisualJob() });
    if (path === "/api/convert/batch") {
      state.uploaded = true;
      return route.fulfill({ json: { batch_id: "visual-batch", jobs: jobs(), rejected: [], package_ready: false, package_count: 0 } });
    }
    if (path === "/api/batches/visual-batch") return route.fulfill({ json: { batch_id: "visual-batch", jobs: jobs(), rejected: [], package_ready: state.status === "done", package_count: state.status === "done" ? 2 : 0 } });
    if (/^\/api\/jobs\/visual-[12]$/.test(path)) return route.fulfill({ json: jobs().find((job) => path.endsWith(job.id)) });
    if (path === "/api/jobs/visual-split") return route.fulfill({ json: splitVisualJob() });
    if (/^\/api\/jobs\/visual-[12]\/markdown$/.test(path)) return route.fulfill({ json: { markdown: "# Converted page\n\nEditable text, mathematics $a^2+b^2=c^2$, and a labelled result." } });
    if (/^\/api\/jobs\/visual-[12]\/page\/\d+\.png$/.test(path)) return route.fulfill({ body: pixel, headers: { "content-type": "image/png" } });
    if (/^\/api\/jobs\/visual-split\/page\/\d+\.png$/.test(path)) return route.fulfill({ body: pixel, headers: { "content-type": "image/png" } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  return state;
}

async function screenshot(page: Page, name: string) {
  // Next's development-only corner badge animates independently of the app and
  // does not exist in the static export. Keep it out of product baselines.
  await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
  await expect(page).toHaveScreenshot(name, { animations: "disabled", fullPage: true });
}

test.describe("visual contracts", () => {
  test.describe.configure({ mode: "serial" });
  for (const width of [375, 768, 1280]) {
    for (const theme of ["light", "dark"] as const) {
      test(`legacy states at ${width}px in ${theme} mode`, async ({ page }) => {
        const state = await mockWorkspace(page);
        await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
        await page.addInitScript((selected) => localStorage.setItem("pdf2docx-theme", selected), theme);

        await page.goto("/login");
        await expect(page.getByRole("tab", { name: "Sign up" })).toBeEnabled();
        await screenshot(page, `login-${width}-${theme}.png`);

        await page.goto("/?tool=pdf-to-docx");
        await expect(page.getByRole("heading", { name: "PDF to DOCX" })).toBeVisible();
        await screenshot(page, `workspace-${width}-${theme}.png`);

        await page.goto("/?tool=images-to-pdf&panel=setup");
        await expect(page.getByRole("heading", { name: "Images to PDF" })).toBeVisible();
        await screenshot(page, `images-workspace-${width}-${theme}.png`);
        await page.locator('input[type="file"]').setInputFiles(Array.from({ length: 8 }, (_, index) => ({
          name: `scanned-research-page-${String(index + 1).padStart(2, "0")}-with-a-long-filename.png`,
          mimeType: "image/png", buffer: pixel,
        })));
        await expect(page.getByRole("list", { name: "PDF pages" })).toBeVisible();
        await expect(page.locator('img:not([alt])')).toHaveCount(0);
        await screenshot(page, `images-board-${width}-${theme}.png`);

        await page.goto("/?tool=split-pdf&panel=setup");
        await expect(page.getByRole("heading", { name: "Split PDF" })).toBeVisible();
        await screenshot(page, `split-workspace-${width}-${theme}.png`);
        await page.locator('input[type="file"]').setInputFiles({ name: "quarterly-archive-with-a-deliberately-long-source-name.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF visual") });
        await expect(page.getByRole("heading", { name: "Page ranges" })).toBeVisible();
        await expect(page.getByRole("textbox", { name: "Start page", exact: true })).toBeEnabled();
        await expect(page.locator('img:not([alt])')).toHaveCount(0);
        await screenshot(page, `split-ranges-${width}-${theme}.png`);

        await page.goto("/?tool=pdf-to-docx&panel=setup");

        await page.locator('input[type="file"]').setInputFiles([
          { name: "research-notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF visual one") },
          { name: "appendix.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF visual two") },
        ]);
        await expect(page.getByText("2 files · 24 pages")).toBeVisible();
        await screenshot(page, `active-batch-${width}-${theme}.png`);

        state.status = "done";
        await page.goto("/?tool=history");
        await expect(page).toHaveURL(/tool=pdf-to-docx.*panel=history/);
        const dockTrigger = page.getByRole("button", { name: "Workspace" });
        if (await dockTrigger.isVisible()) await dockTrigger.click();
        await expect(page.getByText("research-notes.pdf").first()).toBeVisible();
        await screenshot(page, `history-${width}-${theme}.png`);

        await page.getByLabel("More actions for research-notes.pdf").click();
        await page.getByRole("button", { name: "Delete research-notes.pdf" }).click();
        await expect(page.getByRole("dialog", { name: "Delete research-notes.pdf?" })).toBeVisible();
        await screenshot(page, `dialog-${width}-${theme}.png`);
        await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();

        await page.goto("/?tool=pdf-to-docx&job=visual-1");
        await expect(page.getByRole("heading", { name: "research-notes.pdf" })).toBeVisible();
        if (width <= 900) await page.getByRole("tab", { name: "Converted" }).click();
        await expect(page.getByRole("heading", { name: "Converted page" })).toBeVisible();
        await screenshot(page, `viewer-${width}-${theme}.png`);
      });
    }
  }
});
