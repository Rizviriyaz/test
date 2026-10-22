# ATS Fleet — Frontend

React 19 + Vite 8 single-page app for the ATS Fleet Management Platform. It talks only to the FastAPI backend (`VITE_API_URL`) and uses Supabase for sign-in. Project overview, environment variables and deployment: [../README.md](../README.md). Architecture: [../ARCHITECTURE.md](../ARCHITECTURE.md).

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Dev server at `http://localhost:5173` |
| `npm run lint` | ESLint |
| `npm test` | Offline unit tests (Node test runner, `src/**/*.test.js`) |
| `npm run build` | Production build to `dist/`. Fails unless `VITE_API_URL` is an absolute, non-loopback HTTP(S) URL (enforced in `vite.config.js`) |
| `npm run preview` | Serve the built `dist/` locally |
| `npx playwright test` | End-to-end tests in `tests/e2e/` (see below) |

## Environment

Copy `.env.example` to `.env`: `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`. Values are baked in at build time.

## Code layout

- `src/api/fleetApi.js`: every backend call (token attach/refresh, error messages). Pages never call `fetch` directly.
- `src/api/requestCache.js`: in-flight request sharing and a short client cache, cleared on sign-out.
- `src/context/`: `AuthContext` (Supabase session and profile) and `FuelFilterContext` (fuel period/filters).
- `src/pages/`: Landing, Login, Dashboard, FuelPerformance, Tracking, IdleAnalytics, VehicleHealth, FleetAlerts.
- `src/utils/dateRange.js`: UAE-time presets and custom range validation. `src/utils/knownTotals.js`: null-safe totals, so unknown values never become 0.

## End-to-end tests (Playwright)

`playwright.config.js` starts the backend (`../Backend`, port 8000) and the dev server (port 5173), or reuses ones already running. Projects:

- `setup` signs in through the real `/login` form. Credentials come from `E2E_EMAIL` / `E2E_PASSWORD`, or the git-ignored `.env.e2e.local`. The session is stored in `playwright/.auth/user.json` (git-ignored).
- `public` covers the landing page, the login form, protected-route redirects and backend 401 without a token.
- `chromium` (authenticated) covers sidebar navigation and legacy route redirects.

These tests hit **live** Wialon and Supabase and run with one worker. `tests/example.spec.js` is the generated Playwright sample and is ignored by the config.
