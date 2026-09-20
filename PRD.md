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
- **Actionable Idle Intelligence**: Dedicated tracking of vehicles idling > 5 minutes with direct drill-down links.
- **Granular Mileage Filters**: Timeframe filtering (`Today`, `Last 1 Week`, `Last 1 Month`, `Last 1 Year`, `All Time`) for odometer and trip distances.

---

## 7. Current V1 Capabilities (Status Breakdown)

| Capability | Status | Implementation Details |
| :--- | :--- | :--- |
| **Dynamic Wialon Discovery** | `IMPLEMENTED (V1)` | Uses `core/search_items` with flag `5121` to detect all units dynamically. |
| **Live Vehicle Status & Telemetry** | `IMPLEMENTED (V1)` | Resolves GPS, speed, ignition, battery, and Driver ID sensors via `unit/calc_last`. |
| **Interactive Fleet Map** | `IMPLEMENTED (V1)` | Leaflet map with custom status markers, popup cards, and vehicle coordinate rendering. |
| **Idle Duration Analytics** | `IMPLEMENTED (V1)` | Real-time idle timers, severity tiering (Normal, Monitor, Attention, Critical). |
| **Top Vehicles by Mileage** | `IMPLEMENTED (V1)` | Vertical bar chart with `today`, `1w`, `1m`, `1y`, and `all` timeframe filters. |
| **Fleet Category Utilization** | `IMPLEMENTED (V1)` | Group breakdown with moving/idling/stopped vehicle distributions. |
| **Authentication & Route Guards** | `IMPLEMENTED (V1)` | Supabase Auth integration + V1 persistent session fallback with protected routes. |
| **Landing Portal** | `IMPLEMENTED (V1)` | High-conversion SaaS landing page with dark command map and system status summary. |
| **Time-Series Dashboard Trends**| `IMPLEMENTED (V1)` | 24-hour fleet mileage and motion hourly progression area chart. |

---

## 8. V1 Scope

- Full authentication flow with `/login` and protected routes.
- High-level KPI summary cards (Total Fleet, Moving, Idling, Stopped, Utilization %, Average Speed).
- Real-time Leaflet map integration with vehicle clustering and status filters.
- Top Vehicles by Mileage with 5 timeframe presets and category filtering.
- Idle vehicle attention module with direct deep-links to `/idle-analytics`.
- Dynamic Wialon sensor discovery without hardcoded sensor IDs.
- In-memory 300s caching layer for high-throughput dashboard loads.

---

## 9. V1 Non-Goals

- Direct GPS command execution to vehicle hardware (e.g. remote engine immobilizer).
- Automated customer invoice generation.
- Full custom report builder (planned for V2).
- Native iOS/Android mobile applications (V1 is a responsive PWA/web app).

---

## 10. V2 Scope (Planned)

- **Geofencing & Polygon Alerts**: Visual boundary creation with enter/exit webhook alerts.
- **Fuel Sensor Calibration**: Complex polynomial fuel-tank sensor calibration curves.
- **Custom Report Designer**: Drag-and-drop report builder with scheduled PDF/CSV exports.
- **Driver Scorecards**: Multi-metric driver behavior rating (harsh acceleration, harsh braking, speeding).

---

## 11. Future AI Capabilities (Roadmap)

1. **AI Fleet Assistant**: Natural-language query interface (*"Which delivery trucks in Dubai idled for more than 45 minutes today?"*).
2. **Predictive Maintenance Forecasting**: Machine learning models predicting alternator or battery failure based on voltage trends.
3. **Autonomous Route Anomaly Detection**: Unsupervised clustering detecting uncharacteristic route deviations.
4. **Automated Daily Fleet Briefings**: LLM-generated morning executive summaries delivered via email or Slack.

---

## 12. Core Workflows

### 12.1 Authentication & Onboarding
1. User navigates to `/login` or clicks **Sign In** on the Landing Page.
2. User provides email/username and password.
3. System validates credentials against Supabase Auth (or V1 session store).
4. Upon authentication, user is redirected to `/dashboard` with session state persisted.

### 12.2 Live Fleet Monitoring
1. User views KPI metrics at top of `/dashboard`.
2. Map displays all units with color-coded status pins.
3. User selects a specific vehicle to view speed, coordinates, assigned driver, and last update timestamp.

### 12.3 Mileage & Utilization Audit
1. User selects timeframe filter on **Top Vehicles by Mileage** (`Today`, `Last 1 Week`, `Last 1 Month`, `Last 1 Year`, `All Time`).
2. Backend computes trip/odometer distances and returns sorted rankings.
3. User filters by Category (`Delivery Vehicle`, `Passenger Vehicles`, etc.) and adjusts vehicle count limit.

---

## 13. Functional Requirements

- **FR-1**: Dynamic discovery of all AVL units without hardcoded counts or identifiers.
- **FR-2**: Sensor resolution by name matching (`Ignition`, `Battery`, `Driver ID`, `Harsh Acceleration`).
- **FR-3**: Real-time motion status calculation (`speed > 0` -> `MOVING`; `ignition == 1` -> `IDLING`; else `STOPPED`).
- **FR-4**: Secure session persistence with `rememberMe` support.
- **FR-5**: Protected routing preventing unauthenticated access to operational tools.

---

## 14. Non-Functional Requirements

- **NFR-1 (Performance)**: Dashboard initial load `< 1.2s` with cached backend responses.
- **NFR-2 (Reliability)**: Automatic Wialon session re-login on `Error 1` (Invalid Session).
- **NFR-3 (Responsiveness)**: Seamless UX across mobile (375px+), tablet (768px+), and desktop (1280px+).
- **NFR-4 (Security)**: Zero secrets in client-side code; all Wialon requests brokered through the backend.

---

## 15. Acceptance Criteria for V1

- [x] Unauthenticated users are redirected to `/login` when accessing `/dashboard`.
- [x] Landing page contains no live telemetry charts, preserving balanced SaaS presentation.
- [x] Dashboard displays real Wialon fleet status, vehicles, groups, and mileage rankings.
- [x] Mileage filters (`Today`, `Last 1 Week`, `Last 1 Month`, `Last 1 Year`, `All Time`) function smoothly.
- [x] Frontend builds with zero compile errors (`npm run build`).
- [x] Backend responds to `/health` and all `/api/*` endpoints with HTTP 200.
