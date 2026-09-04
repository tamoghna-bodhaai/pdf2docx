import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
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
  await expect(page.getByRole("link", { name: "Download PDF" }).first()).toHaveAttribute("href", /format=pdf/);

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
});

test("an expired or missing session returns to sign in", async ({ page }) => {
  await page.goto("/?tool=split-pdf");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
});
