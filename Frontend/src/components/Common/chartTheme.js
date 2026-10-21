/**
 * Shared Recharts styling. One accent for single-series charts; a fixed categorical
 * order (validated for colour-vision deficiency on white) for pies/donuts with a legend.
 * Status colours (moving / idling / stopped / offline) are never used as series colours.
 */

export const SERIES_COLOR = "#33758F"

// Assign in this order, never cycled by rank; past six slots, fold into "Other".
export const CATEGORICAL = ["#1479A0", "#E0702E", "#2FA88A", "#C98E0E", "#5A63C9", "#C4507F"]
export const OTHER_COLOR = "#94A3B8"

export const categoricalColor = (index) => (index < CATEGORICAL.length ? CATEGORICAL[index] : OTHER_COLOR)

export const GRID_STROKE = "#EEF1F4"
export const AXIS_LINE = "#CBD5E1"
export const TICK = { fontSize: 11, fill: "#64748B" }
export const CATEGORY_TICK = { fontSize: 11, fill: "#334155" }

export const TOOLTIP_STYLE = {
    borderRadius: 6,
    border: "1px solid #E2E8F0",
    boxShadow: "0 8px 24px -6px rgba(15, 23, 42, 0.16)",
    fontSize: 12,
    padding: "6px 10px",
}

export const TOOLTIP_CURSOR = { fill: "rgba(51, 117, 143, 0.06)" }
