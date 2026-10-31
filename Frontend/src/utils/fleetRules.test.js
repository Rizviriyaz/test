import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
    fuelIdleRankingParams, fuelLeaderboardParams, fuelVehicleListParams, groupIdsOf, IDLE_RANKING_SIZE, groupNamesOf, idleDurationLabel, idleDurationStatus, idleElapsedSeconds, inGroup,
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
    assert.deepEqual(groupIdsOf({ group_id: 0, group_ids: [0] }), ["0"], "General Fleet (synthetic group 0) is a real group id")
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

test("Fuel dashboards send the exact vehicle filter for the vehicle table and the idle-waster ranking", () => {
    const page = readFileSync(new URL("../components/Fuel/useFuelEfficiencyData.js", import.meta.url), "utf8")
    assert.equal((page.match(/fuelLeaderboardParams\(/g) || []).length, 1, "leaderboard list")
    assert.equal((page.match(/fuelIdleRankingParams\(/g) || []).length, 1, "idle-waster ranking")
    assert.doesNotMatch(page, /getVehicleEfficiencyList\(\{[^}]*\.\.\.queryParams/s)
    const filters = { date_from: "2026-08-01", date_to: "2026-08-31", vehicle_identifier: "7-AUH-5594" }
    assert.equal(fuelIdleRankingParams(filters, 2).vehicle_filter, "7-AUH-5594")
    assert.equal(fuelLeaderboardParams(filters, { page: 1 }).vehicle_filter, "7-AUH-5594")
})

test("fuel leaderboard: matched Wialon vehicles only, with the table's page, sort, search and status", () => {
    const params = fuelLeaderboardParams({ date_from: "2026-08-01", date_to: "2026-08-31" }, {
        page: 3, pageSize: 20, sortBy: "efficiency", sortOrder: "asc", searchQuery: "559", status: "no_distance_data", idleRate: 2.5,
    })
    assert.deepEqual(params, {
        page: 3, page_size: 20, sort_by: "efficiency", sort_order: "asc", date_from: "2026-08-01", date_to: "2026-08-31",
        vehicle_identifier: "559", status: "no_distance_data", matched_only: true, idle_rate_lph: 2.5,
    })
    assert.equal(fuelLeaderboardParams({}, { page: 1, status: "" }).status, undefined)
})

test("idle fuel ranking: the whole matched fleet's top 5, never the leaderboard's page, search, sort or status", () => {
    const filters = { date_from: "2026-08-01", date_to: "2026-08-31" }
    const ranking = fuelIdleRankingParams(filters, 2.5)
    assert.deepEqual(ranking, {
        page: 1, page_size: IDLE_RANKING_SIZE, sort_by: "estimated_idle_fuel", sort_order: "desc",
        date_from: "2026-08-01", date_to: "2026-08-31", matched_only: true, idle_rate_lph: 2.5,
    })
    assert.equal(IDLE_RANKING_SIZE, 5)
    // The ranking takes only the period/global filters and the idle rate: no leaderboard state can reach it
    assert.equal(fuelIdleRankingParams.length, 2)
    for (const key of ["status", "vehicle_identifier"]) assert.equal(key in ranking, false, key)
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
