// Offline tests for fuel label helpers and frontend accuracy rules. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { existsSync, readFileSync } from "node:fs"
import { LOWER_IS_BETTER, sortOrderLabels, classificationOf, excludedSummary, TREND_STATUS_LABELS, fixedOrDash, fuelTypeLabel, isRefundAdjustment, skippedRowsLabel, fuelShareNote, EXCLUDED_REASON_LABELS } from "./fuelLabels.js"
import { compactTick, formatTrendPoint } from "./telemetryFormat.js"

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8")
// The monthly page with its shared data hook (efficiency, rankings and idle-ranking loaders moved there)
const monthlyDashboardSource = () => read("../../pages/FuelPerformance.jsx") + read("./useFuelEfficiencyData.js")

test("sort labels: lower-is-better metrics list the worst first on desc", () => {
    assert.ok(LOWER_IS_BETTER.has("efficiency") && LOWER_IS_BETTER.has("cost_per_km"))
    assert.deepEqual(sortOrderLabels("efficiency"), { desc: "Highest first (worst)", asc: "Lowest first (best)" })
    assert.deepEqual(sortOrderLabels("litres"), { desc: "High → Low", asc: "Low → High" })
    assert.equal(sortOrderLabels("vehicle_identifier").asc, "A → Z")
})

test("classification badges come only from the backend map", () => {
    const map = { total_litres: "derived", total_distance_km: "recorded", estimated_idle_fuel_litres: "estimated", bogus: "verified" }
    assert.equal(classificationOf(map, "total_litres"), "derived")
    assert.equal(classificationOf(map, "total_distance_km"), "recorded")
    assert.equal(classificationOf(map, "estimated_idle_fuel_litres"), "estimated")
    assert.equal(classificationOf(map, "bogus"), null)
    assert.equal(classificationOf(map, "missing"), null)
    assert.equal(classificationOf(undefined, "total_litres"), null)
})

test("excluded summary reconciles counts and is null when nothing is excluded", () => {
    assert.equal(excludedSummary(null), null)
    assert.equal(excludedSummary({ excluded_transaction_count: 0 }), null)
    const s = excludedSummary({
        excluded_transaction_count: 5, excluded_litres: -10.5, excluded_cost: 30, all_transaction_count: 12,
        unmatched: { transaction_count: 3 }, ambiguous: { transaction_count: 1 }, suspected_duplicates: { transaction_count: 1 },
    })
    assert.equal(s.count, s.unmatched + s.ambiguous + s.duplicates)
    assert.equal(s.litres, -10.5) // refunds stay signed
    assert.equal(s.allCount, 12)
})

test("trend point keeps unknown cumulative efficiency null and statuses have labels", () => {
    const p = formatTrendPoint({ period: "2026-08-03", litres: 10, cost: 30, distance_km: 12, fuel_efficiency_l_per_100km: null, cumulative_l_per_100km: null, efficiency_status: "insufficient_distance" })
    assert.equal(p.cumulative_efficiency, null)
    assert.equal(p.fuel_efficiency, null)
    assert.equal(TREND_STATUS_LABELS[p.efficiency_status], "Below minimum distance")
})

test("hardcoded verification badges and measured-consumption wording are gone", () => {
    const kpi = read("./KpiCards.jsx")
    const modal = read("./VehicleFuelDetailModal.jsx")
    const perf = monthlyDashboardSource()
    for (const banned of ["100% Invoiced", "Verified Wialon", ">Verified<"]) assert.ok(!kpi.includes(banned), banned)
    for (const banned of ["[Rec]", "[Calc]", "Wialon GPS verified"]) {
        assert.ok(!modal.includes(banned), banned)
        assert.ok(!perf.includes(banned), banned)
    }
    assert.doesNotMatch(kpi, /matchedCount \|\| vehicleCount/)
    assert.doesNotMatch(kpi, /vehicle_count \|\| 0/)
})

test("vehicle trend and idle-waster ranking keep unknown values unknown", () => {
    // The fleet-wide idle ranking drops vehicles whose estimate is unknown instead of ranking them as zero
    assert.match(monthlyDashboardSource(), /estimated_idle_fuel_litres !== null/)
    const waste = read("./FuelWasteIntelligence.jsx")
    assert.match(waste, /!isExcludedIdentifierItem\(item\)/)
    const perf = monthlyDashboardSource()
    assert.match(perf, /clearClientCache\("\/api\/fuel\/"\)/)
    assert.match(perf, /all_transaction_count === 0/)
})

test("global filters never overwrite the local vehicle/search parameter", () => {
    const src = monthlyDashboardSource()
    // a local vehicle_identifier followed later in the same object by ...queryParams would be overwritten
    assert.doesNotMatch(src, /vehicle_identifier:[^\n]*\n(?:[^\n}]*\n)*?\s*\.\.\.queryParams,/)
})

