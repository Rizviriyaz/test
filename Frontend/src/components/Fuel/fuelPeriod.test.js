// Offline tests for the fuel reporting-period rules. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import {
    isMonth, shiftMonth, lastCompleteMonth, daysInMonth, monthBounds, monthLabel, isCurrentMonth, canGoNext,
    comparisonWindow, alignMonths, latestMonthWithData,
} from "./fuelPeriod.js"
import { shareSplit, transactionCountLabel } from "./fuelLabels.js"

test("default period is the last complete UAE month, across a year boundary", () => {
    assert.equal(lastCompleteMonth("2026-10-27"), "2026-09")
    assert.equal(lastCompleteMonth("2026-01-01"), "2025-12")
    assert.equal(shiftMonth("2026-12", 1), "2027-01")
    assert.equal(shiftMonth("2026-03", -5), "2025-10")
})

test("month bounds use real calendar lengths", () => {
    assert.deepEqual(monthBounds("2024-02"), { from: "2024-02-01", to: "2024-02-29" })
    assert.deepEqual(monthBounds("2026-02"), { from: "2026-02-01", to: "2026-02-28" })
    assert.equal(daysInMonth("2026-09"), 30)
    assert.equal(monthLabel("2026-09"), "Sep 2026")
    assert.equal(monthLabel("2026-09", true), "September 2026")
    assert.equal(monthLabel("bad"), "")
})

test("navigation never passes the current month", () => {
    assert.equal(canGoNext("2026-09", "2026-10-27"), true)
    assert.equal(canGoNext("2026-10", "2026-10-27"), false)
    assert.equal(isCurrentMonth("2026-10", "2026-10-27"), true)
    assert.equal(isMonth("2026-1"), false)
})

test("six-month comparison ends at the selected month", () => {
    const w = comparisonWindow("2026-03")
    assert.deepEqual(w.months, ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"])
    assert.equal(w.from, "2025-10-01")
    assert.equal(w.to, "2026-03-31")
})

test("a month missing from the API stays null, never zero", () => {
    const aligned = alignMonths(["2026-08", "2026-09"], [{ period: "2026-09", litres: 120 }])
    assert.equal(aligned[0].row, null)
    assert.equal(aligned[1].row.litres, 120)
    assert.deepEqual(alignMonths(["2026-08"], null), [{ month: "2026-08", row: null }])
})

test("latest month with data ignores empty and future periods", () => {
    const rows = [
        { period: "2026-06", transactions: 4 },
        { period: "2026-08", transactions: 0 },
        { period: "2026-07", transactions: 2 },
        { period: "2026-12", transactions: 9 },
    ]
    assert.equal(latestMonthWithData(rows, "2026-10-27"), "2026-07")
    assert.equal(latestMonthWithData([], "2026-10-27"), null)
    assert.equal(latestMonthWithData(undefined, "2026-10-27"), null)
})

test("splits are withheld when the data cannot support them", () => {
    assert.deepEqual(shareSplit(3, 1), { first: 75, second: 25 })
    assert.equal(shareSplit(5, null), null, "one part unknown: no 100% split")
    assert.equal(shareSplit(-2, 4), null, "negative estimate")
    assert.equal(shareSplit(0, 0), null)
    assert.deepEqual(shareSplit(4, 0), { first: 100, second: 0 }, "a recorded zero is a real split")
})

test("monthly-summary rows are not counted as refuels", () => {
    assert.equal(transactionCountLabel(14, 0), "14 refuels")
    assert.equal(transactionCountLabel(14, 3), "14 rows incl. 3 monthly summary")
    assert.equal(transactionCountLabel(61, 61), "61 monthly summary rows", "monthly-only reporting")
    assert.equal(transactionCountLabel(null, 0), "—")
})
