// Totals over values that may be unknown (null/undefined from the backend when a Wialon report cell was
// unreadable or a unit could not be attributed). Unknown values are never counted as zero: they are
// left out of the total and reported, so the UI can mark the total as partial.
import { isKnownNumber } from "../components/Fuel/telemetryFormat.js"

export { isKnownNumber }

// { total, known, unknown, partial, allUnknown } for getValue(item) over items
export function sumKnown(items, getValue) {
    let total = 0
    let known = 0
    let unknown = 0
    for (const item of items || []) {
        const value = getValue(item)
        if (isKnownNumber(value)) {
            total += Number(value)
            known += 1
        } else {
            unknown += 1
        }
    }
    return { total, known, unknown, partial: unknown > 0, allUnknown: unknown > 0 && known === 0 }
}

// Idle seconds of an idle-analytics row. `total_idle_seconds: null` means unknown; older payload
// shapes (idle_seconds / idle_hours) are still read when that key is absent.
export function idleSecondsOf(row) {
    if (!row) return null
    if (Object.prototype.hasOwnProperty.call(row, "total_idle_seconds")) return row.total_idle_seconds
    if (isKnownNumber(row.idle_seconds)) return Number(row.idle_seconds)
    if (isKnownNumber(row.idle_hours)) return Number(row.idle_hours) * 3600
    return null
}

// Driving time (engine on, moving) of an idle-analytics row: the Wialon Engine Hours report's "In motion"
// seconds. `in_motion_seconds: null` or an absent key (older payload) is unknown; it is never estimated from
// distance and average speed.
export function inMotionSecondsOf(row) {
    if (!row) return null
    return isKnownNumber(row.in_motion_seconds) ? Number(row.in_motion_seconds) : null
}

// True when a Dashboard report payload (mileage, fleet trips or idle analytics) is partial or was served stale after
// a Wialon failure, so a closed period's data is worth re-reading. A missing payload counts as partial.
export function isPartialReport(payload) {
    if (!payload || typeof payload !== "object") return true
    if (payload.stale === true) return true
    if (Array.isArray(payload.incomplete_units) && payload.incomplete_units.length > 0) return true
    const summary = payload.summary
    if (summary && (summary.total_distance_partial || summary.total_trips_partial)) return true
    if (payload.kpis?.total_idle_partial) return true
    if (Array.isArray(payload.by_vehicle)
        && payload.by_vehicle.some(row => idleSecondsOf(row) === null || inMotionSecondsOf(row) === null)) return true
    return false
}

// Outcome of one Dashboard poll for yesterday's reports, from its Promise.allSettled results: the first `todayCount`
// entries are today's requests, every later entry is one of yesterday's. Yesterday is loaded only when every one of
// its requests succeeded (a failed one is retried on the next poll); `complete` is false when any of them is partial
// or stale (retried later).
export function yesterdayLoadOutcome(results, todayCount) {
    const yesterday = results.slice(todayCount)
    if (yesterday.length === 0 || !yesterday.every(r => r.status === "fulfilled")) return { loaded: false, complete: false }
    return { loaded: true, complete: yesterday.every(r => !isPartialReport(r.value)) }
}

// Driving seconds per unit for one period, as Map(vehicle_id string -> seconds | null), over the idle-analytics
// rows accepted by includeVehicle(id). Each unit's Engine Hours "In motion" value is used as reported; it is not
// checked against Trips report mileage, which Wialon measures differently. The backend sends null when the value
// is missing, unreadable or unattributable, and that unit's driving time stays unknown (null), never 0.
export function drivingSecondsByVehicle(idleData, includeVehicle = () => true) {
    const result = new Map()
    const rows = idleData?.by_vehicle
    if (!Array.isArray(rows)) return result
    for (const row of rows) {
        const id = String(row.vehicle_id)
        if (!includeVehicle(id)) continue
        result.set(id, inMotionSecondsOf(row))
    }
    return result
}

// Totals of two per-unit maps over the units known in both (like-for-like comparison of partial periods)
export function sumKnownInBoth(mapA, mapB) {
    let a = 0
    let b = 0
    let units = 0
    for (const [id, valueA] of mapA || []) {
        const valueB = mapB?.get(id)
        if (isKnownNumber(valueA) && isKnownNumber(valueB)) {
            a += Number(valueA)
            b += Number(valueB)
            units += 1
        }
    }
    return { a, b, units }
}

// Trip distance in km, or null when the trip is flagged with an unreadable distance cell
export function tripDistanceOf(trip) {
    if (!trip || (trip.incomplete_fields || []).includes("distance_km")) return null
    return isKnownNumber(trip.distance_km) ? Number(trip.distance_km) : null
}

// True when a vehicle trips response flags its distance incomplete as a whole (top-level `incomplete_fields`), e.g.
// a malformed trip row Wialon sent was dropped: the listed trips' sum is then partial even if every trip is known
export function tripsDistanceIncomplete(tripsData) {
    return (tripsData?.incomplete_fields || []).includes("distance_km")
}

// Distance in km of a vehicle trips response, or null when unknown: no readable trip distance at all (every listed
// trip unreadable, or every row dropped as malformed) is unknown, never 0 km. A complete empty trip list is 0 km.
export function tripsDistanceKm(tripsData) {
    if (!tripsData) return null
    const trips = tripsData.trips || []
    if (trips.length > 0) {
        const sum = sumKnown(trips, tripDistanceOf)
        return sum.allUnknown ? null : sum.total
    }
    if (tripsDistanceIncomplete(tripsData)) return null
    return tripsData.summary?.total_distance_km ?? 0
}

// Distance for display ("268.6 km"), or null when unknown. The mileage chart and the vehicle drawer both use it,
// so the same Wialon value never reads differently (e.g. 269 vs 268.6) between them.
export function formatKm(value) {
    if (!isKnownNumber(value)) return null
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} km`
}

// Sort comparator: known values descending, unknown values last
export function compareKnownDesc(a, b) {
    const ka = isKnownNumber(a)
    const kb = isKnownNumber(b)
    if (ka && kb) return Number(b) - Number(a)
    if (ka) return -1
    if (kb) return 1
    return 0
}

// Note for idle-analytics sections built from discrete events (trend, hotspots, event list). The backend sends
// `events_complete: false` when some vehicles' events were not read, so those sections are partial (a lower
// bound). An absent flag (older payload) adds no note.
export const PARTIAL_EVENTS_NOTE = "Partial: idle events for some vehicles could not be read"

export function partialEventsNote(analytics) {
    return analytics?.events_complete === false ? PARTIAL_EVENTS_NOTE : null
}
