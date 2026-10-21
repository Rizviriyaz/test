# Fleet Management Platform — System Architecture

---

## 1. System Overview

The **ATS Fleet Management Platform** is architected as a decoupled, multi-tier telematics web application. It acts as an intelligent abstraction and normalization layer between raw Wialon GPS telematics servers and modern operational user interfaces.

```
+-------------------------------------------------------------------------------+
|                                CLIENT LAYER                                   |
|   React 19 + Vite + Tailwind CSS v4 + Recharts + Leaflet + Lucide Icons       |
+---------------------------------------+---------------------------------------+
                                        | (REST / JSON + JWT Bearer)
                                        v
+---------------------------------------+---------------------------------------+
|                                BACKEND LAYER                                  |
|   FastAPI (Python 3.11+) + In-Memory Caching + ThreadPoolExecutor             |
+-------------------+-----------------------------------+-----------------------+
                    |                                   |
                    v                                   v
+-------------------+-------------------+   +-----------+-----------------------+
|        WIALON INTEGRATION LAYER       |   |       AUTHENTICATION LAYER        |
|  Dynamic Unit Search + Sensor Mapping |   |   Supabase Auth (JWT + Profiles)  |
|  calc_last + load_interval Processing |   |   + V1 Local Session Fallback     |
+-------------------+-------------------+   +-----------------------------------+
                    |
                    v
+-------------------+-------------------+
|      EXTERNAL WIALON REMOTE GATEWAY   |
|      (hst-api.wialon.com / Remote API)|
+---------------------------------------+
```

---

## 2. Fundamental Integration Principle

$$\text{Wialon Remote API} \longrightarrow \text{Integration Layer} \longrightarrow \text{Data Normalization} \longrightarrow \text{REST Backend} \longrightarrow \text{Frontend Dashboard}$$

The frontend **never communicates directly with Wialon** and is completely decoupled from Wialon's proprietary schema formats (e.g. `nm`, `mu`, `sens`, `calc_last`, `avl_unit`).

---

## 3. Frontend Architecture

- **Core Engine**: React 19 with Vite bundler.
- **Styling**: Tailwind CSS v4 with bespoke glassmorphic tokens, dark mode gradients, and custom utility classes.
- **Routing**: React Router v7 with browser routing, protected route wrappers, and deep navigation fallbacks.
- **State Management**:
  - `AuthContext`: Manages user authentication, profile tokens, and session persistence.
  - Local state + `useMemo` hooks for high-performance chart aggregation and map coordinate transforms.
- **Mapping**: `react-leaflet` + `leaflet` with custom SVG status pins, dynamic centering, and popup cards.
- **Data Visualization**: `recharts` for vertical bar charts, line trends, and category distribution bar charts.

---

## 4. Backend Architecture

- **Framework**: FastAPI (ASGI) running on Uvicorn.
- **Concurrency**: `concurrent.futures.ThreadPoolExecutor` for parallelized vehicle history and telemetry retrieval.
- **Caching**: Per-process, bounded, thread-safe `TTLCache` instances (`app/utils/ttl_cache.py`): `_cache` in `main.py` (128 entries, LRU) used through `cached(key, ttl_seconds)` / `set_cache()`, plus separate caches in `fleet_analytics.py`, `auth.py` and `fuel/wialon_distance.py`.
  - Freshness is checked per call against the route's `ttl_seconds` (5 s to 600 s, mostly 60 s); entries are evicted after 600 s.
  - Cache keys include every parameter that changes the result; the unit trips, unit idle and dashboard mileage routes do not cache report results flagged incomplete (`incomplete_fields` / `column_warnings`).
- **Lifecycle Management**: `@asynccontextmanager lifespan` ensuring eager Wialon login on application boot.

---

## 5. Wialon Integration Architecture

The integration layer in `app/wialon.py` and `app/vehicles.py` handles:
1. **Session Lifecycle**: Automatic token exchange via `token/login` returning session token (`eid`).
2. **Session Self-Healing**: Automatically intercepts Wialon `Error 1` (Invalid session) and issues `login(force=True)` with transparent request retry.
3. **Concurrency Throttling**: Handles Wialon `Error 1003` (API concurrent limit) with exponential backoff.
4. **Dynamic Sensor Resolution**: Dynamically iterates over configured unit sensors (`sens` object) matching target sensor names (e.g. `"Ignition"`, `"Battery"`, `"Driver ID"`, `"Harsh Braking"`) rather than relying on brittle hardcoded sensor IDs.

---

## 6. Authentication Architecture

