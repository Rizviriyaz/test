// Today's idle events of each driver (pure, testable offline) from the fleet /api/idle/events response for the UAE day
// (timeframe "today", Wialon Idle Report-Unit template 28). The template names the driver only by text (its Driver
// column), so an event is attributed to a driver only when that text exactly matches the driver's Wialon name (case and
// spacing ignored), the name is unique among Wialon drivers and is not a placeholder record ("Un-assigned A 74").
// The driver's current vehicle assignment is never used.

// Placeholder driver records kept in Wialon for unissued cards: their names are not a person
const PLACEHOLDER_NAME = /^\s*un-?\s*assigned\b/i

export const IDLE_UNATTRIBUTABLE_MESSAGES = {
    no_name: "This driver has no name in Wialon, so idle events cannot be attributed to them.",
    placeholder: "This Wialon driver record has a placeholder name, so idle events cannot be attributed to it.",
    duplicate_name: "Another Wialon driver has the same name, so idle events cannot be attributed reliably.",
}

export function normalizeDriverName(name) {
    return String(name ?? "").replace(/\s+/g, " ").trim().toLowerCase()
}

/**
 * Index of today's idle events by attributable driver name. `idleData` is the /api/idle/events response; its events
 * are attributed only to names held by exactly one non-placeholder driver in `drivers`.
 */
export function buildDriverIdleIndex(drivers, idleData) {
    const nameCounts = new Map()
    for (const d of Array.isArray(drivers) ? drivers : []) {
        const key = normalizeDriverName(d?.name)
        if (key) nameCounts.set(key, (nameCounts.get(key) || 0) + 1)
    }
    const eventsByName = new Map()
    for (const ev of Array.isArray(idleData?.events) ? idleData.events : []) {
        const key = normalizeDriverName(ev?.driver)
        if (!key || nameCounts.get(key) !== 1 || PLACEHOLDER_NAME.test(key)) continue
        if (!eventsByName.has(key)) eventsByName.set(key, [])
        eventsByName.get(key).push(ev)
    }
    for (const list of eventsByName.values()) list.sort((a, b) => (a.start_time || 0) - (b.start_time || 0))
    return { nameCounts, eventsByName, partial: idleData?.events_complete === false }
}

/**
 * One driver's idle events today: { status, events, count, totalSeconds, partial }. `status` is "ok" or why events cannot
 * be attributed (no_name | placeholder | duplicate_name; count and total are then null, never zero). `partial` marks a
 * lower bound: some vehicles' Wialon idle reports could not be read.
 */
export function driverIdleSummary(driver, index) {
    const key = normalizeDriverName(driver?.name)
    const blocked = (status) => ({ status, events: [], count: null, totalSeconds: null, partial: false })
    if (!key) return blocked("no_name")
    if (PLACEHOLDER_NAME.test(key)) return blocked("placeholder")
    if ((index?.nameCounts?.get(key) || 0) > 1) return blocked("duplicate_name")
    const events = index?.eventsByName?.get(key) || []
    return {
        status: "ok",
        events,
        count: events.length,
        totalSeconds: events.reduce((sum, ev) => sum + Math.max(0, Number(ev?.duration) || 0), 0),
        partial: Boolean(index?.partial),
    }
}

/** The vehicle of an idle event as shown: registration plate, else Wialon unit name, else null. */
export function eventVehicleLabel(ev) {
    return ev?.registration_plate || ev?.vehicle_name || null
}
