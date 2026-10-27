// Live vehicle status from a backend record. Movement status and connection health are separate: a vehicle keeps
// its last known MOVING / IDLING / STOPPED status until OFFLINE (no message for 12 h). `telemetry_stale` marks a
// status that is not live: last seen Moving or Idling with a last message older than the stale threshold (10 min by
// default), or last seen Moving with a GPS fix older than that although messages are recent (position_stale). A
// stale unit last seen Stopped is not flagged (a parked tracker reports less often).
// Unknown values keep rendering as STOPPED, as before.

export const LIVE_STATUSES = ["MOVING", "IDLING", "STOPPED", "OFFLINE"]

const STATE_LABELS = { MOVING: "Moving", IDLING: "Idling", STOPPED: "Stopped" }

function isOfflineRecord(vehicle, raw) {
    return vehicle?.connection_status === "OFFLINE" || Boolean(vehicle?.is_offline) || raw === "OFFLINE"
}

export function normalizeVehicleStatus(vehicle) {
    const raw = String(vehicle?.status || "").trim().toUpperCase()
    if (isOfflineRecord(vehicle, raw)) return "OFFLINE"
    // An older payload's DELAYED status falls back to the last known state it carries
    const state = raw === "DELAYED" ? String(vehicle?.last_known_status || "").trim().toUpperCase() : raw
    if (state === "MOVING" || state === "IDLING") return state
    return "STOPPED"
}

// True when the status shown is the last known state, not live. Never for an OFFLINE vehicle.
export function isTelemetryStale(vehicle) {
    const raw = String(vehicle?.status || "").trim().toUpperCase()
    if (isOfflineRecord(vehicle, raw)) return false
    if (typeof vehicle?.telemetry_stale === "boolean") return vehicle.telemetry_stale
    // Older payload without the flag: the previous DELAYED rule
    return raw === "DELAYED" || vehicle?.position_stale === true
        || (vehicle?.connection_status === "DELAYED" && (raw === "MOVING" || raw === "IDLING"))
}

// Mutually exclusive counts: MOVING + IDLING + STOPPED + OFFLINE === ALL. STALE (telemetry not live) overlaps
// MOVING / IDLING and is reported separately.
export function countByStatus(vehicles) {
    const counts = { ALL: 0, MOVING: 0, IDLING: 0, STOPPED: 0, OFFLINE: 0, STALE: 0 }
    for (const v of vehicles || []) {
        counts.ALL += 1
        counts[normalizeVehicleStatus(v)] += 1
        if (isTelemetryStale(v)) counts.STALE += 1
    }
    return counts
}

export function formatAge(seconds) {
    if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return null
    const mins = Math.floor(seconds / 60)
    if (mins < 60) return `${mins} min`
    const h = Math.floor(mins / 60)
    return mins % 60 ? `${h}h ${mins % 60}m` : `${h}h`
}

// True when the backend marked the GPS fix stale although messages are recent (last seen Moving, fix > 10 min old)
export function isPositionStale(vehicle) {
    return vehicle?.position_stale === true && normalizeVehicleStatus(vehicle) !== "OFFLINE"
}

// "Last seen Moving · 11h ago" for a telemetry-stale or OFFLINE vehicle; null for live ones or when unknown.
// A stale GPS fix with recent messages reads "Last GPS fix Moving · 6h ago (data 1 min ago)", so a recent
// heartbeat never makes the old position look current.
export function lastSeenLabel(vehicle) {
    const status = normalizeVehicleStatus(vehicle)
    if (status !== "OFFLINE" && !isTelemetryStale(vehicle)) return null
    const state = STATE_LABELS[String(vehicle?.last_known_status || "").toUpperCase()]
    if (isPositionStale(vehicle)) {
        const fixAge = formatAge(vehicle?.seconds_since_position)
        const dataAge = formatAge(vehicle?.seconds_since_update)
        return `Last GPS fix${state ? ` ${state}` : ""}${fixAge ? ` · ${fixAge} ago` : ""}${dataAge ? ` (data ${dataAge} ago)` : ""}`
    }
    const age = formatAge(vehicle?.seconds_since_update)
    if (!state && !age) return null
    return `Last seen${state ? ` ${state}` : ""}${age ? ` · ${age} ago` : ""}`
}
