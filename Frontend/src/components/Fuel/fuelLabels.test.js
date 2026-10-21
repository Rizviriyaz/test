// Offline tests for fuel label helpers and frontend accuracy rules. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { LOWER_IS_BETTER, sortOrderLabels, classificationOf, excludedSummary, TREND_STATUS_LABELS, fixedOrDash, fuelTypeLabel, isRefundAdjustment } from "./fuelLabels.js"
import { compactTick, formatTrendPoint } from "./telemetryFormat.js"

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8")

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
    const perf = read("../../pages/FuelPerformance.jsx")
    for (const banned of ["100% Invoiced", "Verified Wialon", ">Verified<"]) assert.ok(!kpi.includes(banned), banned)
    for (const banned of ["[Rec]", "[Calc]", "Wialon GPS verified"]) {
        assert.ok(!modal.includes(banned), banned)
        assert.ok(!perf.includes(banned), banned)
    }
    assert.doesNotMatch(kpi, /matchedCount \|\| vehicleCount/)
    assert.doesNotMatch(kpi, /vehicle_count \|\| 0/)
})

test("vehicle trend and attention cards keep unknown values unknown", () => {
    assert.match(read("../../pages/FuelPerformance.jsx"), /fuel_efficiency_l_per_100km !== null/)
    const waste = read("./FuelWasteIntelligence.jsx")
    assert.match(waste, /!isExcludedIdentifierItem\(item\)/)
    const perf = read("../../pages/FuelPerformance.jsx")
    assert.match(perf, /clearClientCache\("\/api\/fuel\/"\)/)
    assert.match(perf, /all_transaction_count === 0/)
})

test("global filters never overwrite the local vehicle/search parameter", () => {
    const src = read("../../pages/FuelPerformance.jsx")
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
    assert.doesNotMatch(read("../../pages/FuelPerformance.jsx"), /fuel_cost_per_km\?\.toFixed/)
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
    assert.doesNotMatch(read("./FuelTrendChart.jsx"), /\/ 1000\)\.toFixed\(0\)}k/)
})

test("failed or stale requests never leave the previous data on screen", () => {
    const perf = read("../../pages/FuelPerformance.jsx")
    for (const ref of ["efficiencyRequestIdRef", "rankingsRequestIdRef", "attentionRequestIdRef", "wasteRequestIdRef"]) {
        assert.match(perf, new RegExp(`requestId !== ${ref}\\.current`), ref)
    }
    assert.match(perf, /setEfficiency\(null\)/)
    assert.match(perf, /setRankingsError\(/)
    assert.match(perf, /if \(pageFilters !== queryParams\) \{\s*setPageFilters\(queryParams\)\s*setPage\(1\)/, "page resets on new filters")
    assert.match(read("./VehicleFuelDetailModal.jsx"), /return \(\) => \{ active = false \}/)
    assert.match(perf, /setHistoryError\(/, 'history outage is not "no imports"')
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
