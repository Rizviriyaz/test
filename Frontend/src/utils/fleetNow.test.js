import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"

import {
    EARLIER_RESULT_NOTICE, activeTodaySummary, fleetAlertsCount, forUaeDay, formatKm2, offlineNote, previousUaeDay,
    reportNotice, roundKm2, sectionState, todayMileageState, yesterdayCellStates,
} from "./fleetNow.js"

// 2026-10-27 00:00 Asia/Dubai (UTC+4) = 2026-10-26T20:00:00Z
const DAY_START = Date.UTC(2026, 9, 26, 20, 0, 0) / 1000
const TODAY = "2026-10-27"
const NOW = DAY_START + 10 * 3600
const YESTERDAY_START = DAY_START - 86400

const live = (id, status, extra = {}) => ({
    vehicle_id: id, status, connection_status: "STABLE", telemetry_stale: false, last_message_timestamp: NOW, ...extra,
})
const mileage = (units, extra = {}) => ({ effective_from: DAY_START, effective_to: NOW, all_units: units, ...extra })
const trips = (vehicles, extra = {}) => ({ from_time: DAY_START, to_time: NOW, vehicles, ...extra })

test("active today: report mileage or trips today count once per vehicle", () => {
    const vehicles = [live(1, "STOPPED"), live(2, "STOPPED"), live(3, "STOPPED")]
    const r = activeTodaySummary(vehicles, {
        mileageData: mileage([{ vehicle_id: 1, mileage: 12.5 }, { vehicle_id: 2, mileage: 0 }, { vehicle_id: 3, mileage: 0 }]),
        tripsData: trips([{ vehicle_id: 1, trip_count: 2, gps_mileage_km: 12.5 }, { vehicle_id: 2, trip_count: 0, gps_mileage_km: 0 },
            { vehicle_id: 3, trip_count: 0, gps_mileage_km: 0 }]),
        todayStr: TODAY,
    })
    assert.deepEqual(r, { active: 1, unknown: 0, complete: true }, "a genuine 0 km is inactive, not unknown")
})

test("active today: a stale last-known Moving/Idling status alone is not activity today", () => {
    const staleFromYesterday = live(1, "MOVING", {
        connection_status: "DELAYED", telemetry_stale: true, last_message_timestamp: DAY_START - 3600,
    })
    const staleToday = live(2, "IDLING", { connection_status: "DELAYED", telemetry_stale: true, last_message_timestamp: NOW - 1800 })
    const liveMoving = live(3, "MOVING")
    const r = activeTodaySummary([staleFromYesterday, staleToday, liveMoving], {
        mileageData: mileage([{ vehicle_id: 1, mileage: 0 }, { vehicle_id: 2, mileage: 0 }, { vehicle_id: 3, mileage: 0 }]),
        tripsData: null,
        todayStr: TODAY,
    })
    assert.equal(r.active, 1, "only the live, non-stale mover counts")
    assert.equal(r.unknown, 0)
})

test("active today: a live status from a message before UAE midnight is not activity today", () => {
    const lateLastNight = live(1, "MOVING", { last_message_timestamp: DAY_START - 60 })
    const r = activeTodaySummary([lateLastNight], { mileageData: mileage([{ vehicle_id: 1, mileage: 0 }]), todayStr: TODAY })
    assert.equal(r.active, 0)
})

test("active today: failed or missing reports leave vehicles unknown, never inactive", () => {
    const vehicles = [live(1, "MOVING"), live(2, "STOPPED"), live(3, "STOPPED")]
    const none = activeTodaySummary(vehicles, { mileageData: null, tripsData: null, todayStr: TODAY })
    assert.deepEqual(none, { active: 1, unknown: 2, complete: false })

    const unreadable = activeTodaySummary(vehicles, {
        mileageData: mileage([{ vehicle_id: 2, mileage: null }]), tripsData: null, todayStr: TODAY,
    })
    assert.equal(unreadable.unknown, 2, "an unreadable value and a vehicle missing from the report are unknown")

    const stale = activeTodaySummary(vehicles, {
        mileageData: mileage([{ vehicle_id: 2, mileage: 0 }, { vehicle_id: 3, mileage: 4.2 }], { stale: true }),
        tripsData: null, todayStr: TODAY,
    })
    assert.deepEqual(stale, { active: 2, unknown: 1, complete: false },
        "a stale report still proves activity, but its 0 km is not confirmed")
})

