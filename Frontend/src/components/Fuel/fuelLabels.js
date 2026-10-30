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
        partialSummaries: part("partial_period_summary"),
        summaryOverlaps: part("summary_dated_overlap"),
    }
}

export const EXCLUDED_REASON_LABELS = {
    unmatched: "No Wialon unit",
    ambiguous: "Several Wialon units",
    suspected_duplicate: "Suspected re-import",
    partial_period_summary: "Monthly summary, partial range",
    summary_dated_overlap: "Summary and dated rows overlap",
    summary_superseded_by_dated: "Summary replaced by full-month dated rows",
    dated_superseded_by_summary: "Dated row superseded by monthly summary",
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

// Skipped rows of an import result, preview or history record. Older history records have null counts
// (not recorded): returns "" rather than claiming 0 skipped rows.
export function skippedRowsLabel(record) {
    const ignored = record?.rows_ignored
    if (ignored === null || ignored === undefined || !(Number(ignored) > 0)) return ""
    const nonFuel = Number(record?.rows_non_fuel) || 0
    const other = Number(ignored) - nonFuel
    const parts = []
    if (other > 0) parts.push(`${other} blank/summary`)
    if (nonFuel > 0) parts.push(`${nonFuel} non-fuel payments/charges`)
    return `${parts.join(", ")} skipped`
}

// estimated_idle/driving_percentage are shares of FUEL (estimated litres / litres of vehicles with known idle hours),
// not of engine time: the label says so. Unknown -> null.
export function fuelShareNote(pct) {
    if (pct === null || pct === undefined || !Number.isFinite(Number(pct))) return null
    return `${Number(pct).toFixed(1)}% of fuel (estimated)`
}

// Statement row count. Monthly-summary rows are totals, not refuels: once any are in scope the count is "rows".
export function transactionCountLabel(count, summaryRows = 0) {
    if (count === null || count === undefined || count === "" || !Number.isFinite(Number(count))) return "—"
    const n = Number(count).toLocaleString("en-US")
    const summaries = Number(summaryRows) || 0
    if (summaries > 0 && summaries >= Number(count)) return `${n} monthly summary rows`
    return summaries > 0 ? `${n} rows incl. ${summaries.toLocaleString("en-US")} monthly summary` : `${n} refuels`
}

// Two-part split in percent, or null when the data cannot support one: a part unknown or negative, or a zero total
export function shareSplit(a, b) {
    const known = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0
    if (!known(a) || !known(b) || a + b <= 0) return null
    return { first: (a / (a + b)) * 100, second: (b / (a + b)) * 100 }
}
