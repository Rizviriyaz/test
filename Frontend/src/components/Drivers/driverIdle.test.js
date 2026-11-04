import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { buildDriverIdleIndex, driverIdleSummary, eventVehicleLabel, normalizeDriverName } from "./driverIdle.js"

const assigned = (vehicle_id) => ({ status: "assigned", vehicle_id, groups: ["Transport"] })
// Shaped like /api/drivers rows and /api/idle/events events (test fixtures only)
const drivers = [
    { driver_id: "100101", name: "Aarav Kumar", assignment: assigned(1) },
    { driver_id: "100102", name: "Bilal Khan", assignment: { status: "unassigned", groups: [] } },
    { driver_id: "100103", name: "Same Name", assignment: assigned(3) },
    { driver_id: "100104", name: "same  name", assignment: assigned(4) },
    { driver_id: null, name: "Un-assigned A 74", assignment: { status: "unassigned", groups: [] } },
    { driver_id: "100105", name: null, assignment: assigned(5) },
    { driver_id: "100106", name: "Chen Li", assignment: assigned(6) },
]
const ev = (vehicle_id, start_time, duration, driver, extra = {}) => ({
    id: `${vehicle_id}-${start_time}`, vehicle_id, start_time, end_time: start_time + duration + 60, duration, driver,
    registration_plate: `P ${vehicle_id}`, vehicle_name: `Unit ${vehicle_id}`, ...extra,
})
const idleData = {
    events_complete: true,
    events: [
        ev(1, 2000, 300, "Aarav Kumar"),
        ev(9, 1000, 120, " aarav   KUMAR "),              // another vehicle: attributed by the report's driver, not assignment
        ev(1, 3000, 600, "Somebody Else"),                 // on Aarav's assigned vehicle but another driver: not his
        ev(3, 1500, 900, "Same Name"),                     // name shared by two Wialon drivers: never attributed
        ev(7, 1600, 400, "Un-assigned A 74"),
        ev(5, 1700, 200, ""),                              // no driver in the report
        ev(6, 1800, 100, null),
    ],
}

test("events are attributed by the Idle Report's driver name, never by the current vehicle assignment", () => {
    const index = buildDriverIdleIndex(drivers, idleData)
    const aarav = driverIdleSummary(drivers[0], index)
    assert.equal(aarav.status, "ok")
    assert.deepEqual(aarav.events.map((e) => e.id), ["9-1000", "1-2000"], "both his events, sorted by start; not the other driver's event on his vehicle")
    assert.equal(aarav.count, 2)
    assert.equal(aarav.totalSeconds, 420, "total idle is the sum of the attributed events' durations")
    assert.equal(aarav.partial, false)

    const chen = driverIdleSummary(drivers[6], index)
    assert.deepEqual([chen.status, chen.count, chen.totalSeconds], ["ok", 0, 0], "an event without a report driver is never attributed by assignment")
    const bilal = driverIdleSummary(drivers[1], index)
    assert.deepEqual([bilal.count, bilal.totalSeconds], [0, 0], "an unassigned driver with no events has zero, not unknown")
})

test("drivers whose name cannot identify them get no events and an unknown (null) total", () => {
    const index = buildDriverIdleIndex(drivers, idleData)
    for (const [driver, status] of [[drivers[2], "duplicate_name"], [drivers[3], "duplicate_name"], [drivers[4], "placeholder"], [drivers[5], "no_name"]]) {
        const s = driverIdleSummary(driver, index)
        assert.deepEqual([s.status, s.count, s.totalSeconds, s.events.length], [status, null, null, 0], String(driver.name))
    }
})

test("partial fleet reports make the total a lower bound; missing data is handled", () => {
    const partial = buildDriverIdleIndex(drivers, { ...idleData, events_complete: false })
    assert.equal(driverIdleSummary(drivers[0], partial).partial, true)
    const empty = buildDriverIdleIndex(drivers, { events: [] })
    assert.deepEqual([driverIdleSummary(drivers[0], empty).count, driverIdleSummary(drivers[0], empty).totalSeconds], [0, 0])
    const none = buildDriverIdleIndex(null, null)
    assert.equal(driverIdleSummary(drivers[0], none).count, 0)
    assert.equal(normalizeDriverName("  A\tB  "), "a b")
    assert.equal(eventVehicleLabel({ vehicle_name: "Unit 1" }), "Unit 1")
    assert.equal(eventVehicleLabel({}), null)
})

test("table column and drawer use one summary and today's UAE fleet idle events", () => {
    const page = readFileSync(new URL("../../pages/Drivers.jsx", import.meta.url), "utf8")
    const modal = readFileSync(new URL("./DriverDetailModal.jsx", import.meta.url), "utf8")
    assert.match(page, /getIdleEvents\(\{ timeframe: "today", signal: controller\.signal \}\)/)
    assert.match(page, /<IdleTimeCell summary=\{idleOf\(d\)\}/)
    assert.match(page, /summary: idleOf\(selected\)/, "the drawer gets the same summary as the table row")
    assert.doesNotMatch(page + modal, /assignment\?*\.vehicle_id\s*===|vehicle_id\) === String/, "events are never matched by assigned vehicle")
    assert.match(modal, /Idle events today \(/)
    assert.match(modal, /aria-expanded=\{expanded\}/, "events expand to their details")
})