test("active today: a report whose window is not today's UAE date is ignored", () => {
    const vehicles = [live(1, "STOPPED")]
    const r = activeTodaySummary(vehicles, {
        mileageData: { ...mileage([{ vehicle_id: 1, mileage: 80 }]), effective_from: YESTERDAY_START },
        tripsData: { ...trips([{ vehicle_id: 1, trip_count: 3, gps_mileage_km: 80 }]), from_time: YESTERDAY_START },
        todayStr: TODAY,
    })
    assert.deepEqual(r, { active: 0, unknown: 1, complete: false })
})

test("active today: matches by vehicle id only, within the given (group) population", () => {
    const groupVehicles = [{ ...live(1, "STOPPED"), vehicle_name: "TRUCK 1" }]
    const r = activeTodaySummary(groupVehicles, {
        // Unit 9 is outside the group; a name match must not attribute its mileage to unit 1
        mileageData: mileage([{ vehicle_id: 9, vehicle_name: "TRUCK 1", mileage: 50 }, { vehicle_id: 1, vehicle_name: "TRUCK 1", mileage: 0 }]),
        todayStr: TODAY,
    })
    assert.deepEqual(r, { active: 0, unknown: 0, complete: true })
})

const alertsPayload = {
    total_alerts: 40, displayed_alerts: 18, alerts_capped: true, safety_partial: false,
    by_vehicle: { 1: 3, 2: 30, 5: 7 },
    alerts: [
        { vehicle_id: 1, alert_type: "DEVICE_OFFLINE", timestamp: DAY_START - 86400 },
        { vehicle_id: 2, alert_type: "HARSH_DRIVING", timestamp: NOW },
    ],
}

test("alerts: the card counts the Alerts page total, including old last-message timestamps and capped events", () => {
    assert.deepEqual(fleetAlertsCount(alertsPayload, { selectedGroup: "ALL" }), { count: 40, partial: false, lowerBound: false })
})

test("alerts: a group counts its vehicles' alerts from by_vehicle (all harsh events, not only listed ones)", () => {
    const r = fleetAlertsCount(alertsPayload, { selectedGroup: "North", vehicleIds: new Set(["1", "2"]) })
    assert.deepEqual(r, { count: 33, partial: false, lowerBound: false })
    const empty = fleetAlertsCount(alertsPayload, { selectedGroup: "South", vehicleIds: new Set(["7"]) })
    assert.equal(empty.count, 0, "a successful response with no alerts for the group is a real 0")
})

test("alerts: older payload without by_vehicle counts listed alerts and marks a capped list as a lower bound", () => {
    const older = { ...alertsPayload }
    delete older.by_vehicle
    const r = fleetAlertsCount(older, { selectedGroup: "North", vehicleIds: new Set(["1", "2"]) })
    assert.deepEqual(r, { count: 2, partial: false, lowerBound: true })
})

test("alerts: unavailable data is null, never 0; a confirmed empty response is 0; partial safety is flagged", () => {
    assert.equal(fleetAlertsCount(null).count, null)
    assert.equal(fleetAlertsCount({}).count, null)
    assert.deepEqual(fleetAlertsCount({ total_alerts: 0, alerts: [], by_vehicle: {} }), { count: 0, partial: false, lowerBound: false })
    assert.equal(fleetAlertsCount({ ...alertsPayload, safety_partial: true }).partial, true)
})

test("offline note: states the observed silence and never-reported units, not an assumed threshold", () => {
    assert.equal(offlineNote([]), null)
    assert.equal(offlineNote([{ seconds_since_update: 50400 }, { seconds_since_update: 90000 }]), "silent ≥ 14 h")
    assert.equal(offlineNote([{ seconds_since_update: null }, { seconds_since_update: 43201 }]), "silent ≥ 12 h · 1 never reported")
    assert.equal(offlineNote([{ seconds_since_update: null }]), "1 never reported")
})

