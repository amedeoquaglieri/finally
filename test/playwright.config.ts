import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.BASE_URL ?? "http://localhost:8000";

export default defineConfig({
  testDir: "./e2e",
  // All tests share one backend (one user, one SQLite DB), so run them serially.
  // Each test still sets up its own preconditions and tolerates any run order.
  fullyParallel: false,
  workers: 1,
  retries: Number(process.env.RETRIES ?? 0),
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    viewport: { width: 1600, height: 1000 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } }],
});