test("missing price, cost and fuel type render as unknown, never as blank or an invented value", () => {
    assert.equal(fixedOrDash(null), "—")
    assert.equal(fixedOrDash(undefined), "—")
    assert.equal(fixedOrDash(""), "—")
    assert.equal(fixedOrDash(Number.NaN), "—")
    assert.equal(fixedOrDash(0), "0.00", "a recorded zero stays zero")
    assert.equal(fixedOrDash(2.856), "2.86")
    assert.equal(fuelTypeLabel(null), "Unspecified")
    assert.equal(fuelTypeLabel("  "), "Unspecified")
    assert.equal(fuelTypeLabel("Diesel"), "Diesel")
    const modal = read("./VehicleFuelDetailModal.jsx")
    assert.doesNotMatch(modal, /fuel_type \|\| "/)
    assert.doesNotMatch(modal, /unit_price\?\.toFixed|total_cost\?\.toFixed/)
    assert.doesNotMatch(modal, /t\.(unit_price|total_cost) \?/, "a recorded 0 must not be hidden")
    assert.doesNotMatch(monthlyDashboardSource(), /fuel_cost_per_km\?\.toFixed/)
})

test("refund adjustments are labelled distinctly from purchases", () => {
    assert.equal(isRefundAdjustment({ transaction_kind: "refund_adjustment", litres: -10 }), true)
    assert.equal(isRefundAdjustment({ transaction_kind: "purchase", litres: 40 }), false)
    assert.equal(isRefundAdjustment({ litres: -5 }), true, "falls back to the sign when kind is absent")
    assert.equal(isRefundAdjustment(null), false)
})

test("axis ticks below 1000 are not collapsed to 0k", () => {
    assert.equal(compactTick(250, "L"), "250 L")
    assert.equal(compactTick(1500, "L"), "1.5k L")
    assert.equal(compactTick(25000), "25k")
    assert.equal(compactTick(null), "")
})

test("failed or stale requests never leave the previous data on screen", () => {
    const perf = monthlyDashboardSource()
    for (const ref of ["efficiencyRequestIdRef", "rankingsRequestIdRef", "idleWastersRequestIdRef"]) {
        assert.match(perf, new RegExp(`requestId !== ${ref}\\.current`), ref)
    }
    assert.match(perf, /setEfficiency\(null\)/)
    assert.match(perf, /setRankingsError\(/)
    assert.match(perf, /if \(pageFilters !== queryParams\) \{\s*setPageFilters\(queryParams\)\s*setPage\(1\)/, "page resets on new filters")
    assert.match(read("./VehicleFuelDetailModal.jsx"), /return \(\) => \{ active = false \}/)
    assert.match(read("../../api/fleetApi.js"), /executeFuelImport[\s\S]*?clearClientCache\("\/api\/fuel\/"\)/)
})

test("idle waste is not a confident zero when Wialon idle data is unavailable or partial", () => {
    const waste = read("./FuelWasteIntelligence.jsx")
    assert.match(waste, /fleet_estimated_idle_fuel_litres != null/)
    assert.match(waste, /telematics_status/)
    assert.match(waste, /effective_idle_threshold_hours/)
    const gauge = read("./FleetEfficiencyGauge.jsx")
    assert.match(gauge, /if \(!efficiencyData\)/, "no response renders an unavailable state, not zero counts")
})

test("skippedRowsLabel separates non-fuel rows and treats unrecorded (legacy) counts as unknown", () => {
    assert.equal(skippedRowsLabel({ rows_ignored: 6, rows_non_fuel: 6 }), "6 non-fuel payments/charges skipped")
    assert.equal(skippedRowsLabel({ rows_ignored: 8, rows_non_fuel: 6 }), "2 blank/summary, 6 non-fuel payments/charges skipped")
    assert.equal(skippedRowsLabel({ rows_ignored: 2 }), "2 blank/summary skipped")
    assert.equal(skippedRowsLabel({ rows_ignored: 0, rows_non_fuel: 0 }), "")
    // History records written before fuel_imports_ignored_counts.sql
    assert.equal(skippedRowsLabel({ rows_ignored: null, rows_non_fuel: null }), "")
    assert.equal(skippedRowsLabel({ rows_imported: 5 }), "")
    assert.equal(skippedRowsLabel(null), "")
})

test("H4: idle/driving percentages are labelled as fuel shares, never as engine time", () => {
    assert.equal(fuelShareNote(5), "5.0% of fuel (estimated)")
    assert.equal(fuelShareNote(null), null)
    assert.equal(fuelShareNote(undefined), null)
    const kpi = read("./KpiCards.jsx")
    for (const wrong of ["of engine time", "Share of Hours", "Hours ÷ Total Engine Hours", "Idle Litres ÷ Total Litres"]) {
        assert.ok(!kpi.includes(wrong), `KpiCards.jsx still says "${wrong}"`)
    }
    // The shares are against idle_basis_litres (vehicles with known idle hours, 0 h included), the backend split basis
    assert.match(kpi, /litres of vehicles with known idle hours/)
    assert.ok(!kpi.includes("vehicles with an idle estimate"), "zero-idle vehicles are in the basis: not \"with an idle estimate\"")
    assert.match(kpi, /idle_basis_litres/)
})

test("H4: hour bars never fall back to the fuel percentages", () => {
    const chart = read("./DrivingVsIdlingChart.jsx")
    const hoursLines = chart.split(/\r?\n/).filter((l) => /const hours(Driving|Idle)Pct =/.test(l))
    assert.equal(hoursLines.length, 2)
    assert.ok(hoursLines.every((l) => !/drivingPct|idlePct|estimated_/.test(l)), hoursLines.join(" | "))
    const board = read("./VehicleLeaderboard.jsx")
    const pctLines = board.split(/\r?\n/).filter((l) => /const (drvPct|idlPct) =/.test(l))
    assert.equal(pctLines.length, 2)
    assert.ok(pctLines.every((l) => !/estimated_(driving|idle)_percentage/.test(l)), pctLines.join(" | "))
})

test("matched-fleet views never present unmatched/ambiguous identifiers as fleet vehicles (Phase 3 P5)", () => {
    const gauge = read("./FleetEfficiencyGauge.jsx")
    assert.doesNotMatch(gauge, /Fuel-only|Untracked|unmatched_vehicle_count/, "gauge shows matched-vehicle coverage only")
    assert.match(gauge, /matched_vehicle_count/)
    const badge = read("./VehicleStatusBadge.jsx")
    assert.match(badge, /not_tracked: \{ label: "No Wialon unit"/)
    assert.match(badge, /\|\| UNKNOWN/, "an unknown status renders as Unknown")
    assert.match(badge, /UNKNOWN = \{ label: "Unknown"/)
    assert.doesNotMatch(badge, /"Untracked"/)
    const board = read("./VehicleLeaderboard.jsx")
    assert.doesNotMatch(board, /"Untracked"/)
    // The ranking requests only the matched KPI scope: no option lists excluded identifiers among fleet vehicles
    assert.doesNotMatch(board, /<option value="(not_tracked|ambiguous)"/)
})

test("monthly-only fuel reporting: no daily page, link or request; idle ranking keeps unknown-distance vehicles", () => {
    const page = monthlyDashboardSource()
    assert.doesNotMatch(page, /getFuelDaily|getFuelTransactions|View Daily Transactions|\/fuel\/daily/)
    assert.equal(existsSync(new URL("../../pages/FuelDailyDashboard.jsx", import.meta.url)), false)
    assert.doesNotMatch(read("../../api/fleetApi.js"), /analytics\/daily/)
    // The old daily URL only redirects to the monthly dashboard
    assert.match(read("../../App.jsx"), /path="\/fuel\/daily"\s*element=\{<Navigate to="\/fuel" replace \/>\}/)
    const idle = page.slice(page.indexOf("const loadIdleWasters"), page.indexOf("useEffect(", page.indexOf("const loadIdleWasters")))
    assert.match(idle, /sort_by: "estimated_idle_fuel"/)
    assert.ok(!/status: "matched"/.test(idle), "no_distance_data vehicles carry idle estimates and must stay ranked")
})

test("excluded panel: Overlaps tab lists dated rows a monthly summary superseded, labelled apart", () => {
    const panel = read("./ExcludedFuelPanel.jsx")
    assert.match(panel, /overlaps: \["dated_superseded_by_summary", "summary_dated_overlap", "summary_superseded_by_dated"\]/)
    assert.match(EXCLUDED_REASON_LABELS.dated_superseded_by_summary, /superseded by monthly summary/)
    assert.notEqual(EXCLUDED_REASON_LABELS.summary_dated_overlap, EXCLUDED_REASON_LABELS.summary_superseded_by_dated)
    assert.match(panel, /"dated_superseded_by_summary"\s*\?\s*"bg-emerald-50/)
    assert.match(panel, /dated_superseded_by_summary: "Dated transaction for a vehicle-month that has a monthly-summary total/)
})

test("leaderboard: unknown distance is never labelled as zero distance", () => {
    const board = read("./VehicleLeaderboard.jsx")
    assert.match(board, /const isZeroDistance = v\.distance_km === 0\r?\n/)
    assert.match(board, /isDistanceUnknown\s*\?\s*"Distance unknown"/)
    assert.ok(!board.includes(">Zero Distance</option>"), "no_distance_data includes unknown distance")
})
