export const UAE_TIMEZONE = "Asia/Dubai"

/**
 * Formats a UNIX timestamp (seconds/ms) or ISO string to UAE Time (Asia/Dubai, UTC+4).
 */
export function formatUAEDateTime(ts, options = {}) {
    if (!ts) return "—"
    const date = typeof ts === "number" ? new Date(ts > 1e11 ? ts : ts * 1000) : new Date(ts)
    if (isNaN(date.getTime())) return "—"

    return date.toLocaleString("en-US", {
        timeZone: UAE_TIMEZONE,
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
        ...options
    })
}

/**
 * Formats a UNIX timestamp to HH:MM AM/PM in UAE Time.
 */
export function formatUAETime(ts, options = {}) {
    if (!ts) return "—"
    const date = typeof ts === "number" ? new Date(ts > 1e11 ? ts : ts * 1000) : new Date(ts)
    if (isNaN(date.getTime())) return "—"

    return date.toLocaleTimeString("en-US", {
        timeZone: UAE_TIMEZONE,
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
        ...options
    })
}

/**
 * Formats a UNIX timestamp to calendar date (e.g. Oct 17, 2026) in UAE Time.
 */
export function formatUAEDate(ts, options = {}) {
    if (!ts) return "—"
    const date = typeof ts === "number" ? new Date(ts > 1e11 ? ts : ts * 1000) : new Date(ts)
    if (isNaN(date.getTime())) return "—"

    return date.toLocaleDateString("en-US", {
        timeZone: UAE_TIMEZONE,
        month: "short",
        day: "numeric",
        year: "numeric",
        ...options
    })
}

/**
 * Returns current UAE calendar date as YYYY-MM-DD.
 */
export function getUAETodayStr() {
    const date = new Date()
    return date.toLocaleDateString("en-CA", { timeZone: UAE_TIMEZONE })
}

export function getUAEDateStr(offsetDays = 0) {
    const date = new Date(Date.now() + offsetDays * 86400 * 1000)
    return date.toLocaleDateString("en-CA", { timeZone: UAE_TIMEZONE })
}

/**
 * Returns exact start of today in UAE time (00:00:00 UAE, UTC+4) and current timestamp in UNIX seconds.
 */
export function getUAETodayBounds() {
    const todayStr = getUAETodayStr()
    const fromTimeMs = new Date(`${todayStr}T00:00:00+04:00`).getTime()
    const from_time = Math.floor(fromTimeMs / 1000)
    const to_time = Math.floor(Date.now() / 1000)
    return { from_time, to_time }
}

/**
 * Formats a duration in seconds to a human-readable live string (e.g. "13m 24s", "1h 04m 12s", "45s").
 */
export function formatElapsedDuration(totalSeconds) {
    const s = Math.max(0, Math.floor(Number(totalSeconds || 0)))
    const hours = Math.floor(s / 3600)
    const minutes = Math.floor((s % 3600) / 60)
    const seconds = s % 60

    if (hours > 0) {
        return `${hours}h ${minutes.toString().padStart(2, "0")}m ${seconds.toString().padStart(2, "0")}s`
    }
    if (minutes > 0) {
        return `${minutes}m ${seconds.toString().padStart(2, "0")}s`
    }
    return `${seconds}s`
}
