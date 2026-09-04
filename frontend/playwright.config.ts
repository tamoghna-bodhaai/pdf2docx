import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

const systemChrome = "/opt/google/chrome/chrome";
const localChrome = !process.env.CI && existsSync(systemChrome);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    launchOptions: localChrome ? { executablePath: systemChrome } : undefined,
  },
  webServer: [
    {
      command: "if [ -x .venv/bin/python ]; then .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000; else python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000; fi",
      cwd: "..",
      url: "http://127.0.0.1:8000/healthz",
      reuseExistingServer: true,
      env: { PDF2DOCX_DATA_DIR: "/tmp/pdf2docx-playwright", PDF2DOCX_INVITE_CODES: "playwright-invite", PDF2DOCX_COOKIE_SECURE: "off" },
    },
    { command: "npm run dev", url: "http://127.0.0.1:3000", reuseExistingServer: true },
  ],
  projects: [
    { name: "desktop", testIgnore: /visual-regression\.spec\.ts/, use: { ...devices["Desktop Chrome"], browserName: "chromium", viewport: { width: 1280, height: 900 } } },
    { name: "mobile", testIgnore: /visual-regression\.spec\.ts/, use: { ...devices["iPhone 13"], browserName: "chromium", viewport: { width: 375, height: 812 } } },
    { name: "visual", testMatch: /visual-regression\.spec\.ts/, use: { ...devices["Desktop Chrome"], browserName: "chromium", viewport: { width: 1280, height: 900 } } },
  ],
});
