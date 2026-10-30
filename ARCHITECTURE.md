# Fleet Management Platform — System Architecture

Reflects `main` at commit `0040ae0`. Setup, environment variables and deployment are in [README.md](README.md).

---

## 1. System Overview

The **ATS Fleet Management Platform** is a decoupled telematics web application. A FastAPI backend sits between Wialon (telematics), Supabase (auth, profiles, fuel data) and Groq (AI). It normalizes their data and serves it to a React frontend.

```text
+-------------------------------------------------------------------------------+
|                                CLIENT LAYER                                   |
|   React 19 + Vite + Tailwind CSS v4 + Recharts + react-leaflet + lucide       |
|   Supabase JS (sign-in only)  ·  api/fleetApi.js (every backend call)         |
+---------------------------------------+---------------------------------------+
                                        | REST / JSON + Supabase Bearer token
                                        v
+---------------------------------------+---------------------------------------+
|                                BACKEND LAYER                                  |
|   FastAPI + Uvicorn · auth middleware · TTL caches · ThreadPoolExecutor       |
+-----------+-----------------------+-----------------------+-------------------+
            |                       |                       |
            v                       v                       v
+-----------+-----------+ +---------+-------------+ +-------+-------------------+
|  WIALON (wialon.py)   | |  SUPABASE              | |  GROQ (ai/)               |
|  shared + "events"    | |  /auth/v1/user         | |  tool-calling loop over   |
|  sessions; native     | |  profiles (role)       | |  read-only fleet & fuel   |
|  report engine        | |  fuel_imports/         | |  tools                    |
|                       | |  fuel_transactions     | |                           |
+-----------------------+ +------------------------+ +---------------------------+
```

---

## 2. Fundamental Integration Principle

$$\text{Wialon / Supabase / Groq} \longrightarrow \text{Backend integration + normalization} \longrightarrow \text{REST API} \longrightarrow \text{Frontend}$$

The frontend never talks to Wialon or Groq, and it uses Supabase only for sign-in. It is decoupled from Wialon's schema (`nm`, `sens`, `calc_last`, `avl_unit`, report tables).

**Unknown is not zero.** When Wialon returns an unreadable cell, an incomplete sub-row list or no row for a unit, the API returns `null` and flags it (`incomplete_fields`, `column_warnings`, `events_complete`, `idle_events_complete`). It never returns `0`. The frontend renders `null` as "—" or "unavailable" (`utils/knownTotals.js`, `components/Fuel/telemetryFormat.js`).

---

## 3. Frontend Architecture

- **Engine**: React 19 + Vite 8. Pages are lazy-loaded under `Suspense`. The import alias `@` maps to `src/`.
- **Routing** (`App.jsx`, React Router v7):
  - Public: `/`, `/login`.
  - Protected (`ProtectedRoute`): `/dashboard`, `/fuel`, `/tracking`, `/idle-analytics`, `/vehicle-health`, `/alerts`.
  - Aliases rendering an existing page: `/drivers` and `/settings` render Dashboard, `/trips` renders Tracking, `/reports` renders IdleAnalytics.
  - Redirects: `/overview` and `/vehicles` go to `/dashboard`. `/fuel/ranking`, `/fuel/vehicles`, `/fuel/import`, `/ranking` and `/import` go to `/fuel`.
  - Unknown paths go to `/`.
- **State**:
  - `AuthContext` holds the Supabase session and profile. The profile loads in the background once the session is known, so pages render without waiting for it; until it arrives role-gated UI (fuel import) stays hidden. A profile response is applied only while its user is still the one signed in. The backend enforces roles on every request regardless.
  - `FuelFilterContext` holds the fuel period, vehicle and fuel type.
  - Pages use local state plus `useMemo` for aggregation.
