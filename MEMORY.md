# Fleet Management Platform — Project Memory

Decisions and knowledge that are not obvious from the code. Reflects `main` at commit `0040ae0`. For system design see [ARCHITECTURE.md](ARCHITECTURE.md); for delivery status see [TASKS.md](TASKS.md).

---

## 1. Project Identity

- **Project Name**: ATS Fleet Management Platform
- **Repository Context**: Advanced Technology Solutions (ATS) Telematics & Fleet Intelligence
- **Domain**: Real-time fleet telematics, GPS vehicle monitoring, idle and mileage analytics, fuel management, AI-assisted fleet queries.

---

## 2. Product Purpose

Turns Wialon telematics into operational metrics:

- live location and motion state (`MOVING`, `IDLING`, `STOPPED`)
- idle and engine-hour analytics
- trip and mileage rankings
- vehicle health and alerts

It also joins Supabase fuel transactions with Wialon distance to produce fuel efficiency.

---

## 3. Current Status

- **Backend**: FastAPI with Wialon session management, including a dedicated `events` session for long report paging. It uses Wialon's native report engine for distance, trips, engine hours and idle, keeps bounded TTL caches, mounts the fuel module under `/api/fuel`, and runs a Groq assistant at `/api/ai/chat`.
- **Frontend**: React 19 + Vite 8 + Tailwind CSS v4 + Recharts + react-leaflet + lucide-react.
- **Authentication**: Supabase only. Every `/api/*` route except `/api/health` needs a bearer token whose user has a permitted `profiles.role`.
- **Hosting**: intended to be Render (backend) and Vercel (frontend). Live deployment has not been verified from this repository; see [README.md](README.md#deployment).

---

## 4. Wialon Integration Knowledge

- **Login / sessions**:
  - `token/login` with `{"token": WIALON_TOKEN}` returns the `eid` session id.
  - `login(session="events")` opens a second session with the same token.
  - Wialon keeps **one report result per session**, so reports are serialized per session.
- **Errors**:
  - Error 1 (invalid session) re-logs in the session that owned the stale id.
  - Error 1003 (concurrency limit) retries with back-off.
- **Discovery**: `core/search_items` with `itemsType: "avl_unit"` and flags `8393729` (base + last message + admin fields + profile fields `pflds`, which hold the registration plate), then `unit/calc_last` for computed sensors, position and odometer.
- **Groups**: `core/search_items` with `itemsType: "avl_unit_group"`. The fleet-wide "All Unit" group is found **by name**. `WIALON_GROUP_ALL_UNITS` is only an override; the old hardcoded default ID was removed in `0040ae0`.
- **Reports**:
  - `report/exec_report` on resource `WIALON_REPORT_RESOURCE_ID`.
  - Templates: unit trips 4, unit engine 1, group trips 5, group engine/idle 2. The defaults match the ATS production account and can be overridden by env vars.
- **Report quirks verified live** (recorded in code comments in `wialon_reports.py`):
  - The unit trips template groups trips **by day**. A row with sub-rows (`d > 0`) is a day summary whose sub-rows are the trips.
  - In the group trips template, each unit row's sub-rows are its trips. A unit with no trips gets one `-----` placeholder sub-row.
  - `report/get_result_subrows` can **ignore `indexFrom/indexTo`** and return the whole list for every page (seen live: the same 313 intervals for every page). Sub-rows are therefore de-duplicated, and completeness is checked against `d`.
  - One unit can have 100k+ idle sub-rows. Paging them can hold a report lock for minutes; this is why event paging runs on the `events` session and is limited to the target vehicles.
- **History**: `messages/load_interval` is read in chunks (`WIALON_HISTORY_CHUNK_SECONDS`, `WIALON_HISTORY_PAGE_LIMIT`, `WIALON_HISTORY_MAX_REQUESTS`).

---

## 5. Normalization and Accuracy Decisions

1. **Sensors by name**: sensor IDs differ across hardware, so sensors are matched by name.
2. **Sentinel filtering**: values equal to `INVALID_WIALON_VALUE = -348201.3876`, or `<= -100000`, are dropped (`is_valid_sensor_value()`).
3. **Live motion state** (`status.calculate_status`): `speed > 0` gives `MOVING`; otherwise ignition on gives `IDLING`; otherwise `STOPPED`. Connection status: `STABLE` for messages within 10 minutes, `DELAYED` beyond that, and `OFFLINE` after `CONN_DELAYED_SECONDS` (default 12 h) or with no message at all.
4. **Reports are authoritative** for distance, trip counts, engine hours and idle totals. Raw-message segmentation (`stop_tolerance_seconds = 60`) is kept for history, route, safety and deep analytics only. The AI trips tool was switched to the trips report in `0040ae0`, because segmentation gave counts that differed from the dashboard.
5. **Unknown is `null`, never `0`**:
   - An unreadable Wialon cell, a missing row or an incomplete sub-row list yields `null` plus a flag: `incomplete_fields`, `column_warnings`, `events_complete`, `idle_events_complete`.
   - Period-derived idle metrics (count, longest, average) are `null` unless the event list is complete.
   - The UI shows "—", "unavailable" or "partial", never a fabricated 0.
6. **Incomplete results are not cached**: unit trips, unit idle and dashboard mileage.
7. **Time**: compute in UTC epoch seconds. Named periods resolve in UAE time (UTC+4, no DST). Format for display only.

---

## 6. Authentication Decisions

- There is **no local or demo login fallback**. Without Supabase configuration, sign-in fails with a clear error and no API call can succeed.
- The backend validates each token with Supabase `/auth/v1/user`, caches it for 5 minutes, and requires a `profiles` row with role `admin`, `manager`, `fleet_manager` or `viewer`. Fuel import excludes `viewer`.
- The "Remember me" checkbox is currently a no-op: Supabase always persists the session.

---

## 7. Things Explicitly NOT to Change

1. Do **not** hardcode vehicle IDs, names, counts, sensor IDs or group IDs.
2. Do **not** replace report-based metrics with message segmentation.
3. Do **not** expose the Wialon token, Supabase service-role key or Groq key to the client.
4. Do **not** add live telemetry charts to the Landing page. It uses static screenshots.
5. Do **not** change API response envelopes without updating `fleetApi.js`.
6. Do **not** turn unknown values into `0`.

---

## 8. Current Limitations & Technical Debt

- **Per-process state**: caches, Wialon sessions and report locks live in memory. On Vercel (several possible instances) the bounds are 30 s for fuel figures after an import, 60 s for the fuel unit list and 120 s for today's telematics per instance; move caches to Redis before needing tighter cross-instance freshness.
- **Cold loads**: the fleet alerts build takes about 30-37 s cold. It is no longer pre-built at startup (no page reads alerts; the AI chat tool builds them on demand), so the first alerts question after a restart waits for it.
- **Groq limits**: tool results are truncated to about 12k characters to stay under the account's 8,000 tokens-per-minute request budget (HTTP 413 was measured live before the cap).
- **Proposed fuel dedup**: `Backend/sql/proposed_fuel_dedup.sql` has not been applied by code.
- **Deployment config**: the root `vercel.json` defines one Vercel project with `backend` (FastAPI, Vercel Function, 300 s max duration) and `frontend` (Vite, SPA rewrite) services. There is no `render.yaml`. Neither has been confirmed against a live deployment.
- **Geofences**: evaluated client-side only.

---

## 9. Roadmap

- Driver scorecards (harsh braking/acceleration, speeding).
- Automated alerts and notifications.
- Predictive maintenance from voltage and engine trends.
- Scheduled fleet briefings built on the existing AI tools.
