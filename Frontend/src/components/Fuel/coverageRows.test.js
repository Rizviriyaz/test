import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { buildCoverage, buildExcelWithoutWialon, distanceReadiness, filterCoverageRows, overlapUnitIds, partialSummaryRowCount, withoutWialonTotalsOf } from "./coverageRows.js"

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

// /analytics/excluded groups (one per identifier and reason) as the backend's summarize_excluded returns them
const excludedGroups = [
    { vehicle_identifier: "ZZZ-999", reason: "unmatched", candidate_unit_names: [], ambiguity_reason: null, transaction_count: 2, litres: 25, cost: 75 },
    { vehicle_identifier: "PLATE-1234", reason: "ambiguous", candidate_unit_names: ["1234", "1234 "], ambiguity_reason: null, transaction_count: 1, litres: 40, cost: 120 },
    { vehicle_identifier: "7-DXB-555", reason: "ambiguous", candidate_unit_names: ["555"], ambiguity_reason: "plate_code_conflict", transaction_count: 1, litres: 12.5, cost: 37.5 },
    // Other exclusions: never listed as "without a Wialon vehicle"
    { vehicle_identifier: "17-AUH-55943", reason: "suspected_duplicate", candidate_unit_names: [], transaction_count: 1, litres: 160, cost: 480 },
    { vehicle_identifier: "7-AUH-55943", reason: "partial_period_summary", candidate_unit_names: [], transaction_count: 1, litres: 70, cost: 210 },
]

test("Excel records without a Wialon vehicle: unmatched and ambiguous identifiers only, told apart", () => {
    const { rows } = buildExcelWithoutWialon(excludedGroups)
    assert.deepEqual(rows.map((r) => [r.identifier, r.reason, r.plateCodeConflict]), [
        ["PLATE-1234", "ambiguous", false], ["ZZZ-999", "unmatched", false], ["7-DXB-555", "ambiguous", true],
    ])
    assert.deepEqual(rows.find((r) => r.identifier === "PLATE-1234").candidates, ["1234", "1234 "])
    assert.deepEqual(rows.find((r) => r.identifier === "ZZZ-999").candidates, [])
    // Each row keeps its own backend values; duplicates and partial-month rows are never listed
    assert.deepEqual(rows.map((r) => [r.records, r.litres, r.cost]), [[1, 40, 120], [2, 25, 75], [1, 12.5, 37.5]])
    // Searchable by identifier and candidate vehicle
    assert.deepEqual(filterCoverageRows(rows, "1234").map((r) => r.identifier), ["PLATE-1234"])
    assert.deepEqual(filterCoverageRows(rows, "555").map((r) => r.identifier), ["7-DXB-555"])
})

test("Excel records without a Wialon vehicle: unknown litres stay unknown, empty input is empty", () => {
    const { rows } = buildExcelWithoutWialon([{ vehicle_identifier: "X1", reason: "unmatched", transaction_count: 1, litres: null, cost: 10 }])
    assert.equal(rows[0].litres, null)
    assert.equal(rows[0].cost, 10)
    assert.deepEqual(buildExcelWithoutWialon(undefined), { rows: [] })
})

test("the three coverage lists never share a vehicle or a fuel record", () => {
    // The matched list has Wialon unit ids; the Excel-without-Wialon list holds identifiers with no single unit
    const coverage = buildCoverage(fleet, fuel, { ambiguousUnitNames: ["Bus 4"] })
    const unmapped = buildExcelWithoutWialon(excludedGroups).rows
    const matchedIds = new Set(fuel.filter((r) => r.wialon_unit_id !== null).map((r) => r.vehicle_identifier))
    assert.ok(unmapped.every((r) => !matchedIds.has(r.identifier)))
    const notMatchedIds = new Set(coverage.notMatched.map((r) => r.unitId))
    assert.ok(coverage.withoutDistance.every((r) => !notMatchedIds.has(r.unitId)))
    assert.equal(coverage.notMatched.find((r) => r.name === "Bus 4").ambiguous, true)
})

test("Data Coverage lists matched vehicles only and loads every excluded group once", () => {
    const page = readFileSync(new URL("./DataCoverage.jsx", import.meta.url), "utf8")
    assert.match(page, /getVehicleEfficiencyList\(\{ \.\.\.listParams, matched_only: true/)
    assert.match(page, /getFuelExcluded\(\{ \.\.\.queryParams, page, page_size: PAGE_SIZE \}, \{ signal \}\)/)
    // A newer load or leaving the page cancels the previous load (the request cache aborts only unneeded requests)
    assert.match(page, /controllerRef\.current\?\.abort\(\)/)
    assert.match(page, /Excel, no Wialon vehicle/)
})

// Live August 2026 figures (Phase 4 audit): costs are stored to 3 decimals, so separately rounded rows add up to
// 10,293.73 while the unmatched total rounded once is 10,293.72; the list totals follow the KPI cards instead
const AUGUST = { transaction_count: 61, matched_row_count: 59, total_litres: 30049.99, matched_litres: 27056.8,
    total_cost: 103343.35, matched_cost: 93049.62, unmatched_litres: 2993.19, unmatched_cost: 10293.72 }

test("Excel-without-Wialon totals are statement − matched, so they add up with the KPI cards", () => {
    const totals = withoutWialonTotalsOf(AUGUST)
    assert.deepEqual(totals, { records: 2, litres: 2993.2, cost: 10293.73 })
    // Displayed figures reconcile exactly in fils: 93,049.62 + 10,293.73 = 103,343.35
    assert.equal(Math.round(AUGUST.matched_cost * 100) + Math.round(totals.cost * 100), Math.round(AUGUST.total_cost * 100))
    assert.equal(Math.round(27056.8 * 10) + Math.round(totals.litres * 10), Math.round(30050.0 * 10))
    assert.equal(withoutWialonTotalsOf(null), null)
    assert.deepEqual(withoutWialonTotalsOf({ transaction_count: 3 }), { records: null, litres: null, cost: null })
})

test("matched, no distance: a recorded 0 km stays 0; an unknown distance stays null", () => {
    const fleetUnits = [{ vehicle_id: 5, vehicle_name: "13158" }, { vehicle_id: 6, vehicle_name: "Truck 6" }, { vehicle_id: 7, vehicle_name: "Truck 7" }]
    const rows = buildCoverage(fleetUnits, [
        { wialon_unit_id: 5, vehicle_identifier: "13158", status: "no_distance_data", litres: 89.05, total_cost: 306.25, distance_km: 0 },
        { wialon_unit_id: 6, vehicle_identifier: "T6", status: "no_distance_data", litres: 10, total_cost: 30, distance_km: null },
        { wialon_unit_id: 7, vehicle_identifier: "T7", status: "no_distance_data", litres: 10, total_cost: 30 },
    ]).withoutDistance
    assert.deepEqual(rows.map((r) => [r.name, r.distance]), [["13158", 0], ["Truck 6", null], ["Truck 7", null]])
    const page = readFileSync(new URL("./DataCoverage.jsx", import.meta.url), "utf8")
    assert.match(page, /r\.distance === null \? \(\s*<td[^>]*>Unavailable<\/td>/)
    assert.match(page, /\{fmt\(r\.distance, 2\)\} km/)
    assert.match(page, />Recorded 0 km</)
})
