# Fleet Management Platform — UI/UX Design System

Source of truth for tokens: `Frontend/src/index.css` (`@theme` block and `type-*` utilities) and `Frontend/src/components/Common/chartTheme.js`. Reflects `main` at commit `0040ae0`.

---

## 1. Design Principles

1. **Clarity Over Clutter**: Present mission-critical fleet metrics at a glance with minimal cognitive load.
2. **Honest Data**: Unknown or partial Wialon data reads as "—", "unavailable" or "partial", never as 0. Numbers are tabular (`tabular-nums`).
3. **Cohesive Visual Identity**: Consistent typography, radii, borders and palette tokens across the marketing and operational views.
4. **Context-Rich Diagnostics**: Status badges, severity tiers and map pins that tell an immediate operational story. State never depends on color alone (marker + label).

---

## 2. Tokens

| Token | Value | Use |
| --- | --- | --- |
| `--color-brand` | `#33758F` (hover `#285E73`, light `#F0F6F9`, border `#D1E2EA`) | Primary actions, series color |
| `--color-canvas` | `#F4F6F8` | App background |
| `--color-status-moving` | `#059669` | MOVING |
| `--color-status-idling` | `#D97706` | IDLING |
| `--color-status-stopped` | `#94A3B8` | STOPPED |
| `--color-status-offline` | `#E11D48` | OFFLINE / critical |
| `--color-status-delayed` | `#7C3AED` | DELAYED (last seen Moving/Idling, telemetry older than 10 min; shows last known state). Last seen Stopped stays STOPPED until OFFLINE (12 h) |
| Chart categorical | `#1479A0`, `#E0702E`, `#2FA88A`, `#C98E0E`, `#5A63C9`, `#C4507F`; other `#94A3B8` | Multi-series charts |

- **Radius**: 4 px badges · 6 px controls · 8 px panels · 12 px dialogs.
- **Shadow**: `--shadow-pop` (menus, tooltips, expanded nav) and `--shadow-dialog` only.
- **Type**:
  - Inter for UI text.
  - Plus Jakarta Sans for page titles; Outfit as the display font.
  - Sizes: page title 18 → 20 px (sm+), section and card titles 14 px, body 12–13 px, table headers 11 px. Use the `type-*` utilities.

The app uses a light theme only.

---

## 3. Landing Page (`/`)

- Marketing page with the ATS logo and **Sign In** / **Launch Dashboard** actions.
- Static product screenshots (`src/assets/screenshots/`: fleet overview, live tracking, vehicle telemetry, fleet alerts).
- **No live telemetry charts or API calls.** The page must stay lightweight.

---

## 4. Login Page (`/login`)

- Centered ATS logo and sign-in card.
- Email and password inputs, with a show/hide password toggle (`Eye` / `EyeOff`).
- "Remember me" checkbox. It is currently a no-op, because Supabase always persists the session.
- Submit button with a `Loader2` spinner, and an error banner with safe messages (e.g. "Supabase authentication is not configured").
- No demo credentials or quick-fill.

---

## 5. Fleet Overview Layout (`/dashboard`)

```text
+--------+----------------------------------------------------------------------+
|        | Page header: "Fleet Overview" · "Live fleet status and today's activity" |
| SIDE   +----------------------------------------------------------------------+
| BAR    | Status KPIs · links to Live Tracking and Alerts                      |
|        | Today (trips, distance, idle)        | Idling over 5 minutes         |
|        | Top vehicles by mileage (timeframe + limit selectors)                |
|        | Fleet by group                       | Utilization by group          |
|        | Idle time today                                                      |
+--------+----------------------------------------------------------------------+
```

---

## 6. Navigation

- **Sidebar** (`components/Layout/Sidebar.jsx`):
  - Floating panel, 58 px collapsed and 244 px expanded on hover.
  - On small screens it is hidden off-canvas and opened from the page header.
  - Items: **Fleet Overview**, **Fuel & Performance**; then **OPERATIONS**: Live Tracking, Idle Analytics, Vehicle Health, Alerts.
  - The footer shows the signed-in user and **Sign Out**.
- **Page header** (`components/Common/PageHeader.jsx`): title, subtitle and refresh action. There is no separate topbar.
- **AI assistant**: floating chat widget, rendered only for signed-in users.

---

## 7. Charts & Data Visualizations

- **Top vehicles by mileage**:
  - Horizontal bar chart (`radius={[0, 3, 3, 0]}`).
  - Timeframe: Today, Yesterday, This Week, This Month, This Year, All Time.
  - Limit: Top 5, Top 10, Top 15, Show All.
  - Tooltips show kilometres with the timeframe's unit label.
- **Fleet / utilization by group**: comparative bar charts per Wialon group.
- **Idle trend** (Idle Analytics): hourly buckets up to 48 h, then daily buckets aligned to UAE midnight. Labelled "partial" when events are incomplete.
- **Fuel**: trend chart, fuel-type donut, efficiency gauge, waste intelligence and KPI strips that separate recorded from estimated values.
- Every data component has loading, empty and error states (`components/Common/StateViews.jsx`).

---

## 8. Interactive Fleet Map

- Leaflet raster map with custom status markers (moving, idling, stopped).
- Popups and the vehicle detail drawer show name, group, speed, coordinates, driver and last update.
- In the drawer, idle events read "Engine on HH:MM → HH:MM" with the idle duration within that period. The list shows "+" or "Partial" when Wialon events were not fully read, and "—" when the list could not be loaded.

---

## 9. Responsive Design & Breakpoints

| Breakpoint | Target Devices | Layout Adjustments |
| :--- | :--- | :--- |
| **Mobile (`< 768px`)** | Smartphones | Off-canvas sidebar, single-column cards, simplified map viewport |
| **Tablet (`768px – 1024px`)** | Tablets | 2-column KPI grid, stacked chart sections |
| **Desktop (`> 1024px`)** | Laptops / Monitors | Multi-column grid, split panels, hover-expand sidebar |

---

## 10. Accessibility (a11y)

- Semantic landmarks (`<header>`, `<nav>`, `<main>`, `<aside>`).
- `aria-label` on icon-only buttons and selectors (e.g. "Select mileage timeframe").
- Status conveyed by marker plus label, not color alone.
- `motion-reduce` variants on animated navigation.
- Keyboard navigation for login inputs, filters and buttons.
