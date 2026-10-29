// Offline regression tests: unknown (null) idle, engine and mileage values are never summed as zero.
// Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
    PARTIAL_EVENTS_NOTE, compareKnownDesc, drivingSecondsByVehicle, formatKm, idleSecondsOf, inMotionSecondsOf,
    isPartialReport, partialEventsNote, sumKnown, sumKnownInBoth, tripDistanceOf, tripsDistanceIncomplete, tripsDistanceKm,
} from "./knownTotals.js"

test("sumKnown keeps known values (including a recorded 0) and reports unknown ones", () => {
    const units = [{ mileage: 12.5 }, { mileage: null }, { mileage: 0 }, { mileage: undefined }, { mileage: 7.5 }]
    assert.deepEqual(sumKnown(units, u => u.mileage), { total: 20, known: 3, unknown: 2, partial: true, allUnknown: false })
})

test("sumKnown marks an all-unknown set instead of reporting 0", () => {
    assert.deepEqual(sumKnown([{ m: null }, { m: null }], u => u.m), { total: 0, known: 0, unknown: 2, partial: true, allUnknown: true })
    assert.deepEqual(sumKnown([], u => u.m), { total: 0, known: 0, unknown: 0, partial: false, allUnknown: false })
})

test("idleSecondsOf: total_idle_seconds null is unknown, not a fallback to 0", () => {
    assert.equal(idleSecondsOf({ total_idle_seconds: null, idle_seconds: 999 }), null)
    assert.equal(idleSecondsOf({ total_idle_seconds: 0 }), 0)
    assert.equal(idleSecondsOf({ total_idle_seconds: 120 }), 120)
    // Older payload shapes are still read when the key is absent
    assert.equal(idleSecondsOf({ idle_seconds: 60 }), 60)
    assert.equal(idleSecondsOf({ idle_hours: 0.5 }), 1800)
    assert.equal(idleSecondsOf({}), null)
})

test("fleet idle total over by_vehicle with an unattributable (duplicate-name) vehicle is partial", () => {
    const byVehicle = [
        { vehicle_id: 1, total_idle_seconds: null, idle_minutes: null },
        { vehicle_id: 2, total_idle_seconds: null, idle_minutes: null },
        { vehicle_id: 3, total_idle_seconds: 900, idle_minutes: 15 },
    ]
    const idle = sumKnown(byVehicle, idleSecondsOf)
    assert.equal(idle.total, 900)
    assert.equal(idle.unknown, 2)
    assert.equal(idle.partial, true)
})

test("tripDistanceOf: a trip flagged with an unreadable distance cell is unknown, not 0 km", () => {
    const trips = [
        { distance_km: 10.2 },
        { distance_km: 0.0, incomplete_fields: ["distance_km"] },
        { distance_km: 0.0 },
    ]
    assert.equal(tripDistanceOf(trips[1]), null)
    assert.equal(tripDistanceOf(trips[2]), 0)
    const sum = sumKnown(trips, tripDistanceOf)
    assert.equal(sum.total, 10.2)
    assert.equal(sum.unknown, 1)
})

test("compareKnownDesc sorts known values descending and unknown values last", () => {
    const values = [null, 5, 0, undefined, 12]
    assert.deepEqual([...values].sort(compareKnownDesc), [12, 5, 0, null, undefined])
})

test("dashboard no longer coerces nullable idle/mileage with || 0 or ?? 0 fallbacks", () => {
    // Static guard alongside the behaviour tests above: the Dashboard is a React page without a DOM test
    // harness, so this checks it routes these values through the null-aware helpers.
    const dashboard = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.doesNotMatch(dashboard, /Number\(u\.mileage \|\| 0\)/)
    assert.doesNotMatch(dashboard, /v\.total_idle_seconds \?\? v\.idle_seconds/)
    assert.match(dashboard, /sumKnown\(filteredUnits, u => u\.mileage\)/)
    assert.match(dashboard, /sumKnown\(matchingVehicles, idleSecondsOf\)/)
    const drawer = readFileSync(new URL("../components/Vehicles/VehicleDetailDrawer.jsx", import.meta.url), "utf8")
    assert.doesNotMatch(drawer, /Number\(t\.distance_km \|\| 0\)/)
})

