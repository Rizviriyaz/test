// Live vehicle status from a backend record. DELAYED means the last message, showing Moving or Idling, is older
// than the stale threshold (10 min by default), or last seen Moving with a GPS fix older than that although messages
// are recent (position_stale): last_known_status is what that message showed, not the vehicle's
// current state. A stale unit last seen Stopped stays STOPPED until OFFLINE (12 h).
// Unknown values keep rendering as STOPPED, as before.

export const LIVE_STATUSES = ["MOVING", "IDLING", "STOPPED", "DELAYED", "OFFLINE"]

const STATE_LABELS = { MOVING: "Moving", IDLING: "Idling", STOPPED: "Stopped" }

export function normalizeVehicleStatus(vehicle) {
    const raw = String(vehicle?.status || "").trim().toUpperCase()
    if (vehicle?.connection_status === "OFFLINE" || Boolean(vehicle?.is_offline) || raw === "OFFLINE") return "OFFLINE"
    // A stale connection hides only a last-seen Moving/Idling state; a stale parked (Stopped) unit stays Stopped
    if (raw === "DELAYED" || (vehicle?.connection_status === "DELAYED" && (raw === "MOVING" || raw === "IDLING"))) return "DELAYED"
    if (raw === "MOVING" || raw === "IDLING") return raw
    return "STOPPED"
}

// Mutually exclusive counts: MOVING + IDLING + STOPPED + DELAYED + OFFLINE === ALL
export function countByStatus(vehicles) {
    const counts = { ALL: 0, MOVING: 0, IDLING: 0, STOPPED: 0, DELAYED: 0, OFFLINE: 0 }
    for (const v of vehicles || []) {
        counts.ALL += 1
        counts[normalizeVehicleStatus(v)] += 1
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
    return vehicle?.position_stale === true && normalizeVehicleStatus(vehicle) === "DELAYED"
}

// "Last seen Moving · 11h ago" for a DELAYED or OFFLINE vehicle; null for live ones or when unknown.
// A stale GPS fix with recent messages reads "Last GPS fix Moving · 6h ago (data 1 min ago)", so a recent
// heartbeat never makes the old position look current.
export function lastSeenLabel(vehicle) {
    const status = normalizeVehicleStatus(vehicle)
    if (status !== "DELAYED" && status !== "OFFLINE") return null
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
