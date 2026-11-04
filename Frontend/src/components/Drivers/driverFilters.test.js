import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import {
    DEFAULT_FILTERS, NOT_AVAILABLE, detailRows, filterDrivers, groupLabel, kpiCounts, matchesSearch, vehicleLabel,
} from "./driverFilters.js"

const assignment = (status, extra = {}) => ({
    status, vehicle_id: null, vehicle_name: null, plate: null, groups: [], since: null, since_iso: null, ...extra,
})

// Shaped like /api/drivers rows (test fixtures only)
const drivers = [
    { driver_id: "100101", name: "Aarav Assigned", phone: "+971500000101", card: "0158BE4201000101", issues: [],
        assignment: assignment("assigned", { vehicle_id: 1, vehicle_name: "Truck A", plate: "DXB 12345", groups: ["Logistics", "Transport"], since: 1793600000 }) },
    { driver_id: "100102", name: "Bilal Unassigned", phone: null, card: "0158BE4201000102", issues: ["missing_phone"],
        assignment: assignment("unassigned") },
    { driver_id: null, name: "Chen NoCard", phone: "+971500000103", card: null, issues: ["missing_card", "missing_driver_id"],
        assignment: assignment("unassigned") },
    { driver_id: "100109", name: "Dina Shared", phone: null, card: null, issues: ["missing_phone", "missing_card", "duplicate_driver_id", "ambiguous"],
        assignment: assignment("ambiguous", { vehicle_id: 2, vehicle_name: "Van B", groups: ["Transport"], since: 1793600000 }) },
    { driver_id: "100109", name: "Eli Shared", phone: "+971500000105", card: "0158BE4201000105", issues: ["duplicate_driver_id", "ambiguous"],
        assignment: assignment("ambiguous", { vehicle_id: 2, vehicle_name: "Van B", groups: ["Transport"] }) },
    { driver_id: "100106", name: "Farah Gone", phone: "+971500000106", card: "0158BE4201000106", issues: ["vehicle_not_found"],
        assignment: assignment("vehicle_not_found", { vehicle_id: 999 }) },
    { driver_id: "100107", name: "Gita Ungrouped", phone: "+971500000107", card: "0158BE4201000107", issues: [],
        assignment: assignment("assigned", { vehicle_id: 3, vehicle_name: "Bus C", groups: ["General Fleet"] }) },
]
const names = (list) => list.map((d) => d.name)

test("search matches driver name, displayed Employee ID and vehicle plate or name", () => {
    assert.deepEqual(names(filterDrivers(drivers, { search: "aarav" })), ["Aarav Assigned"])
    assert.deepEqual(names(filterDrivers(drivers, { search: "  BILAL   unassigned " })), ["Bilal Unassigned"], "case and spaces")
    assert.deepEqual(names(filterDrivers(drivers, { search: "100109" })), ["Dina Shared", "Eli Shared"], "Employee ID (jp.ID)")
    assert.deepEqual(names(filterDrivers(drivers, { search: "dxb12345" })), ["Aarav Assigned"], "plate without its space")
    assert.deepEqual(names(filterDrivers(drivers, { search: "van b" })), ["Dina Shared", "Eli Shared"], "vehicle name")
    assert.deepEqual(names(filterDrivers(drivers, { search: "0158BE4201000101" })), [], "card numbers are not a search field")
    assert.equal(matchesSearch(drivers[2], "null"), false, "a missing Employee ID never matches the text 'null'")
    assert.equal(filterDrivers(drivers, { search: "" }).length, drivers.length)
})

test("assignment filters", () => {
    assert.equal(filterDrivers(drivers, DEFAULT_FILTERS).length, 7, "All shows every driver, assigned or not")
    assert.deepEqual(names(filterDrivers(drivers, { status: "assigned" })), ["Aarav Assigned", "Gita Ungrouped"])
    assert.deepEqual(names(filterDrivers(drivers, { status: "unassigned" })), ["Bilal Unassigned", "Chen NoCard"])
    assert.deepEqual(names(filterDrivers(drivers, { status: "incomplete" })), ["Dina Shared", "Eli Shared", "Farah Gone"])
})

test("missing-data filters", () => {
    assert.deepEqual(names(filterDrivers(drivers, { missingPhone: true })), ["Bilal Unassigned", "Dina Shared"])
    assert.deepEqual(names(filterDrivers(drivers, { missingCard: true })), ["Chen NoCard", "Dina Shared"])
    assert.deepEqual(names(filterDrivers(drivers, { missingPhone: true, missingCard: true })), ["Dina Shared"])
})

test("filters combine: status, group, missing data and search together", () => {
    assert.deepEqual(names(filterDrivers(drivers, { group: "Transport" })), ["Aarav Assigned", "Dina Shared", "Eli Shared"])
    assert.deepEqual(names(filterDrivers(drivers, { group: "Transport", status: "assigned" })), ["Aarav Assigned"])
    assert.deepEqual(names(filterDrivers(drivers, { group: "Transport", missingPhone: true })), ["Dina Shared"])
    assert.deepEqual(names(filterDrivers(drivers, { group: "Transport", status: "incomplete", search: "eli" })), ["Eli Shared"])
    assert.deepEqual(names(filterDrivers(drivers, { group: "General Fleet" })), ["Gita Ungrouped"])
    assert.deepEqual(filterDrivers(drivers, { status: "unassigned", group: "Logistics" }), [], "unassigned drivers have no group")
    assert.deepEqual(filterDrivers(null, DEFAULT_FILTERS), [], "a missing list is empty, never a crash")
})