- **API client** (`api/fleetApi.js`): attaches and refreshes the Supabase token, maps HTTP errors to messages, and throws when there is no session. `api/requestCache.js` shares in-flight promises and keeps a short client-side cache. Each sign-out clears it and bumps a generation counter, so a response from before sign-out is never cached or shared. Requests for UAE calendar timeframes (`today`, `yesterday`, ...) are keyed with the UAE date, so one day's response is never reused for the next. `peekClientCache` lets a page show an earlier visit's response (live vehicles up to 2 min old, reports up to 10 min) while it refetches; the Dashboard labels such data "Earlier result · refreshing" until this visit's request succeeds, and shows a report only for the UAE day its window starts on.
- **Code splitting**: operational pages are lazy-loaded; after sign-in their code (not data) is prefetched in the background.
- **AI widget**: `components/AI/AiAssistantWidget.jsx` renders only for authenticated users.
- **Maps / charts**: `react-leaflet` with custom status markers; `recharts`.

---

## 4. Backend Architecture

- **Framework**: FastAPI on Uvicorn.
- **Modules**:
  - `main.py`: app and most routes.
  - `fuel/routes.py`: `/api/fuel` router.
  - `fleet_analytics.py`: `/api/fleet/*` builders.
  - `ai/`: assistant loop and tools.
  - `auth.py`: token validation.
- **Lifespan**: at startup the app attempts `login(force=True)`. It does not pre-build the fleet alerts: no page reads them (only the AI chat tool, on demand), and the cold scan (measured 36.8 s, 107 message loads) counts toward Wialon's message-load limit. If Wialon login fails, startup continues and login is retried lazily on the next request, so `/api/health` and fuel-only routes stay available.
- **Concurrency**: `ThreadPoolExecutor` for per-vehicle fan-out. Request trace IDs are propagated into workers (`performance_diagnostics.map_with_request_context`).
- **Errors**:
  - `WialonAPIError` is mapped to 502 `"Wialon upstream request failed"`.
  - Other failures return a generic 500.
  - Exception text is logged, never returned.
- **Window limits**: `MAX_HISTORY_HOURS` = 31 days for raw message endpoints. `MAX_REPORT_HOURS` = 366 days for report endpoints. Longer windows get a 400 before any Wialon work starts.

### 4.1 Caching

All caches are per-process, bounded, thread-safe `TTLCache` instances (`app/utils/ttl_cache.py`).

