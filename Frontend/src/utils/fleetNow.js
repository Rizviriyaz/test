// Fleet Now card rules (Dashboard). Free of React/browser imports so they run under `node --test`.
import { isKnownNumber } from "./knownTotals.js"
import { isTelemetryStale, normalizeVehicleStatus } from "./vehicleStatus.js"

const UAE_TIMEZONE = "Asia/Dubai"

function uaeDateOf(epochSeconds) {
    if (!isKnownNumber(epochSeconds)) return null
    return new Date(epochSeconds * 1000).toLocaleDateString("en-CA", { timeZone: UAE_TIMEZONE })
}

// ─── UAE day guard ──────────────────────────────────────────────────────────
// A report response is used for a UAE day only while its window starts on that date (YYYY-MM-DD), so a payload
// loaded before midnight is never read as the next day's. `startOf` reads the window start (epoch seconds):
// fleet trips `from_time`, idle analytics `timeframe.from_time`, dashboard mileage `effective_from`.
export function forUaeDay(data, dayStr, startOf = d => d.from_time) {
    return data && uaeDateOf(startOf(data)) === dayStr ? data : null
}

// The UAE calendar day before dayStr (YYYY-MM-DD; the UAE has no DST)
export function previousUaeDay(dayStr) {
    const [y, m, d] = dayStr.split("-").map(Number)
    return new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10)
}

// ─── Active Today ───────────────────────────────────────────────────────────
// A vehicle is active today when there is evidence of activity inside today's UAE day:
//   - today's Trips report mileage (/api/dashboard/mileage) is above 0, or
//   - today's fleet trips (/api/fleet/trips) show a trip or GPS mileage above 0, or
//   - its live status is MOVING / IDLING, not telemetry-stale, from a message sent today (UAE).
// A stale last-known status alone is not evidence. A report counts only when its window starts on today's UAE date,
// so a payload loaded before midnight is not read as today's. A vehicle with no evidence is inactive only when a
// fresh report gives it a known 0 km; otherwise (report missing, failed, served stale, or its value unknown) it is
// unknown and counted separately, never as inactive.
// Engine-on time without distance (idling only, now stopped) is not counted: the existing rule never used the idle
// report, and that business definition is left unchanged.
export function activeTodaySummary(vehicles, { mileageData, tripsData, todayStr }) {
    const mileageToday = uaeDateOf(mileageData?.effective_from) === todayStr && Array.isArray(mileageData?.all_units)
    const tripsToday = uaeDateOf(tripsData?.from_time) === todayStr && Array.isArray(tripsData?.vehicles)
    const mileageById = new Map()
    if (mileageToday) {
        for (const u of mileageData.all_units) {
            if (u.vehicle_id !== null && u.vehicle_id !== undefined) mileageById.set(String(u.vehicle_id), u)
        }
    }
    const tripsById = new Map()
    if (tripsToday) {
        for (const tv of tripsData.vehicles) tripsById.set(String(tv.vehicle_id), tv)
    }

    let active = 0
    let unknown = 0
    for (const v of vehicles || []) {
        const id = String(v.vehicle_id)
        const status = normalizeVehicleStatus(v)
        const liveActive = (status === "MOVING" || status === "IDLING") && !isTelemetryStale(v)
            && uaeDateOf(v.last_message_timestamp) === todayStr
        const m = mileageById.get(id)
        const t = tripsById.get(id)
        const reportActive = (isKnownNumber(m?.mileage) && m.mileage > 0)
            || (isKnownNumber(t?.trip_count) && t.trip_count > 0)
            || (isKnownNumber(t?.gps_mileage_km) && t.gps_mileage_km > 0)
        if (liveActive || reportActive) {
            active += 1
            continue
        }
        const knownIdle = (mileageData?.stale !== true && isKnownNumber(m?.mileage))
            || (tripsData?.stale !== true && isKnownNumber(t?.gps_mileage_km))
        if (!knownIdle) unknown += 1
    }
    return { active, unknown, complete: unknown === 0 }
}

// ─── Alerts ─────────────────────────────────────────────────────────────────
// /api/fleet/alerts lists current conditions (battery, offline, delayed telemetry, GPS) plus harsh-driving events of
// the last 24 h. A condition alert's `timestamp` is the vehicle's last message, not when the alert began, so it
// cannot be filtered by day; the card counts the same alerts as the Alerts page. `total_alerts` and `by_vehicle`
// include harsh-driving events beyond the listed (capped) ones.
// Returns { count, partial, lowerBound }; count is null when the response is unavailable.
export function fleetAlertsCount(alertsData, { selectedGroup = "ALL", vehicleIds } = {}) {
    if (!alertsData || !Array.isArray(alertsData.alerts)) return { count: null, partial: false, lowerBound: false }
    const partial = alertsData.safety_partial === true
    if (selectedGroup === "ALL") {
        const count = isKnownNumber(alertsData.total_alerts) ? alertsData.total_alerts : alertsData.alerts.length
        return { count, partial, lowerBound: !isKnownNumber(alertsData.total_alerts) && alertsData.alerts_capped === true }
    }
    const ids = vehicleIds || new Set()
    if (alertsData.by_vehicle && typeof alertsData.by_vehicle === "object") {
        let count = 0
        for (const [vid, n] of Object.entries(alertsData.by_vehicle)) {
            if (ids.has(String(vid)) && isKnownNumber(n)) count += n
        }
        return { count, partial, lowerBound: false }
    }
    // Older payload without per-vehicle totals: only the listed alerts can be counted
    const count = alertsData.alerts.filter(a => a.vehicle_id !== null && a.vehicle_id !== undefined
        && ids.has(String(a.vehicle_id))).length
    return { count, partial, lowerBound: alertsData.alerts_capped === true }
}

