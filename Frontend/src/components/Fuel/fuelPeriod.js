// Pure reporting-period helpers for the fuel views (unit-tested in fuelPeriod.test.js).
// Months are "YYYY-MM" and days "YYYY-MM-DD" UAE calendar strings; callers pass today's UAE date (getUAETodayStr).

const MONTH_RE = /^(\d{4})-(\d{2})$/
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
const pad = (n) => String(n).padStart(2, "0")

export function isMonth(value) {
    const m = MONTH_RE.exec(typeof value === "string" ? value : "")
    return Boolean(m) && Number(m[2]) >= 1 && Number(m[2]) <= 12
}

export function monthOf(day) {
    return String(day || "").slice(0, 7)
}

export function shiftMonth(month, delta) {
    const [y, m] = month.split("-").map(Number)
    const index = y * 12 + (m - 1) + delta
    return `${Math.floor(index / 12)}-${pad((index % 12) + 1)}`
}

// Default reporting period: the month before the current UAE month
export function lastCompleteMonth(today) {
    return shiftMonth(monthOf(today), -1)
}

export function daysInMonth(month) {
    const [y, m] = month.split("-").map(Number)
    return new Date(Date.UTC(y, m, 0)).getUTCDate()
}

export function monthBounds(month) {
    return { from: `${month}-01`, to: `${month}-${pad(daysInMonth(month))}` }
}

export function monthLabel(month, long = false) {
    if (!isMonth(month)) return ""
    const [y, m] = month.split("-").map(Number)
    if (!long) return `${MONTH_NAMES[m - 1]} ${y}`
    return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" })
}

export function isCurrentMonth(month, today) {
    return month === monthOf(today)
}

// Navigation stops at the current month: later months cannot hold data yet
export function canGoNext(month, today) {
    return month < monthOf(today)
}

// The `count` months ending at `month`, oldest first, and the date range covering them
export function comparisonWindow(month, count = 6) {
    const months = Array.from({ length: count }, (_, i) => shiftMonth(month, i - count + 1))
    return { months, from: monthBounds(months[0]).from, to: monthBounds(month).to }
}

// One entry per month; a month the API returned no row for stays null (no record), never a zero
export function alignMonths(months, rows) {
    const byPeriod = new Map((Array.isArray(rows) ? rows : []).map((r) => [String(r?.period || "").slice(0, 7), r]))
    return months.map((month) => ({ month, row: byPeriod.get(month) ?? null }))
}

// Latest month (not after the current one) with at least one transaction in a group_by=month fuel trend; null if none
export function latestMonthWithData(rows, today) {
    const current = monthOf(today)
    let latest = null
    for (const r of Array.isArray(rows) ? rows : []) {
        const month = String(r?.period || "").slice(0, 7)
        const count = Number(r?.transactions ?? r?.transaction_count)
        if (isMonth(month) && month <= current && count > 0 && (!latest || month > latest)) latest = month
    }
    return latest
}
