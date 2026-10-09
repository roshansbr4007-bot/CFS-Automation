import { defineConfig } from "@playwright/test";

// Runs against a live stack: Django on :8000 and the frontend on :5173 (see README).
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173", trace: "retain-on-failure" },
});