test("KPI counts come from the response summary, else from the drivers", () => {
    const summary = { total: 80, assigned: 37, unassigned: 43, ambiguous: 0, vehicle_not_found: 0, incomplete_assignments: 0,
        missing_phone: 14, missing_card: 0, missing_driver_id: 7, duplicate_driver_id: 2 }
    assert.deepEqual(kpiCounts({ drivers, summary }),
        { total: 80, assigned: 37, unassigned: 43, missingPhone: 14, missingCard: 0, incomplete: 0 })
    assert.deepEqual(kpiCounts({ drivers }),
        { total: 7, assigned: 2, unassigned: 2, missingPhone: 2, missingCard: 2, incomplete: 3 })
    assert.deepEqual(kpiCounts(null), { total: 0, assigned: 0, unassigned: 0, missingPhone: 0, missingCard: 0, incomplete: 0 })
})

test("vehicle and group labels", () => {
    assert.equal(vehicleLabel(drivers[0].assignment), "DXB 12345", "plate first")
    assert.equal(vehicleLabel(drivers[3].assignment), "Van B", "unit name without a plate")
    assert.equal(vehicleLabel(drivers[5].assignment), null, "a vehicle not found has no label, never its id")
    assert.equal(groupLabel(drivers[0].assignment), "Logistics, Transport")
    assert.equal(groupLabel(drivers[1].assignment), null)
})

test("detail modal rows: contact, assignment and data issues, with Not available for gaps", () => {
    const rows = detailRows(drivers[0], (ts) => `T${ts}`)
    const value = (list, label) => list.find((r) => r.label === label).value
    assert.equal(value(rows.contact, "Employee ID"), "100101")
    assert.equal(value(rows.contact, "Phone"), "+971500000101")
    assert.equal(rows.contact.find((r) => r.label === "Phone").href, "tel:+971500000101")
    assert.equal(value(rows.contact, "Driver card"), "0158BE4201000101")
    assert.equal(value(rows.assignment, "Status"), "Assigned")
    assert.equal(value(rows.assignment, "Vehicle number"), "DXB 12345")
    assert.equal(value(rows.assignment, "Vehicle group"), "Logistics, Transport")
    assert.equal(value(rows.assignment, "Assigned since"), "T1793600000")
    assert.deepEqual(rows.issues, [])

    const gaps = detailRows(drivers[2])
    assert.equal(value(gaps.contact, "Employee ID"), null, "missing Employee ID is null (shown as Not available)")
    assert.equal(value(gaps.contact, "Driver card"), null)
    assert.equal(value(gaps.assignment, "Vehicle number"), null)
    assert.equal(value(gaps.assignment, "Assigned since"), null)
    assert.deepEqual(gaps.issues, ["Driver card missing", "Employee ID missing"])
    assert.equal(value(detailRows(drivers[5]).assignment, "Status"), "Vehicle not found")
    assert.equal(NOT_AVAILABLE, "Not available")

    const labels = [...rows.contact, ...rows.assignment].map((r) => r.label.toLowerCase())
    assert.ok(!labels.some((l) => l.includes("record")), "no row shows an internal record id")
})

test("modal, page and API read live data only and never show an internal record id", () => {
    const modal = readFileSync(new URL("./DriverDetailModal.jsx", import.meta.url), "utf8")
    const page = readFileSync(new URL("../../pages/Drivers.jsx", import.meta.url), "utf8")
    const api = readFileSync(new URL("../../api/fleetApi.js", import.meta.url), "utf8")
    for (const source of [modal, page]) {
        assert.doesNotMatch(source, /record_id|\b(d|driver|selected)\.id\b/, "no internal record id is read")
        assert.doesNotMatch(source, /\+971|0158BE/, "no hardcoded driver data")
    }
    assert.match(page, /getDrivers\(/, "the page loads the directory from the API")
    assert.match(api, /`\$\{API_URL\}\/api\/drivers`/)
    const sidebar = readFileSync(new URL("../Layout/Sidebar.jsx", import.meta.url), "utf8")
    assert.match(sidebar, /section: "OPERATIONS",[\s\S]*\{ name: "Drivers",\s+icon: Users,\s+path: "\/drivers" \}/)
})

test("page layout: three KPIs, assignment and group filters, four columns; the modal keeps every detail", async () => {
    const { STATUS_FILTERS } = await import("./driverFilters.js")
    assert.deepEqual(STATUS_FILTERS.map((f) => f.label), ["All Drivers", "Assigned", "Unassigned"])
    const page = readFileSync(new URL("../../pages/Drivers.jsx", import.meta.url), "utf8")
    assert.equal((page.match(/<KpiCard\b/g) || []).length, 3)
    assert.deepEqual([...page.matchAll(/<th scope="col"[^>]*>([^<]+)<\/th>/g)].map((m) => m[1]), ["Driver Name", "Employee ID", "Assigned Vehicle", "Group", "Today's Idle Time"])
    assert.match(page, /ariaLabel="Filter by vehicle group"/)
    assert.doesNotMatch(page, /missingPhone|missingCard|DriverStatusBadge/)
    const labels = Object.values(detailRows(drivers[0])).flat().map((r) => r.label).filter(Boolean)
    for (const label of ["Phone", "Driver card", "Status", "Vehicle number", "Vehicle group"]) assert.ok(labels.includes(label), label)
})
