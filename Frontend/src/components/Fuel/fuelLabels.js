// Pure label helpers for the fuel views (unit-tested in fuelLabels.test.js).

// Metrics where a lower value is better: "desc" lists the worst first.
export const LOWER_IS_BETTER = new Set(["efficiency", "fuel_efficiency_l_per_100km", "cost_per_km", "fuel_cost_per_km"])

export function sortOrderLabels(sortBy) {
    if (LOWER_IS_BETTER.has(sortBy)) {
        return { desc: "Highest first (worst)", asc: "Lowest first (best)" }
    }
    if (sortBy === "vehicle_identifier") {
        return { desc: "Z → A", asc: "A → Z" }
    }
    return { desc: "High → Low", asc: "Low → High" }
}

export const CLASSIFICATION_LABELS = {
    recorded: "Recorded",
    derived: "Derived",
    estimated: "Estimated",
}

// Classification of one field from a response's value_classification map; null when not provided.
export function classificationOf(map, field) {
    const kind = map && typeof map === "object" ? map[field] : null
    return CLASSIFICATION_LABELS[kind] ? kind : null
}

// Summary of the KPI scope reconciliation block; null when nothing was excluded.
export function excludedSummary(scope) {
    if (!scope || typeof scope !== "object") return null
    const count = Number(scope.excluded_transaction_count) || 0
    if (count <= 0) return null
    const part = (key) => Number(scope[key]?.transaction_count) || 0
    return {
        count,
        litres: Number(scope.excluded_litres) || 0,
        cost: Number(scope.excluded_cost) || 0,
        allCount: Number(scope.all_transaction_count) || 0,
        unmatched: part("unmatched"),
        ambiguous: part("ambiguous"),
        duplicates: part("suspected_duplicates"),
    }
}

export const EXCLUDED_REASON_LABELS = {
    unmatched: "No Wialon unit",
    ambiguous: "Several Wialon units",
    suspected_duplicate: "Suspected re-import",
}

// Number with fixed decimals, or "—" when the value is missing/not finite (a recorded 0 stays "0.00").
export function fixedOrDash(value, digits = 2) {
    if (value === null || value === undefined || value === "") return "—"
    const n = Number(value)
    return Number.isFinite(n) ? n.toFixed(digits) : "—"
}

// A missing fuel type is shown as such, never as an invented product.
export function fuelTypeLabel(fuelType) {
    const s = typeof fuelType === "string" ? fuelType.trim() : ""
    return s || "Unspecified"
}

// Refund/credit adjustments (negative litres) are labelled distinctly from purchases.
export function isRefundAdjustment(tx) {
    if (!tx || typeof tx !== "object") return false
    if (tx.transaction_kind) return tx.transaction_kind === "refund_adjustment"
    return Number(tx.litres) < 0
}

export const TREND_STATUS_LABELS = {
    ok: null,
    no_fuel: "No fuel purchased by tracked units",
    no_distance: "No Wialon distance",
    insufficient_distance: "Below minimum distance",
    non_positive_net_fuel: "Refunds exceed purchases",
    unavailable: "Distance unavailable",
}
