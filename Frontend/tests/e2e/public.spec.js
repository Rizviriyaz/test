// @ts-check
import { test, expect } from '@playwright/test';

/** Unauthenticated checks: no session, real backend and frontend. */

test('landing page renders without console errors', async ({ page }) => {
  const errors = [];
  page.on('console', (msg) => msg.type() === 'error' && errors.push(msg.text()));
  page.on('pageerror', (err) => errors.push(err.message));
  await page.goto('/');
  await expect(page.locator('body')).not.toBeEmpty();
  await page.screenshot({ path: 'test-results/screens/landing.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('login page shows the sign-in form', async ({ page }) => {
  await page.goto('/login');
  await expect(page.getByRole('heading', { name: /sign in to ats fleet/i })).toBeVisible();
  await expect(page.getByPlaceholder('Enter your email')).toBeVisible();
  await expect(page.locator('input[type="password"]')).toBeVisible();
  await page.screenshot({ path: 'test-results/screens/login.png', fullPage: true });
});

test('protected routes redirect to login without a session', async ({ page }) => {
  for (const path of ['/dashboard', '/idle-analytics', '/fuel', '/alerts']) {
    await page.goto(path);
    await expect(page, `${path} must require login`).toHaveURL(/\/login/);
  }
});

test('backend rejects API calls without a bearer token', async ({ request }) => {
  const health = await request.get('http://127.0.0.1:8000/api/health');
  expect(health.ok()).toBeTruthy();
  const vehicles = await request.get('http://127.0.0.1:8000/api/vehicles');
  expect(vehicles.status()).toBe(401);
});
