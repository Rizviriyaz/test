// @ts-check
import { test as setup, expect } from '@playwright/test';
import fs from 'node:fs';
import process from 'node:process';
import { AUTH_FILE } from '../../playwright.config.js';

/**
 * Signs in through the real /login form (Supabase password auth); nothing is mocked or bypassed.
 * Reuses a saved session from playwright/.auth/user.json while it still opens the dashboard,
 * otherwise needs E2E_EMAIL and E2E_PASSWORD.
 */
setup('authenticate through the real login form', async ({ browser, page }) => {
  if (fs.existsSync(AUTH_FILE)) {
    const saved = await browser.newContext({ storageState: AUTH_FILE });
    const probe = await saved.newPage();
    await probe.goto('/dashboard');
    await probe.waitForTimeout(3_000);
    const stillValid = !new URL(probe.url()).pathname.startsWith('/login');
    if (stillValid) await saved.storageState({ path: AUTH_FILE });
    await saved.close();
    if (stillValid) return;
  }

  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  if (!email || !password) {
    throw new Error(
      'No valid saved session and no credentials. Set E2E_EMAIL and E2E_PASSWORD in Frontend/.env.e2e.local '
      + '(git-ignored), or save a session by logging in manually: '
      + 'npx playwright codegen --save-storage=playwright/.auth/user.json http://localhost:5173/login',
    );
  }

  await page.goto('/login');
  await page.getByPlaceholder('Enter your email').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: /sign in to dashboard/i }).click();
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 60_000 });
  await page.context().storageState({ path: AUTH_FILE });
});
