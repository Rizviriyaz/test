// Pure helpers for the Fuel Dashboard Data Coverage section (unit-tested in coverageRows.test.js).
// Both lists start from the dashboard (Wialon) fleet. Matching is the backend's own: a fleet unit has fuel records when
// the fuel efficiency vehicle list (the KPI scope) resolves a statement identifier to that unit's id, or when the
// backend reports that unit's rows as excluded from the KPIs (excluded_scope.overlap_conflicts). Nothing here compares
// plates or names against the statement.
import { statementSplit } from "./fuelLabels.js"

const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v))

const groupsOf = (v) => {
    if (Array.isArray(v?.groups) && v.groups.length > 0) return v.groups.filter(Boolean)
    return v?.group ? [v.group] : []
}

const nameOf = (v) => String(v?.vehicle_name || v?.unit_name || v?.plate || v?.vehicle_id || "").trim()

const byName = (a, b) => a.name.localeCompare(b.name, "en", { numeric: true })

// Wialon unit ids whose fuel rows the backend excluded from the KPIs (monthly-summary / dated overlap). These units
// do have statement records, so they are never reported as having no matching record.
export function overlapUnitIds(excludedScope) {
    const conflicts = Array.isArray(excludedScope?.overlap_conflicts) ? excludedScope.overlap_conflicts : []
    return conflicts.map((c) => c?.wialon_unit_id).filter((id) => id !== null && id !== undefined)
}

// Monthly-summary rows excluded because the range covers only part of their month. The backend splits them off before
// resolving units, so their vehicles cannot be identified here; a count above 0 means the unmatched list may be wrong.
export function partialSummaryRowCount(excludedScope) {
    return Number(excludedScope?.partial_period_summary?.transaction_count) || 0
}

/**
 * Whether the fuel vehicle list's distance values are final:
 * - "pending": Wialon reports are still running (telematics_pending); distances are placeholders
 * - "unavailable": Wialon reports failed or timed out; no distance is known, so no vehicle can be classified
 * - "partial": reports answered but some values were unreadable; per-vehicle statuses are final
 * - "ready": every value was reported
 */
export function distanceReadiness(response) {
    if (response?.telematics_pending) return "pending"
    if (response?.telematics_status === "unavailable") return "unavailable"
    if (response?.telematics_status === "partial") return "partial"
    return "ready"
}

/**
 * fleet: /api/vehicles records; fuelVehicles: every row of /api/fuel/analytics/efficiency/vehicles for the period.
 * options.ambiguousUnitNames: Wialon unit names the backend lists as candidates of an ambiguous statement identifier;
 * options.excludedUnitIds: units whose rows the backend excluded from the KPIs (see overlapUnitIds).
 * Each fleet unit appears at most once, in at most one list:
 * - notMatched: fleet units with no statement record resolved to them (ambiguous = a statement identifier names this
 *   unit among several candidates, so its fuel is not attributed)
 * - excludedFromKpi: fleet units whose statement records are all excluded from the KPIs (not "unmatched")
 * - withoutDistance: fleet units with fuel in the KPI scope and no usable Wialon distance (no_distance_data): `distance`
 *   0 when Wialon recorded no movement, null when the distance is unknown
 */