test("distance: Trips report precision is kept (no rounding to whole km), unknown stays null, 0 is 0", () => {
    // Floating-point sum of per-unit 0.01 km values
    const sum = [4540.25, 4541.26].reduce((a, b) => a + b, 0)
    assert.equal(roundKm2(sum), 9081.51)
    assert.equal(formatKm2(sum), "9,081.51 km")
    assert.equal(formatKm2(0), "0.00 km")
    assert.equal(formatKm2(null), null)
    assert.equal(roundKm2(undefined), null)
})

test("Dashboard wires the Fleet Now rules and shows unavailable data as —", () => {
    const src = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.match(src, /activeTodaySummary\(filteredVehicles/)
    // Before the first vehicles response: a skeleton while loading, "—" once it failed; never a fleet of 0
    assert.match(src, /fleetValue = \(count\) => \(vehiclesLoaded \? count : error \? "—" : <Pending \/>\)/)
    assert.match(src, /distance: distanceAllUnknown \? "—" : formatKm2\(distanceKm\)/)
    assert.doesNotMatch(src, /no signal > 12 h/, "the threshold is server-configurable and not hardcoded in the label")
    assert.doesNotMatch(src, /todayAlerts/, "alerts are not filtered by a vehicle's last-message date")
})

test("today's mileage: a successful current response is used as is, with no notice", () => {
    const res = mileage([{ vehicle_id: 1, mileage: 0 }], { stale: false, total_km: 0 })
    const s = todayMileageState(res, { refreshFailed: false, todayStr: TODAY })
    assert.equal(s.data, res, "the response itself is used: calculation unchanged, a genuine 0 km stays 0")
    assert.equal(s.notice, null)
})

test("today's mileage: a failed refresh keeps the last successful result but marks it not current", () => {
    const res = mileage([{ vehicle_id: 1, mileage: 12.5 }])
    const s = todayMileageState(res, { refreshFailed: true, todayStr: TODAY })
    assert.equal(s.data, res)
    assert.equal(s.notice.text, "Not current: refresh failed")
})

test("today's mileage: a backend result served after a Wialon failure (stale) is marked not current", () => {
    const s = todayMileageState(mileage([], { stale: true }), { refreshFailed: false, todayStr: TODAY })
    assert.equal(s.notice.text, "Not current: Wialon unavailable")
})

test("today's mileage: never loaded and failing is unavailable; loading is not an error", () => {
    assert.deepEqual(todayMileageState(null, { refreshFailed: true, todayStr: TODAY }).notice.text, "Mileage report unavailable")
    assert.deepEqual(todayMileageState(null, { refreshFailed: false, todayStr: TODAY }), { data: null, notice: null })
})

test("today's mileage: after UAE midnight a previous day's result is never shown as today's", () => {
    const yesterdays = { ...mileage([{ vehicle_id: 1, mileage: 300 }]), effective_from: YESTERDAY_START }
    const failing = todayMileageState(yesterdays, { refreshFailed: true, todayStr: TODAY })
    assert.equal(failing.data, null)
    assert.equal(failing.notice.text, "Mileage report unavailable")
    assert.equal(todayMileageState(yesterdays, { refreshFailed: false, todayStr: TODAY }).data, null)
})

test("Dashboard: Distance shows no report-window line, uses the validated result and records refresh failures", () => {
    const src = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.doesNotMatch(src, /reportWindow|Report window unavailable/, "the window timestamp under Distance is removed")
    assert.match(src, /calculateTimeframeActivity\(todayMileage\.data, todayTrips, todayIdle\)/)
    assert.match(src, /setTodayMileageData\(res\); setTodayMileageFailed\(false\)/)
    assert.match(src, /err => \{ if \(isCurrent\(\)\) setTodayMileageFailed\(true\); throw err \}/)
    assert.match(src, /notice: todayMileage\.notice/)
})

test("section state: no data is pending until its request fails, never an empty or zero result", () => {
    assert.equal(sectionState(null, false), "pending")
    assert.equal(sectionState(undefined, false), "pending")
    assert.equal(sectionState(null, true), "failed")
    assert.equal(sectionState({ vehicles: [] }, false), "ready", "a real empty response is ready (its own empty state)")
    assert.equal(sectionState({ total: 0 }, true), "ready", "a failed refresh keeps the data shown")
})

test("Dashboard renders its layout without a page-level loading gate", () => {
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.doesNotMatch(page, /LoadingState/, "no full-page loading state")
    assert.match(page, /SectionPlaceholder state=\{idleState\}/)
    assert.match(page, /row\.state === "pending" \? <Pending/)
})

test("UAE day guard: a report is used only for the day its window starts on", () => {
    const trips = { from_time: DAY_START, vehicles: [] }
    assert.equal(forUaeDay(trips, TODAY), trips)
    assert.equal(forUaeDay({ from_time: YESTERDAY_START }, TODAY), null, "loaded before midnight: not today's")
    assert.equal(forUaeDay({ from_time: DAY_START - 1 }, TODAY), null, "23:59:59 UAE belongs to the previous day")
    const idle = { timeframe: { from_time: DAY_START } }
    assert.equal(forUaeDay(idle, TODAY, d => d.timeframe?.from_time), idle, "idle analytics keep the window in timeframe")
    assert.equal(forUaeDay({ timeframe: null }, TODAY, d => d.timeframe?.from_time), null, "no window: not used")
    assert.equal(forUaeDay(null, TODAY), null)
    assert.equal(forUaeDay({ from_time: YESTERDAY_START }, previousUaeDay(TODAY)).from_time, YESTERDAY_START)
    assert.equal(previousUaeDay("2026-10-27"), "2026-10-26")
    assert.equal(previousUaeDay("2026-03-01"), "2026-02-28")
    assert.equal(previousUaeDay("2027-01-01"), "2026-12-31")
})

test("report notice: failure outranks the earlier-visit label; nothing while pending", () => {
    assert.equal(reportNotice("pending", { earlier: true }), null)
    assert.equal(reportNotice("failed", { earlier: true }).text, "Report unavailable, retrying")
    assert.equal(reportNotice("ready", { failed: true, earlier: true }).text, "Not current: refresh failed")
    assert.equal(reportNotice("ready", { earlier: true }), EARLIER_RESULT_NOTICE)
    assert.equal(reportNotice("ready"), null, "data from this visit carries no notice")
})

test("Dashboard: every today/yesterday section reads day-guarded data and labels an earlier visit's data", () => {
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    const topMileage = page.slice(page.indexOf("const topMileageVehicles"), page.indexOf("// Currently Idling"))
    assert.doesNotMatch(topMileage, /todayMileageData|\btripsData\b/, "the chart never reads unguarded payloads")
    assert.match(topMileage, /todayMileage\.data\?\.all_units/)
    assert.match(page, /const topMileageState = sectionState\(todayMileage\.data \?\? todayTrips,/)
    assert.match(page, /const tripsState = sectionState\(todayTrips,/)
    assert.match(page, /const idleState = sectionState\(todayIdle,/)
    const idleCharts = page.slice(page.indexOf("const idleTimeByGroupData"), page.indexOf("const topMileageVehicles"))
    assert.doesNotMatch(idleCharts, /idleAnalyticsData/, "idle charts never read the unguarded payload")
    assert.match(page, /const todayIdle = forUaeDay\(idleAnalyticsData, todayStr, d => d\.timeframe\?\.from_time\)/)
    // An earlier visit's cached report is labelled until this visit's first successful request clears it
    assert.match(page, /setEarlier\(e => \(e\[key\] \? \{ \.\.\.e, \[key\]: false \} : e\)\)/)
    assert.match(page, /reportNotice\(tripsState, \{ failed: auxFailed\.trips, earlier: earlier\.trips \}\)/)
    assert.equal((page.match(/subtitle=\{earlierSubtitle\(/g) || []).length, 4, "charts and the comparison card")
})

test("yesterday cells: each keeps its earlier-visit label until its own request succeeds", () => {
    const trips = { vehicles: [] }
    const idle = { by_vehicle: [] }
    const EARLIER = EARLIER_RESULT_NOTICE.text
    // Both trips and idle shown from the client cache; nothing answered yet
    let cells = yesterdayCellStates({ mileage: null, trips, idle }, { earlier: { trips: true, idle: true } })
    assert.deepEqual(Object.values(cells).map(c => c.notice?.text), [EARLIER, EARLIER, EARLIER, EARLIER])
    // Only the trips request succeeded: driving and idle are still refreshing and keep their label
    cells = yesterdayCellStates({ mileage: null, trips, idle }, { earlier: { trips: false, idle: true } })
    assert.equal(cells.trips.notice, null)
    assert.equal(cells.distance.notice, null, "distance reads the refreshed trips while mileage has not answered")
    assert.equal(cells.driving.notice.text, EARLIER)
    assert.equal(cells.idle.notice.text, EARLIER)
    // Only the idle request succeeded: trips (and distance read from it) keep their label
    cells = yesterdayCellStates({ mileage: null, trips, idle }, { earlier: { trips: true, idle: false } })
    assert.equal(cells.trips.notice.text, EARLIER)
    assert.equal(cells.distance.notice.text, EARLIER)
    assert.equal(cells.driving.notice, null)
    assert.equal(cells.idle.notice, null)
    // Mileage answering clears only distance (mileage is never served from the client cache)
    cells = yesterdayCellStates({ mileage: { all_units: [] }, trips, idle }, { earlier: { trips: true, idle: true } })
    assert.equal(cells.distance.notice, null)
    assert.equal(cells.trips.notice.text, EARLIER)
    assert.equal(cells.idle.notice.text, EARLIER)
})

test("yesterday cells: a failure outranks the earlier-visit label and stays in its own cells", () => {
    const trips = { vehicles: [] }
    const idle = { by_vehicle: [] }
    const cells = yesterdayCellStates({ mileage: null, trips, idle }, {
        failed: { idle: true }, earlier: { trips: true, idle: true },
    })
    assert.equal(cells.idle.notice.text, "Not current: refresh failed")
    assert.equal(cells.driving.notice.text, "Not current: refresh failed")
    assert.equal(cells.trips.notice.text, EARLIER_RESULT_NOTICE.text, "another request's failure does not touch trips")
    // No data and failed: unavailable; no data and still loading: pending without a notice
    const empty = yesterdayCellStates({ mileage: null, trips: null, idle: null }, {
        failed: { trips: true, mileage: true }, earlier: { idle: true },
    })
    assert.equal(empty.trips.state, "failed")
    assert.equal(empty.trips.notice.text, "Report unavailable, retrying")
    assert.equal(empty.distance.state, "failed", "distance is unavailable only when mileage and trips both failed")
    assert.equal(empty.idle.state, "pending")
    assert.equal(empty.idle.notice, null)
    const mileageOnlyFailed = yesterdayCellStates({ mileage: null, trips: null, idle: null }, { failed: { mileage: true } })
    assert.equal(mileageOnlyFailed.distance.state, "pending", "trips may still answer for distance")
    // Distance shown from trips while the mileage refresh failed: not current, ahead of the earlier label
    const fallback = yesterdayCellStates({ mileage: null, trips, idle }, { failed: { mileage: true }, earlier: { trips: true } })
    assert.equal(fallback.distance.notice.text, "Not current: refresh failed")
})

test("Dashboard: yesterday requests are tracked per report and each cell renders its own state", () => {
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    for (const key of ["yesterdayTrips", "yesterdayIdle", "yesterdayMileage"]) {
        assert.match(page, new RegExp(`tracked\\("${key}", `), key)
    }
    assert.doesNotMatch(page, /tracked\("yesterday", /, "no shared key clears every yesterday label at once")
    assert.match(page, /yesterdayTrips: Boolean\(cached\.yesterdayTrips\), yesterdayIdle: Boolean\(cached\.yesterdayIdle\)/)
    assert.match(page, /yesterdayCellById\[m\.id\]\.state === "pending"/)
    assert.match(page, /yesterdayCellById\[m\.id\]\.notice\.text/)
})

test("AuthContext: a late profile response never applies to another session", () => {
    const src = readFileSync(new URL("../context/AuthContext.jsx", import.meta.url), "utf8")
    assert.match(src, /const stillCurrent = \(\) => mounted && activeUserIdRef\.current === session\.user\.id/)
    assert.doesNotMatch(src, /if \(profileData && mounted\)/, "the background load checks the active user")
    assert.match(src, /if \(activeUserIdRef\.current === data\.user\.id\) setProfile\(fetchedProfile\)/)
    assert.equal((src.match(/activeUserIdRef\.current = null/g) || []).length, 2, "sign-out (event and logout) clears it")
})