test("formatKm: one shared display for a mileage value; unknown stays unknown", () => {
    assert.equal(formatKm(268.6), "268.6 km")
    assert.equal(formatKm(268.64), "268.6 km")
    assert.equal(formatKm(1234.56), "1,234.6 km")
    assert.equal(formatKm(0), "0.0 km")
    assert.equal(formatKm(null), null)
    assert.equal(formatKm(undefined), null)
})

test("mileage chart and vehicle drawer show the same Wialon value the same way", () => {
    const dashboard = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    // A whole-km chart value (269) disagreed with the drawer (268.6) for the same unit and day
    assert.doesNotMatch(dashboard, /mileage: isKnownNumber\(u\.mileage\) \? Math\.round/)
    assert.match(dashboard, /formatter=\{\(val\) => \[formatKm\(val\) \?\? "Unknown \(unreadable\)", "Mileage"\]\}/)
    const drawer = readFileSync(new URL("../components/Vehicles/VehicleDetailDrawer.jsx", import.meta.url), "utf8")
    assert.match(drawer, /formatKm\(todayDistance\) \?\? "—"/)
})

test("partialEventsNote flags incomplete idle events and stays silent when complete or absent", () => {
    assert.equal(partialEventsNote({ events_complete: false }), PARTIAL_EVENTS_NOTE)
    assert.equal(partialEventsNote({ events_complete: true }), null)
    assert.equal(partialEventsNote({}), null)
    assert.equal(partialEventsNote(null), null)
})

test("Idle analytics labels its event figures as Wialon engine-on periods, not continuous idle stops", () => {
    // Verified live 2026-10-27: an Engine Hours sub-row is an engine-on period (69% also include driving) whose
    // Idling cell may add up several idle stops, so the event KPIs must not read as single idle stops
    const page = readFileSync(new URL("../pages/IdleAnalytics.jsx", import.meta.url), "utf8")
    assert.match(page, /sub=\{eventsPending \? "Loading events…" : eventsNote\}/)
    assert.match(page, /label="Most Idle in One Period"/)
    assert.match(page, /Most idle time Wialon reports within one engine-on period/)
    assert.doesNotMatch(page, /<span>Longest Idle<\/span>|Per idle occurrence|"Recorded in period"/)
})

