// Offline tests for date-range presets and From/To parsing in UAE time (UTC+4).
// Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { parseUaeInputValue, resolvePreset, toUaeInputValue, validateRange } from "./dateRange.js"

// Epoch seconds for a UAE wall-clock time.
const uae = (iso) => Date.parse(`${iso}+04:00`) / 1000
const fmt = (r) => ({ from: toUaeInputValue(r.from), to: toUaeInputValue(r.to) })

test("Today runs from 00:00:00 UAE to now", () => {
    const now = uae("2026-10-21T14:35:12")
    assert.deepEqual(fmt(resolvePreset("today", now)), { from: "2026-10-21T00:00:00", to: "2026-10-21T14:35:12" })
})

test("Today uses the UAE date even when the UTC date differs", () => {
    const now = uae("2026-10-22T01:30:00") // still Oct 21 in UTC
    assert.equal(toUaeInputValue(resolvePreset("today", now).from), "2026-10-22T00:00:00")
})

test("Yesterday is the full previous day, across month and year boundaries", () => {
    assert.deepEqual(fmt(resolvePreset("yesterday", uae("2026-10-21T09:00:00"))), { from: "2026-10-20T00:00:00", to: "2026-10-20T23:59:59" })
    assert.deepEqual(fmt(resolvePreset("yesterday", uae("2026-03-01T09:00:00"))), { from: "2026-02-28T00:00:00", to: "2026-02-28T23:59:59" })
    assert.deepEqual(fmt(resolvePreset("yesterday", uae("2027-01-01T00:00:05"))), { from: "2026-12-31T00:00:00", to: "2026-12-31T23:59:59" })
})

test("Last 1 Week is today plus the previous 6 calendar days, ending now", () => {
    const now = uae("2026-10-21T14:35:12")
    assert.deepEqual(fmt(resolvePreset("last_week", now)), { from: "2026-10-15T00:00:00", to: "2026-10-21T14:35:12" })
    assert.equal(toUaeInputValue(resolvePreset("last_week", uae("2026-03-03T08:00:00")).from), "2026-02-25T00:00:00")
    assert.equal(toUaeInputValue(resolvePreset("last_week", uae("2027-01-02T08:00:00")).from), "2026-12-27T00:00:00")
})

test("Last Month is the complete previous calendar month", () => {
    assert.deepEqual(fmt(resolvePreset("last_month", uae("2026-10-21T14:00:00"))), { from: "2026-09-01T00:00:00", to: "2026-09-30T23:59:59" })
    assert.deepEqual(fmt(resolvePreset("last_month", uae("2027-01-15T10:00:00"))), { from: "2026-12-01T00:00:00", to: "2026-12-31T23:59:59" })
    assert.deepEqual(fmt(resolvePreset("last_month", uae("2028-03-31T23:59:59"))), { from: "2028-02-01T00:00:00", to: "2028-02-29T23:59:59" })
    assert.deepEqual(fmt(resolvePreset("last_month", uae("2026-03-01T00:00:00"))), { from: "2026-02-01T00:00:00", to: "2026-02-28T23:59:59" })
})

test("Unknown presets resolve to null", () => {
    assert.equal(resolvePreset("custom", uae("2026-10-21T00:00:00")), null)
})

test("Field values round-trip and accept values without seconds", () => {
    assert.equal(parseUaeInputValue("2026-10-21T14:35:12"), uae("2026-10-21T14:35:12"))
    assert.equal(parseUaeInputValue("2026-10-21T14:35"), uae("2026-10-21T14:35:00"))
    assert.equal(toUaeInputValue(parseUaeInputValue("2024-02-29T23:59:59")), "2024-02-29T23:59:59")
})

test("Incomplete or impossible field values are rejected", () => {
    for (const v of ["", null, "2026-10-21", "2026-02-30T10:00", "2026-13-01T00:00", "2025-02-29T00:00", "2026-10-21T24:00", "garbage"]) {
        assert.equal(parseUaeInputValue(v), null, String(v))
    }
})

test("validateRange rejects invalid ranges", () => {
    const now = uae("2026-10-21T12:00:00")
    assert.match(validateRange("", "2026-10-21T10:00", now).error, /From/)
    assert.match(validateRange("2026-10-21T10:00", "2026-02-30T10:00", now).error, /To/)
    assert.match(validateRange("2026-10-21T10:00", "2026-10-21T09:00", now).error, /earlier/)
    assert.match(validateRange("2026-10-21T10:00", "2026-10-21T10:00", now).error, /earlier/)
    assert.match(validateRange("2026-10-22T10:00", "2026-10-23T10:00", now).error, /future/)
    assert.match(validateRange("2025-01-01T00:00", "2026-10-21T00:00", now).error, /366 days/)
})

test("validateRange returns epochs and caps a future To at now", () => {
    const now = uae("2026-10-21T12:00:00")
    assert.deepEqual(validateRange("2026-10-01T08:30", "2026-10-02T17:45:30", now), { from: uae("2026-10-01T08:30:00"), to: uae("2026-10-02T17:45:30") })
    assert.deepEqual(validateRange("2026-10-21T00:00", "2026-10-21T23:59:59", now), { from: uae("2026-10-21T00:00:00"), to: now })
})
