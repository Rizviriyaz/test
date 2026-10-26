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

export const DRIVING_MILEAGE_TOLERANCE_KM = 2
export const DRIVING_MILEAGE_TOLERANCE_RATIO = 0.05

// True when a unit's Engine Hours report mileage agrees with its Dashboard (Trips report) mileage within
// max(2 km, 5% of the Dashboard mileage). False when either is unknown: the check cannot be performed.
export function engineMileageAgrees(engineKm, dashboardKm) {
    if (!isKnownNumber(engineKm) || !isKnownNumber(dashboardKm)) return false
    const tolerance = Math.max(DRIVING_MILEAGE_TOLERANCE_KM, DRIVING_MILEAGE_TOLERANCE_RATIO * Number(dashboardKm))
    // Report mileage has two decimals; the epsilon keeps a difference exactly at the tolerance inside it
    return Math.abs(Number(engineKm) - Number(dashboardKm)) <= tolerance + 1e-9
}

// Driving seconds per unit for one period, as Map(vehicle_id string -> seconds | null), over the idle-analytics
// rows accepted by includeVehicle(id). A unit's "In motion" value is used only when its Engine Hours mileage
// agrees with its Dashboard mileage for the same period start; otherwise, or when the unit covered distance
// with 0 s in motion, or when it is missing / duplicated in the mileage list, its driving time is unknown (null).
export function drivingSecondsByVehicle(idleData, mileageData, includeVehicle = () => true) {
    const result = new Map()
    const rows = idleData?.by_vehicle
    if (!Array.isArray(rows)) return result
    const samePeriod = isKnownNumber(idleData?.timeframe?.from_time)
        && idleData.timeframe.from_time === mileageData?.effective_from
    const mileageById = new Map()
    const duplicateIds = new Set()
    for (const unit of (samePeriod && mileageData.all_units) || []) {
        if (unit?.vehicle_id === null || unit?.vehicle_id === undefined) continue
        const id = String(unit.vehicle_id)
        if (mileageById.has(id)) duplicateIds.add(id)
        mileageById.set(id, unit.mileage)
    }
    for (const row of rows) {
        const id = String(row.vehicle_id)
        if (!includeVehicle(id)) continue
        const motion = inMotionSecondsOf(row)
        const dashboardKm = duplicateIds.has(id) ? null : mileageById.get(id)
        const reliable = motion !== null
            && engineMileageAgrees(row.engine_mileage_km, dashboardKm)
            && !(motion === 0 && Number(dashboardKm) > 0)
        result.set(id, reliable ? motion : null)
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
