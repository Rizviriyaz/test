# ATS Fleet Management Platform

> **Production-grade fleet telematics command center** powered by Wialon GPS tracking, real-time vehicle telemetry, and Supabase authentication.

---

## Overview

ATS Fleet is a full-stack web application that provides live fleet visibility, operational analytics, and vehicle health monitoring for logistics and transport operations. It integrates directly with the Wialon telematics platform via API to deliver real-time vehicle data.

---

## Features

- 🗺️ **Live Fleet Map** — Real-time vehicle positions with Leaflet.js
- 📊 **Operational Dashboard** — Fleet status KPIs, mileage rankings, utilization charts
- 🚛 **Vehicle Tracking** — Trip history, speed, idle duration, route playback
- 💤 **Idle Analytics** — Identify and analyze idle vehicles to reduce fuel waste
- 🔐 **Authentication** — Supabase Auth (or local session fallback for V1)
- 📡 **Live Telemetry** — Real-time vehicle status (Moving / Idling / Stopped)
- 🏷️ **Fleet Groups** — Dynamic Wialon group-based vehicle categorization
- 📈 **Mileage Filters** — Today / Last 1 Week / Last 1 Month / Last 1 Year / All Time

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite, TailwindCSS v4, Recharts, Leaflet |
| Backend | Python, FastAPI, Uvicorn |
| Telematics | Wialon Remote API (hst-api.wialon.eu) |
| Authentication | Supabase Auth (with local session fallback) |
| Maps | Leaflet.js + OpenStreetMap |

---

## Architecture

```
Frontend (React + Vite)          Backend (FastAPI)            Wialon API
        │                               │                          │
  Login / Dashboard  ──── REST ────►  /api/vehicles       ──►  Vehicle Data
  Tracking / Analytics              /api/dashboard/*       ──►  Trip History
  Fleet Map                         /api/groups             ──►  Telemetry
        │                               │
  Supabase Auth  ◄────────────────── (optional)
```

---

## Environment Variables

### Backend — `Backend/.env`

```env
WIALON_TOKEN=your_wialon_sdk_token_here
WIALON_API=https://hst-api.wialon.eu/wialon/ajax.html
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### Frontend — `Frontend/.env`

```env
VITE_API_URL=http://127.0.0.1:8000
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=your_supabase_anon_key_here
```

> ⚠️ **Never commit `.env` files.** Use the `.env.example` templates.

---

## Local Development Setup

### Prerequisites

- Python 3.10+
- Node.js 18+
- A Wialon account with SDK token
- (Optional) Supabase project for production auth

### Backend

```bash
cd Backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env         # Fill in your Wialon credentials
uvicorn app.main:app --reload
```

Backend runs at: `http://127.0.0.1:8000`

### Frontend

```bash
cd Frontend
npm install
cp .env.example .env         # Fill in API URL and optional Supabase keys
npm run dev
```

Frontend runs at: `http://localhost:5173`

---

## Authentication

The platform supports two authentication modes:

| Mode | When Active | Behaviour |
|------|-------------|-----------|
| **Supabase Auth** | `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` are set | Real user accounts via Supabase Auth |
| **Local Fallback** | No Supabase env vars | Any valid email + 6-char password works (V1 demo mode) |

To create users with Supabase enabled: go to **Supabase Dashboard → Authentication → Users → Add user**.

---

## Wialon Integration

- Vehicle discovery is fully **dynamic** — no vehicle IDs are hardcoded
- The backend authenticates with Wialon at startup using the SDK token
- All vehicle data (position, speed, status, trips, mileage) is fetched live from Wialon
- The Wialon token lives **only on the backend** and is never exposed to the frontend

---

## Security Notes

- Wialon credentials are backend-only (never exposed to the browser)
- Supabase **anon key** is safe for frontend use (it is the public/publishable key)
- Supabase **service-role key** must never be in frontend code
- All `.env` files are excluded from version control via `.gitignore`

---

## Production / Deployment

1. Set all required environment variables on your server
2. Build the frontend: `cd Frontend && npm run build` (outputs to `Frontend/dist/`)
3. Serve the backend with: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
4. Serve the frontend `dist/` via Nginx, Vercel, or similar

---

## V1 Scope

- ✅ Live vehicle tracking and status
- ✅ Fleet dashboard with KPIs and charts
- ✅ Vehicle idle analytics
- ✅ Trip history and route visualization
- ✅ Wialon group-based fleet categorization
- ✅ Mileage rankings with time filters
- ✅ Supabase authentication (with local fallback)
- ✅ Responsive UI with dark/light themes

---

## Future Roadmap

- AI-powered predictive maintenance
- Driver behaviour scoring
- Fuel consumption analytics
- Automated alerts and notifications
- Multi-tenant fleet management
- Mobile app (React Native)

---

© Advanced Technology Solutions. All rights reserved.