test("Idle analytics labels trend, hotspots and the event list when events are partial", () => {
    const page = readFileSync(new URL("../pages/IdleAnalytics.jsx", import.meta.url), "utf8")
    assert.match(page, /const eventsNote = eventsPending \? "Loading idle events…" : partialEventsNote\(analytics\)/)
    // The trend shows the partial-events note as its only subtitle
    assert.match(page, /title="Idle Duration Trend"\s+subtitle=\{eventsNote \|\| undefined\}/)
    assert.match(page, /subtitle=\{eventsNote \? `Top 10 locations · \$\{eventsNote\}` : undefined\}/)
    assert.match(page, /subtitle=\{eventsNote \? `Ranked idle locations · \$\{eventsNote\}` : /, "hotspot table note")
    assert.equal((page.match(/\{eventsNote && \(/g) || []).length, 1, "event list note")
    assert.match(page, /\{eventsPending \? "…" : \(kpis\.idle_event_count \?\? "—"\)\}/,
        "unknown event count renders as a dash (and as loading while the full response is pending)")
})

test("Vehicle drawer marks partial or failed idle events instead of a definitive count", () => {
    const drawer = readFileSync(new URL("../components/Vehicles/VehicleDetailDrawer.jsx", import.meta.url), "utf8")
    assert.match(drawer, /setIdleEventsStatus\(res\?\.events_complete === false \? "partial" : "complete"\)/)
    assert.match(drawer, /setIdleEventsStatus\(idleEventsComplete === false \? "partial" : "complete"\)/)
    assert.match(drawer, /setIdleEventsStatus\("error"\)/)
    assert.doesNotMatch(drawer, /if \(active\) setVehicleIdleEvents\(\[\]\)/, "a failed fetch is not 'no idle events'")
    const page = readFileSync(new URL("../pages/IdleAnalytics.jsx", import.meta.url), "utf8")
    assert.match(page, /idleEvents=\{rangePreset === "today" && !showingSummary \? analytics\?\.events : null\}/,
        "only today's full event list is handed to the drawer (never the events-free summary)")
    assert.match(page, /idleEventsComplete=\{drawerEventsComplete\}/)
    assert.match(page, /vehicle=\{drawerVehicleWithLive\}/, "live status comes from by_vehicle, not the event row")
})

test("tripsDistanceIncomplete: a dropped malformed trip row makes the listed trips' sum partial", () => {
    // Every listed trip is readable, but the backend dropped a malformed row: the sum alone looks complete
    const tripsData = { trips: [{ distance_km: 10 }, { distance_km: 5 }], incomplete_fields: ["distance_km", "row_shape"] }
    assert.equal(sumKnown(tripsData.trips, tripDistanceOf).partial, false)
    assert.equal(tripsDistanceIncomplete(tripsData), true)
    assert.equal(tripsDistanceIncomplete({ trips: [], incomplete_fields: [] }), false)
    assert.equal(tripsDistanceIncomplete({ trips: [] }), false)
    assert.equal(tripsDistanceIncomplete(null), false)
})

test("Vehicle drawer shows the partial note when the trips response flags distance_km incomplete", () => {
    const drawer = readFileSync(new URL("../components/Vehicles/VehicleDetailDrawer.jsx", import.meta.url), "utf8")
    assert.match(drawer, /const distanceIncomplete = currentVehicle\.today_mileage == null && tripsDistanceIncomplete\(tripsData\)/)
    assert.match(drawer, /\(distanceIncomplete \? "Partial: some Wialon trip rows could not be read" : null\)/)
    assert.match(drawer, /hint=\{!loadingTrips \? distanceHint : null\}/)
})

test("tripsDistanceKm: no readable trip distance is unknown (null), never 0 km", () => {
    // Every Wialon trip row was malformed and dropped: an empty list flagged incomplete is unknown, not 0.0 km
    assert.equal(tripsDistanceKm({ trips: [], trip_count: 0, incomplete_fields: ["row_shape", "distance_km"] }), null)
    // Every listed trip's distance unreadable
    assert.equal(tripsDistanceKm({ trips: [{ distance_km: null, incomplete_fields: ["distance_km"] }], incomplete_fields: ["distance_km"] }), null)
    // Some rows dropped, others readable: the known sum is kept (the drawer marks it partial)
    assert.equal(tripsDistanceKm({ trips: [{ distance_km: 10 }, { distance_km: 5 }], incomplete_fields: ["distance_km", "row_shape"] }), 15)
    // Complete data: a legitimate zero stays 0 km
    assert.equal(tripsDistanceKm({ trips: [], trip_count: 0, incomplete_fields: [] }), 0)
    assert.equal(tripsDistanceKm({ trips: [{ distance_km: 0 }], incomplete_fields: [] }), 0)
    assert.equal(tripsDistanceKm(null), null)
})

test("Vehicle drawer takes its trips distance from tripsDistanceKm and shows — for unknown", () => {
    const drawer = readFileSync(new URL("../components/Vehicles/VehicleDetailDrawer.jsx", import.meta.url), "utf8")
    assert.match(drawer, /const tripsDistance = tripsDistanceKm\(tripsData\)/)
    assert.doesNotMatch(drawer, /total_distance_km \?\? \(tripsData \? 0 : null\)/)
})

// ─── H5: driving time (engine on, moving) from Wialon Engine Hours "In motion" ───

const DAY = 1793131200 // a UAE day start (epoch seconds)
const idlePayload = (rows, from = DAY) => ({ timeframe: { from_time: from }, by_vehicle: rows })
const motionRow = (id, seconds, engineKm) => ({ vehicle_id: id, in_motion_seconds: seconds, engine_mileage_km: engineKm })

test("inMotionSecondsOf reads a number and treats null or a missing key as unknown", () => {
    assert.equal(inMotionSecondsOf({ in_motion_seconds: 3600 }), 3600)
    assert.equal(inMotionSecondsOf({ in_motion_seconds: 0 }), 0)
    assert.equal(inMotionSecondsOf({ in_motion_seconds: null }), null)
    assert.equal(inMotionSecondsOf({}), null)
    assert.equal(inMotionSecondsOf(null), null)
})

test("the six inspected mileage discrepancies keep their In motion values: Trips and Engine Hours mileage may differ", () => {
    // [unit, Trips (Dashboard) km, Engine Hours km, In motion seconds] from the 25 Oct 2026 inspection
    const cases = [[54836, 59.4, 1.7, 294], [72031, 42.7, 214.0, 14340], [99238, 26.8, 125.0, 8760],
        [40630, 26.1, 46.3, 3960], [42897, 9.9, 5.3, 420], [94181, 496.0, 536.5, 37320]]
    const byVehicle = drivingSecondsByVehicle(
        idlePayload(cases.map(([id, , engineKm, motion]) => motionRow(id, motion, engineKm))),
    )
    for (const [id, , , motion] of cases) assert.equal(byVehicle.get(String(id)), motion, `unit ${id}`)
})

test("drivingSecondsByVehicle uses In motion as reported, including a legitimate zero and an unknown engine mileage", () => {
    const byVehicle = drivingSecondsByVehicle(
        idlePayload([motionRow(1, 5400, 101.5), motionRow(2, 0, 0), motionRow(3, 1800, null), motionRow(4, 0, 12.5)]),
    )
    assert.equal(byVehicle.get("1"), 5400, "valid positive driving time accepted")
    assert.equal(byVehicle.get("2"), 0, "In motion 0, mileage 0: a real 0")
    assert.equal(byVehicle.get("3"), 1800, "unreadable Engine Hours mileage does not reject a readable In motion value")
    assert.equal(byVehicle.get("4"), 0, "In motion 0, mileage > 0: a real 0, not unknown")
    assert.deepEqual(sumKnown([...byVehicle.values()], s => s), { total: 7200, known: 4, unknown: 0, partial: false, allUnknown: false },
        "zeros are counted as known and included in the total")
})

test("drivingSecondsByVehicle: missing or unreadable In motion stays unknown, never 0", () => {
    const rows = [motionRow(1, null, 10), { vehicle_id: 2, engine_mileage_km: 10 }, motionRow(3, "", 10), motionRow(4, 600, 10)]
    const byVehicle = drivingSecondsByVehicle(idlePayload(rows))
    assert.deepEqual([...byVehicle.entries()], [["1", null], ["2", null], ["3", null], ["4", 600]],
        "null In motion (unreadable or unattributed), absent key and non-numeric value are unknown")
    const sum = sumKnown([...byVehicle.values()], s => s)
    assert.deepEqual(sum, { total: 600, known: 1, unknown: 3, partial: true, allUnknown: false })
    assert.equal(drivingSecondsByVehicle(null).size, 0, "no idle payload (report failed): no units")
    assert.equal(drivingSecondsByVehicle({ by_vehicle: null }).size, 0)
})

test("missing driving-time data never falls back to distance ÷ average speed", () => {
    const legacyRow = { vehicle_id: 1, gps_mileage_km: 50, avg_speed_kmh: 50, total_idle_seconds: 60 }
    const byVehicle = drivingSecondsByVehicle(idlePayload([legacyRow]))
    assert.equal(byVehicle.get("1"), null)
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.doesNotMatch(page, /avg_speed_kmh/, "the Dashboard no longer reads average speed")
    assert.doesNotMatch(page, /Est\. [Dd]riving/)
})

test("group-filtered driving totals exclude other groups and count unknown units for Partial", () => {
    const idle = idlePayload([motionRow(1, 3600, 50), motionRow(2, 1800, 20), motionRow(3, null, 5), motionRow(9, 7200, 80)])
    const group = new Set(["1", "2", "3"])
    const byVehicle = drivingSecondsByVehicle(idle, id => group.has(id))
    assert.deepEqual([...byVehicle.keys()], ["1", "2", "3"], "unit 9 (other group) left out entirely")
    const sum = sumKnown([...byVehicle.values()], s => s)
    assert.deepEqual(sum, { total: 5400, known: 2, unknown: 1, partial: true, allUnknown: false },
        "unit 3 (unknown In motion) is unknown, not 0")
})

test("Today and Yesterday use one driving definition; the comparison covers units known on both days", () => {
    const today = drivingSecondsByVehicle(idlePayload([motionRow(1, 3600, 50), motionRow(2, null, 60)]))
    const yesterday = drivingSecondsByVehicle(idlePayload([motionRow(1, 1800, 40), motionRow(2, 900, 20)], DAY - 86400))
    assert.deepEqual(sumKnownInBoth(today, yesterday), { a: 3600, b: 1800, units: 1 }, "unit 2 unknown today is left out of both sides")
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    // Both days pass their reports through the UAE day guard (a previous day's payload is dropped), same function
    assert.match(page, /calculateTimeframeActivity\(todayMileage\.data, todayTrips, todayIdle\)/)
    assert.match(page, /calculateTimeframeActivity\(yesterdayMileage, yesterdayTrips, yesterdayIdle\)/)
    assert.match(page, /drivingSecondsByVehicle\(\s*idleDataPayload, id =>/)
    assert.doesNotMatch(page, /engineMileageAgrees|engine_mileage_km/, "no cross-report mileage check on the Dashboard")
    assert.match(page, /sumKnownInBoth\(todayFleetActivity\.drivingByVehicle, yesterdayFleetActivity\.drivingByVehicle\)/)
    assert.equal((page.match(/label: "Driving time \(engine on, moving\)"/g) || []).length, 2)
})

test("isPartialReport flags partial or stale Dashboard reports so a partial Yesterday is re-read", () => {
    assert.equal(isPartialReport(null), true, "a missing payload is not complete")
    assert.equal(isPartialReport({ incomplete_units: [], all_units: [{ mileage: 0 }] }), false, "complete mileage (0 km kept)")
    assert.equal(isPartialReport({ incomplete_units: ["UNIT-C"] }), true, "mileage with an unknown unit")
    assert.equal(isPartialReport({ summary: { total_distance_partial: false, total_trips_partial: false }, vehicles: [] }), false)
    assert.equal(isPartialReport({ summary: { total_distance_partial: false, total_trips_partial: true } }), true, "unknown trip count")
    assert.equal(isPartialReport({ kpis: { total_idle_partial: false }, by_vehicle: [{ total_idle_seconds: 0, in_motion_seconds: 0 }] }), false)
    assert.equal(isPartialReport({ kpis: { total_idle_partial: true }, by_vehicle: [] }), true)
    assert.equal(isPartialReport({ kpis: {}, by_vehicle: [{ total_idle_seconds: 60, in_motion_seconds: null }] }), true, "unknown driving time")
    assert.equal(isPartialReport({ incomplete_units: [], stale: true }), true, "served stale after a Wialon failure")
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.match(page, /isPartialReport\(v\)/, "the Dashboard checks each yesterday report")
    assert.match(page, /YESTERDAY_PARTIAL_RETRY_MS/)
})

test("Dashboard trips sum known counts and show the unknown vehicles; group idle chart counts unknowns", () => {
    const page = readFileSync(new URL("../pages/Dashboard.jsx", import.meta.url), "utf8")
    assert.doesNotMatch(page, /tripCountsKnown \?/, "one unknown count no longer hides the whole total")
    assert.match(page, /const tripSum = sumKnown\(filteredTripVehs, tv => tv\.trip_count\)/)
    assert.match(page, /tripsUnknownVehicles > 0 \?/)
    assert.match(page, /item\.unknownCount \+= 1/, "unknown idle is counted per group, never added as 0")
    const trips = [{ trip_count: 3 }, { trip_count: null }, { trip_count: 0 }]
    assert.deepEqual(sumKnown(trips, t => t.trip_count), { total: 3, known: 2, unknown: 1, partial: true, allUnknown: false })
})
