// Offline tests for the data freshness state shown by FreshnessBadge. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { FRESHNESS_LABELS, freshnessAgeSeconds, freshnessState, freshnessTitle } from "./freshness.js"

const NOW = 1_800_000_000_000
const block = (over = {}) => ({ fetched_at: NOW / 1000 - 20, refresh_status: "cached", is_stale: false, target_age_seconds: 30, ...over })

test("freshness states from the backend block: fresh, cached, delayed, stale, unavailable", () => {
    assert.equal(freshnessState({ freshness: block({ fetched_at: NOW / 1000 - 2, refresh_status: "fresh" }), nowMs: NOW }), "fresh")
    assert.equal(freshnessState({ freshness: block(), nowMs: NOW }), "cached")
    // The age is recomputed in the browser: a response kept on screen (or in the client cache) turns Delayed
    assert.equal(freshnessState({ freshness: block({ fetched_at: NOW / 1000 - 31 }), nowMs: NOW }), "delayed")
    assert.equal(freshnessState({ freshness: block({ is_stale: true, refresh_status: "stale" }), nowMs: NOW }), "stale")
    assert.equal(freshnessState({ freshness: block({ refresh_status: "unavailable", fetched_at: null }), nowMs: NOW }), "unavailable")
    // A closed window has no target age: never Delayed, however old
    assert.equal(freshnessState({ freshness: block({ fetched_at: NOW / 1000 - 9000, target_age_seconds: null }), nowMs: NOW }), "cached")
})

test("request state wins over the block: refreshing, refresh failed, unavailable without data", () => {
    assert.equal(freshnessState({ freshness: block(), loading: true, nowMs: NOW }), "refreshing")
    assert.equal(freshnessState({ freshness: block(), error: "boom", nowMs: NOW }), "failed", "data on screen is older than the failed refresh")
    assert.equal(freshnessState({ hasData: false, error: "boom" }), "unavailable")
    assert.equal(freshnessState({ hasData: false, loading: true }), "refreshing")
    assert.equal(freshnessState({ hasData: false }), null)
    assert.equal(freshnessState({ freshness: null }), null, "nothing to say without a block")
})

test("no age without a read time; the title separates the backend read time from the source's own time", () => {
    assert.equal(freshnessAgeSeconds({ fetched_at: null }, NOW), null)
    assert.equal(freshnessAgeSeconds({ fetched_at: NOW / 1000 - 75 }, NOW), 75)
    const title = freshnessTitle({ fetched_at: 1000, source_updated_at: 900, refresh_error: "Source down" }, "stale", (d) => String(d.getTime() / 1000))
    assert.equal(title, "Read from the source at 1000; latest source update 900; Source down")
    assert.equal(Object.keys(FRESHNESS_LABELS).length, 7)
})

test("freshness is shown on every operational page and the vehicle drawer, separate from vehicle status", () => {
    const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8")
    for (const page of ["../pages/Dashboard.jsx", "../pages/Tracking.jsx", "../pages/VehicleHealth.jsx", "../pages/IdleAnalytics.jsx",
        "../pages/FuelPerformance.jsx", "../components/Vehicles/VehicleDetailDrawer.jsx"]) {
        assert.match(read(page), /<FreshnessBadge/, page)
    }
    // The badge never reads vehicle statuses: freshness is about the response, not MOVING/OFFLINE
    const src = read("./freshness.js")
    const rule = src.slice(src.indexOf("export function freshnessState"), src.indexOf("export function freshnessTitle"))
    assert.ok(rule.length > 100)
    assert.doesNotMatch(rule, /MOVING|OFFLINE|IDLING|STOPPED/)
})
