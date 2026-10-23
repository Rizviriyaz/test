// Offline regression tests: unknown (null) idle, engine and mileage values are never summed as zero.
// Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { PARTIAL_EVENTS_NOTE, compareKnownDesc, idleSecondsOf, partialEventsNote, sumKnown, tripDistanceOf } from "./knownTotals.js"

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

test("partialEventsNote flags incomplete idle events and stays silent when complete or absent", () => {
    assert.equal(partialEventsNote({ events_complete: false }), PARTIAL_EVENTS_NOTE)
    assert.equal(partialEventsNote({ events_complete: true }), null)
    assert.equal(partialEventsNote({}), null)
    assert.equal(partialEventsNote(null), null)
})

test("Idle analytics labels trend, hotspots and the event list when events are partial", () => {
    const page = readFileSync(new URL("../pages/IdleAnalytics.jsx", import.meta.url), "utf8")
    assert.match(page, /const eventsNote = eventsPending \? "Loading idle events…" : partialEventsNote\(analytics\)/)
    assert.match(page, /title="Idle duration trend"\s+subtitle=\{eventsNote \|\| undefined\}/)
    assert.match(page, /subtitle=\{eventsNote \? `Top 10 locations · \$\{eventsNote\}` : "Top 10 locations"\}/)
    assert.equal((page.match(/\{eventsNote && </g) || []).length, 2, "event list and hotspot list notes")
    assert.match(page, /value=\{eventsPending \? "…" : kpis\.idle_event_count \?\? "—"\}/,
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