| Cache | Location | Freshness |
| --- | --- | --- |
| `_cache` (128 entries, LRU, evicted after 600 s or the entry's own TTL if longer) | `main.py`, via `cached(key, ttl_seconds)` / `set_cache()` | Per route, see §9 |
| Fleet analytics (same retention rule) | `fleet_analytics.py` | Alerts: fresh 180 s, background refresh-ahead after 120 s, 30 s retry back-off after a failed refresh. Health, utilization, trips (180 s for an open window), safety, groups: concurrent identical calls run once and share the result or exception. One groups / units / calc_last read is shared by builders for 5 s. |
| Group Trips snapshot | `fleet_analytics.py` (`group_trips_snapshot`) | One report run per report group and window, shared by `/api/dashboard/mileage` and `/api/fleet/trips` (timeframe, date and exact windows; `hours=N` trips run their own report). 60 s open, 1800 s closed, 30 s with unreadable cells or guessed columns. Both results report its window and `retrieved_at` and never outlive it; a failure is never cached. |
| Validated tokens (1024 entries) | `auth.py` | 300 s; reused up to 600 s only while Supabase is unreachable, never past the JWT `exp`. Concurrent validations of one token share one Supabase round trip and its outcome. |
| Fuel telematics bundles | `fuel/wialon_distance.py` | 120 s for ranges ending today, 3600 s for closed ranges, 30 s for incomplete bundles (`FUEL_TELEMATICS_TTL_*`). Every request for a range (routes and AI fuel tools) shares one build on the fuel telematics executor, and a cached range answers without an executor worker (`fuel/routes.py`). A complete bundle of a range ending today that is at least 75% of its live TTL old is still served and refreshed once in the background; no refresh within 30 s of a failed one or 300 s of a Wialon 1004. |
| Fuel analytics reads | `fuel/db.py` (`cached_analytics_read`, 16 entries) | 30 s, keyed by filters, caller token (without the service-role key) and the table-layout flags. Cleared by any fuel row insert or import commit in the process; a read that started before a write is never served after it. |
| Fuel Wialon unit list | `fuel/routes.py` (8 entries) | 60 s per Wialon session, for plate/name matching only; an empty list is never kept. |

- Cache keys include every parameter that changes the result.
- A window that ended before today's UAE midnight (e.g. `yesterday`) is a closed report: mileage, idle analytics and fleet trips cache it for 1800 s (`CLOSED_WINDOW_TTL_SECONDS`).
- Report-result policy: complete results, and results partial only through permanent gaps (unmatched rows, ambiguous names, units outside the report group), use the normal TTL and keep their partial flags. Results partial through a possibly transient read problem (unreadable cells, guessed columns, a group member without a row) are kept 30 s; so is a partial safety scan. The utilization raw-message fallback is never cached. The unit trips and unit idle routes do not cache a result flagged incomplete.
- After a Wialon failure, mileage and idle analytics serve the last good result marked `stale`, with its own `retrieved_at` (the Wialon read time).
- Fuel efficiency's `period_key_for_date` is memoized (`lru_cache`), because it dominated trip bucketing time.

### 4.2 Performance diagnostics

`app/performance_diagnostics.py` and the auth middleware add trace IDs and `PERF` log lines for the heavy routes:

- `/api/vehicles`, `/api/dashboard/mileage`, `/api/idle/fleet-analytics`, `/api/fleet/*` and `/api/wialon/audit`.
- Lines cover request start/end, calculation timings, cache hit/miss, data window and report-lock wait/hold times.

Successful timings log at DEBUG (set `LOG_LEVEL=DEBUG`). 4xx responses log at INFO; 5xx responses and exceptions log at WARNING.

---

## 5. Wialon Integration Architecture

`app/wialon.py` is the single gateway (`wialon_request`, `login`). `app/vehicles.py` handles discovery. `app/wialon_reports.py` runs reports.

1. **Sessions**:
   - `token/login` returns the shared session id (`eid`).
   - A named secondary session, `EVENTS_SESSION = "events"`, uses the same token with its own session id. Fleet idle analytics runs its event-paging engine report there, so long sub-row paging never blocks the dashboard reports on the shared session.
2. **Self-healing**:
   - Error 1 (invalid session) re-logs in the session that owns the stale id: shared or named, tracked in `_sid_owner`. The request is then retried.
   - Error 1003 (concurrency limit) retries with back-off.
   - HTTP failures retry up to 3 times.
3. **Report serialization**: Wialon keeps one report result per session, so reports are serialized with one lock per session (`_holding_report_lock`).
4. **"All Unit" group**: `resolve_all_units_group_id` finds the account's group named "All Unit"/"All Units". With several matches it takes the one with the most members. `WIALON_GROUP_ALL_UNITS` overrides the lookup. If neither yields a group, the call raises `WialonAPIError`. No group ID is hardcoded.
5. **Sub-row paging** (`_fetch_all_subrows`, `_fetch_rows_subrows`):
   - First pages for up to 50 rows go out in one `core/batch` call.
   - Wialon can ignore `indexFrom/indexTo` and resend the whole list, so sub-rows are de-duplicated and paging stops when a page brings nothing new.
   - A list is complete only when exactly the row's `d` distinct sub-rows were read. Otherwise the list is flagged incomplete.
6. **Dynamic sensors**: sensors are resolved by name, never by ID. Invalid values (`INVALID_WIALON_VALUE = -348201.3876`, or anything `<= -100000`) are filtered by `is_valid_sensor_value`.

### 5.1 Authoritative sources

| Metric | Source |
| --- | --- |
| Fleet mileage for a period | Group trips report (template 5) on the "All Unit" group |
| Fleet trip count per unit | Same report: the unit row's sub-row count. `0` when the only sub-row is the `-----` placeholder; `null` when unreadable |
| Unit trips, distance, max speed | Unit trips report (template 4). Day-summary rows are replaced by their trip sub-rows; a partial list raises an error instead of undercounting |
| Idle time, engine hours, idle events | Engine reports (templates 1 / 2) |
| All-time mileage | Hardware odometer from `unit/calc_last` |
| Live status, position, sensors | `core/search_items` + `unit/calc_last` |
| Fuel volumes and cost | Supabase `fuel_transactions` |
| Fuel efficiency (km/L) | Supabase fuel joined with Wialon distance, matched by registration plate |

The AI `get_vehicle_trips` tool uses the same unit trips report as the vehicle drawer, so its trip counts match the dashboard. Raw message segmentation (`segment_vehicle_activity`, `stop_tolerance_seconds = 60`) is still used for history, route, safety and deep-analytics views.

---

## 6. Authentication Architecture

1. **Frontend**: `@supabase/supabase-js` with `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY` and `persistSession: true`.
   - When Supabase is not configured, sign-in fails with "Supabase authentication is not configured". There is no local or demo fallback.
   - The Login page's "Remember me" checkbox is passed to `login()` but has no effect: Supabase always persists the session.
2. **Backend middleware** (`require_supabase_auth`):
   - Every `/api/*` request needs `Authorization: Bearer <token>`, except `OPTIONS` requests and `/`, `/health` and `/api/health`.
   - `auth.authenticate_access_token` validates the token against Supabase `/auth/v1/user`.
   - It then loads `public.profiles` (`id`, `role`). Permitted roles: `admin`, `manager`, `fleet_manager`, `viewer`.
   - A missing profile or another role returns 403. A missing or invalid token returns 401. Supabase down or unconfigured returns 503.
3. **Fuel import**: needs `admin`, `manager` or `fleet_manager`; `viewer` gets 403.
4. **CORS**: registered outermost, so 401/403/503 responses carry CORS headers.
   - `CORS_ORIGINS` is an explicit list, and `*` raises at startup.
   - Localhost:5173 is the default only when `ENVIRONMENT` is not `production`.
5. **API docs**: `/docs`, `/redoc` and `/openapi.json` are served only when `ENVIRONMENT=development`.

---

## 7. Database Architecture (Supabase)

- `public.profiles`:
  - `id` references `auth.users`.
  - `role` must be one of the permitted roles above. The frontend also reads it for display.
- `fuel_imports` / `fuel_transactions`: written and read through PostgREST with the service-role key (`fuel/db.py`).
- `Backend/sql/proposed_fuel_dedup.sql` is a **proposed** dedup migration. Nothing in the code applies it.

There is no other persistence. Caches and the Wialon session are in-memory.

---

## 8. Data & Telemetry Flow

```text
[ Wialon GPS device ] → [ Wialon servers ]
        │
        ├─ core/search_items + unit/calc_last ──► vehicles.py ──► normalize.py / status.py
        │                                          (live status: speed>0 MOVING; ignition on IDLING; else STOPPED)
        ├─ report/exec_report (templates 1,2,4,5) ──► wialon_reports.py
        │                                          (column lookup by header, sub-row paging, completeness flags)
        └─ messages/load_interval (chunked) ──► normalize.py (history, route, safety)
                     │
                     v
        main.py / fleet_analytics.py / fuel/* ──► JSON (UTC epoch seconds + null-for-unknown)
                     │
                     v
        fleetApi.js ──► pages (UAE-time formatting at presentation only)
```

---

## 9. API Reference

Every route except `/`, `/health` and `/api/health` needs a Supabase bearer token. "Cache" is the `_cache` freshness in `main.py`; "—" means not cached there.

### Core and fleet

| Endpoint | Method | Purpose | Cache |
| :--- | :--- | :--- | :--- |
| `/`, `/health`, `/api/health` | GET | Status; `/api/health` reports `wialon_configured` | — |
| `/api/vehicles` | GET | Discovered fleet with live status and sensors | 5 s |
| `/api/vehicles/{id}` | GET | Single vehicle (from the live fleet list) | 5 s |
| `/api/groups` | GET | Wialon groups and member unit IDs | 15 s |
| `/api/vehicles/{id}/trips` | GET | Unit trips report | 60 s (complete results only) |
| `/api/vehicles/{id}/history` | GET | Raw normalized messages (≤ 31 days) | — |
| `/api/vehicles/{id}/idle` | GET | Engine-report idle totals and periods; `events_complete` | 60 s (complete results only) |
| `/api/vehicles/{id}/efficiency` | GET | Speed distribution, idle %, driving time | — |
| `/api/vehicles/{id}/safety` | GET | Safety events from messages | — |
| `/api/vehicles/{id}/analytics` | GET | Deep analytics (≤ 31 days) | fleet analytics cache (180 s) |
| `/api/vehicles/{id}/route` | GET | Route points (≤ 31 days) | — |
| `/api/drivers` | GET | Driver assignments | — |
| `/api/wialon/audit` | GET | Fleet sensor/configuration audit | — |
| `/api/dashboard/summary` | GET | Fleet distance, trips, safety for a rolling window | 60 s |
| `/api/dashboard/fleet-health` | GET | Status counts, sensor availability, odometer | 300 s |
| `/api/dashboard/mileage` | GET | Mileage ranking (`timeframe`, `from_time`/`to_time`, `from_date`/`to_date`, `group_id`) | 60 s open / 1800 s closed window, 30 s transient partial; never past the shared Group Trips snapshot |
| `/api/dashboard/trends` | GET | Hourly motion/mileage trend | 600 s |
| `/api/idle/fleet-analytics` | GET | Fleet idle totals, events, hotspots, trend, groups; `events_complete` | 180 s open / 1800 s closed window (`refresh` / `skip_cache` bypass) |
| `/api/fleet/health`, `/utilization`, `/trips`, `/safety`, `/alerts`, `/groups` | GET | Fleet analytics rollups | fleet analytics cache |
| `/api/ai/chat` | POST | AI assistant | — |

### Fuel (`/api/fuel`)

| Endpoint | Method | Purpose |
| :--- | :--- | :--- |
| `/import/preview` | POST | Parse an Excel statement without writing (import role) |
| `/import` | POST | Commit an import (import role) |
| `/imports`, `/transactions` | GET | Import history, transactions |
| `/analytics/summary`, `/trend`, `/vehicles`, `/fuel-types`, `/excluded` | GET | Fuel KPIs and breakdowns |
| `/analytics/distance` | GET | Wialon distance for the fuel scope |
| `/analytics/efficiency`, `/efficiency/vehicles`, `/efficiency/trend` | GET | km/L fleet, per vehicle, over time |
| `/analytics/waste`, `/analytics/vehicle-detail` | GET | Waste intelligence, per-vehicle detail with Wialon activity (`idle_events_count` is `null` when `idle_events_complete` is false) |

Response envelopes such as `{"count": n, "vehicles": [...]}` are a contract with `fleetApi.js`. Change them on both sides together.

---

## 10. Date and Time-Range Behaviour

All computation uses UTC epoch seconds. Named periods are resolved in **UAE time (Asia/Dubai, UTC+4, no DST)**: backend `utils/datetime_helpers.get_timeframe_timestamps`, frontend `utils/dateRange.js`.

| Timeframe | Window |
| --- | --- |
| `today` | 00:00 UAE today → now |
| `yesterday` | 00:00 → 23:59:59 UAE yesterday |
| `week` / `this_week` | Monday 00:00 UAE → now |
| `month` / `this_month` | 1st of month 00:00 UAE → now |
| `year` / `this_year` | 1 Jan 00:00 UAE → now |
| `24h`, `7d`/`1w`, `30d`/`1m`, `365d`/`1y` | Rolling window ending now (never calendar-to-date) |
| `all` (mileage only) | Hardware odometer; no window |
| `from_time`/`to_time` | Exact epoch seconds |
| `from_date`/`to_date` | UAE calendar-day bounds |

Per page:

- **Dashboard mileage**: Today, Yesterday, This Week, This Month, This Year, All Time. Today's trip, idle and mileage KPIs always load with `timeframe=today`, independent of the ranking selector.
- **Idle Analytics**:
  - Presets: Today, Yesterday, Last 1 Week (today plus the previous 6 UAE days, ending now) and Last Month (the full previous calendar month). A custom From/To range is also available.
  - Today and Yesterday are sent as `timeframe` (cached by name). The other choices are sent as `from_time`/`to_time`.
  - Custom ranges are validated: From must be before To, From cannot be in the future, To is capped at now, and the range is at most 366 days.
  - The trend uses hourly buckets up to 48 h. Longer spans use one bucket per UAE calendar day; the first bucket starts at the range start, and later ones start at UAE midnight.
- **Fuel & Performance**: Today, Last 7 Days (today plus 6 days), Last 30 Days (today plus 29 days), Custom Range and All Historical Data (unbounded). The page's default period is All Historical Data. Periods are sent as `date_from`/`date_to` (UAE dates).

---

## 11. AI Assistant

- `POST /api/ai/chat` runs a Groq tool-calling loop (`ai/assistant.py`, model `openai/gpt-oss-120b`, at most 6 steps).
- Tools in `ai/tools.py` are read-only wrappers over the deterministic functions above:
  - Fleet: `get_fleet_summary`, `get_fleet_health`, `get_fleet_utilization`, `get_fleet_safety`, `get_fleet_alerts`, `get_group_analytics`.
  - Vehicles: `get_vehicles_by_status`, `get_vehicle_status`, `get_vehicle_trips`, `get_vehicle_deep_analytics`, `get_idle_events`.
  - Fuel: `get_fuel_summary`, `get_fleet_fuel_efficiency`, `get_vehicle_fuel_ranking`, `get_vehicle_fuel_performance`.
- Vehicle references resolve by exact ID first, then by exact name (case-insensitive), because fleet names are often numeric.
- Tool results are capped at 12,000 characters to fit the Groq per-minute token limit. The longest lists are halved until the result fits; count and total fields stay complete. `truncated_lists` tells the model which lists were cut.

---

## 12. Security Boundaries

1. **Secret isolation**: `WIALON_TOKEN`, `SUPABASE_SERVICE_ROLE_KEY` and `GROQ_API_KEY` are server-side only and never returned.
2. **Auth at the boundary**: every operational route is protected by middleware, not per route.
3. **CORS**: explicit origins only.
4. **Error masking**: generic 500/502 bodies; upstream text is only logged.
5. **Input bounds**: `Query(ge, le)` on `hours`, window limits on resolved ranges, and fuel import size limits (`FUEL_IMPORT_MAX_FILE_BYTES`, default 10 MB).

---

## 13. Scaling Strategy

- **Current**: caches, Wialon sessions and report locks are per process. Locally that is one Uvicorn process. The root `vercel.json` runs the backend as a Vercel Function, where the platform may run several instances, each with its own caches: an instance that did not run an import can serve pre-import fuel figures for up to 30 s, new Wialon units reach fuel matching within 60 s, and today's telematics are at most one live TTL (120 s) old per instance. Shared caching needs new infrastructure (e.g. Redis) and has not been added.
- **Next**: move caches to Redis, add background workers for report pre-computation, and add time-series storage for telemetry archives.
- **Enterprise**: Wialon data-streaming ingestion and horizontally scaled stateless API nodes.
