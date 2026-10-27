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
- [x] Accuracy follow-up P1: live records expose `position_timestamp`, `seconds_since_position` and `position_stale` (exactly the R2 case: last seen MOVING, GPS fix > `CONN_STABLE_SECONDS`, messages recent). A missing or malformed `pos.t` (None, bool, non-numeric, NaN/inf, <= 0) gives null time and age for that unit instead of failing `/api/vehicles`. A missing or malformed last-message time (`lmsg.t`) falls back to calc_last's position time as before, else the unit is OFFLINE with a null `last_message_timestamp` / `seconds_since_update`; Vehicle Health (fleet health, alerts, vehicle deep analytics) parses it with the same `parse_wialon_epoch` (uncommitted).
- [x] Accuracy follow-up P2: Tracking list, vehicle drawer ("Last GPS fix" row, "Last GPS fix Moving · 6h ago (data 1 min ago)"; a stale fix labels coordinates and satellites as last-reported) and the Dashboard DELAYED note show GPS fix age separately from message age (uncommitted).
- [x] Accuracy follow-up P3: Vehicle Health marks a `position_stale` unit's GPS `STALE` (−15, counted in GPS warnings) with a `GPS_POSITION_STALE` alert, only while its connection is STABLE (no double penalty); STOPPED/IDLING units are never position-stale (D3). Health page lists STALE under warnings, never healthy (uncommitted).
- [x] Accuracy follow-up P4: AI vehicle tools return `last_known_status`, message age, GPS fix age, `gps_position_stale` and `speed_is_last_reported` (uncommitted).
- [x] Accuracy follow-up P5: the fuel efficiency gauge shows matched-vehicle coverage only (the "Untracked · Fuel-only vehicles" count of unmatched + ambiguous identifiers is removed); `not_tracked` reads "No Wialon unit" and an unknown status "Unknown". Matching rules, data and the legacy `unmatched_*` API fields are unchanged (uncommitted).

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
- [ ] SPA deep-link refresh (e.g. `/dashboard`) verified on Vercel. `vercel.json` (repo root) defines the SPA rewrite and also deploys the backend as a Vercel service; which host runs the backend (Vercel functions or Render) is not yet decided.

---

## PHASE 8 — Monitoring & Reliability

- [x] Health check endpoints.
- [x] `PERF` trace logging (request, calculation, cache, data window, report-lock wait/hold).
- [x] TTL caching to limit Wialon load. Complete and permanently partial report results use the normal TTL (idle analytics 180 s, mileage 60 s, fleet trips 180 s) and stay flagged partial; transiently partial ones (and partial safety scans) 30 s. Windows that ended before today's UAE midnight are cached 1800 s. Distance (mileage) and Trips share one Group Trips run per report group and window (60 s while open), so fleet trips for a timeframe, date or exact window refresh with it; `hours=N` trips keep their own run and 180 s. On a Wialon failure, idle analytics and mileage serve the last good result marked `stale` with its own `retrieved_at` and back off (15 s doubling to 120 s); identical concurrent requests (including analytics builders and token validations) run once (uncommitted).
- [x] Dashboard: sections render at once with skeletons; an earlier visit's cached responses are shown labelled "Earlier result · refreshing"; every today/yesterday section uses a report only for the UAE day its window starts on (uncommitted).
- [x] Offline or silent vehicles handled with null checks.

---

## PHASE 9 — Testing

- [x] Mocked backend regression scripts (`Backend/scripts/test_*.py`) with `offline_guard.py`.
- [x] Frontend unit tests (`npm test`, Node test runner).
- [x] Playwright E2E suite (`Frontend/tests/e2e/`: public, auth setup, authenticated navigation).
- [ ] Playwright E2E run recorded against live Wialon/Supabase.
- [ ] `test_ai_fuel_assistant.py` and 3 cases of `test_fuel_efficiency.py` need live services; not yet run in this verification.
- [ ] A CI workflow for this repository layout. The tracked `Frontend/.github/workflows/playwright.yml` is the generic Playwright template: it runs from the repo root, starts no backend, and is not where GitHub reads workflows.

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

Run on 2026-10-27 against the uncommitted performance, caching and Dashboard changes on top of `885d944`, on Windows with Python 3.13 and Node 24. Automated checks called no live service: backend scripts ran under `OfflineGuard`, which blocks all outbound HTTP. One earlier approved read-only live check is recorded below.