The platform supports dual-mode production authentication:
1. **Supabase Authentication**:
   - Uses `@supabase/supabase-js` client initialized with `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.
   - Manages JWT tokens, automatic token refresh, and user profile metadata from the `profiles` table.
2. **V1 Local Session Fallback**:
   - Active when Supabase credentials are not populated in development/staging.
   - Provides full session persistence (`localStorage` / `sessionStorage`), credential validation, role attribution (`admin`, `fleet_manager`), and clean sign-out capabilities.
3. **Route Protection**:
   - `ProtectedRoute.jsx` intercepts all operational routes (`/dashboard`, `/tracking`, `/idle-analytics`, etc.) and redirects unauthenticated users to `/login`.

---

## 7. Database Architecture

- **V1 Active State**: In-memory caching and session store + external Wialon AVL unit storage.
- **Supabase PostgreSQL Schema (when enabled)**:
  ```sql
  create table public.profiles (
      id uuid references auth.users on delete cascade primary key,
      email text unique not null,
      full_name text,
      role text default 'fleet_manager',
      organization text default 'ATS Fleet Operations',
      created_at timestamp with time zone default timezone('utc'::text, now())
  );
  ```

---

## 8. Data & Telemetry Flow

```
[ Wialon GPS Device ]
       | (Cellular GPRS packet)
       v
[ Wialon Ingestion Server ]
       |
       v (Wialon unit/calc_last & messages/load_interval)
[ Backend: app/wialon.py ]
       |
       v (Raw JSON)
[ Backend: app/normalize.py ]
       | ---> Resolves valid sensor values (filters INVALID_WIALON_VALUE = -348201.3876 and any value <= -100000)
       | ---> Segments trips & idle intervals (stop_tolerance_seconds = 60)
       v
[ Backend: app/main.py REST Endpoints ]
       | (Standardized JSON: vehicle_id, vehicle_name, status, speed, mileage, idle_duration)
       v
[ Frontend: fleetApi.js -> Dashboard.jsx / Tracking.jsx ]
       |
       v
[ Rendered UI: Map pins, KPI counts, Mileage vertical bar charts ]
```

---

## 9. API Architecture

| Endpoint | Method | Purpose | Cache TTL |
| :--- | :--- | :--- | :--- |
| `/` | `GET` | API status and root greeting | — |
| `/health` | `GET` | Health check endpoint for uptime monitors | — |
| `/api/vehicles` | `GET` | Discovered fleet list with live position, status, and sensors | Live (10s poll) |
| `/api/vehicles/{id}` | `GET` | Granular vehicle profile, sensors, and state | Live |
| `/api/groups` | `GET` | Wialon fleet group mappings and member unit IDs | Live |
| `/api/vehicles/{id}/trips` | `GET` | Segmented trip history with distance, duration, max speed | 60s |
| `/api/vehicles/{id}/idle` | `GET` | Detailed continuous and historical idling periods | 60s |
| `/api/vehicles/{id}/efficiency`| `GET` | Speed distribution, idle percentage, driving duration | 60s |
| `/api/dashboard/fleet-health` | `GET` | Fleet status counts, sensor availability, total odometer | 300s |
| `/api/dashboard/summary` | `GET` | Fleet aggregate distance, trips, and safety violations | 300s |
| `/api/dashboard/mileage` | `GET` | Mileage rankings filtered by timeframe (`today`, `1w`, `1m`, `1y`, `all`) | 300s |
| `/api/dashboard/trends` | `GET` | Hourly progression points for fleet motion and mileage | 600s |

---

## 10. Security Boundaries

1. **Secret Isolation**: `WIALON_TOKEN` is strictly maintained server-side in `Backend/.env` and is never returned in API payloads.
2. **CORS Hardening**: Explicit origin whitelisting via `CORSMiddleware` with support for comma-delimited `CORS_ORIGINS`.
3. **Safe Error Masking**: Endpoints return standardized HTTP error responses without raw stack trace exposure.
4. **Input Sanitization**: Query parameters (`hours`, `timeframe`, `vehicle_id`) are clamped and validated against injection.

---

## 11. Scaling Strategy

- **10–100 Vehicles (Current V1)**: Handled via parallel `ThreadPoolExecutor` and in-memory caching.
- **100–1,000 Vehicles (V2 Target)**:
  - Transition in-memory cache to **Redis**.
  - Background worker (`Celery` / `Arq`) for periodic Wialon message polling.
  - TimescaleDB / ClickHouse for time-series telemetry archiving.
- **1,000+ Vehicles (Enterprise Target)**:
  - Kafka message queue ingestion from Wialon Data Streaming webhooks.
  - Read-replica backend clusters behind cloud load balancers.

---

## 12. Future AI Architecture

```
+-------------------------------------------------------------------------------+
|                                AI AGENT LAYER                                 |
|   FastAPI AI Gateway + LangChain / Semantic Router + LLM Tool Interface      |
+---------------------------------------+---------------------------------------+
                                        | (Structured Tool Calls)
                                        v
+---------------------------------------+---------------------------------------+
|                            DETERMINISTIC TOOLS                                |
|   - get_fleet_status(group)       - query_idle_violations(duration)          |
|   - get_mileage_ranking(timeframe) - calculate_route_anomaly(vehicle_id)      |
+---------------------------------------+---------------------------------------+
                                        |
                                        v
+---------------------------------------+---------------------------------------+
|                     NORMALIZED FLEET DATA LAYER                               |
|                     (PostgreSQL / TimescaleDB)                                |
+-------------------------------------------------------------------------------+
```
