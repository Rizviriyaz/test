// Pure helpers for the Fuel Dashboard Data Coverage section (unit-tested in coverageRows.test.js).
// Both lists start from the dashboard (Wialon) fleet. Matching is the backend's own: a fleet unit has fuel records when
// the fuel efficiency vehicle list (the KPI scope) resolves a statement identifier to that unit's id, or when the
// backend reports that unit's rows as excluded from the KPIs (excluded_scope.overlap_conflicts). Nothing here compares
// plates or names against the statement.

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
 * - withoutDistance: fleet units with fuel in the KPI scope whose Wialon distance is not available (no_distance_data)
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
            note: Array.isArray(r.quality_warnings) && r.quality_warnings[0] ? r.quality_warnings[0] : null,
        })
    }

    return {
        notMatched: notMatched.sort(byName),
        excludedFromKpi: excludedFromKpi.sort(byName),
        withoutDistance: withoutDistance.sort(byName),
    }
}

// Case-insensitive search over the vehicle name, plate, statement identifier and groups
export function filterCoverageRows(rows, query) {
    const q = String(query || "").trim().toLowerCase()
    if (!q) return rows
    return rows.filter((r) => [r.name, r.plate, r.fuelIdentifier, ...(r.groups || [])]
        .some((s) => s && String(s).toLowerCase().includes(q)))
}
