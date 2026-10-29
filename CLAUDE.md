# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

ATS Fleet Management Platform: a FastAPI backend that brokers the Wialon telematics API (plus Supabase for auth and fuel data, Groq for an AI assistant) and a React 19 + Vite frontend. For product and design context see `README.md`, `ARCHITECTURE.md`, `RULES.md`, `MEMORY.md`, `PRD.md`, `DESIGN.md` and `TASKS.md`. Those docs were last synced with the code at commit `0040ae0`; trust the code when they disagree. Latest test evidence is in `TASKS.md` (Verification Status).

## Commands

Backend (run from `Backend/`, Python 3.10+, Windows shell):

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload   # http://127.0.0.1:8000, logs into Wialon at startup
```

Frontend (run from `Frontend/`):

```bash
npm install
npm run dev     # http://localhost:5173
npm run lint    # eslint
npm run build   # must pass before release; requires a non-loopback absolute VITE_API_URL (enforced in vite.config.js)
```

### Tests

There is no pytest suite. `Backend/scripts/test_*.py` are standalone scripts that add `Backend/` to `sys.path`, use `fastapi.testclient.TestClient` plus `unittest.mock`, and assert/print `[PASS]`. Run one from `Backend/`:

```bash
python scripts/test_fuel_analytics.py
```

Most use `scripts/offline_guard.py`, which blocks outbound HTTP so a missing mock fails loudly. Exceptions that need live services: `test_ai_fuel_assistant.py` and three endpoint cases in `test_fuel_efficiency.py`. `test_fuel_efficiency_accuracy.py` and `test_fuel_preview.py` have no `OfflineGuard`. The `audit_*`, `verify_*`, `check_*`, `inspect_*`, `reconcile_*` and `wialon_*_inspector.py` scripts also hit live Wialon/Supabase and need a populated `Backend/.env`.

Frontend: `npm test` runs the offline Node unit tests (`src/**/*.test.js`). `npx playwright test` runs `tests/e2e/` against live Wialon/Supabase (it starts both servers and needs `E2E_EMAIL`/`E2E_PASSWORD` or `.env.e2e.local`), so it needs approval like any live test.

### Environment

Copy `Backend/.env.example` and `Frontend/.env.example`.

- Backend: `WIALON_TOKEN`, `WIALON_API`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (fuel DB writes), `GROQ_API_KEY` (AI chat), `CORS_ORIGINS`, `ENVIRONMENT`.
- Frontend: `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.

## Architecture

### Backend (`Backend/app/`)