export function buildCoverage(fleet, fuelVehicles, { ambiguousUnitNames = [], excludedUnitIds = [] } = {}) {
    const fleetById = new Map()
    for (const v of fleet || []) {
        const id = v?.vehicle_id
        if (id === null || id === undefined || fleetById.has(id)) continue
        fleetById.set(id, v)
    }

    // The list is one row per Wialon unit; a repeated unit id keeps its first row and is never counted twice
    const fuelByUnit = new Map()
    for (const r of fuelVehicles || []) {
        const id = r?.wialon_unit_id
        if (id === null || id === undefined || fuelByUnit.has(id)) continue
        fuelByUnit.set(id, r)
    }

    const ambiguous = new Set((ambiguousUnitNames || []).map((n) => String(n || "").trim()).filter(Boolean))
    const excluded = new Set((excludedUnitIds || []).map(String))

    const notMatched = []
    const excludedFromKpi = []
    for (const [id, v] of fleetById) {
        if (fuelByUnit.has(id)) continue
        const row = { unitId: id, name: nameOf(v), plate: v.registration_plate || null, groups: groupsOf(v) }
        if (excluded.has(String(id))) excludedFromKpi.push(row)
        else notMatched.push({ ...row, ambiguous: ambiguous.has(nameOf(v)) })
    }

    const withoutDistance = []
    for (const [id, r] of fuelByUnit) {
        if (r.status !== "no_distance_data") continue
        const v = fleetById.get(id)
        // Only dashboard fleet vehicles are listed
        if (!v) continue
        withoutDistance.push({
            unitId: id,
            name: nameOf(v),
            plate: v.registration_plate || r.registration_plate || null,
            fuelIdentifier: r.vehicle_identifier || null,
            groups: groupsOf(v),
            litres: num(r.litres),
            cost: num(r.total_cost),
            // The API's own value: 0 is a distance Wialon recorded (no movement); null or missing is unknown
            distance: num(r.distance_km),
            note: Array.isArray(r.quality_warnings) && r.quality_warnings[0] ? r.quality_warnings[0] : null,
        })
    }

    return {
        notMatched: notMatched.sort(byName),
        excludedFromKpi: excludedFromKpi.sort(byName),
        withoutDistance: withoutDistance.sort(byName),
    }
}

const WITHOUT_WIALON_REASONS = new Set(["unmatched", "ambiguous"])

/**
 * Excel records without a matching Wialon vehicle: the /analytics/excluded groups (one per identifier and reason) whose
 * reason is unmatched (no Wialon unit) or ambiguous (several candidate units, or a plate-code conflict). Suspected
 * duplicates and partial-month summaries are other exclusions and never listed here. These rows are statement records,
 * disjoint from the matched fleet, so their litres and cost are never also counted in the other two lists.
 * Largest volume first. Each row keeps its own backend value (null litres/cost stay unknown, never 0); the list's totals
 * come from withoutWialonTotalsOf, not from a sum of these separately rounded rows.
 */
export function buildExcelWithoutWialon(groups) {
    const rows = []
    for (const g of groups || []) {
        if (!WITHOUT_WIALON_REASONS.has(g?.reason)) continue
        rows.push({
            key: `${g.reason}|${g.vehicle_identifier}`,
            identifier: String(g.vehicle_identifier || "").trim(),
            reason: g.reason,
            plateCodeConflict: g.ambiguity_reason === "plate_code_conflict",
            candidates: Array.isArray(g.candidate_unit_names) ? g.candidate_unit_names.filter(Boolean) : [],
            records: Number(g.transaction_count) || 0,
            litres: num(g.litres),
            cost: num(g.cost),
        })
    }
    rows.sort((a, b) => (Math.abs(b.litres ?? 0) - Math.abs(a.litres ?? 0)) || a.identifier.localeCompare(b.identifier, "en", { numeric: true }))
    return { rows }
}

/**
 * Totals of the Excel-without-Wialon list from the fleet efficiency response (the KPI cards' figures): Excel statement −
 * matched fleet for records, litres (0.1 L) and cost (0.01 AED), the same rule as the cards (fuelLabels.statementSplit),
 * so the list's totals and the two cards always add up. null without the response; an unknown figure stays null.
 */
export function withoutWialonTotalsOf(efficiency) {
    if (!efficiency) return null
    const rows = Number(efficiency.transaction_count)
    const matchedRows = Number(efficiency.matched_row_count)
    return {
        records: Number.isFinite(rows) && Number.isFinite(matchedRows) ? rows - matchedRows : null,
        litres: statementSplit(efficiency.total_litres, efficiency.matched_litres, 1).rest,
        cost: statementSplit(efficiency.total_cost, efficiency.matched_cost, 2).rest,
    }
}

// Case-insensitive search over the vehicle name, plate, statement identifier and groups
export function filterCoverageRows(rows, query) {
    const q = String(query || "").trim().toLowerCase()
    if (!q) return rows
    return rows.filter((r) => [r.name, r.plate, r.fuelIdentifier, r.identifier, ...(r.groups || []), ...(r.candidates || [])]
        .some((s) => s && String(s).toLowerCase().includes(q)))
}
