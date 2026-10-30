// Offline regression tests for unknown-vs-zero telemetry display. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
    TELEMATICS_MAX_RETRIES,
    shouldRetryTelematics,
    UNKNOWN,
    isKnownNumber,
    firstKnown,
    formatMeasurement,
    resolveActivityHours,
    collectTelemetryNotice,
    formatTrendPoint,
    trendNotice,
    idleSessionsLabel,
} from "./telemetryFormat.js"

const wialonActivity = (over = {}) => ({
    status: "complete",
    warnings: [],
    driving_duration_hours: 4.5,
    idle_duration_hours: 1.25,
    engine_hours: 5.75,
    ...over,
})

test("unknown measurements render as dash, recorded zero renders as 0", () => {
    assert.equal(formatMeasurement(null, 1, "hrs"), UNKNOWN)
    assert.equal(formatMeasurement(undefined, 1, "hrs"), UNKNOWN)
    assert.equal(formatMeasurement(NaN, 1, "hrs"), UNKNOWN)
    assert.equal(formatMeasurement("", 1, "hrs"), UNKNOWN)
    assert.equal(formatMeasurement(0, 1, "hrs"), "0.0 hrs")
    assert.equal(formatMeasurement(1234.56, 1, "km"), "1,234.6 km")
    assert.equal(isKnownNumber(0), true)
    assert.equal(isKnownNumber(null), false)
})

test("firstKnown keeps a recorded zero instead of falling through like ||", () => {
    assert.equal(firstKnown(0, 7), 0)
    assert.equal(firstKnown(null, 7), 7)
    assert.equal(firstKnown(null, undefined), null)
})

test("null summary hours with null activity hours stay unknown (old code showed 0.0 hrs)", () => {
    const summary = { driving_duration_hours: null, idle_duration_hours: null, engine_hours: null }
    const activity = wialonActivity({ status: "partial", driving_duration_hours: null, idle_duration_hours: null, engine_hours: null })
    const h = resolveActivityHours(summary, activity)
    assert.deepEqual(h, { drivingHours: null, idleHours: null, engineHours: null })
    assert.equal(formatMeasurement(h.drivingHours, 1, "hrs"), UNKNOWN)
})

test("recorded zero hours are preserved and not replaced by activity values", () => {
    const summary = { driving_duration_hours: 0, idle_duration_hours: 0, engine_hours: 0 }
    const h = resolveActivityHours(summary, wialonActivity())
    assert.deepEqual(h, { drivingHours: 0, idleHours: 0, engineHours: 0 })
    assert.equal(formatMeasurement(h.idleHours, 1, "hrs"), "0.0 hrs")
})

test("null summary hours fall back to Wialon activity values", () => {
    const h = resolveActivityHours({ driving_duration_hours: null }, wialonActivity())
    assert.equal(h.drivingHours, 4.5)
    assert.equal(h.idleHours, 1.25)
})

test("placeholder activity without a Wialon status is not used as recorded zeros", () => {
    const placeholder = { driving_duration_hours: 0.0, idle_duration_hours: 0.0, engine_hours: 0.0, has_data: false }
    const h = resolveActivityHours({}, placeholder)
    assert.deepEqual(h, { drivingHours: null, idleHours: null, engineHours: null })
    const notice = collectTelemetryNotice({ status: "matched" }, placeholder)
    assert.equal(notice.status, "partial")
    assert.equal(notice.warnings.length, 1)
})

test("telemetry notice surfaces partial status and deduplicated warnings", () => {
    const w = "Wialon 'In motion' time unavailable; driving hours not reported."
    const notice = collectTelemetryNotice(
        { telemetry_status: "partial" },
        wialonActivity({ status: "partial", warnings: [w, w, ""] }),
    )
    assert.deepEqual(notice, { status: "partial", warnings: [w] })
    assert.equal(collectTelemetryNotice({ telemetry_status: "unavailable" }, wialonActivity()).status, "unavailable")
})

test("no notice when telemetry is complete", () => {
    assert.equal(collectTelemetryNotice({ telemetry_status: "complete" }, wialonActivity()), null)
})

test("trend point keeps unknown distance and efficiency null (old code showed 0 km)", () => {
    const p = formatTrendPoint({ period: "2026-08-03", litres: 50.04, cost: 140.6, transaction_count: 2, distance_km: null, fuel_efficiency_l_per_100km: null })
    assert.equal(p.distance_km, null)
    assert.equal(p.fuel_efficiency, null)
    assert.equal(p.litres, 50)
    assert.equal(p.cost, 141)
    assert.equal(p.displayPeriod, "08-03")
    assert.equal(formatMeasurement(p.distance_km, 1, "km"), UNKNOWN)
})

