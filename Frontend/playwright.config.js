// @ts-check
import { defineConfig, devices } from '@playwright/test';
import fs from 'node:fs';
import process from 'node:process';

/**
 * Real-browser end-to-end tests against the local backend + frontend.
 *
 * Authentication is never bypassed: tests/e2e/auth.setup.js signs in through the real /login form
 * (Supabase) and stores the session in playwright/.auth/user.json (git-ignored). Credentials come from
 * E2E_EMAIL / E2E_PASSWORD, optionally read from the git-ignored file .env.e2e.local.
 */
if (fs.existsSync('.env.e2e.local')) {
  process.loadEnvFile('.env.e2e.local');
}

const baseURL = process.env.E2E_BASE_URL || 'http://localhost:5173';
export const AUTH_FILE = 'playwright/.auth/user.json';

export default defineConfig({
  testDir: './tests',
  // The generated example spec targets playwright.dev, not this app
  testIgnore: ['example.spec.js'],
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  // One worker: every test hits live Wialon, whose session allows limited concurrency
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.js/ },
    {
      name: 'public',
      testMatch: /public\.spec\.js/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chromium',
      testMatch: /e2e\/.*\.spec\.js/,
      testIgnore: /public\.spec\.js/,
      use: { ...devices['Desktop Chrome'], storageState: AUTH_FILE },
      dependencies: ['setup'],
    },
  ],
  webServer: [
    {
      command: 'python -m uvicorn app.main:app --port 8000',
      cwd: '../Backend',
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: baseURL,
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
