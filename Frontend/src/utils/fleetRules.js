// Shared display rules that several pages must apply identically. Free of React/browser imports so they run
// under `node --test`.

// ─── Idle duration ──────────────────────────────────────────────────────────
// The backend sends `idle_duration` (seconds, always a number) with `idle_duration_status`:
//   "known"       the measured current idle run
//   "lower_bound" the run started before the history searched: it lasted AT LEAST idle_duration
//   "unknown"     the idle start could not be read; idle_duration is 0 and is not a measurement
// An absent status (older payload) is treated as "known", as before.
export function idleDurationStatus(vehicle) {
    const status = vehicle?.idle_duration_status ?? vehicle?.idle?.duration_status
    return status === "lower_bound" || status === "unknown" ? status : "known"
}

// "Unknown", "<duration>+" (lower bound), "Just started" (a known 0) or the formatted duration
export function idleDurationLabel(vehicle, formatSeconds) {
    const status = idleDurationStatus(vehicle)
    if (status === "unknown") return "Unknown"
    const seconds = Number(vehicle?.idle_duration)
    if (!Number.isFinite(seconds) || seconds <= 0) return status === "lower_bound" ? "Unknown start" : "Just started"
    const text = formatSeconds(seconds)
    return status === "lower_bound" ? `${text}+` : text
}

function toEpochSeconds(value) {
    if (value === null || value === undefined || value === "") return NaN
    return typeof value === "number"
        ? (value > 1e11 ? Math.floor(value / 1000) : value)
        : Math.floor(new Date(value).getTime() / 1000)
}

// Seconds of the current idle run shown by the live idle timer: from its start to now, capped at `endTime` (the
// last message time) because idling is only known up to then. Falls back to baseDuration without a usable start.
export function idleElapsedSeconds(startTime, baseDuration, nowMs, endTime = null) {
    if (!startTime) {
        return Number(baseDuration || 0)
    }
    const startSec = toEpochSeconds(startTime)

    if (isNaN(startSec) || startSec <= 0) {
        return Number(baseDuration || 0)
    }

    // Idling is only known up to the last message: the timer never runs past it
    const endSec = toEpochSeconds(endTime)
    const nowSec = Math.floor(nowMs / 1000)
    const untilSec = Number.isFinite(endSec) && endSec > 0 ? Math.min(nowSec, endSec) : nowSec
    return Math.max(0, untilSec - startSec)
}

// ─── Group membership ───────────────────────────────────────────────────────
// Every group name a row belongs to: live vehicles carry `group` + `groups`, idle-analytics rows `group_name` +
// `group_names`. A vehicle in several groups belongs to each of them.
export function groupNamesOf(row) {
    const names = new Set()
    for (const name of [row?.group, row?.group_name, ...(Array.isArray(row?.groups) ? row.groups : []),
        ...(Array.isArray(row?.group_names) ? row.group_names : [])]) {
        if (typeof name === "string" && name.trim()) names.add(name.trim())
    }
    return Array.from(names)
}

export function inGroup(row, groupName) {
    if (!groupName || groupName === "ALL") return true
    const target = String(groupName).trim().toLowerCase()
    return groupNamesOf(row).some(name => name.toLowerCase() === target)
}

// Group ids of an idle-analytics row (all memberships; `group_id` is the primary group)
export function groupIdsOf(row) {
    const ids = Array.isArray(row?.group_ids) && row.group_ids.length ? row.group_ids : [row?.group_id]
    return ids.filter(id => id !== null && id !== undefined).map(String)
}

// ─── Vehicle health ─────────────────────────────────────────────────────────
// The backend's "healthy" rule (fleet_analytics.get_fleet_health_analytics): no battery warning, no GPS warning,
// a stable connection and a score of at least 80. The page's Healthy filter uses it so the list matches the KPI.
export const HEALTHY_MIN_SCORE = 80

export function isHealthyVehicle(v) {
    const battery = String(v?.battery?.status || "").toUpperCase()
    const gps = String(v?.gps?.status || "").toUpperCase()
    const conn = String(v?.connectivity?.status || "").toUpperCase()
    return battery !== "WARNING" && battery !== "CRITICAL"
        && gps !== "WEAK" && gps !== "STALE"
        && conn === "STABLE"
        && Number(v?.health_score ?? 0) >= HEALTHY_MIN_SCORE
}

// ─── Fuel vehicle lists ─────────────────────────────────────────────────────
// The global Fuel filter is an exact vehicle filter (KPI scope). The vehicle-efficiency list endpoint reads
// `vehicle_identifier` as its search box (substring), so the global filter is sent as `vehicle_filter` instead.
export function fuelVehicleListParams(queryParams = {}, searchQuery = "") {
    const { vehicle_identifier: vehicleFilter, ...rest } = queryParams || {}
    const params = { ...rest }
    if (vehicleFilter) params.vehicle_filter = vehicleFilter
    if (searchQuery) params.vehicle_identifier = searchQuery
    return params
}
