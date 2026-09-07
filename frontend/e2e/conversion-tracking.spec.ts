import { expect, test, type Page } from "@playwright/test";
import { config, job } from "../test/fixtures";
import type { JobDto } from "../lib/api/types";

async function settings(page: Page, open: boolean) {
  const button = open
    ? page.getByRole("button", {name: "Settings & export", exact: true})
    : page.getByRole("dialog", {name: "Tool settings"}).getByRole("button", {name: "Close", exact: true});
  if (await button.isVisible()) await button.click();
}

test("tracks two batches independently through reset, refresh, tool switches and cancellation", async ({page}) => {
  let jobs: JobDto[] = [];
  let uploads = 0;
  const formats = ["docx", "tex"].map(ext => ({ext, media_type: "text/plain", requestable: true, always: false, note: ""}));
  const batch = (id: string) => ({batch_id: id, jobs: jobs.filter(job => job.batch_id === id), rejected: [], package_ready: false, package_count: 0});
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({json: {id: "user", email: "test@example.com"}});
    if (path === "/api/config") return route.fulfill({json: {...config, mathpix_key_configured: true, mathpix_formats: formats}});
    if (path === "/api/history") return route.fulfill({json: {jobs, count: jobs.length, total_cost: 0}});
    if (path === "/api/convert/batch") {
      const id = `batch-${++uploads}`;
      const members = [1].map(n => job({id: `${id}-${n}`, filename: `${id}-${n}.pdf`, kind: "pdf_to_docx", batch_id: id}));
      jobs = [...members, ...jobs];
      return route.fulfill({json: batch(id)});
    }
    const batchMatch = path.match(/^\/api\/batches\/([^/]+)(?:\/(start|pause|resume|cancel))?$/);
    if (batchMatch) {
      const [, id, action] = batchMatch;
      const body = route.request().postData() || "";
      const field = (name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]+)`))?.[1];
      if (action === "cancel") jobs = jobs.filter(job => job.batch_id !== id);
      else if (action) jobs = jobs.map(job => job.batch_id !== id ? job : {...job, status: action === "pause" ? "paused" : "queued", ...(action === "start" ? {requested_formats: (field("formats") || "docx").split(","), multi_column: field("multi_column") === "true"} : {})});
      return route.fulfill({json: batch(id)});
    }
    const jobAction = path.match(/^\/api\/jobs\/([^/]+)\/(start|pause|resume|cancel)$/);
    if (jobAction) {
      const [, id, action] = jobAction;
      const body = route.request().postData() || "";
      const field = (name: string) => body.match(new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]+)`))?.[1];
      if (action === "cancel") jobs = jobs.filter(job => job.id !== id);
      else jobs = jobs.map(job => job.id !== id ? job : {...job, status: action === "pause" ? "paused" : "queued", ...(action === "start" ? {requested_formats: (field("formats") || "docx").split(","), multi_column: field("multi_column") === "true"} : {})});
      return route.fulfill({json: jobs.find(job => job.id === id) || {id, status: "cancelled"}});
    }
    const single = jobs.find(job => path === `/api/jobs/${job.id}`);
    if (single) return route.fulfill({json: single});
    return route.fulfill({status: 404, json: {detail: "Unknown job id"}});
  });
  await page.goto("/");
  const upload = async () => page.locator('input[type="file"]').setInputFiles([{name: "source.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF test")}]);
  await upload();
  await expect(page.getByRole("heading", {name: "batch-1-1.pdf"})).toBeVisible();
  await settings(page, true);
  await page.getByText("Output formats · DOCX").click();
  await page.getByRole("checkbox", {name: "TEX", exact: true}).check();
  await page.getByRole("checkbox", {name: /Match source page columns/}).check();
  await settings(page, false);
  await page.getByRole("button", {name: "Convert", exact: true}).click();
  await page.getByRole("button", {name: "New conversion"}).click();
  await expect(page.getByRole("region", {name: "Previous conversions"})).toContainText("batch-1-1.pdf");
  await upload();
  await expect(page.getByRole("heading", {name: "batch-2-1.pdf"})).toBeVisible();
  await settings(page, true);
  await settings(page, false);
  await page.getByRole("button", {name: "Convert", exact: true}).click();
  const previous = page.getByRole("region", {name: "Previous conversions"});
  await previous.getByRole("button", {name: "Pause"}).click();
  await expect(previous.getByRole("button", {name: "Resume"})).toBeVisible();
  await page.reload();
  await expect(page.getByRole("button", {name: "View file"})).toHaveCount(2);
  await page.getByRole("button", {name: "Images to PDF", exact: true}).click();
  jobs = jobs.map(job => job.batch_id === "batch-2" ? {...job, status: "done", has_docx: true} : job);
  await page.getByRole("button", {name: "PDF to DOCX", exact: true}).click();
  await page.getByRole("region", {name: "File conversion"}).filter({hasText: "batch-1-1.pdf"}).getByRole("button", {name: "View file"}).click();
  await expect(page.getByRole("region", {name: "File conversion"}).filter({hasText: "batch-1-1.pdf"})).toHaveCount(1);
  await settings(page, true);
  await expect(page.getByText("Output formats · DOCX, TEX")).toBeVisible();
  await expect(page.getByRole("checkbox", {name: /Match source page columns/})).toBeChecked();
  await settings(page, false);
  await page.getByRole("region", {name: "File conversion"}).filter({hasText: "batch-1-1.pdf"}).getByRole("button", {name: "Cancel"}).click();
  await expect(page).not.toHaveURL(/job=/);
  await expect(page.getByText(/Couldn’t restore this job/)).toHaveCount(0);
  await expect(page.getByText("batch-1-1.pdf")).toHaveCount(0);
});
