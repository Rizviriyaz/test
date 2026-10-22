# ATS Fleet Management Platform

> Fleet telematics command center built on the Wialon GPS platform, with Supabase authentication, Supabase-backed fuel management and a grounded AI assistant.

---

## Overview

ATS Fleet is a full-stack web application for live fleet visibility, operational analytics, fuel management and vehicle health monitoring. A FastAPI backend brokers every call to the Wialon Remote API, Supabase and Groq. A React 19 + Vite frontend talks only to that backend.

Related documents:

| File | Contents |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, Wialon data flow, caching, API reference, date-range rules |
| [PRD.md](PRD.md) | Product scope, capabilities, requirements |
| [DESIGN.md](DESIGN.md) | UI/UX design system |
| [RULES.md](RULES.md) | Engineering rules |
| [MEMORY.md](MEMORY.md) | Project decisions, Wialon knowledge, known limitations |
| [TASKS.md](TASKS.md) | Delivery status and the latest verification results |
| [CLAUDE.md](CLAUDE.md) | Guidance for AI coding agents |

---

## Features

- **Fleet Overview** (`/dashboard`): status KPIs, live map, trip and idle KPIs for today, mileage rankings (Today, Yesterday, This Week, This Month, This Year, All Time), group utilization.
- **Fuel & Performance** (`/fuel`): Excel fuel-transaction import (preview, then commit), fuel KPIs, trend, fuel-type mix, efficiency (km/L joined with Wialon distance), waste intelligence, anomalies, excluded records and per-vehicle detail.
- **Live Tracking** (`/tracking`): map, vehicle detail drawer, trip history and route.
- **Idle Analytics** (`/idle-analytics`): fleet idle totals from Wialon's engine-hours report, idle events, location hotspots, trend, CSV export, date presets and a custom From/To range.
- **Vehicle Health** (`/vehicle-health`) and **Alerts** (`/alerts`): health scoring and fleet alerts.
- **AI Assistant**: a chat widget for signed-in users, answering from read-only fleet and fuel tools (Groq).
- **Authentication**: Supabase Auth on the frontend, with server-side token validation on every `/api/*` route except `/api/health`.

---

