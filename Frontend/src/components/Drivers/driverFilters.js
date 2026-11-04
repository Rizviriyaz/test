// Drivers page logic (pure, testable offline): labels, search and filters, KPI counts and the detail rows of one driver
// from the /api/drivers response. Missing values stay null and are shown as NOT_AVAILABLE, never invented.

export const NOT_AVAILABLE = "Not available"

export const STATUS_LABELS = {
    assigned: "Assigned",
    unassigned: "Unassigned",
    ambiguous: "Ambiguous",
    vehicle_not_found: "Vehicle not found",
}

// Assignments that need attention in Wialon: a vehicle shared by several drivers, or a bound vehicle that is not found
export const INCOMPLETE_STATUSES = new Set(["ambiguous", "vehicle_not_found"])

export const STATUS_FILTERS = [
    { id: "all", label: "All Drivers" },
    { id: "assigned", label: "Assigned" },
    { id: "unassigned", label: "Unassigned" },
]

export const ISSUE_LABELS = {
    missing_name: "Name missing",
    missing_phone: "Phone number missing",
    missing_card: "Driver card missing",
    missing_driver_id: "Employee ID missing",
    duplicate_driver_id: "Employee ID shared with another driver",
    ambiguous: "Vehicle bound to more than one driver",
    vehicle_not_found: "Bound vehicle not found in Wialon",
}

export const DEFAULT_FILTERS = { status: "all", search: "", group: "", missingPhone: false, missingCard: false }

/** The vehicle as shown: registration plate, else Wialon unit name, else null. */
export function vehicleLabel(assignment) {
    return assignment?.plate || assignment?.vehicle_name || null
}

const normalize = (value) => String(value ?? "").toLowerCase().replace(/\s+/g, " ").trim()
const compact = (value) => normalize(value).replace(/[\s-]/g, "")

/** Search by driver name, Employee ID (jp.ID) or vehicle plate/name; plates also match without spaces or dashes. */
export function matchesSearch(driver, search) {
    const q = normalize(search)
    if (!q) return true
    const a = driver?.assignment || {}
    const fields = [driver?.name, driver?.driver_id, a.plate, a.vehicle_name]
    const qc = compact(search)
    return fields.some((f) => f != null && (normalize(f).includes(q) || (qc && compact(f).includes(qc))))
}

export function matchesStatus(driver, status) {
    const s = driver?.assignment?.status
    if (!status || status === "all") return true
    if (status === "incomplete") return INCOMPLETE_STATUSES.has(s)
    return s === status
}

/** Every filter applies together: status, search, group and the missing-phone / missing-card toggles. */
export function filterDrivers(drivers, filters = DEFAULT_FILTERS) {
    const { status, search, group, missingPhone, missingCard } = { ...DEFAULT_FILTERS, ...filters }
    return (Array.isArray(drivers) ? drivers : []).filter((d) =>
        matchesStatus(d, status)
        && (!group || (d?.assignment?.groups || []).includes(group))
        && (!missingPhone || !d?.phone)
        && (!missingCard || !d?.card)
        && matchesSearch(d, search)
    )
}

/** KPI values from the response summary; computed from the drivers when the summary lacks a value. */
export function kpiCounts(data) {
    const drivers = Array.isArray(data?.drivers) ? data.drivers : []
    const summary = data?.summary || {}
    const statusCount = (fn) => drivers.filter((d) => fn(d?.assignment?.status)).length
    const pick = (key, compute) => (Number.isFinite(summary[key]) ? summary[key] : compute())
    return {
        total: pick("total", () => drivers.length),
        assigned: pick("assigned", () => statusCount((s) => s === "assigned")),
        unassigned: pick("unassigned", () => statusCount((s) => s === "unassigned")),
        missingPhone: pick("missing_phone", () => drivers.filter((d) => !d?.phone).length),
        missingCard: pick("missing_card", () => drivers.filter((d) => !d?.card).length),
        incomplete: pick("incomplete_assignments", () => statusCount((s) => INCOMPLETE_STATUSES.has(s))),
    }
}

/** The group(s) shown for a driver: the vehicle's groups, or null when the driver has no vehicle. */
export function groupLabel(assignment) {
    const groups = assignment?.groups || []
    return groups.length ? groups.join(", ") : null
}

/** Detail modal rows (label, value or null) for one driver; the internal Wialon record id is never part of them. */
export function detailRows(driver, formatTime = (ts) => String(ts)) {
    const a = driver?.assignment || {}
    const hasVehicle = a.status && a.status !== "unassigned"
    return {
        contact: [
            { label: "Employee ID", value: driver?.driver_id || null, mono: true },
            { label: "Phone", value: driver?.phone || null, href: driver?.phone ? `tel:${String(driver.phone).replace(/\s+/g, "")}` : null },
            { label: "Driver card", value: driver?.card || null, mono: true },
        ],
        assignment: [
            { label: "Status", value: STATUS_LABELS[a.status] || null },
            { label: "Vehicle number", value: hasVehicle ? (a.plate || null) : null, mono: true },
            { label: "Vehicle name", value: hasVehicle ? (a.vehicle_name || null) : null },
            { label: "Vehicle group", value: groupLabel(a) },
            { label: "Assigned since", value: a.since ? formatTime(a.since) : null },
        ],
        issues: (driver?.issues || []).map((issue) => ISSUE_LABELS[issue] || issue),
    }
}
