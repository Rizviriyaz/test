// @ts-check
import { test, expect } from '@playwright/test';

/**
 * Signed-in checks (saved session from auth.setup.js): real backend, live Wialon/Supabase data.
 * Each protected page must render its header, finish loading without an error state, and get only
 * successful /api responses. Titles and nav labels come from PageHeader/FuelHeader and Sidebar.jsx.
 */

const PAGES = [
  { path: '/dashboard', title: 'Fleet Overview' },
  { path: '/fuel', title: 'Fuel & Performance' },
  { path: '/tracking', title: 'Live Tracking' },
  { path: '/idle-analytics', title: 'Idle Analytics' },
  { path: '/vehicle-health', title: 'Vehicle Health' },
  { path: '/alerts', title: 'Fleet Alerts' },
];

/** Records uncaught page errors and failed backend API responses for the current page. */
function watchFailures(page) {
  const pageErrors = [];
  const apiFailures = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));
  page.on('response', (res) => {
    if (new URL(res.url()).pathname.startsWith('/api/') && res.status() >= 400) {
      apiFailures.push(`${res.status()} ${res.request().method()} ${res.url()}`);
    }
  });
  return { pageErrors, apiFailures };
}

for (const { path, title } of PAGES) {
  test(`${path} loads live data without errors`, async ({ page }) => {
    const { pageErrors, apiFailures } = watchFailures(page);
    await page.goto(path);
    await expect(page, 'saved session must stay signed in').toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('alert'), 'no error state after loading').toHaveCount(0);
    await page.screenshot({ path: `test-results/screens${path}.png`, fullPage: true });
    expect(apiFailures).toEqual([]);
    expect(pageErrors).toEqual([]);
  });
}

test('sidebar navigates between the operational pages', async ({ page }) => {
  await page.goto('/dashboard');
  const nav = page.getByRole('complementary', { name: 'Main Navigation' });
  for (const { label, path, title } of [
    { label: 'Idle Analytics', path: '/idle-analytics', title: 'Idle Analytics' },
    { label: 'Alerts', path: '/alerts', title: 'Fleet Alerts' },
    { label: 'Fleet Overview', path: '/dashboard', title: 'Fleet Overview' },
  ]) {
    await nav.getByRole('link', { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
    await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
  }
});

test('legacy routes redirect to their consolidated pages', async ({ page }) => {
  for (const [from, to] of [['/overview', '/dashboard'], ['/fuel/ranking', '/fuel'], ['/vehicles', '/dashboard']]) {
    await page.goto(from);
    await expect(page, `${from} -> ${to}`).toHaveURL(new RegExp(`${to}$`));
  }
});
