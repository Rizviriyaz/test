// Offline tests for the fuel reporting-period and daily-coverage rules. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import {
    isMonth, shiftMonth, lastCompleteMonth, daysInMonth, monthBounds, monthLabel, isCurrentMonth, canGoNext,
    parseMonthParam, comparisonWindow, alignMonths, latestMonthWithData, datedTransactionCount,
    dailySearch, DAILY_COVERAGE_REASONS, datedDailyAvailability,
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

test("navigation never passes the current month and URL months are validated", () => {
    assert.equal(canGoNext("2026-09", "2026-10-27"), true)
    assert.equal(canGoNext("2026-10", "2026-10-27"), false)
    assert.equal(isCurrentMonth("2026-10", "2026-10-27"), true)
    assert.equal(parseMonthParam("2026-10", "2026-10-27"), "2026-10")
    assert.equal(parseMonthParam("2026-11", "2026-10-27"), null, "future month")
    assert.equal(parseMonthParam("2026-13", "2026-10-27"), null)
    assert.equal(parseMonthParam(null, "2026-10-27"), null)
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

test("monthly-summary rows are never evidence of daily coverage", () => {
    assert.equal(datedTransactionCount({ kpi: { transaction_count: 12 }, monthly_summary_rows: 12 }), 0)
    assert.equal(datedTransactionCount({ kpi: { transaction_count: 12 }, monthly_summary_rows: 2 }), 10)
    assert.equal(datedTransactionCount({ kpi: {} }), null)
    assert.equal(datedTransactionCount(null), null)
})

test("daily links carry the month and the optional vehicle", () => {
    assert.equal(dailySearch("2026-09"), "month=2026-09")
    assert.equal(dailySearch("2026-09", "A 12345"), "month=2026-09&vehicle=A+12345")
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
    assert.equal(transactionCountLabel(null, 0), "—")
})

test("daily analysis is available only when the month has dated transaction rows", () => {
    const daily = (dated, extra = {}) => ({ dated_row_count: dated, data: [{ date: "2026-09-03", transactions: dated }], ...extra })
    assert.deepEqual(datedDailyAvailability(daily(4)), { available: true, reason: null })
    // A few dated days are enough: full-month statement coverage is not required
    assert.equal(datedDailyAvailability(daily(1, { coverage_metadata_available: false })).available, true)
    // Monthly summaries only: dated_row_count excludes them, so no daily view (nothing on the month-end date)
    const summariesOnly = daily(0, { monthly_summary_excluded: { rows: 12 } })
    assert.deepEqual(datedDailyAvailability(summariesOnly), { available: false, reason: "no_dated_rows" })
    assert.deepEqual(datedDailyAvailability(null), { available: false, reason: "unknown" })
    assert.deepEqual(datedDailyAvailability({ dated_row_count: 3 }), { available: false, reason: "unknown" }, "malformed")
    for (const reason of ["unknown", "no_dated_rows"]) assert.ok(DAILY_COVERAGE_REASONS[reason])
})
