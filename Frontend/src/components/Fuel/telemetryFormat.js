// Display helpers for Wialon telemetry values in the fuel views.
// The backend sends null for any measurement Wialon did not report readably; a recorded 0 stays 0.
// These helpers keep that distinction: unknown renders as "—", never as 0.

export const UNKNOWN = "—"

export function isKnownNumber(val) {
    return typeof val === "number" && Number.isFinite(val)
}

// First argument that is a known number (unlike `||`, a recorded 0 is kept), else null.
export function firstKnown(...vals) {
    for (const v of vals) {
        if (isKnownNumber(v)) return v
    }
    return null
}

export function formatMeasurement(val, decimals = 1, unit = "") {
    if (!isKnownNumber(val)) return UNKNOWN
    const text = val.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })
    return unit ? `${text} ${unit}` : text
}

// Axis tick label: plain below 1000 (so 250 L is not shown as "0k"), compact "k" above.
export function compactTick(val, unit = "") {
    if (val === null || val === undefined || val === "") return ""
    const n = Number(val)
    if (!Number.isFinite(n)) return ""
    const abs = Math.abs(n)
    const s = abs >= 1000 ? `${(n / 1000).toFixed(abs >= 10000 ? 0 : 1)}k` : `${Math.round(n)}`
    return unit ? `${s} ${unit}` : s
}

export function roundOrNull(val, decimals = 1) {
    if (!isKnownNumber(val)) return null
    const f = 10 ** decimals
    return Math.round(val * f) / f
}

// activity_breakdown carries a `status` only when it came from a Wialon report for a matched unit.
// Without it the breakdown is a placeholder whose zeros are not measurements.
export function isWialonActivity(activity) {
    return Boolean(activity && typeof activity.status === "string")
}

export function resolveActivityHours(summary, activity) {
    const act = isWialonActivity(activity) ? activity : {}
    return {
        drivingHours: firstKnown(summary?.driving_duration_hours, act.driving_duration_hours),
        idleHours: firstKnown(summary?.idle_duration_hours, act.idle_duration_hours),
        engineHours: firstKnown(summary?.engine_hours, act.engine_hours),
    }
}

// Collects completeness status and warnings from the vehicle summary and activity breakdown.
// Returns null when everything is complete and there is nothing to say.
export function collectTelemetryNotice(summary, activity) {
    const warnings = []
    const add = (w) => {
        if (typeof w === "string" && w && !warnings.includes(w)) warnings.push(w)
    }
    const statuses = []
    if (isWialonActivity(activity)) {
        statuses.push(activity.status)
        ;(Array.isArray(activity.warnings) ? activity.warnings : []).forEach(add)
    } else if (activity && summary?.status === "matched") {
        add("No Wialon activity report is available for this vehicle in the selected period.")
    }
    if (typeof summary?.telemetry_status === "string") statuses.push(summary.telemetry_status)

    let status = "complete"
    if (statuses.includes("unavailable")) status = "unavailable"
    else if (statuses.includes("partial")) status = "partial"
    if (status === "complete" && warnings.length === 0) return null
    return { status: warnings.length && status === "complete" ? "partial" : status, warnings }
}

// Trend points: keep fuel/cost as recorded, keep unknown distance/efficiency as null.
export function formatTrendPoint(d) {
    return {
        ...d,
        displayPeriod: d.period ? d.period.slice(5) : "",
        litres: roundOrNull(d.litres, 1) ?? 0,
        cost: roundOrNull(d.cost, 0) ?? 0,
        distance_km: roundOrNull(d.distance_km, 1),
        fuel_efficiency: roundOrNull(d.fuel_efficiency_l_per_100km, 1),
        cumulative_efficiency: roundOrNull(d.cumulative_l_per_100km, 1),
    }
}

export function trendNotice(res) {
    const status = typeof res?.telematics_status === "string" ? res.telematics_status : "complete"
    const warnings = Array.isArray(res?.data_warnings) ? res.data_warnings.filter((w) => typeof w === "string" && w) : []
    const unitsFailed = Array.isArray(res?.units_failed) ? res.units_failed.length : 0
    const mismatches = Array.isArray(res?.distance_reconciliation?.mismatches) ? res.distance_reconciliation.mismatches.length : 0
    if (status === "complete" && warnings.length === 0 && !unitsFailed && !mismatches) return null
    return { status, warnings, unitsFailed, mismatches }
}
