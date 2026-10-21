# Fleet Management Platform — Project Memory

---

## 1. Project Identity
- **Project Name**: ATS Fleet Management Platform
- **Repository Context**: Advanced Technology Solutions (ATS) Telematics & Fleet Intelligence
- **Version**: V1 Production Hardened
- **Domain**: Real-time fleet telematics, GPS vehicle monitoring, operational analytics, and mileage intelligence.

---

## 2. Product Purpose
Transforms raw Wialon GPS tracker message streams into standardized operational metrics: real-time location mapping, vehicle motion state segregation (`MOVING`, `IDLING`, `STOPPED`), continuous idle duration diagnostics, dynamic driver assignments, and timeframe-based mileage rankings.

---

## 3. Current V1 Status
- **Backend**: FastAPI (Python 3.11+) with dynamic Wialon session management, thread-pool parallelization, in-memory TTL caching, and 14 REST endpoints.
- **Frontend**: React 19 + Vite + Tailwind CSS v4 + Recharts + Leaflet + Lucide Icons.
- **Authentication**: Dual-mode authentication (Supabase Auth + V1 Local Session fallback with route protection).
- **Hardening**: Secrets isolated, health check endpoints implemented, full documentation suite generated.

---

## 4. Current Stack

| Layer | Technology |
| :--- | :--- |
| **Frontend Framework** | React 19 (`react`, `react-dom`, `react-router-dom` v7) |
| **Styling & Icons** | Tailwind CSS v4 (`@tailwindcss/vite`), Lucide React |
| **Maps & Charts** | Leaflet, React-Leaflet, Recharts |
| **Authentication** | `@supabase/supabase-js` + Session Context Provider |
| **Backend Framework** | FastAPI, Uvicorn, Pydantic |
| **Telematics Gateway** | Wialon Remote API (`https://hst-api.wialon.com/wialon/ajax.html`) |
| **Concurrency & Cache**| `concurrent.futures.ThreadPoolExecutor`, In-Memory Thread-Safe Cache |

---

## 5. Wialon Integration Knowledge & SDK Methods

- **Login / Session**:
  - Service: `token/login` with `{"token": WIALON_TOKEN}`. Returns `eid` session token.
  - Automatic Recovery: Intercepts `Error 1` (Invalid Session) and executes `login(force=True)`.
  - Rate Limiting: Intercepts `Error 1003` (API Concurrent Limit) and retries with backoff.
- **Vehicle Discovery**:
  - Service: `core/search_items`
  - Params: `{"spec": {"itemsType": "avl_unit", "propName": "sys_name", "propValueMask": "*", "sortType": "sys_name"}, "force": 1, "flags": 5121, "from": 0, "to": 0}`
  - Returns: Array of all AVL units (`items`).
- **Telemetry & Sensor Calculation**:
  - Service: `unit/calc_last` with `{"itemIds": [vehicle_ids...]}`. Returns computed sensors, position (`pos`), mileage, engine hours.
- **History & Message Telemetry**:
  - Service: `messages/load_interval` with `{"itemId": vehicle_id, "timeFrom": start, "timeTo": end, "flags": 0x0000, "flagsMask": 0xFF00, "loadCount": 1000}`.
- **Group Mapping**:
  - Service: `core/search_items` with `itemsType: "avl_unit_group"`.

---

## 6. Normalization Decisions

1. **Sensor Resolution by Name**: Wialon does not guarantee fixed sensor IDs across hardware models. Sensors are resolved dynamically by matching `sensor_config.get("n", "").strip().lower() == target_name`.
2. **Sentinel Filtering**: Wialon outputs large negative sentinels for disconnected or uncalibrated sensors. The normalization layer filters `INVALID_WIALON_VALUE = -348201.3876` and any value `<= -100000` (`is_valid_sensor_value()`).
3. **Motion State Logic**:
   - `speed > 0` -> `MOVING`
   - `speed == 0` AND `ignition == 1` -> `IDLING`
   - `speed == 0` AND `ignition == 0` -> `STOPPED`
4. **Trip Segmentation**: Sequences of moving points separated by stops exceeding `stop_tolerance_seconds = 60` are segmented into individual trips.

---

## 7. Authentication & Route Protection Decisions

- Dual-mode architecture enables the platform to work immediately in local / demo mode (`admin@ats-fleet.com` / `admin123`) while being 100% prepared for production Supabase Auth with zero code alterations.
- `ProtectedRoute.jsx` intercepts all operations pages and redirects unauthenticated visits to `/login`.
- Session tokens are stored in `localStorage` when `rememberMe` is enabled, or `sessionStorage` for single-session browsing.

---

## 8. Things Explicitly NOT to Change

1. **Do NOT hardcode vehicle IDs or names**: Dynamic discovery must always be used.
2. **Do NOT remove backend telemetry processing**: Telemetry is critical for trip, idle, and speed calculations.
3. **Do NOT expose Wialon Token to client**: Must remain server-side.
4. **Do NOT re-add telemetry charts to the Landing Page**: Landing page must remain lightweight and balanced.
5. **Do NOT alter existing REST API contract envelopes** without cross-layer coordination.

---

## 9. Current Limitations & Technical Debt

- **In-Memory Caching**: Cache is currently local in-memory. For multi-instance horizontal scaling, transition to Redis.
- **Message Limit**: `messages/load_interval` currently caps at 1,000 points per batch. Multi-page iteration should be added in V2 for 90-day audits.
- **Geofence Evaluation**: Currently evaluated client-side on Leaflet; server-side polygon containment can be added in V2.

---

## 10. Future AI Roadmap

- **AI Fleet Assistant**: Natural language querying of live telemetry and driver assignments.
- **Predictive Anomaly Detection**: Identifying erratic speed fluctuations or unusual fuel drops.
- **Automated Fleet Reports**: Scheduled LLM summaries generated from trip analytics.