- `main.py`: the FastAPI app and most routes (`/api/vehicles*`, `/api/dashboard/*`, `/api/idle/fleet-analytics`, `/api/fleet/*`, `/api/ai/chat`, `/api/groups`, `/api/drivers`). Holds `_cache`, a bounded thread-safe `TTLCache` (`app/utils/ttl_cache.py`, 128 entries, LRU) behind the `cached(key, ttl_seconds)` / `set_cache()` helpers: freshness is checked per call against the caller's `ttl_seconds` (and an entry's own shorter TTL), and entries are evicted after 600 s or their own TTL if longer. `fleet_analytics.py`, `auth.py` and `fuel/wialon_distance.py` keep their own `TTLCache` instances. Closed windows (ended before today's UAE midnight) are cached 1800 s; transiently partial report results 30 s; the unit trips and unit idle routes do not cache results flagged incomplete. Dashboard mileage and fleet trips share one Group Trips run per group and window (`group_trips_snapshot`). The fuel feature is mounted from `app/fuel/routes.py` under `/api/fuel`.
- `wialon.py`: the single gateway to Wialon (`wialon_request`, `login`). Manages a shared session id behind a lock, re-logs in on Wialon error 1 (invalid session), backs off on 1003 (concurrency limit), and raises `WialonAPIError`, which `main.py` maps to HTTP 502. Loads `Backend/.env`.
- `vehicles.py`: fleet discovery (`get_all_vehicles`) via `core/search_items` and `unit/calc_last`, plus registration-plate extraction from Wialon profile fields.
- `normalize.py` / `status.py`: turn raw Wialon messages and sensors into normalized fields. Sensors are resolved **by name**, never by ID. The invalid-value sentinel `INVALID_WIALON_VALUE = -348201.3876`, and any value `<= -100000`, is filtered out (`is_valid_sensor_value`). Status is MOVING / IDLING / STOPPED; trips and idle periods are segmented here.
- `wialon_reports.py`: runs Wialon's native report engine (unit/group trips, engine and idle reports). The authoritative source for distance, engine hours and idle totals.
- `fleet_analytics.py`: cached fleet-wide health, utilization, trips, safety, alerts and group rollups behind `/api/fleet/*`.
- `fuel/`: fuel management backed by Supabase PostgREST (tables `fuel_imports` and `fuel_transactions` in `db.py`). Covers Excel import (`importer.py` / `mapper.py`, via openpyxl), analytics, anomalies and efficiency. Efficiency joins fuel transactions with Wialon distance (`wialon_distance.py`), matching vehicles by registration plate. Pydantic models live in `schemas.py`.
- `ai/`: a Groq tool-calling loop (`assistant.py`, max 6 steps). Tools in `tools.py` are read-only wrappers over the deterministic fleet functions; answers must stay grounded in that data.
- `utils/`: env-number helpers, UAE-timezone datetime helpers (`to_iso_uae`) and status thresholds.

### Auth

- The backend middleware (`require_supabase_auth` in `main.py`) requires a Supabase bearer token on **every** `/api/*` route except `/api/health`. It validates the token against Supabase `/auth/v1/user` and loads the profile (`auth.py`, which caches tokens for 5 minutes).
- The frontend `apiFetch` in `src/api/fleetApi.js` attaches and refreshes the Supabase token and throws if there is no session.
- There is no local/demo login fallback; Supabase must be configured, and the user needs a `profiles` row with role `admin`, `manager`, `fleet_manager` or `viewer` (fuel import excludes `viewer`).
- CORS defaults to localhost:5173 only when `ENVIRONMENT` is not `production`, and `*` is rejected.
- `/docs`, `/redoc` and `/openapi.json` are served only when `ENVIRONMENT=development` is set explicitly; unset or any other value disables them.

### Frontend (`Frontend/src/`)

- All backend calls go through `api/fleetApi.js`, which also centralizes HTTP error-to-message mapping. Do not call `fetch` directly from pages.
- Routes are in `App.jsx`. Operational pages are wrapped in `components/Auth/ProtectedRoute`; fuel pages live under `/fuel/*`. Contexts: `AuthContext` and `FuelFilterContext`.
- Stack: Tailwind v4 (via `@tailwindcss/vite`), Recharts, react-leaflet, framer-motion, lucide-react. The import alias `@` maps to `src/`.

## Project rules (from RULES.md / MEMORY.md)

- Never hardcode vehicle IDs, names, counts or sensor IDs. Discover them dynamically from Wialon.
- The Wialon token, Supabase service-role key and Groq key are backend-only. The frontend talks only to the backend, never to Wialon.
- Keep response envelopes (e.g. `{"count": n, "vehicles": [...]}`) stable. Changing one requires updating the frontend too.
- Compute in UTC epoch seconds; format for display only at the presentation layer.
- Every data component needs loading, empty and error states. Keep the Landing page free of live telemetry charts.
- Reuse the existing helpers in `normalize.py`, `status.py` and `fleetApi.js`.

## Project safeguards

- Read this file first, then inspect only the relevant files and dependencies.
- **Existing features first**: preserve all working features and the current architecture. Avoid unrelated refactoring, new dependencies, duplicate utilities and unnecessary files.
- **Minimal, focused changes**: make the smallest change that fully solves the requested problem.
- **Real-data accuracy**: use real backend data. Never put mock data, hardcoded business values or placeholder implementations into production code.
- **Evidence-based performance fixes**: base performance changes on measured evidence (`PERF` logging and trace IDs in `app/performance_diagnostics.py` and the `main.py` middleware), not guesses.
- **Mocked tests by default**: when tests are authorized, run mocked/offline tests (patched `wialon_request` / `login`, Supabase helpers and auth) first.
- **Explicit approval required** before: running live Wialon/Supabase tests, installing packages, committing, pushing or deploying. Do not run tests at all unless authorized.
- Do not duplicate work another agent has already completed.
- Report verified facts separately from assumptions, and reference changed or affected files and lines.


## Graphify Code Intelligence

- **When to use:** For codebase exploration, debugging, data-accuracy audits, performance investigations, and changes involving multiple files, consult the Graphify code graph before investigating dependencies.
- **Trace dependencies:** Use Graphify to identify relationships between frontend components, API endpoints, backend functions, shared utilities, data sources, and affected pages.
- **Data-accuracy investigations:** Trace each dashboard metric from its frontend display through the API and backend calculations to its original data source. Verify findings against actual source code and, when authorized, real data.
- **Change-impact analysis:** Before modifying shared functions, caching, API contracts, normalization, or calculations, use the graph to identify potentially affected components and dependencies.
- **Simple tasks:** Do not invoke Graphify unnecessarily for isolated edits, simple styling changes, or straightforward questions.
- **Graph freshness:** If the graph is missing, stale, incomplete, or inconsistent with the source code, inspect the actual files. Refresh the graph when necessary and only through supported Graphify commands.
- **Semantic coverage:** Check whether relevant documentation and code are represented in the graph. Do not assume indexing is complete when extraction has failed or skipped files.
- **No false confidence:** Graphify describes code structure and relationships; it does not prove runtime behavior, calculation correctness, or live-data accuracy. Validate conclusions independently.
- **Safe operation:** Do not modify application files merely to build or refresh the graph. Follow all existing project safeguards, approval requirements, testing restrictions, and no-commit/push/deploy rules.
- **No unnecessary repetition:** Reuse the existing graph when it is sufficiently current. Do not rebuild it for every task.
- **Reporting:** When Graphify materially informs an investigation, state what was traced, identify relevant files and lines, and distinguish verified findings from assumptions.




## Subagent delegation

Subagents are optional, not automatic. The main agent handles simple and routine tasks directly. For a complex task that genuinely benefits from specialized or independent work, invoke only the one specialist it needs. Ask the user before launching subagents for broad or expensive investigations.

Project agents are defined in `.claude/agents/`: `backend-wialon-specialist`, `frontend-specialist`, `test-performance-specialist` and `code-reviewer`. Each scope below says when that agent *may* be used, not when it must be.

- **`backend-wialon-specialist`**: FastAPI routes, Wialon integration (`wialon.py`, sessions, errors), report processing (`wialon_reports.py`), vehicle status (`status.py`), normalization (`normalize.py`), caching (`_cache`, `fleet_analytics.py`) and backend data accuracy (mileage, idle time, trips, fuel efficiency).
- **`frontend-specialist`**: React, Vite, Tailwind, UI components and pages, routing (`App.jsx`), API integration through `api/fleetApi.js`, and frontend performance.
- **`test-performance-specialist`**: regression tests (`Backend/scripts/test_*.py`), performance bottlenecks, caching behaviour, redundant or duplicate requests (backend Wialon fan-out or frontend fetches), and test strategy.
- **`code-reviewer`**: independent, read-only review when warranted by significant changes, such as multi-file edits, API contract changes, or auth, caching or data-accuracy logic.
- **Full-stack work**: use only the specialists the task needs, and never multiple agents for routine changes. Run independent investigations in parallel only when that clearly helps (e.g. a backend root-cause search alongside a frontend consumer audit).
- **Proportionate delegation**: never launch agents by default. Give each agent one narrow, explicit objective with the relevant files and context. Never run two agents on the same investigation or let more than one agent edit the same files at once.
- **Main agent responsibilities**: coordinate the work, check agent findings against the code before acting on them, implement or integrate the final changes, and run or verify approved tests itself. An agent's report is input, not proof.
- **Honest reporting**: never claim an agent was used unless it was actually invoked in the current session, and never present an agent's findings as verified unless the main agent confirmed them. When agents were used, say which ones and what each contributed.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.
