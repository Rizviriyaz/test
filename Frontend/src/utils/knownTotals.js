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

// Trip distance in km, or null when the trip is flagged with an unreadable distance cell
export function tripDistanceOf(trip) {
    if (!trip || (trip.incomplete_fields || []).includes("distance_km")) return null
    return isKnownNumber(trip.distance_km) ? Number(trip.distance_km) : null
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