test("trend point preserves recorded zero distance", () => {
    const p = formatTrendPoint({ period: "2026-08-03", litres: 0, cost: 0, transaction_count: 0, distance_km: 0, fuel_efficiency_l_per_100km: null })
    assert.equal(p.distance_km, 0)
    assert.equal(formatMeasurement(p.distance_km, 1, "km"), "0.0 km")
})

test("trend notice reflects response status and warnings", () => {
    assert.equal(trendNotice({ telematics_status: "complete", data_warnings: [] }), null)
    assert.equal(trendNotice({}), null)
    assert.deepEqual(trendNotice({ telematics_status: "partial", data_warnings: ["UNIT-A not in engine report"] }), {
        status: "partial",
        warnings: ["UNIT-A not in engine report"],
        unitsFailed: 0,
        mismatches: 0,
    })
    assert.deepEqual(trendNotice({ telematics_status: "complete", data_warnings: [], units_failed: [{}], distance_reconciliation: { mismatches: [{}, {}] } }), {
        status: "complete", warnings: [], unitsFailed: 1, mismatches: 2,
    })
})

test("components no longer coerce telemetry with || 0 / ?? 0", () => {
    const modal = readFileSync(new URL("./VehicleFuelDetailModal.jsx", import.meta.url), "utf8")
    for (const field of ["driving_duration_hours", "idle_duration_hours", "engine_hours"]) {
        assert.doesNotMatch(modal, new RegExp(`${field}\\s*(\\|\\||\\?\\?)`), `modal still falls back on ${field}`)
    }
    assert.doesNotMatch(modal, /Hours\.toFixed/, "modal formats possibly-null hours with toFixed")
})

test("idle session caption: unknown count is unavailable, never 'Stationary ignition'", () => {
    assert.equal(idleSessionsLabel(wialonActivity({ idle_events_count: 54, idle_events_complete: true })), "54 idle sessions")
    assert.equal(idleSessionsLabel(wialonActivity({ idle_events_count: 0, idle_events_complete: true })), "Stationary ignition")
    assert.equal(idleSessionsLabel(wialonActivity({ idle_events_count: null, idle_events_complete: false })), "Idle sessions unavailable")
    assert.equal(idleSessionsLabel(wialonActivity({ idle_events_count: 12, idle_events_complete: false })), "Idle sessions unavailable")
    assert.equal(idleSessionsLabel({ status: "unavailable", idle_events_count: null }), "Idle sessions unavailable")
    // Older payloads without the flag keep their previous captions
    assert.equal(idleSessionsLabel({ idle_events_count: 3 }), "3 idle sessions")
    assert.equal(idleSessionsLabel({}), "Stationary ignition")
})

test("fuel detail modal uses the idle session caption and marks a partial event log", () => {
    const modal = readFileSync(new URL("./VehicleFuelDetailModal.jsx", import.meta.url), "utf8")
    assert.match(modal, /\{idleSessionsLabel\(activity\)\}/)
    assert.doesNotMatch(modal, /idle_events_count \?/, "modal still tests the count by truthiness")
    assert.match(modal, /activity\.idle_events_complete === false && \(/)
})

test("telematics still running on the server is retried a bounded number of times; failures never", () => {
    const pending = { telematics_status: "unavailable", telematics_pending: true }
    assert.equal(shouldRetryTelematics(pending, 0), true)
    assert.equal(shouldRetryTelematics(pending, TELEMATICS_MAX_RETRIES - 1), true)
    assert.equal(shouldRetryTelematics(pending, TELEMATICS_MAX_RETRIES), false, "bounded")
    assert.equal(shouldRetryTelematics({ telematics_status: "unavailable", telematics_pending: false }, 0), false, "a failure stays unavailable")
    assert.equal(shouldRetryTelematics({ telematics_status: "complete" }, 0), false)
    assert.equal(shouldRetryTelematics(null, 0), false)
    const page = readFileSync(new URL("./useFuelEfficiencyData.js", import.meta.url), "utf8")
    assert.match(page, /shouldRetryTelematics\(efficiency, telematicsRetries\)/)
    assert.match(page, /clearClientCache\("\/api\/fuel\/analytics\/efficiency"\)/, "a retry bypasses the 30 s client cache")
    assert.match(page, /loadEfficiencyData\(\{ silent: true \}\)/, "retries keep the fuel figures on screen")
})
