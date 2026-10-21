// Report date-range presets and From/To field helpers, resolved in UAE time (Asia/Dubai, fixed UTC+4, no DST).
// All ranges are UNIX epoch seconds; field values are "YYYY-MM-DDTHH:mm:ss" in UAE local time.

const UAE_OFFSET_SECONDS = 4 * 3600
const DAY_SECONDS = 86400
export const MAX_RANGE_DAYS = 366

export const RANGE_PRESETS = [
    { id: "today", label: "Today" },
    { id: "yesterday", label: "Yesterday" },
    { id: "last_week", label: "Last 1 Week" },
    { id: "last_month", label: "Last Month" },
]

const pad = (n) => String(n).padStart(2, "0")

// Calendar fields of an epoch (seconds) as seen in UAE time.
function uaeParts(epochSec) {
    const d = new Date((epochSec + UAE_OFFSET_SECONDS) * 1000)
    return {
        year: d.getUTCFullYear(),
        month: d.getUTCMonth() + 1,
        day: d.getUTCDate(),
        hour: d.getUTCHours(),
        minute: d.getUTCMinutes(),
        second: d.getUTCSeconds(),
    }
}

// Epoch seconds of 00:00:00 UAE on the given calendar day; out-of-range day/month values roll over
// (day 0 = last day of the previous month, month 0 = December of the previous year).
function uaeMidnight(year, month, day) {
    return Date.UTC(year, month - 1, day) / 1000 - UAE_OFFSET_SECONDS
}

/** Resolves a preset id to { from, to } epoch seconds relative to `nowSec`. */
export function resolvePreset(id, nowSec = Math.floor(Date.now() / 1000)) {
    const { year, month, day } = uaeParts(nowSec)
    const todayStart = uaeMidnight(year, month, day)
    switch (id) {
        case "today":
            return { from: todayStart, to: nowSec }
        case "yesterday":
            return { from: uaeMidnight(year, month, day - 1), to: todayStart - 1 }
        case "last_week":
            // Today plus the previous 6 calendar days, ending now.
            return { from: uaeMidnight(year, month, day - 6), to: nowSec }
        case "last_month":
            // The complete previous calendar month.
            return { from: uaeMidnight(year, month - 1, 1), to: uaeMidnight(year, month, 1) - 1 }
        default:
            return null
    }
}

/** Formats epoch seconds as a datetime-local field value in UAE time. */
export function toUaeInputValue(epochSec) {
    const p = uaeParts(epochSec)
    return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`
}

/** Parses a datetime-local field value (seconds optional) as UAE time; returns null when incomplete or not a real date. */
export function parseUaeInputValue(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(value || "").trim())
    if (!m) return null
    const [year, month, day, hour, minute, second] = m.slice(1).map((part) => Number(part || 0))
    if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return null
    const midnight = uaeMidnight(year, month, day)
    // Reject impossible days such as Feb 30 instead of letting them roll into the next month.
    const check = uaeParts(midnight)
    if (check.year !== year || check.month !== month || check.day !== day) return null
    return midnight + hour * 3600 + minute * 60 + second
}

/**
 * Validates the From/To field values. Returns { from, to } epoch seconds or { error }.
 * A To time in the future is capped at `nowSec`, since no data exists past now.
 */
export function validateRange(fromValue, toValue, nowSec = Math.floor(Date.now() / 1000)) {
    const from = parseUaeInputValue(fromValue)
    const to = parseUaeInputValue(toValue)
    if (from == null) return { error: "Enter a valid From date and time." }
    if (to == null) return { error: "Enter a valid To date and time." }
    if (from >= to) return { error: "From must be earlier than To." }
    if (from > nowSec) return { error: "From cannot be in the future." }
    const cappedTo = Math.min(to, nowSec)
    if (cappedTo - from > MAX_RANGE_DAYS * DAY_SECONDS) {
        return { error: `The range cannot be longer than ${MAX_RANGE_DAYS} days.` }
    }
    return { from, to: cappedTo }
}