## Technology Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19, Vite 8, React Router 7, Tailwind CSS v4, Recharts, react-leaflet, framer-motion, lucide-react |
| Backend | Python 3.10+, FastAPI, Uvicorn, requests, openpyxl |
| Telematics | Wialon Remote API (default `https://hst-api.wialon.com/wialon/ajax.html`) |
| Auth and fuel data | Supabase (Auth, `profiles`, `fuel_imports`, `fuel_transactions` via PostgREST) |
| AI | Groq (`openai/gpt-oss-120b`) with tool calling |
| Hosting (intended) | Render (backend), Vercel (frontend). See [Deployment](#deployment) |

---

## Architecture

```text
Browser (React + Vite)                FastAPI backend                     External
        │                                    │
  Supabase JS sign-in ───────────────────────┼──────────────────────►  Supabase Auth
        │                                    │
  fleetApi.js ── REST + Bearer token ──►  auth middleware ── validate ►  Supabase /auth/v1/user
                                          /api/vehicles, /api/dashboard/*,
                                          /api/idle/*, /api/fleet/*   ──►  Wialon Remote API
                                          /api/fuel/*                 ──►  Supabase PostgREST + Wialon
                                          /api/ai/chat                ──►  Groq
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for details.

---

## Environment Variables

Templates: [Backend/.env.example](Backend/.env.example) and [Frontend/.env.example](Frontend/.env.example). Never commit real `.env` files; all are git-ignored.

### Backend (`Backend/.env`)

| Variable | Required | Purpose |
| --- | --- | --- |
| `WIALON_TOKEN` | Yes | Wialon SDK token (backend only) |
| `WIALON_API` | Yes | Wialon AJAX endpoint |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Yes | Server-side validation of user bearer tokens |
| `SUPABASE_SERVICE_ROLE_KEY` | For fuel | Fuel database reads/writes. Backend only, never in `VITE_*` |
| `GROQ_API_KEY` | For AI chat | AI assistant |
| `CORS_ORIGINS` | Yes in production | Comma-separated exact frontend origins. `*` is rejected at startup |
| `ENVIRONMENT` | Recommended | `production` disables localhost CORS defaults. Only `development` enables `/docs`, `/redoc`, `/openapi.json` |
| `LOG_LEVEL` | No | `INFO` (default) or `DEBUG` to see `PERF` timings |
| `WIALON_REPORT_RESOURCE_ID`, `WIALON_TEMPLATE_*` | No | Report resource/template IDs. Defaults match the ATS production account |
| `WIALON_GROUP_ALL_UNITS` | No | Force a specific "All Unit" group. Unset = discovered by name |
| `FUEL_*`, `WIALON_HISTORY_*`, `CONN_DELAYED_SECONDS` | No | Tuning with code defaults: import size limits, telematics cache TTLs, history paging, offline threshold (default 12 h) |

`PORT` and `HOST` in the template are informational: the start command sets the port.

### Frontend (`Frontend/.env`)

| Variable | Required | Purpose |
| --- | --- | --- |
| `VITE_API_URL` | Yes | Backend base URL. Production builds fail unless it is an absolute, non-loopback HTTP(S) URL |
| `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` | Yes | Supabase sign-in. The anon key is public by design |

---

## Local Development

Prerequisites: Python 3.10+, Node.js 18+, a Wialon SDK token, and a Supabase project with at least one user. Sign-in and every API call need Supabase; there is no local login fallback.

### Backend

```bash
cd Backend
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env           # fill in credentials; set ENVIRONMENT=development locally
uvicorn app.main:app --reload    # http://127.0.0.1:8000
```

The backend tries a Wialon login at startup and pre-warms the fleet alerts cache in the background. A Wialon outage does not block startup: login is retried on the next request.

### Frontend

```bash
cd Frontend
npm install
copy .env.example .env           # VITE_API_URL=http://127.0.0.1:8000 for local dev
npm run dev                      # http://localhost:5173
```

To create a user:

1. In **Supabase Dashboard → Authentication → Users → Add user**, create the user.
2. Add a row to `public.profiles` with the same `id` and a `role` of `admin`, `manager`, `fleet_manager` or `viewer`.

Without a permitted role, the backend returns 403 for every API call. `viewer` is read-only: fuel import needs `admin`, `manager` or `fleet_manager`.

---

## Testing

| Check | Command | Network |
| --- | --- | --- |
| Frontend lint | `cd Frontend && npm run lint` | Offline |
| Frontend unit tests | `cd Frontend && npm test` (Node test runner) | Offline |
| Frontend production build | `cd Frontend && npm run build` (needs `VITE_API_URL`) | Offline |
| Backend regression scripts | `cd Backend && python scripts/test_<name>.py` | Mocked (`TestClient` + patched Wialon/Supabase/Groq) |
| End-to-end (Playwright) | `cd Frontend && npx playwright test` | **Live** Wialon + Supabase; needs `E2E_EMAIL` / `E2E_PASSWORD` (or `Frontend/.env.e2e.local`) |
| Audit/verify scripts | `Backend/scripts/audit_*`, `verify_*`, `wialon_*_inspector.py`, etc. | **Live**; need a populated `Backend/.env` |

Many backend scripts install `scripts/offline_guard.py`, which blocks outbound HTTP so a missing mock fails loudly. Latest results are recorded in [TASKS.md](TASKS.md#verification-status).

---

## Deployment

The repository holds no `render.yaml` or `vercel.json`; Render and Vercel are configured in their dashboards. The settings below are what the code requires. They have **not** been confirmed against a live deployment from this repository.

### Backend on Render (web service)

| Setting | Value |
| --- | --- |
| Root directory | `Backend` |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/api/health` (public; reports `wialon_configured`) |
| Environment | All backend variables above, with `ENVIRONMENT=production` and `CORS_ORIGINS=https://<your-vercel-domain>` |

Run a single process. Caches, the Wialon session and report locks are per process, so extra workers multiply Wialon sessions and cold caches.

### Frontend on Vercel

| Setting | Value |
| --- | --- |
| Root directory | `Frontend` |
| Framework preset | Vite |
| Build command / output | `npm run build` / `dist` |
| Environment | `VITE_API_URL=https://<your-render-service>`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` |

`VITE_*` values are baked in at build time; redeploy after changing them.

### Post-deploy checks

1. `GET https://<backend>/api/health` returns `"status": "healthy"` and `"wialon_configured": true`.
2. `GET https://<backend>/api/vehicles` without a token returns 401. `/docs` returns 404 in production.
3. Sign in on the Vercel site, then confirm the dashboard loads with no CORS errors in the browser console.
4. Open a deep link such as `https://<frontend>/dashboard` directly and refresh it. The app uses client-side routing; if Vercel returns 404, add an SPA rewrite to `index.html` in the Vercel project settings.
5. Check backend logs for `Wialon session established at startup`.

---

## Security Notes

- Wialon token, Supabase service-role key and Groq key are backend-only.
- The Supabase anon key is safe for the frontend.
- Wialon errors map to HTTP 502 with a generic message; internal errors return a generic 500. Raw upstream text is logged, never returned.
- Time windows are bounded: 31 days for raw message history, 366 days for Wialon report endpoints.

---

## Roadmap

- Driver behaviour scoring and scorecards
- Automated alerts and notifications
- Predictive maintenance
- Shared cache (e.g. Redis) for multi-instance hosting
- Multi-tenant fleet management

---

© Advanced Technology Solutions. All rights reserved.
