# Fleet Management Platform — Product Requirements Document (PRD)

---

## 1. Product Overview

The **ATS Fleet Management Platform** is a modern, web-based vehicle fleet intelligence and telematics command system designed for logistics operators, enterprise fleets, and transport service providers.

Built directly on top of the **Wialon GPS Tracking and Telematics API**, the platform transforms raw telematics packets into normalized operational KPIs, live geospatial tracking, dynamic driver assignment analytics, idle time monitoring, and cumulative mileage rankings across all active vehicles.

---

## 2. Problem Statement

Fleet operators often struggle with:
1. **Disjointed Telematics Data**: Raw GPS trackers output inconsistent sensor IDs, varying status codes, and unformatted message streams.
2. **Excessive Idle Times & Fuel Waste**: Vehicles left idling for extended periods increase operating costs and emissions without management visibility.
3. **Delayed Operational Awareness**: Lack of instant, real-time status breakdowns (Moving vs. Idling vs. Stopped) impairs dispatch decisions.
4. **Complex Configuration**: Legacy telematics platforms require manual vehicle mapping and rigid sensor configurations that fail when new hardware is deployed.

---

## 3. Product Vision

To deliver a unified, lightning-fast fleet operating system that provides zero-configuration live tracking, instant operational diagnostics, and proactive efficiency management for commercial fleets of any size.

---

## 4. Target Users

- **Fleet Managers**: Oversee overall vehicle utilization, health, and aggregate mileage trends.
- **Operations Managers**: Monitor fleet efficiency, schedule maintenance, and enforce idle policies.
- **Dispatchers**: Track live vehicle locations, assign active units, and verify routes in real time.
- **Supervisors & Safety Officers**: Identify idling violations, driver safety events, and vehicle alerts.
- **System Administrators**: Manage organization access, user roles, and telematics configurations.

---

## 5. User Personas

| Persona | Role | Key Goals | Primary Pain Points |
| :--- | :--- | :--- | :--- |
| **Marcus Vance** | Fleet Operations Director | Reduce fuel waste, ensure 95%+ fleet uptime, track annual mileage | Unreliable batch reports, lack of unified fleet KPIs |
| **Elena Rostova** | Chief Dispatcher | Locate nearest moving vehicle, monitor route progress | Slow map loads, inaccurate vehicle status |
| **Tariq Al-Mansoor**| Maintenance Supervisor | Spot high-mileage units and critical idle vehicles | Delayed alert notifications, sensor inconsistencies |

---

## 6. Core User Problems Addressed

- **Dynamic Vehicle Discovery**: Automatically detects newly registered Wialon units without code changes or manual indexing.
- **Standardized Status Segregation**: Uniform classification of all units into `MOVING`, `IDLING`, and `STOPPED`.
- **Actionable Idle Intelligence**: Fleet idle totals, events, hotspots and trends from Wialon's engine-hours report, with drill-down per vehicle.
- **Trustworthy Numbers**: Distance, trips, engine hours and idle come from Wialon's own report engine; data Wialon could not deliver is shown as unknown or partial, never as 0.
- **Granular Time Filters**: Mileage by Today, Yesterday, This Week, This Month, This Year or All Time; idle analytics by preset or custom From/To range (UAE time).
- **Fuel Visibility**: Imported fuel statements joined with Wialon distance for km/L efficiency, waste and anomaly detection.

---

## 7. Current Capabilities (Status Breakdown)

| Capability | Status | Implementation Details |
| :--- | :--- | :--- |
| **Dynamic Wialon Discovery** | `IMPLEMENTED` | `core/search_items` + `unit/calc_last`; "All Unit" group discovered by name. |
| **Live Vehicle Status & Telemetry** | `IMPLEMENTED` | GPS, speed, ignition and other sensors resolved by name; connection status STABLE / DELAYED / OFFLINE. |
| **Interactive Fleet Map** | `IMPLEMENTED` | Leaflet map with status markers, popups and vehicle detail drawer. |
| **Idle Analytics** | `IMPLEMENTED` | Wialon engine-hours report totals, events, hotspots, trend, CSV export, presets and custom range; partial data flagged. |
| **Mileage Rankings** | `IMPLEMENTED` | Group trips report per period; hardware odometer for All Time; six timeframes. |
| **Trips** | `IMPLEMENTED` | Unit and fleet trip counts from Wialon trips reports. |
| **Fleet Group Utilization** | `IMPLEMENTED` | Group breakdown with moving/idling/stopped distributions. |
| **Vehicle Health & Alerts** | `IMPLEMENTED` | Health scoring and fleet alerts (`/api/fleet/*`), alerts pre-warmed at startup. |
| **Fuel Management** | `IMPLEMENTED` | Excel import with preview, KPIs, trends, fuel types, efficiency (km/L), waste, anomalies, excluded records. |
| **AI Fleet Assistant** | `IMPLEMENTED` | Groq tool-calling chat over read-only fleet and fuel tools. |
| **Authentication & Route Guards** | `IMPLEMENTED` | Supabase Auth; backend validates every API call and requires a permitted profile role. |
| **Landing Portal** | `IMPLEMENTED` | Marketing page with static product screenshots; no live telemetry. |
| **Production Deployment** | `NOT VERIFIED` | Intended Render (backend) + Vercel (frontend); see README. |

---

## 8. Current Scope

