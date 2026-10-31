// Data freshness state of a response (its backend `freshness` block, see Backend/app/utils/freshness.py) together with
// the request's own state. Free of React/browser imports so it runs under `node --test`.
//
// Separate from vehicle operational statuses: a vehicle can be MOVING while its data is Delayed, and OFFLINE never
// makes a response Stale. The age is recomputed here from fetched_at, so a response kept in the client cache keeps
// ageing; nothing is inferred when the backend gave no read time.

export const FRESHNESS_LABELS = {
    fresh: "Fresh",
    cached: "Cached",
    delayed: "Delayed",
    stale: "Stale",
    refreshing: "Refreshing",
    failed: "Refresh failed",
    unavailable: "Unavailable",
}

// A response read within this many seconds counts as just read
const FRESH_SECONDS = 5

const epoch = (v) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null)

// Seconds since the backend read the data, or null when it gave no read time
export function freshnessAgeSeconds(freshness, nowMs = Date.now()) {
    const fetched = epoch(freshness?.fetched_at)
    return fetched === null ? null : Math.max(0, Math.floor(nowMs / 1000 - fetched))
}

/**
 * freshness: the response's freshness block (or null); loading: a request for it is running; error: the latest
 * request failed; hasData: data is on screen. Returns a key of FRESHNESS_LABELS, or null when there is nothing to say.
 * - no data on screen: unavailable after an error, refreshing while loading
 * - data on screen: refreshing while a request runs, failed when the latest one failed (the data shown is older)
 * - otherwise from the block: unavailable / stale (last good result after a source failure) / delayed (older than the
 *   category's target age) / fresh (just read) / cached
 */
export function freshnessState({ freshness = null, loading = false, error = null, hasData = true, nowMs = Date.now() } = {}) {
    if (!hasData) {
        if (error) return "unavailable"
        if (loading) return "refreshing"
        return freshness?.refresh_status === "unavailable" ? "unavailable" : null
    }
    if (loading) return "refreshing"
    if (error) return "failed"
    if (!freshness) return null
    if (freshness.refresh_status === "unavailable") return "unavailable"
    if (freshness.is_stale === true || freshness.refresh_status === "stale") return "stale"
    const age = freshnessAgeSeconds(freshness, nowMs)
    const target = epoch(freshness.target_age_seconds)
    if (age !== null && target !== null && age > target) return "delayed"
    if (age !== null && age <= FRESH_SECONDS) return "fresh"
    return "cached"
}

// Hover text: what the times mean (backend read time vs the source's own time) and any refresh error
export function freshnessTitle(freshness, state, formatTime) {
    const parts = []
    const fetched = epoch(freshness?.fetched_at)
    if (fetched !== null) parts.push(`Read from the source at ${formatTime(new Date(fetched * 1000))}`)
    const source = epoch(freshness?.source_updated_at)
    if (source !== null) parts.push(`latest source update ${formatTime(new Date(source * 1000))}`)
    if (state === "delayed") parts.push("older than this data's usual refresh interval")
    if (freshness?.refresh_error) parts.push(freshness.refresh_error)
    return parts.join("; ") || FRESHNESS_LABELS[state] || ""
}
