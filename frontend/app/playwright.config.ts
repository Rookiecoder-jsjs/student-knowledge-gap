import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const python = process.env.SC_TEST_PYTHON || "python";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 45000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://127.0.0.1:15173", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: `"${python}" ../../backend/tests/e2e_server.py`, url: "http://127.0.0.1:18000/health", timeout: 60000, reuseExistingServer: false },
    { command: "npm run dev -- --host 127.0.0.1 --port 15173 --strictPort", url: "http://127.0.0.1:15173", timeout: 60000,
      env: { SC_DEV_API_URL: "http://127.0.0.1:18000" }, reuseExistingServer: false },
  ],
  outputDir: path.join("test-results", "browser"),
});