- Supabase sign-in at `/login` and protected operational routes.
- Fleet Overview KPIs, map, today's trip/idle KPIs, mileage rankings and group utilization.
- Live Tracking, Idle Analytics, Vehicle Health, Alerts, Fuel & Performance.
- AI assistant for signed-in users.
- Dynamic Wialon discovery with no hardcoded vehicle, sensor or group IDs.
- Bounded in-memory caching tuned per endpoint (5 s live status to 600 s trends).

---

## 9. Non-Goals

- Direct GPS command execution to vehicle hardware (e.g. remote engine immobilizer).
- Automated customer invoice generation.
- Full custom report builder (planned for V2).
- Native iOS/Android mobile applications (the product is a responsive web app).

---

## 10. V2 Scope (Planned)

- **Geofencing & Polygon Alerts**: Visual boundary creation with enter/exit webhook alerts.
- **Fuel Sensor Calibration**: Complex polynomial fuel-tank sensor calibration curves (fuel today comes from imported statements).
- **Custom Report Designer**: Drag-and-drop report builder with scheduled PDF/CSV exports.
- **Driver Scorecards**: Multi-metric driver behavior rating (harsh acceleration, harsh braking, speeding).

---

## 11. Future AI Capabilities (Roadmap)

1. **AI Fleet Assistant** (implemented): Natural-language queries such as *"Which delivery trucks idled for more than 45 minutes today?"*, answered from deterministic tools.
2. **Predictive Maintenance Forecasting**: Machine learning models predicting alternator or battery failure based on voltage trends.
3. **Autonomous Route Anomaly Detection**: Unsupervised clustering detecting uncharacteristic route deviations.
4. **Automated Daily Fleet Briefings**: LLM-generated morning executive summaries delivered via email or Slack.

---

## 12. Core Workflows

### 12.1 Authentication & Onboarding

1. User navigates to `/login` or clicks **Sign In** on the Landing Page.
2. User provides email/username and password.
3. System validates credentials against Supabase Auth; the backend then validates the token and the user's `profiles.role` on every API call.
4. Upon authentication, user is redirected to `/dashboard` with the Supabase session persisted.

### 12.2 Live Fleet Monitoring

1. User views KPI metrics at top of `/dashboard`.
2. Map displays all units with color-coded status pins.
3. User selects a specific vehicle to view speed, coordinates, assigned driver, and last update timestamp.

### 12.3 Mileage & Utilization Audit

1. User selects a timeframe on the mileage ranking (`Today`, `Yesterday`, `This Week`, `This Month`, `This Year`, `All Time`), resolved in UAE time.
2. Backend reads the Wialon group trips report (or the hardware odometer for All Time) and returns sorted rankings; unreadable values are returned as unknown.
3. User filters by Category (`Delivery Vehicle`, `Passenger Vehicles`, etc.) and adjusts vehicle count limit.

---

## 13. Functional Requirements

- **FR-1**: Dynamic discovery of all AVL units without hardcoded counts or identifiers.
- **FR-2**: Sensor resolution by name matching (`Ignition`, `Battery`, `Driver ID`, `Harsh Acceleration`).
- **FR-3**: Real-time motion status calculation (`speed > 0` -> `MOVING`; ignition on -> `IDLING`; else `STOPPED`).
- **FR-4**: Secure, persisted Supabase sessions; every `/api/*` call except `/api/health` authenticated server-side with a permitted role.
- **FR-5**: Protected routing preventing unauthenticated access to operational tools.
- **FR-6**: Distance, trips, engine hours and idle from Wialon's report engine; missing or incomplete data returned as `null` with a completeness flag, never 0.
- **FR-7**: Fuel import restricted to write roles (`admin`, `manager`, `fleet_manager`).

---

## 14. Non-Functional Requirements

- **NFR-1 (Performance)**: Dashboard loads quickly from cached backend responses (target `< 1.2s` warm; not measured in the current verification). Cold report-heavy views depend on Wialon response times.
- **NFR-2 (Reliability)**: Automatic Wialon session re-login on `Error 1`, backoff on `Error 1003`; startup never blocks on Wialon.
- **NFR-3 (Responsiveness)**: Seamless UX across mobile (375px+), tablet (768px+), and desktop (1280px+).
- **NFR-4 (Security)**: Zero secrets in client-side code; all Wialon requests brokered through the backend.

---

## 15. Acceptance Criteria

Verified offline on 2026-10-22 (details in [TASKS.md](TASKS.md#verification-status)):

- [x] Frontend lint passes and unit tests pass (60/60).
- [x] Frontend production build succeeds with a valid `VITE_API_URL` and refuses a missing or loopback one.
- [x] Mocked backend regression scripts pass (31 of 33; the other two require live services).
- [x] Landing page contains no live telemetry charts (static screenshots only).
- [x] Auth middleware returns 401 without a bearer token, with CORS headers (`test_api_hardening.py`, in-process).

Not yet verified (require live Wialon/Supabase or a deployment):

- [ ] Unauthenticated users are redirected to `/login` (covered by the Playwright `public` project; not run).
- [ ] Same 401 behaviour confirmed against the deployed backend.
- [ ] Dashboard displays real Wialon fleet status, groups and mileage rankings for every timeframe.
- [ ] Production `/api/health` returns `healthy` with `wialon_configured: true`.
