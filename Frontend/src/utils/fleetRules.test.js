import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
    fuelVehicleListParams, groupIdsOf, groupNamesOf, idleDurationLabel, idleDurationStatus, idleElapsedSeconds, inGroup,
    isHealthyVehicle,
} from "./fleetRules.js"

const fmt = (s) => `${Math.round(s / 60)}m`

test("idle duration: unknown is never shown as a duration, a lower bound reads as at-least", () => {
    assert.equal(idleDurationLabel({ idle_duration: 0, idle_duration_status: "unknown" }, fmt), "Unknown")
    assert.equal(idleDurationLabel({ idle_duration: 600, idle_duration_status: "unknown" }, fmt), "Unknown")
    assert.equal(idleDurationLabel({ idle_duration: 86400, idle_duration_status: "lower_bound" }, fmt), "1440m+")
    assert.equal(idleDurationLabel({ idle_duration: 600, idle_duration_status: "known" }, fmt), "10m")
    assert.equal(idleDurationLabel({ idle_duration: 0, idle_duration_status: "known" }, fmt), "Just started")
    // Older payload without a status: unchanged behaviour
    assert.equal(idleDurationLabel({ idle_duration: 120 }, fmt), "2m")
    assert.equal(idleDurationStatus({ idle: { duration_status: "unknown" } }), "unknown")
    assert.equal(idleDurationStatus({ idle_duration_status: "bogus" }), "known")
})

test("group membership: a vehicle belongs to its primary and every secondary group", () => {
    const live = { group: "North", groups: ["North", "Contract A"] }
    const idleRow = { group_name: "North", group_names: ["North", "Contract A"], group_id: 1, group_ids: [1, 7] }
    assert.deepEqual(groupNamesOf(live).sort(), ["Contract A", "North"])
    assert.equal(inGroup(live, "contract a"), true)
    assert.equal(inGroup(idleRow, "Contract A"), true, "idle charts must match the same groups as Fleet Now")
    assert.equal(inGroup(idleRow, "South"), false)
    assert.equal(inGroup(idleRow, "ALL"), true)
    assert.deepEqual(groupIdsOf(idleRow), ["1", "7"])
    assert.deepEqual(groupIdsOf({ group_id: 3 }), ["3"], "older payload: primary group only")
})

test("healthy rule matches the backend KPI (battery warning with excellent GPS is not healthy)", () => {
    const base = { battery: { status: "HEALTHY" }, gps: { status: "EXCELLENT" }, connectivity: { status: "STABLE" }, health_score: 100 }
    assert.equal(isHealthyVehicle(base), true)
    assert.equal(isHealthyVehicle({ ...base, battery: { status: "WARNING" }, health_score: 80 }), false)
    assert.equal(isHealthyVehicle({ ...base, gps: { status: "STALE" }, health_score: 85 }), false)
    assert.equal(isHealthyVehicle({ ...base, connectivity: { status: "DELAYED" } }), false)
    assert.equal(isHealthyVehicle({ ...base, health_score: 79 }), false)
})

test("fuel vehicle list: the global filter is exact (vehicle_filter), the search box stays a substring", () => {
    assert.deepEqual(fuelVehicleListParams({ date_from: "2026-10-01", vehicle_identifier: "7-AUH-5594" }, ""),
        { date_from: "2026-10-01", vehicle_filter: "7-AUH-5594" })
    assert.deepEqual(fuelVehicleListParams({ vehicle_identifier: "7-AUH-5594" }, "559"),
        { vehicle_filter: "7-AUH-5594", vehicle_identifier: "559" })
    assert.deepEqual(fuelVehicleListParams({}, ""), {})
})

test("Fuel page sends the exact vehicle filter for the vehicle table and the idle-waster ranking", () => {
    const page = readFileSync(new URL("../pages/FuelPerformance.jsx", import.meta.url), "utf8")
    assert.equal((page.match(/fuelVehicleListParams\(/g) || []).length, 2, "rankings and idle-waster lists")
    assert.doesNotMatch(page, /getVehicleEfficiencyList\(\{[^}]*\.\.\.queryParams/s)
})

test("vehicle drawer refetches by vehicle id, not on every live poll", () => {
    const drawer = readFileSync(new URL("../components/Vehicles/VehicleDetailDrawer.jsx", import.meta.url), "utf8")
    assert.match(drawer, /\}, \[activeVehicleId, refreshTick, idleEvents, idleEventsComplete\]\)/)
    assert.doesNotMatch(drawer, /\}, \[activeVehicle, idleEvents, idleEventsComplete\]\)/)
    assert.doesNotMatch(drawer, /Trips & Activity/)
})

test("dashboard loads closed-day (yesterday) reports once per UAE day", () => {
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.match(page, /yesterdayLoadedForRef\.current !== dayKey/)
})

test("alias routes redirect instead of rendering another page under a misleading URL", () => {
    const app = readFileSync(new URL("../App.jsx", import.meta.url), "utf8")
    for (const [from, to] of [["/trips", "/tracking"], ["/reports", "/idle-analytics"], ["/drivers", "/dashboard"], ["/settings", "/dashboard"]]) {
        assert.match(app, new RegExp(`path="${from}"\\s+element=\\{<Navigate to="${to}" replace />\\}`), from)
    }
})

test("idleElapsedSeconds stops the live idle timer at the last message", () => {
    const start = 1_800_000_000
    const nowMs = (start + 3 * 3600) * 1000
    assert.equal(idleElapsedSeconds(start, 0, nowMs), 3 * 3600, "no cap given: runs to now")
    assert.equal(idleElapsedSeconds(start, 0, nowMs, start + 3600), 3600, "stale unit: capped at its last message")
    assert.equal(idleElapsedSeconds(start, 0, nowMs, start + 5 * 3600), 3 * 3600, "a later end never runs past now")
    assert.equal(idleElapsedSeconds(start, 0, nowMs, start - 60), 0, "never negative")
    assert.equal(idleElapsedSeconds(start, 0, nowMs, null), 3 * 3600, "unknown end: as before")
    assert.equal(idleElapsedSeconds(null, 420, nowMs, start), 420, "no start: the backend duration")
    assert.equal(idleElapsedSeconds(start * 1000, 0, nowMs, (start + 60) * 1000), 60, "millisecond inputs")
})
