// Offline tests for the fuel reporting-period rules. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
    isMonth, shiftMonth, lastCompleteMonth, daysInMonth, monthBounds, monthLabel, isCurrentMonth, canGoNext,
    comparisonWindow, alignMonths, latestMonthWithData, hasReportedDailyRows, validLatestMonth, jumpTargetMonth,
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

test("daily analysis only for months whose reporting scope holds dated rows, never from monthly summaries", () => {
    // Monthly-summary month: summary rows are not dated purchases or refunds
    assert.equal(hasReportedDailyRows({ dated_purchase_count: 0, refund_count: 0, monthly_summary_rows: 12 }), false)
    assert.equal(hasReportedDailyRows({ dated_purchase_count: 3, refund_count: 0 }), true)
    assert.equal(hasReportedDailyRows({ dated_purchase_count: 0, refund_count: 1 }), true)
    assert.equal(hasReportedDailyRows(null), false)
    assert.equal(hasReportedDailyRows({}), false)
})

test("latest month with data: a valid month up to the current one, else none", () => {
    // Live data: only August 2026 has matched fuel; today is 2026-10-30
    assert.equal(validLatestMonth("2026-08", "2026-10-30"), "2026-08")
    assert.equal(validLatestMonth("2026-10", "2026-10-30"), "2026-10", "the current month, when it has data")
    for (const bad of [null, undefined, "", "2026-13", "2026-12", "x"]) {
        assert.equal(validLatestMonth(bad, "2026-10-30"), null, String(bad))
    }
})

test("jump button: offered only once the latest month is found and differs from the selected month", () => {
    const found = { status: "found", month: "2026-08" }
    // Selected month stays as it is; the button offers the latest month (empty month or a month with data alike)
    assert.equal(jumpTargetMonth(found, "2026-09"), "2026-08")
    assert.equal(jumpTargetMonth(found, "2026-05"), "2026-08")
    // Already on the latest month: no button
    assert.equal(jumpTargetMonth(found, "2026-08"), null)
    // Still looking, none, failed or not started: no button, and nothing navigates
    for (const latest of [{ status: "idle", month: null }, { status: "loading", month: "2026-08" }, { status: "none", month: null },
        { status: "error", month: null }, null, undefined]) {
        assert.equal(jumpTargetMonth(latest, "2026-09"), null, JSON.stringify(latest))
    }
    assert.equal(jumpTargetMonth({ status: "found", month: "bad" }, "2026-09"), null)
})

test("the selected month changes only through the user's navigation or the jump button, never when a request finishes", () => {
    const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8")
    const ctx = read("../../context/FuelFilterContext.jsx")
    // setSelectedMonth: the initial state, setMonth (month navigation, jump button) and resetFilters only
    assert.equal((ctx.match(/setSelectedMonth\(/g) || []).length, 2, "setMonth and resetFilters")
    const lookup = ctx.slice(ctx.indexOf("const lookupLatestMonth"), ctx.indexOf("// Exact vehicle filter"))
    assert.ok(lookup.length > 100)
    assert.doesNotMatch(lookup, /setSelectedMonth|setMonth|setPeriod/, "the background lookup only records the month")
    // Fuel-only lookup (summary identifiers and months, no Wialon reports), once per session or after an import
    assert.match(lookup, /getFuelLatestMonth\(\)/)
    assert.match(lookup, /if \(latestLookupRef\.current && !force\) return/)
    assert.doesNotMatch(ctx, /monthResolved|resolveOpeningMonth|monthPickedRef/)

    const page = read("../../pages/FuelPerformance.jsx")
    // The selected month loads at once: nothing waits for the lookup
    assert.match(page, /useFuelEfficiencyData\(queryParams\)/)
    assert.doesNotMatch(page, /monthResolved|resolveOpeningMonth|getFuelTrend/)
    assert.match(page, /\n {16}<DataCoverage/, "Data Coverage is not gated")
    // The page calls setMonth only from the jump button's click
    assert.equal((page.match(/setMonth\(/g) || []).length, 1)
    assert.match(page, /onClick=\{\(\) => setMonth\(jumpMonth\)\}/)
    // The lookup starts after the selected month's first request finished (it reuses that request's Wialon unit list)
    assert.match(page, /const firstLoadDone = !loadingEfficiency[\s\S]*?if \(firstLoadDone\) lookupLatestMonth\(\)/)
    // The button sits in the header and in the empty state, never both at once; an import looks the month up again
    assert.match(page, /\{!hasNoData && jumpButton\}/)
    assert.match(page, /jumpButton \?\? \(latestMonth\.status === "loading"/)
    assert.match(page, /lookupLatestMonth\(\{ force: true \}\)/)
    // The data hook loads whatever month is selected, with no gate
    assert.doesNotMatch(read("./useFuelEfficiencyData.js"), /enabled/)
    // Previous/next navigation still sets the month through setMonth
    assert.match(read("./FuelHeader.jsx"), /<MonthNavigator month=\{selectedMonth\} onChange=\{setMonth\} \/>/)
})
