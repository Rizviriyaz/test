# Fleet Management Platform — UI/UX Design System

---

## 1. Design Principles

1. **Clarity Over Clutter**: Present mission-critical fleet metrics at a glance with zero cognitive overload.
2. **Deterministic Speed**: Instant UI feedback, responsive chart interactions, and silky smooth transitions.
3. **Cohesive Visual Identity**: Consistent typography, radii, border treatments, and palette tokens across both marketing and authenticated operational views.
4. **Context-Rich Diagnostics**: Visual status badges, severity tiers, and interactive map pins that tell an immediate operational story.

---

## 2. Brand & Visual Direction

- **Primary Brand Color**: `#1C5E7A` (Deep Atlantic Teal)
- **Neutral Palette**:
  - Light Views: Slate 50 (`#f8fafc`) to Slate 900 (`#0f172a`)
  - Dark Mode / Marketing: Slate 950 (`#020617`), Zinc 900 (`#18181b`), Obsidian (`#141210`)
- **Status Accents**:
  - `MOVING`: Emerald 500 (`#10b981`), Mint Glow (`rgba(16,185,129,0.2)`)
  - `IDLING`: Amber 500 (`#f59e0b`), Warm Amber Glow (`rgba(245,158,11,0.2)`)
  - `STOPPED`: Slate 400 (`#94a3b8`), Slate Glow (`rgba(148,163,184,0.15)`)
  - `CRITICAL / ATTENTION`: Rose 600 (`#e11d48`), Red 50 (`#fef2f2`)

---

## 3. Landing Page

- **Hero Command Center**: Features an interactive dark SVG projection map showcasing discovered vehicles, animated status pulses, and live aggregate counters.
- **Removed Telemetry Chart**: The landing page **does NOT contain the live telemetry chart** in order to keep the marketing presentation balanced, lightweight, and focused on value proposition.
- **Navigation & Actions**: Includes ATS Logo, animated tab navigation, a dedicated **Sign In** action button, and a **Launch Dashboard** CTA.
- **Fixed Interactive Dock**: MacOS-style floating bottom dock with quick route transitions.

---

## 4. Login Page

- **Visual Architecture**: High-conversion SaaS authentication card set against a dark radial ambient gradient.
- **ATS Brand Identity**: Centered ATS Logo with teal glow accent header.
- **Interactive Controls**:
  - Email / Username input with icon prefix.
  - Password input with toggleable show/hide eye control.
  - "Remember me" checkbox with persistent session storage.
  - Submit button with animated `Loader2` state during authentication.
  - Error alert banner with safe, non-leaking user feedback.
  - **One-Click Demo Fill**: Quick-fill chip for instant local evaluation (`admin@ats-fleet.com` / `admin123`).

---

## 5. Main Dashboard Layout

```
+-------------------------------------------------------------------------------+
| SIDEBAR (56px collapsed / 240px hover expanded) | TOPBAR (Context + Profile) |
+-------------------------------------------------+-----------------------------+
| [ KPI Metrics Grid: Total Fleet | Moving | Idling | Stopped | Utilization % ] |
|-------------------------------------------------------------------------------|
| [ Left: Interactive Leaflet Fleet Map ] | [ Right: Idle Attention List ]      |
|-------------------------------------------------------------------------------|
| [ Top Vehicles by Mileage (Vertical Bar Chart + Timeframe & Category Filters)]|
|-------------------------------------------------------------------------------|
| [ Group Utilization Bar Chart ]         | [ Motion & Mileage Trends Chart ]   |
+-------------------------------------------------------------------------------+
```

---

## 6. Navigation, Sidebar & Topbar

- **Collapsible Hover Sidebar**:
  - Collapsed state (56px) shows clean icons.
  - Hover expanded state (240px) slides open with smooth cubic-bezier transition, revealing categorized navigation items: *Operations*, *Analytics*, *Reporting*.
  - Footer displays logged-in user profile, email, and one-click **Sign Out** button.
- **Topbar**:
  - Displays operational breadcrumbs, global search trigger, notifications counter bell, and user avatar pill.

---

## 7. KPI Metric Cards

- **Total Fleet**: Crisp count with discovered badge.
- **Moving Vehicles**: Emerald indicator with live moving count.
- **Idling Vehicles**: Amber indicator showing count of vehicles with ignition active but zero velocity.
- **Stopped Vehicles**: Slate indicator showing parked units.
- **Fleet Utilization**: Circular / percentage badge showing active operational ratio.
- **Average Moving Speed**: Dynamic speed aggregation across all units in motion.

---

## 8. Charts & Data Visualizations

- **Top Vehicles by Mileage**:
  - Vertical bar chart layout with rounded bar ends (`radius={[0, 8, 8, 0]}`).
  - Timeframe selector pill buttons: `Today` | `Last 1 Week` | `Last 1 Month` | `Last 1 Year` | `All Time`.
  - Category dropdown filter + Vehicle limit dropdown (`Top 5`, `Top 10`, `Top 15`, `All`).
  - Formatted tooltips displaying exact distance in kilometers with timeframe unit labels.
- **Category Utilization Chart**: Comparative bar chart evaluating moving/idling/stopped vehicle volume per fleet group.
- **24-Hour Trend Chart**: Area / line chart visualizing hourly mileage accumulation and motion progression.

---

## 9. Interactive Fleet Map

- **Leaflet Engine**: High-performance raster map with custom markers.
- **Marker Color Coding**:
  - Green moving vehicle pins with directional arrow indicators.
  - Amber idling vehicle pins with glowing pulse animation.
  - Grey parked pins with subtle border contrast.
- **Vehicle Detail Popup**: Shows vehicle name, group, exact speed (km/h), latitude/longitude coordinates, and driver ID.

---

## 10. Responsive Design & Breakpoints

| Breakpoint | Target Devices | Layout Adjustments |
| :--- | :--- | :--- |
| **Mobile (`< 640px`)** | Smartphones | Single-column KPI stack, simplified map viewport, mobile drawer menu |
| **Tablet (`640px – 1024px`)** | iPads / Tablets | 2-column KPI grid, stacked chart sections, condensed table views |
| **Desktop (`> 1024px`)** | Laptops / Monitors | Full multi-column grid, split map & idle attention layout, hover sidebar |

---

## 11. Accessibility (a11y)

- Semantic HTML5 landmark tags (`<header>`, `<nav>`, `<main>`, `<aside>`, `<footer>`).
- Explicit `aria-label` attributes on icon-only buttons (zoom controls, password visibility, dock icons).
- WCAG AA compliant color contrast ratios across all text and interactive status indicators.
- Full keyboard tab navigation support on login inputs, filter dropdowns, and button controls.
