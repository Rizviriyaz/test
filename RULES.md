# Fleet Management Platform — Engineering Rules & Standards

---

## 1. Coding Rules

1. **Maintainable & Modular Code**: Keep components focused on a single responsibility. Avoid monolith files where sub-components can be extracted cleanly.
2. **Small Focused Edits**: Make minimal, non-destructive modifications. Never rewrite working components for stylistic preferences.
3. **Preserve Existing Utilities**: Reuse existing normalization functions (`normalize.py`), status calculators (`status.py`), and API clients (`fleetApi.js`).
4. **Explicit Naming Conventions**: Use descriptive variable and function names (e.g. `normalizedVehicles`, `selectedMileageGroup`, `calculate_idle_duration`).
5. **No Dead Code**: Remove temporary debug prints and unused variables before finalizing features.

---

## 2. Security Rules

1. **Zero Hardcoded Secrets**: NEVER commit API tokens, passwords, or session keys into source control. All credentials must reside in `.env` files.
2. **Server-Side Token Isolation**: NEVER expose `WIALON_TOKEN` to the browser or frontend bundle. All telematics calls must be brokered through backend endpoints.
3. **No Plaintext Passwords**: Passwords must be hashed or processed through secure OAuth/JWT providers (Supabase Auth).
4. **Input Validation & Sanitization**: Validate all query parameters (`hours`, `timeframe`, `vehicle_id`) to prevent injection or parameter tampering.
5. **Masked Error Responses**: Never return unhandled stack traces or internal server memory dumps in API error responses.
6. **CORS Enforcement**: Maintain strict origin whitelisting in production deployments.

---

## 3. API Rules

1. **Contract Consistency**: Maintain predictable JSON response envelopes:
   ```json
   {
       "count": 30,
       "vehicles": [ ... ]
   }
   ```
2. **Accurate HTTP Status Codes**:
   - `200 OK`: Successful retrieval.
   - `400 Bad Request`: Invalid input parameter.
   - `401 Unauthorized`: Missing or expired session.
   - `404 Not Found`: Target vehicle or resource does not exist.
   - `500 Internal Server Error`: Masked server failure.
3. **Wialon Error Resilience**: Catch Wialon errors (`Error 1`, `Error 1003`) and trigger automatic session re-authentication or exponential backoff without crashing the process.
4. **Intelligent Caching**: Apply TTL caching to computationally expensive aggregations (e.g. mileage rankings, historical trends).

---

## 4. Data Rules

1. **Dynamic Fleet Sizing**: NEVER hardcode vehicle counts, vehicle IDs, or unit names. Always discover units dynamically via Wialon search items.
2. **Dynamic Sensor Discovery**: NEVER hardcode sensor IDs. Dynamically match configured sensors by sensor name (`"Ignition"`, `"Battery"`, `"Driver ID"`).
3. **Handle Missing / Null Data Gracefully**: Vehicles offline or lacking specific sensors must output clean default states (`null`, `0`, or `"Unknown"`).
4. **Invalid Value Filtering**: Filter out Wialon sentinel invalid values (`INVALID_WIALON_VALUE = -348201.3876` and anything `<= -100000`, via `is_valid_sensor_value`) before mathematical aggregation.
5. **Timezone Standardization**: Store and compute timestamps in **UTC** Unix epoch seconds internally. Format to local user timezone exclusively at the presentation layer.

---

## 5. UI & UX Rules

1. **Consistent Design System**: Use curated palette tokens (`#1C5E7A` primary, slate/zinc neutrals, emerald/amber/rose status colors) and clean typography.
2. **Complete State Handling**: Every data-driven component MUST implement:
   - **Loading State**: Subtle spinner or animated pulse indicator.
   - **Empty State**: Clear explanation when filters yield 0 results.
   - **Error State**: Informative, non-disruptive feedback.
3. **Landing Page Balance**: The public landing page MUST remain clean, uncluttered, and free of unnecessary live telemetry charts.
4. **Accessible & Responsive**: Ensure 100% usability across mobile, tablet, and desktop breakpoints with accessible ARIA labels and keyboard navigation.

---

## 6. Production Rules

1. **Environment-Driven Configuration**: All environment configurations must load through `.env` with comprehensive `.env.example` templates.
2. **Health Monitoring**: Production deployments must provide `/health` and `/api/health` endpoints returning service status and dependencies health.
3. **Git Hygiene**: Strict adherence to `.gitignore` rules. Ensure `node_modules`, `venv`, `.env`, `dist`, and `__pycache__` are never committed.
4. **Build Verification**: Every release must pass `npm run build` with zero compile errors before deployment.

---

## 7. AI Rules (Future Architecture)

1. **Deterministic Separation**: AI and LLM agents are an **enhancement layer**, never a replacement for deterministic telematics calculations.
2. **Read-Only Telematics Access**: AI tools must strictly operate in read-only mode when querying live fleet status and history.
3. **Hallucination Prevention**: AI assistants must strictly ground responses in normalized API data returned by deterministic tools.
4. **Traceability**: All AI-assisted actions, queries, and summaries must be logged with timestamp and user ID.