| Check | Result |
| --- | --- |
| `npm run lint` | Pass (no issues) |
| `npm test` | 137/137 pass |
| `npm run build` with an absolute non-loopback URL | Pass (built to a directory outside the repository; no tracked or ignored file changed) |
| `npm run build` with `VITE_API_URL` unset or loopback | Not re-run (last verified failing as designed at `0040ae0`) |
| Backend `scripts/test_*.py` under `OfflineGuard` (directly or via the fuel test runner; 40 scripts, incl. the new `test_cache_coalescing.py`) | 40 pass |
| `test_vehicle_history_chunking.py`, `test_wialon_report_batching.py` (fully mocked, no `offline_guard`) | Pass |
| `test_fuel_fleet_trips.py` | 12/12 pass. It failed 11/12 before this fix: commit `e85b501` gave fixture unit UNIT-B one idle sub-row, but `test_engine_idle_subrows_batched_for_used_rows_only` still expected none. The expectation was updated; report code is unchanged. |
| `test_ai_fuel_assistant.py`, `test_fuel_efficiency.py`, `test_fuel_efficiency_accuracy.py`, `test_fuel_preview.py` (no `OfflineGuard`) | Not run (may contact live services; fuel code unchanged) |
| Live, read-only: Wialon Engine Hours template 2 for 2026-10-26 (report executed and cleaned up; nothing modified) | 103 rows; 83 with idling, all 83 with event sub-rows; 0 rows with idling but no sub-rows; sampled sub-rows are engine-on periods whose Idling sums to the row total |

**Not verified**:

- Playwright E2E (live Wialon + Supabase).
- `audit_*` / `verify_*` / inspector scripts.
- Any Render or Vercel deployment.
- Production sign-in, CORS and deep-link behaviour.
- Live Wialon accuracy against the Wialon UI (sample units were provided for a manual check; not confirmed).

**Known limitations** (documented, not fixed):

- Dashboard driving time is Engine Hours (template 2) "In motion" as reported, never checked against Trips (template 5) mileage, which Wialon measures differently; a unit is unknown only when its value is missing, unreadable or unattributable. Distance and Trips come from one shared Group Trips run; the Engine Hours report's window can still differ from it by up to its 180 s cache time.
- Fuel: 138 identifiers / 942 of 2,335 rows (40%) do not match the current Wialon fleet. They are excluded from KPIs and shown only in the excluded-record views, labelled "No Wialon unit" / "Several Wialon units", not as untracked vehicles. Business question for ATS, per identifier: (a) a vehicle without a tracker, (b) a tracked vehicle whose fuel-card identifier differs from Wialon (needs an alias), or (c) a vehicle sold or transferred before the period. 100 identifiers / 1,393 rows match, with no plate-code conflicts.
- Stale GPS fix: only a unit last seen MOVING is judged by fix age (`position_stale` / `telemetry_stale`, Health GPS STALE). A unit last seen IDLING or STOPPED with an old fix is not flagged (a stationary tracker may not send new fixes); its fix time is shown in the drawer. The drawer shows the fix time of day without a date.
- Follow-up (not fixed): a future `pos.t` (clock skew) gives a negative `seconds_since_position` (never stale; the AI tools would report a negative `gps_fix_age_minutes`). A malformed `pos.t` or `lmsg.t` is handled in `/api/vehicles` and Vehicle Health.
- A parked (last seen STOPPED) unit whose tracker has failed is not flagged in Health/Alerts until it is OFFLINE after 12 hours.
- Dash-only engine cells (`-----`) stay unknown. They count as a permanent report gap, like units outside the report group, ambiguous names and unmatched rows: such results are cached for the normal TTL and stay flagged partial. Unreadable cells, guessed columns and report-group members without a row are treated as transient and re-read after 30 s.
- A group engine row with idling but no event sub-rows keeps its idle duration; its event count and details are unknown (no event is invented from the row total). Not seen live on 2026-10-26.
- Live status: a vehicle keeps its last known MOVING / IDLING / STOPPED status until OFFLINE (12 h); `telemetry_stale` marks a status that is not live, and the live idle timer stops at the last message. The `delayed` counts in the summary/groups responses now count those stale vehicles (they overlap moving/idling).
- Ignition: a missing or invalid reading is `null` (shown as Unknown). Live status does not use the `io_11` / `in1` / `io_239` fallbacks that history parsing uses; their meaning per device is unverified (in Teltonika's AVL ID list `io_239` is Ignition but `io_11` is ICCID, not an ignition input; not checked against this fleet's devices).
