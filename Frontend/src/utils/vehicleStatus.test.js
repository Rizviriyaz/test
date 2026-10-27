// Offline tests for the shared live-status helper (audit H2, revised by F1). Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { LIVE_STATUSES, countByStatus, formatAge, isPositionStale, isTelemetryStale, lastSeenLabel, normalizeVehicleStatus } from "./vehicleStatus.js"

test("stale telemetry keeps the last known Moving/Idling status, never Offline", () => {
    const staleMoving = { status: "MOVING", connection_status: "DELAYED", telemetry_stale: true, last_known_status: "MOVING" }
    assert.equal(normalizeVehicleStatus(staleMoving), "MOVING")
    assert.equal(isTelemetryStale(staleMoving), true)
    assert.equal(normalizeVehicleStatus({ status: "IDLING", connection_status: "DELAYED", telemetry_stale: true }), "IDLING")
    assert.equal(normalizeVehicleStatus({ status: "MOVING", connection_status: "STABLE" }), "MOVING")
    assert.equal(isTelemetryStale({ status: "MOVING", connection_status: "STABLE", telemetry_stale: false }), false)
    assert.equal(normalizeVehicleStatus({ status: "idling" }), "IDLING")
    assert.deepEqual(LIVE_STATUSES, ["MOVING", "IDLING", "STOPPED", "OFFLINE"], "DELAYED is no longer a status")
})

test("an older payload's DELAYED status falls back to its last known state", () => {
    const legacy = { status: "DELAYED", connection_status: "DELAYED", last_known_status: "MOVING" }
    assert.equal(normalizeVehicleStatus(legacy), "MOVING")
    assert.equal(isTelemetryStale(legacy), true, "the older payload's DELAYED rule still marks it stale")
    assert.equal(normalizeVehicleStatus({ status: "DELAYED" }), "STOPPED", "unknown last state renders as STOPPED")
    assert.equal(isTelemetryStale({ status: "IDLING", connection_status: "DELAYED" }), true, "flag absent: previous rule")
})

test("a stale unit last seen parked stays STOPPED and is not flagged stale", () => {
    const parked = { status: "STOPPED", connection_status: "DELAYED", telemetry_stale: false, last_known_status: "STOPPED", ignition: false, seconds_since_update: 3600 }
    assert.equal(normalizeVehicleStatus(parked), "STOPPED")
    assert.equal(isTelemetryStale(parked), false)
    assert.equal(lastSeenLabel(parked), null)
    assert.equal(isTelemetryStale({ status: "STOPPED", connection_status: "DELAYED" }), false, "flag absent: D3 kept")
    assert.equal(normalizeVehicleStatus({ ...parked, status: "OFFLINE", connection_status: "OFFLINE", is_offline: true }), "OFFLINE")
})

test("OFFLINE takes precedence, is never stale, and unknown values stay STOPPED", () => {
    assert.equal(normalizeVehicleStatus({ status: "DELAYED", is_offline: true }), "OFFLINE")
    assert.equal(normalizeVehicleStatus({ status: "MOVING", connection_status: "OFFLINE" }), "OFFLINE")
    assert.equal(isTelemetryStale({ status: "OFFLINE", is_offline: true, telemetry_stale: true }), false)
    assert.equal(normalizeVehicleStatus({ status: "SOMETHING" }), "STOPPED")
    assert.equal(normalizeVehicleStatus({}), "STOPPED")
    assert.equal(normalizeVehicleStatus(null), "STOPPED")
})

test("counts partition the fleet; stale vehicles are counted under their last known status, never as Offline", () => {
    const fleet = [
        { status: "MOVING" }, { status: "IDLING" }, { status: "STOPPED" },
        { status: "MOVING", connection_status: "DELAYED", telemetry_stale: true },
        { status: "IDLING", connection_status: "DELAYED", telemetry_stale: true },
        { status: "OFFLINE", is_offline: true }, { status: "?" },
        { status: "STOPPED", connection_status: "DELAYED", telemetry_stale: false },
    ]
    const c = countByStatus(fleet)
    assert.deepEqual(c, { ALL: 8, MOVING: 2, IDLING: 2, STOPPED: 3, OFFLINE: 1, STALE: 2 })
    assert.equal(c.MOVING + c.IDLING + c.STOPPED + c.OFFLINE, c.ALL)
})

test("last seen label describes the last reported state and its age for stale or offline vehicles", () => {
    assert.equal(lastSeenLabel({ status: "MOVING", telemetry_stale: true, last_known_status: "MOVING", seconds_since_update: 11 * 3600 }), "Last seen Moving · 11h ago")
    assert.equal(lastSeenLabel({ status: "IDLING", telemetry_stale: true, last_known_status: "IDLING", seconds_since_update: 754 }), "Last seen Idling · 12 min ago")
    assert.equal(lastSeenLabel({ status: "OFFLINE", is_offline: true, last_known_status: null, seconds_since_update: 50000 }), "Last seen · 13h 53m ago")
    assert.equal(lastSeenLabel({ status: "MOVING", last_known_status: "MOVING", seconds_since_update: 30, telemetry_stale: false }), null)
    assert.equal(lastSeenLabel({ status: "MOVING", telemetry_stale: true }), null, "nothing known to show")
    assert.equal(formatAge(null), null)
})