// ─── Offline note ───────────────────────────────────────────────────────────
// OFFLINE is decided by the backend: no message for longer than CONN_DELAYED_SECONDS (12 h by default, configurable
// on the server and not sent to the frontend), or no message time at all. The note states what the data shows: the
// shortest silence among the offline units and how many never reported a time.
export const OFFLINE_RULE_TITLE = "Offline: no message for longer than the server's offline threshold "
    + "(CONN_DELAYED_SECONDS, 12 h by default), or no message time from Wialon at all"

export function offlineNote(offlineVehicles) {
    let neverReported = 0
    let minAge = null
    for (const v of offlineVehicles || []) {
        if (!isKnownNumber(v.seconds_since_update)) {
            neverReported += 1
        } else if (minAge === null || v.seconds_since_update < minAge) {
            minAge = v.seconds_since_update
        }
    }
    const parts = []
    if (minAge !== null) parts.push(`silent ≥ ${Math.floor(minAge / 3600)} h`)
    if (neverReported > 0) parts.push(`${neverReported} never reported`)
    return parts.length ? parts.join(" · ") : null
}

// ─── Distance ───────────────────────────────────────────────────────────────
// Trips report mileage at the report's own precision (the backend rounds each unit to 0.01 km)
export function roundKm2(value) {
    return isKnownNumber(value) ? Math.round(value * 100) / 100 : null
}

// Today's mileage (/api/dashboard/mileage?timeframe=today). The latest successful response is kept and used only
// while its window starts on today's UAE date, so after midnight a previous day's total is never shown as today's.
// It is shown but marked not current when the latest refresh failed, or when the backend served its last good
// result after a Wialon failure (`stale: true`). Notice: null, or { text, title } for the Distance KPI.
export function todayMileageState(mileageData, { refreshFailed = false, todayStr }) {
    const data = forUaeDay(mileageData, todayStr, d => d.effective_from)
    let notice = null
    if (data && refreshFailed) {
        notice = { text: "Not current: refresh failed", title: "The latest mileage refresh failed; the last successful result is shown" }
    } else if (data && data.stale === true) {
        notice = { text: "Not current: Wialon unavailable", title: "Wialon failed; the backend served its last good mileage result" }
    } else if (!data && refreshFailed) {
        notice = { text: "Mileage report unavailable", title: "Today's mileage report could not be loaded" }
    }
    return { data, notice }
}

export function formatKm2(value) {
    const km = roundKm2(value)
    return km === null ? null : `${km.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} km`
}

// ─── Section state ──────────────────────────────────────────────────────────
// A Dashboard section with no data yet is "pending" (skeleton) until its request fails ("failed": unavailable,
// retried by the poll). Data already shown stays "ready" through a failed refresh, which only marks it not current.
// No data is never shown as an empty or zero result.
export function sectionState(data, failed) {
    if (data !== null && data !== undefined) return "ready"
    return failed ? "failed" : "pending"
}

// Label for data shown from an earlier visit's client cache until this visit's first request succeeds
export const EARLIER_RESULT_NOTICE = {
    text: "Earlier result · refreshing",
    title: "Shown from your previous visit until this visit's refresh answers",
}

// Notice under a report value: null, or { text, title }. A failure outranks the earlier-visit label.
export function reportNotice(state, { failed = false, earlier = false } = {}) {
    if (state === "failed") {
        return { text: "Report unavailable, retrying", title: "This report could not be loaded; it is retried automatically" }
    }
    if (state !== "ready") return null
    if (failed) return { text: "Not current: refresh failed", title: "The latest refresh failed; the last successful result is shown" }
    return earlier ? EARLIER_RESULT_NOTICE : null
}

// Yesterday column of "Today vs. Yesterday": each cell follows only the request(s) it is read from, so one request
// answering never clears another cell's pending, failed or earlier-visit state. `failed` and `earlier` are keyed by
// request (mileage, trips, idle); mileage is never served from the client cache, so it is never "earlier".
export function yesterdayCellStates({ mileage, trips, idle }, { failed = {}, earlier = {} } = {}) {
    const cell = (data, dataFailed, noticeFailed, isEarlier) => {
        const state = sectionState(data, dataFailed)
        return { state, notice: reportNotice(state, { failed: noticeFailed, earlier: isEarlier }) }
    }
    const idleCell = cell(idle, failed.idle, failed.idle, earlier.idle)
    return {
        // Distance reads the mileage report, else trips (as calculateTimeframeActivity does): it is unavailable only
        // when both failed, and not current when either of its sources failed
        distance: mileage
            ? cell(mileage, failed.mileage, failed.mileage, false)
            : cell(trips, failed.mileage && failed.trips, failed.mileage || failed.trips, earlier.trips),
        trips: cell(trips, failed.trips, failed.trips, earlier.trips),
        driving: idleCell,
        idle: idleCell,
    }
}
