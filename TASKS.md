# Fleet Management Platform — Phased Implementation Tasks

Status as of `main` @ `0040ae0` (2026-10-22). `[x]` = implemented in code, `[ ]` = open or not yet verified. Test evidence is in [Verification Status](#verification-status).

---

## PHASE 0 — Current State Audit

- [x] Inspect project architecture and repository structure.
- [x] Inspect Wialon API communication patterns and session management.
- [x] Inspect backend normalization layer and status calculations.
- [x] Inspect frontend React components, routing, and visualization libraries.
- [x] Inspect environment variables and secret management.

---

## PHASE 1 — Wialon Integration

- [x] Dynamic AVL unit discovery via `core/search_items` (flags `8393729`, including profile fields).
- [x] Batch sensor and telemetry calculation via `unit/calc_last`.
- [x] Dynamic sensor discovery matching by name.
- [x] Wialon session recovery on `Error 1`, per owning session (shared or `events`).
- [x] Rate limit and concurrency backoff on `Error 1003`.
- [x] Elimination of hardcoded vehicle IDs, names, fleet counts and the "All Unit" group ID (discovered by name since `0040ae0`).
- [x] Native report engine for trips, mileage, engine hours and idle (`wialon_reports.py`).
- [x] Per-session report locks and a dedicated `events` session for long idle-event paging.
- [x] Sub-row paging hardened against Wialon ignoring page ranges (de-duplication + completeness check), with first pages batched via `core/batch`.

---

## PHASE 2 — Data Normalization & Accuracy

- [x] Motion state classification (`MOVING`, `IDLING`, `STOPPED`) and connection status (`STABLE`, `DELAYED`, `OFFLINE`).
- [x] Filtering of Wialon sentinel invalid values (`-348201.3876` and anything `<= -100000`).
- [x] Normalization of message streams into uniform telemetry points (chunked history loads).
- [x] Unknown values returned as `null` with completeness flags; never coerced to 0 (backend and UI).
- [x] Fleet trip counts from the group trips report's sub-rows, no longer always `null` (`0040ae0`).
- [x] Unit trips read from day-summary sub-rows; partial lists fail instead of undercounting (`0040ae0`).
- [x] Idle period count / longest / average are `null` unless the event list is complete (`events_complete`, `idle_events_complete`) (`0040ae0`).
- [x] Idle trend daily buckets aligned to UAE midnight for spans over 48 h (`0040ae0`).
- [x] AI trips tool uses the same trips report as the dashboard (`0040ae0`).

---

## PHASE 3 — Backend & REST APIs

- [x] Vehicles: `/api/vehicles`, `/api/vehicles/{id}`, `/trips`, `/history`, `/idle`, `/efficiency`, `/safety`, `/analytics`, `/route`.
- [x] `/api/groups`, `/api/drivers`, `/api/wialon/audit`.
- [x] Dashboard: `/api/dashboard/summary`, `/fleet-health`, `/mileage` (Today … All Time, custom ranges), `/trends`.
- [x] `/api/idle/fleet-analytics` with events, hotspots, trend, groups and vehicle/group scoping.
- [x] Fleet analytics: `/api/fleet/health`, `/utilization`, `/trips`, `/safety`, `/alerts`, `/groups`.
- [x] Fuel: `/api/fuel/import/preview`, `/import`, `/imports`, `/transactions`, `/analytics/*`.
- [x] AI: `POST /api/ai/chat`.
- [x] `/health` and `/api/health`.
- [x] Window limits (31 days history / 366 days reports) and bounded TTL caches.

---

## PHASE 4 — Dashboard & UI

- [x] Fleet Overview: KPIs, live map, today's trip/idle KPIs, mileage ranking with 6 timeframes, group utilization.
- [x] Fuel & Performance page with import, KPIs, trend, efficiency, waste, anomalies, excluded records and vehicle detail.
- [x] Live Tracking with vehicle detail drawer.
- [x] Idle Analytics with presets, custom From/To range, CSV export, partial-data notices.
- [x] Vehicle Health and Alerts pages.
- [x] AI Assistant widget for signed-in users.
- [x] Landing page with static product screenshots and no live telemetry charts.

---

## PHASE 5 — Authentication & Route Protection

- [x] Supabase Auth client (`Frontend/src/lib/supabase.js`) with persisted sessions.
- [x] `AuthProvider` / `useAuth` with profile retrieval from `profiles`.
- [x] `ProtectedRoute` guarding all operational routes.
- [x] Backend auth middleware on every `/api/*` route (token validation + permitted role).
- [x] Fuel import restricted to `admin`, `manager`, `fleet_manager`.
- [x] Local/demo login fallback removed (Supabase is required).
- [ ] "Remember me" checkbox is a no-op: Supabase always persists the session. Wire it up or remove it.

---

## PHASE 6 — Production Hardening

- [x] Zero hardcoded credentials or tokens in source code.
- [x] `.gitignore` files for root, backend and frontend (including Playwright output and `.auth`).
- [x] `.env.example` templates for backend and frontend.
- [x] Error masking (generic 500/502; upstream text only logged).
- [x] CORS whitelisting from `CORS_ORIGINS`, `*` rejected; API docs only with `ENVIRONMENT=development`.
- [x] Production build guard requiring a non-loopback absolute `VITE_API_URL`.
- [x] Startup never blocks on Wialon; fleet alerts pre-warmed in the background (`0040ae0`).

---

## PHASE 7 — Deployment Readiness (Render + Vercel)

- [x] Deployment settings and post-deploy checks documented ([README.md](README.md#deployment)).
- [ ] Render backend service configured and `/api/health` verified live.
- [ ] Vercel frontend configured with production `VITE_*` values and verified live.
- [ ] `CORS_ORIGINS` set to the Vercel origin and sign-in verified end to end in production.
- [ ] SPA deep-link refresh (e.g. `/dashboard`) verified on Vercel; no rewrite config exists in the repo.

---

## PHASE 8 — Monitoring & Reliability

- [x] Health check endpoints.
- [x] `PERF` trace logging (request, calculation, cache, data window, report-lock wait/hold).
- [x] TTL caching to limit Wialon load; incomplete report results not cached.
- [x] Offline or silent vehicles handled with null checks.

---

## PHASE 9 — Testing

- [x] Mocked backend regression scripts (`Backend/scripts/test_*.py`) with `offline_guard.py`.
- [x] Frontend unit tests (`npm test`, Node test runner).
- [x] Playwright E2E suite (`Frontend/tests/e2e/`: public, auth setup, authenticated navigation).
- [ ] Playwright E2E run recorded against live Wialon/Supabase.
- [ ] `test_ai_fuel_assistant.py` and 3 cases of `test_fuel_efficiency.py` need live services; not yet run in this verification.
- [ ] A CI workflow for this repository layout. The untracked `Frontend/.github/workflows/playwright.yml` is the generic Playwright template: it runs from the repo root, starts no backend, and is not where GitHub reads workflows.

---

## PHASE 10 — AI & Roadmap

- [x] Groq tool-calling assistant with read-only fleet and fuel tools.
- [x] Tool-result size cap for the Groq token budget (`0040ae0`).
- [ ] AI audit logging with timestamp and user ID.
- [ ] Driver scorecards.
- [ ] Predictive maintenance forecasting.
- [ ] Automated fleet briefings.

---

## Verification Status

Run on 2026-10-22 against the working tree at `0040ae0`, on Windows with Python 3.13 and Node 24. No live service was called: backend scripts ran with all outbound HTTP blocked (`requests` sessions and `httpx` network transports).

| Check | Result |
| --- | --- |
| `npm run lint` | Pass, no issues |
| `npm test` | Pass, 60/60 tests |
| `npm run build` with `VITE_API_URL` unset | Fails as designed: "VITE_API_URL is required for production builds." |
| `npm run build` with a loopback `VITE_API_URL` | Fails as designed: "must not use a loopback host in production." |
| `npm run build` with an absolute non-loopback URL | Pass (built to a temporary directory) |
| Backend `scripts/test_*.py` (33 scripts) | 31 pass offline |
| `test_fuel_efficiency.py` | 10/13 pass. 3 endpoint cases reach real Supabase pagination (`fuel/db.py`), so they need live access |
| `test_ai_fuel_assistant.py` | Live test by design (logs in to Wialon); not run |

**Not verified**:

- Playwright E2E (live Wialon + Supabase).
- `audit_*` / `verify_*` / inspector scripts.
- Any Render or Vercel deployment.
- Production sign-in, CORS and deep-link behaviour.
- Live Wialon accuracy against the Wialon UI.