test("a stale GPS fix with recent messages shows the fix age, never the message age alone (Phase 3 P2)", () => {
    const staleFix = {
        status: "MOVING", connection_status: "STABLE", last_known_status: "MOVING", telemetry_stale: true,
        seconds_since_update: 30, seconds_since_position: 6 * 3600, position_stale: true,
    }
    assert.equal(normalizeVehicleStatus(staleFix), "MOVING")
    assert.equal(isPositionStale(staleFix), true)
    assert.equal(isTelemetryStale(staleFix), true)
    assert.equal(lastSeenLabel(staleFix), "Last GPS fix Moving · 6h ago (data 0 min ago)")
    // Fix age unknown: still labelled as a GPS fix, never "Last seen · 0 min ago"
    assert.equal(lastSeenLabel({ ...staleFix, seconds_since_position: null }), "Last GPS fix Moving (data 0 min ago)")
    // Not stale: unchanged behaviour (message age), including an old fix on a live or parked unit
    assert.equal(isPositionStale({ ...staleFix, position_stale: false }), false)
    assert.equal(lastSeenLabel({ status: "MOVING", position_stale: false, telemetry_stale: false, seconds_since_position: 30, seconds_since_update: 30 }), null)
    assert.equal(lastSeenLabel({ status: "STOPPED", connection_status: "DELAYED", position_stale: false, telemetry_stale: false, seconds_since_position: 9000 }), null)
    // OFFLINE wins over a stale flag: the message age is reported
    assert.equal(isPositionStale({ ...staleFix, is_offline: true }), false)
    assert.equal(lastSeenLabel({ ...staleFix, status: "OFFLINE", is_offline: true, seconds_since_update: 50000 }), "Last seen Moving · 13h 53m ago")
})

test("Tracking and Dashboard no longer fold stale vehicles into Offline (source checks)", async () => {
    const { readFileSync } = await import("node:fs")
    const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8")
    const tracking = read("../pages/Tracking.jsx")
    assert.doesNotMatch(tracking, /counts\.OFFLINE \+ counts\.DELAYED/)
    assert.doesNotMatch(tracking, /"DELAYED" \? "OFFLINE"/)
    assert.match(tracking, /isTelemetryStale\(vehicle\) &&/, "the Stale badge follows telemetry_stale")
    const dashboard = read("../pages/Dashboard.jsx")
    assert.doesNotMatch(dashboard, /offlineCount \+ delayedCount/)
    assert.match(dashboard, /key: "OFFLINE", label: "Offline", count: offlineCount,/)
})

test("Tracking, drawer and Health page read the GPS fix age separately (source checks)", async () => {
    const { readFileSync } = await import("node:fs")
    const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8")
    const tracking = read("../pages/Tracking.jsx")
    assert.match(tracking, /isPositionStale\(vehicle\)[\s\S]{0,200}seconds_since_position/, "Tracking list shows the fix age")
    const drawer = read("../components/Vehicles/VehicleDetailDrawer.jsx")
    assert.match(drawer, /label="Last GPS fix"/)
    assert.match(drawer, /position_timestamp/)
    // A 0 timestamp must not render a stray "0" (a bare `x && <Row>` renders 0)
    assert.match(drawer, /Boolean\(currentVehicle\.position_timestamp\) &&/)
    assert.doesNotMatch(drawer, /\{currentVehicle\.position_timestamp &&/)
    // A stale fix labels coordinates and satellites as last-reported
    assert.match(drawer, /positionStale \? "Last coordinates" : "Coordinates"/)
    assert.match(drawer, /positionStale \? "Last satellites" : "Satellites"/)
    assert.match(drawer, /at last fix/)
    // Unknown ignition is shown as Unknown, never Off; the idle timer stops at the last message
    assert.match(drawer, /rawIgnition === null \? "UNKNOWN"/)
    assert.match(drawer, /endTime=\{currentVehicle\.last_message_timestamp\}/)
    const health = read("../pages/VehicleHealth.jsx")
    assert.match(health, /s === "STALE"/, "STALE GPS badge")
    assert.match(health, /filter === "warning"[^\n]*gps\?\.status === "STALE"/, "STALE listed under warnings")
    // The Healthy filter uses the shared rule (fleetRules.isHealthyVehicle excludes STALE GPS; unit-tested there)
    assert.match(health, /filter === "healthy"[^\n]*isHealthyVehicle/, "STALE never healthy")
})
