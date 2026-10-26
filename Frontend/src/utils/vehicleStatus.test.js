// Offline tests for the shared live-status helper (audit H2). Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { countByStatus, formatAge, lastSeenLabel, normalizeVehicleStatus } from "./vehicleStatus.js"

test("DELAYED is its own status, never live Moving/Idling", () => {
    assert.equal(normalizeVehicleStatus({ status: "DELAYED", connection_status: "DELAYED", last_known_status: "MOVING" }), "DELAYED")
    // A record still carrying the last computed state but a DELAYED connection is not shown as live either
    assert.equal(normalizeVehicleStatus({ status: "MOVING", connection_status: "DELAYED" }), "DELAYED")
    assert.equal(normalizeVehicleStatus({ status: "MOVING", connection_status: "STABLE" }), "MOVING")
    assert.equal(normalizeVehicleStatus({ status: "IDLING", connection_status: "DELAYED" }), "DELAYED")
    assert.equal(normalizeVehicleStatus({ status: "idling" }), "IDLING")
})

test("a stale unit last seen parked stays STOPPED until OFFLINE", () => {
    const parked = { status: "STOPPED", connection_status: "DELAYED", last_known_status: "STOPPED", ignition: false, seconds_since_update: 3600 }
    assert.equal(normalizeVehicleStatus(parked), "STOPPED")
    assert.equal(lastSeenLabel(parked), null)
    assert.equal(normalizeVehicleStatus({ ...parked, status: "OFFLINE", connection_status: "OFFLINE", is_offline: true }), "OFFLINE")
})

test("OFFLINE takes precedence and unknown values stay STOPPED", () => {
    assert.equal(normalizeVehicleStatus({ status: "DELAYED", is_offline: true }), "OFFLINE")
    assert.equal(normalizeVehicleStatus({ status: "MOVING", connection_status: "OFFLINE" }), "OFFLINE")
    assert.equal(normalizeVehicleStatus({ status: "SOMETHING" }), "STOPPED")
    assert.equal(normalizeVehicleStatus({}), "STOPPED")
    assert.equal(normalizeVehicleStatus(null), "STOPPED")
})

test("counts partition the fleet", () => {
    const fleet = [
        { status: "MOVING" }, { status: "IDLING" }, { status: "STOPPED" },
        { status: "DELAYED", connection_status: "DELAYED" }, { status: "OFFLINE", is_offline: true }, { status: "?" },
        { status: "STOPPED", connection_status: "DELAYED" },
    ]
    const c = countByStatus(fleet)
    assert.deepEqual(c, { ALL: 7, MOVING: 1, IDLING: 1, STOPPED: 3, DELAYED: 1, OFFLINE: 1 })
    assert.equal(c.MOVING + c.IDLING + c.STOPPED + c.DELAYED + c.OFFLINE, c.ALL)
})

test("last seen label describes the last reported state and its age", () => {
    assert.equal(lastSeenLabel({ status: "DELAYED", last_known_status: "MOVING", seconds_since_update: 11 * 3600 }), "Last seen Moving · 11h ago")
    assert.equal(lastSeenLabel({ status: "DELAYED", last_known_status: "IDLING", seconds_since_update: 754 }), "Last seen Idling · 12 min ago")
    assert.equal(lastSeenLabel({ status: "OFFLINE", is_offline: true, last_known_status: null, seconds_since_update: 50000 }), "Last seen · 13h 53m ago")
    assert.equal(lastSeenLabel({ status: "MOVING", last_known_status: "MOVING", seconds_since_update: 30 }), null)
    assert.equal(lastSeenLabel({ status: "DELAYED" }), null)
    assert.equal(formatAge(null), null)
})
