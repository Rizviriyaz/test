import { test } from "node:test"
import assert from "node:assert/strict"
import { buildCoverage, distanceReadiness, filterCoverageRows, overlapUnitIds, partialSummaryRowCount } from "./coverageRows.js"

const fleet = [
    { vehicle_id: 1, vehicle_name: "Truck 10", registration_plate: "A 100", groups: ["North"] },
    { vehicle_id: 2, vehicle_name: "Truck 2", registration_plate: "B 200", groups: ["South", "Night"] },
    { vehicle_id: 3, vehicle_name: "Van 3", registration_plate: null, group: "General Fleet" },
    { vehicle_id: 3, vehicle_name: "Van 3 (duplicate)" },
    { vehicle_id: 4, vehicle_name: "Bus 4" },
]

const fuel = [
    { wialon_unit_id: 1, vehicle_identifier: "A100", status: "matched", litres: 50, total_cost: 150, distance_km: 400 },
    { wialon_unit_id: 2, vehicle_identifier: "B200", status: "no_distance_data", litres: 20.5, total_cost: 61.5, distance_km: null, quality_warnings: ["Wialon distance for this unit could not be read; distance unavailable."] },
    { wialon_unit_id: 2, vehicle_identifier: "B-200", status: "no_distance_data", litres: 99, total_cost: 99 },
    // Statement-only unit, not in the dashboard fleet
    { wialon_unit_id: 99, vehicle_identifier: "Z999", status: "no_distance_data", litres: 5, total_cost: 15 },
    { wialon_unit_id: null, vehicle_identifier: "UNMATCHED", status: "not_tracked", litres: 7, total_cost: 21 },
]

test("fleet units with no resolved fuel record are listed once, never as without distance", () => {
    const { notMatched, withoutDistance } = buildCoverage(fleet, fuel)
    assert.deepEqual(notMatched.map((r) => r.unitId), [4, 3])
    assert.equal(notMatched.find((r) => r.unitId === 3).name, "Van 3")
    assert.deepEqual(notMatched.find((r) => r.unitId === 3).groups, ["General Fleet"])
    assert.ok(!withoutDistance.some((r) => r.unitId === 3 || r.unitId === 4))
})

test("matched fleet units without distance keep their fuel totals, counted once; statement-only units are left out", () => {
    const { withoutDistance } = buildCoverage(fleet, fuel)
    assert.equal(withoutDistance.length, 1)
    const [row] = withoutDistance
    assert.equal(row.unitId, 2)
    assert.equal(row.fuelIdentifier, "B200")
    assert.equal(row.litres, 20.5)
    assert.equal(row.cost, 61.5)
    assert.deepEqual(row.groups, ["South", "Night"])
    assert.match(row.note, /distance unavailable/)
})

test("unknown fuel totals stay null, never 0", () => {
    const { withoutDistance } = buildCoverage(fleet, [{ wialon_unit_id: 2, status: "no_distance_data", litres: null, total_cost: undefined }])
    assert.equal(withoutDistance[0].litres, null)
    assert.equal(withoutDistance[0].cost, null)
})

test("fleet units named as ambiguous candidates are flagged, not reported as plainly unmatched", () => {
    const { notMatched } = buildCoverage(fleet, fuel, { ambiguousUnitNames: ["Bus 4"] })
    assert.equal(notMatched.find((r) => r.unitId === 4).ambiguous, true)
    assert.equal(notMatched.find((r) => r.unitId === 3).ambiguous, false)
})

test("empty inputs give empty lists", () => {
    assert.deepEqual(buildCoverage(null, undefined), { notMatched: [], excludedFromKpi: [], withoutDistance: [] })
})

test("search matches name, plate, statement identifier and group, case-insensitively", () => {
    const { notMatched, withoutDistance } = buildCoverage(fleet, fuel)
    assert.deepEqual(filterCoverageRows(withoutDistance, "b200").map((r) => r.unitId), [2])
    assert.deepEqual(filterCoverageRows(withoutDistance, "night").map((r) => r.unitId), [2])
    assert.deepEqual(filterCoverageRows(notMatched, "bus").map((r) => r.unitId), [4])
    assert.equal(filterCoverageRows(notMatched, "  ").length, notMatched.length)
    assert.equal(filterCoverageRows(notMatched, "nothing").length, 0)
})

test("fleet units whose rows the backend excluded from the KPIs (overlap) are not reported as unmatched", () => {
    const scope = { overlap_conflicts: [{ wialon_unit_id: 4, month: "2026-09", resolution: "summary_precedence" }, { wialon_unit_id: null }] }
    assert.deepEqual(overlapUnitIds(scope), [4])
    const { notMatched, excludedFromKpi, withoutDistance } = buildCoverage(fleet, fuel, { excludedUnitIds: overlapUnitIds(scope) })
    assert.deepEqual(notMatched.map((r) => r.unitId), [3])
    assert.deepEqual(excludedFromKpi.map((r) => r.unitId), [4])
    assert.ok(!withoutDistance.some((r) => r.unitId === 4))
    assert.deepEqual(overlapUnitIds(null), [])
})

test("partial-period monthly-summary rows are counted from the excluded scope", () => {
    assert.equal(partialSummaryRowCount({ partial_period_summary: { transaction_count: 3 } }), 3)
    assert.equal(partialSummaryRowCount({}), 0)
    assert.equal(partialSummaryRowCount(undefined), 0)
})

test("distance readiness: pending reports are never final; failed reports classify nothing", () => {
    assert.equal(distanceReadiness({ telematics_pending: true, telematics_status: "partial" }), "pending")
    assert.equal(distanceReadiness({ telematics_status: "unavailable" }), "unavailable")
    assert.equal(distanceReadiness({ telematics_status: "partial" }), "partial")
    assert.equal(distanceReadiness({ telematics_status: "complete" }), "ready")
})
