import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

const fixtures = mkdtempSync(path.join(tmpdir(), "pdf2docx-e2e-"));
const firstImage = path.join(fixtures, "first.png");
const secondImage = path.join(fixtures, "second.webp");
const sourcePdf = path.join(fixtures, "source.pdf");

test.beforeAll(() => {
  execFileSync("python3", ["-c", [
    "import fitz", "from PIL import Image", "import sys",
    "Image.new('RGB',(40,80),'red').save(sys.argv[1])",
    "Image.new('RGB',(90,40),'blue').save(sys.argv[2])",
    "d=fitz.open()",
    "[d.new_page().insert_text((72,72),f'Page {n}') for n in range(1,4)]",
    "d.save(sys.argv[3])", "d.close()",
  ].join(";"), firstImage, secondImage, sourcePdf]);
});

test("creates, restores, regenerates, and downloads local PDFs", async ({ page }, testInfo) => {
  async function selectTool(name: "Images to PDF" | "Split PDF") {
    const menu = page.getByRole("button", { name: "Open navigation" });
    if (await menu.isVisible()) await menu.click();
    await page.getByRole("button", { name }).click();
  }
  await page.goto("/login");
  const signup = page.getByRole("tab", { name: "Sign up" });
  await expect(signup).toBeEnabled();
  await signup.click();
  await page.getByLabel("Email").fill(`playwright-${testInfo.project.name}-${Date.now()}@example.com`);
  await page.getByLabel("Password", { exact: true }).fill("a good password");
  await page.getByLabel("Invite code").fill("playwright-invite");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Convert a PDF into editable work." })).toBeVisible();
  const mobileMenu = page.getByRole("button", { name: "Open navigation" });
  if (await mobileMenu.isVisible()) {
    await mobileMenu.click();
    await expect(page.locator("#sidebar")).not.toHaveAttribute("aria-hidden", "true");
    await page.keyboard.press("Escape");
    await expect(mobileMenu).toBeFocused();
    await expect(page.locator("#sidebar")).toHaveAttribute("aria-hidden", "true");
  }

  await selectTool("Images to PDF");
  await expect(page).toHaveURL(/tool=images-to-pdf/);
  await expect(page.getByRole("heading", { name: "Turn images into one PDF." })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("not an image") });
  await expect(page.getByText("Use JPEG, PNG, or WebP images.")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles([firstImage, secondImage]);
  await page.getByRole("button", { name: "Move second.webp earlier" }).click();
  await page.getByRole("button", { name: "Create PDF" }).click();
  await expect(page).toHaveURL(/tool=images-to-pdf&job=/);
  await expect(page.getByText("Local · no charge").first()).toBeVisible();
  await expect(page.getByText("done", { exact: true }).first()).toBeVisible();
  await page.reload();
  await expect(page.getByText("done", { exact: true }).first()).toBeVisible();
  await page.getByText("Download", { exact: true }).first().click();
  const imageDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download PDF" }).first().click();
  await expect((await imageDownload).suggestedFilename()).toMatch(/\.pdf$/);

  await selectTool("Split PDF");
  await expect(page).toHaveURL(/tool=split-pdf/);
  await expect(page.getByRole("heading", { name: "Extract pages from a PDF." })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(sourcePdf);
  await expect(page).toHaveURL(/tool=split-pdf&job=/);
  await page.getByLabel("Start page").fill("1");
  await page.getByLabel("End page").fill("2");
  await expect(page.getByText("Pages 1–2 · 2 pages")).toBeVisible();
  await page.getByRole("button", { name: "Extract pages" }).click();
  await expect(page.getByRole("button", { name: "Regenerate PDF" })).toBeVisible();
  await page.getByLabel("Start page").fill("3");
  await page.getByLabel("End page").fill("3");
  await page.getByRole("button", { name: "Regenerate PDF" }).click();
  await expect(page.getByText("1 output page")).toBeVisible();
  await page.getByText("Download", { exact: true }).first().click();
  const splitDownload = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download PDF" }).first().click();
  expect((await splitDownload).suggestedFilename()).toBe("source-pages-3-3.pdf");

  await page.getByRole("button", { name: "Delete", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Delete source-pages-3-3.pdf?" });
  await dialog.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page).toHaveURL(/tool=split-pdf(?!.*job=)/);
});

test("keeps the existing PDF batch and comparison workflow", async ({ page }, testInfo) => {
  const email = `playwright-docx-${testInfo.project.name}-${Date.now()}@example.com`;
  const signup = await page.request.post("/api/auth/signup", {
    data: { email, password: "a good password", invite_code: "playwright-invite" },
  });
  expect(signup.ok()).toBeTruthy();

  let uploaded = false;
  let status = "ready";
  const makeJob = (id: string, filename: string) => ({
    id, filename, kind: "pdf_to_docx", source_filenames: [filename], output_filename: "",
    output_pages: 0, page_range: null, batch_id: "mock-batch", pages: 3, layout: "mathpix",
    requested_formats: ["docx"], multi_column: false, diagnostics: [], status,
    done: status === "done" ? 3 : 0, total: 3, error: null, size_bytes: 1000,
    cost: status === "done" ? 0.0045 : 0, cost_known: status === "done",
    created_at: "2026-09-04T12:00:00Z", started_at: null, finished_at: null,
    has_docx: status === "done", has_md: status === "done", has_source: true,
    has_pdf: false, has_rebuilt: false, mathpix_formats: [], has_detection: false,
    has_package: status === "done",
  });
  const jobs = () => [makeJob("docx-1", "report-a.pdf"), makeJob("docx-2", "report-b.pdf")];
  const browserConfig = {
    provider: "Mathpix", mathpix_key_configured: true,
    mathpix_formats: [{ ext: "docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", note: "", requestable: true, always: false }],
    max_pages: 0, max_upload_mb: 50, batch_max_files: 10, batch_workers: 3,
    mathpix_page_rate: 0.0015, remote_delete: true, improve_mathpix: false,
    history_limit: 100, local_tools_available: true,
    accepted_image_types: ["image/jpeg", "image/png", "image/webp"],
    image_max_files: 30, image_max_pixels: 40_000_000, image_max_upload_mb: 50,
  };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { id: "docx-user", email } });
    if (url.pathname === "/api/config") return route.fulfill({ json: browserConfig });
    if (url.pathname === "/api/history") return route.fulfill({ json: { jobs: uploaded ? jobs() : [], total_cost: 0, count: uploaded ? 2 : 0 } });
    if (url.pathname === "/api/convert/batch" && method === "POST") {
      uploaded = true;
      return route.fulfill({ json: { batch_id: "mock-batch", jobs: jobs(), rejected: [], package_ready: false, package_count: 0 } });
    }
    if (url.pathname === "/api/batches/mock-batch/start" && method === "POST") {
      status = "done";
      return route.fulfill({ json: { batch_id: "mock-batch", jobs: jobs(), rejected: [], package_ready: true, package_count: 2 } });
    }
    if (url.pathname === "/api/batches/mock-batch") return route.fulfill({ json: { batch_id: "mock-batch", jobs: jobs(), rejected: [], package_ready: status === "done", package_count: status === "done" ? 2 : 0 } });
    if (url.pathname === "/api/batches/mock-batch/package.zip") return route.fulfill({ body: "PK mock zip", headers: { "content-type": "application/zip", "content-disposition": "attachment; filename=batch-mock-batch.zip" } });
    if (/^\/api\/jobs\/docx-[12]$/.test(url.pathname)) return route.fulfill({ json: jobs().find((job) => url.pathname.endsWith(job.id)) });
    if (/^\/api\/jobs\/docx-[12]\/markdown$/.test(url.pathname)) return route.fulfill({ json: { markdown: "# Converted page" } });
    if (/^\/api\/jobs\/docx-[12]\/page\/\d+\.png$/.test(url.pathname)) return route.fulfill({ body: readFileSync(firstImage), headers: { "content-type": "image/png" } });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Convert a PDF into editable work." })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles([sourcePdf, sourcePdf]);
  await expect(page.getByText("2 files · 6 pages")).toBeVisible();
  await page.getByRole("button", { name: "Convert all" }).click();
  await expect(page.getByText("done", { exact: true }).first()).toBeVisible();
  const archive = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download batch ZIP" }).click();
  expect((await archive).suggestedFilename()).toBe("batch-mock-batch.zip");
  await page.getByRole("button", { name: "Open", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "report-a.pdf" })).toBeVisible();
  if (testInfo.project.name === "mobile") await page.getByRole("tab", { name: "Converted" }).click();
  await expect(page.getByRole("heading", { name: "Converted page" })).toBeVisible();
});

test("an expired or missing session returns to sign in", async ({ page }) => {
  await page.goto("/?tool=split-pdf");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
