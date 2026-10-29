// Offline tests for the fuel import flow helpers. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import {
    canConfirmImport,
    FIELD_LABELS,
    PICKABLE_FIELDS,
    isReportingMonth,
    reportingMonthInSync,
    formatReportingDate,
    formatReportingMonth,
    importOptions,
    mappingForImport,
    needsReportingMonth,
    needsSingleDateChoice,
    replacementRecordDateLabel,
    showColumnChoices,
    transactionDateLabel,
} from "./fuelImportState.js"

test("monthly summaries are labelled by reporting month, never by a day", () => {
    assert.equal(formatReportingMonth("2026-07"), "Jul 2026")
    assert.equal(formatReportingMonth("2026-12-01"), "Dec 2026")
    assert.equal(formatReportingMonth("2026-13"), "—")
    assert.equal(formatReportingMonth(null), "—")
    // fuel_monthly_summaries record (no transaction date) and a legacy month-end record
    assert.equal(replacementRecordDateLabel({ date_basis: "reporting_month_end", reporting_month: "2026-07", transaction_date: null }),
        "Monthly summary Jul 2026")
    assert.equal(replacementRecordDateLabel({ date_basis: "reporting_month_end", transaction_date: "2026-07-31" }),
        "Monthly summary Jul 2026")
    assert.equal(replacementRecordDateLabel({ date_basis: "transaction", transaction_date: "2026-07-03T09:00:00" }), "2026-07-03 09:00")
})

const dated = {
    import_mode: "dated",
    ready_to_import: true,
    active_mapping: { transaction_date: "Date", vehicle_identifier: "Vehicle", litres: "Litres", unit_price: "Rate", total_cost: null },
    mapping_confirmation_required: [],
    replacement: { expected_replace_count: 3 },
}
const summary = {
    import_mode: "monthly_summary",
    reporting_month: "2026-02",
    assigned_transaction_date: "2026-02-28",
    ready_to_import: true,
    active_mapping: { transaction_date: null, vehicle_identifier: "Vehicle", litres: "Litres", total_cost: "Amount" },
    mapping_confirmation_required: [],
    replacement: { expected_replace_count: 0 },
}

test("mapping sent to the import drops unmapped fields", () => {
    assert.deepEqual(mappingForImport(dated), { transaction_date: "Date", vehicle_identifier: "Vehicle", litres: "Litres", unit_price: "Rate" })
    assert.deepEqual(mappingForImport(null), {})
})

test("reporting month is needed only without a date column", () => {
    assert.equal(needsReportingMonth(dated), false)
    assert.equal(needsReportingMonth(summary), true)
    assert.equal(needsReportingMonth({ ...summary, import_mode: null, reporting_month: null }), true)
    assert.equal(showColumnChoices(dated), false)
    assert.equal(showColumnChoices({ ...dated, mapping_confirmation_required: ["unit_price"] }), true)
    assert.equal(showColumnChoices(summary), true)
})

test("confirm is enabled only when the backend preview is ready", () => {
    assert.equal(canConfirmImport(dated), true)
    assert.equal(canConfirmImport(dated, true), false, "not while importing")
    assert.equal(canConfirmImport({ ...dated, ready_to_import: false }), false)
    assert.equal(canConfirmImport({ ...dated, ready_to_import: undefined }), false, "older backend: never assume ready")
    assert.equal(canConfirmImport(null), false)
})

test("import options mirror the preview", () => {
    assert.deepEqual(importOptions(summary, { mappingConfirmed: true }),
        { reportingMonth: "2026-02", mappingConfirmed: true, extendToFullMonths: false, singleDateMode: null, expectedReplaceCount: 0 })
    assert.deepEqual(importOptions(dated, { extendToFullMonths: true }),
        { reportingMonth: null, mappingConfirmed: false, extendToFullMonths: true, singleDateMode: null, expectedReplaceCount: 3 })
    assert.equal(importOptions({ ...dated, replacement: null }).expectedReplaceCount, null)
})

test("monthly-summary rows are labelled by reporting month, never shown as a refuel date", () => {
    assert.equal(formatReportingDate("2024-02-29"), "29 Feb 2024")
    assert.equal(formatReportingDate("bad"), "—")
    assert.equal(transactionDateLabel({ transaction_date: "2026-08-31", date_basis: "reporting_month_end" }),
        "Monthly summary · reporting date 31 Aug 2026")
    assert.equal(transactionDateLabel({ transaction_date: "2026-08-05T08:00:00", date_basis: "transaction" }), "2026-08-05T08:00:00")
    assert.equal(transactionDateLabel({ transaction_date: "2026-08-05" }), "2026-08-05")
})

test("only a complete YYYY-MM reporting month is sent to the preview", () => {
    for (const ok of ["2026-08", "2025-12", "2026-01"]) assert.equal(isReportingMonth(ok), true, ok)
    // Partial or malformed text (browsers without a month picker show a text box) stays in the form only
    for (const bad of ["", "2", "2026-", "2026-0", "2026-13", "2026-00", "26-08", "2026/08", "2026-08-31", null, undefined]) {
        assert.equal(isReportingMonth(bad), false, String(bad))
    }
})

test("the form's reporting month must match the previewed month before confirming (undated files only)", () => {
    // Undated statement previewed for 2026-02: confirm only while the form still says 2026-02
    assert.equal(reportingMonthInSync(summary, "2026-02"), true)
    assert.equal(canConfirmImport(summary, false, "2026-02"), true)
    assert.equal(canConfirmImport(summary, false, "2026-0"), false, "partial month being typed")
    assert.equal(canConfirmImport(summary, false, "2026-03"), false, "month changed, preview not yet refreshed")
    assert.equal(canConfirmImport(summary, false, ""), false, "month cleared")
    // Undated statement with no month chosen yet: field empty, preview blocked
    const noMonth = { ...summary, import_mode: null, reporting_month: null, ready_to_import: false }
    assert.equal(reportingMonthInSync(noMonth, ""), true)
    assert.equal(canConfirmImport(noMonth, false, ""), false)
    // Dated statements never need or check a reporting month
    assert.equal(reportingMonthInSync(dated, ""), true)
    assert.equal(canConfirmImport(dated, false, ""), true)
    assert.equal(canConfirmImport(dated, false, "2026-02"), true)
    // Callers that pass no month keep the previous behaviour
    assert.equal(canConfirmImport(summary), true)
})

test("repeated vehicle or litres columns can be chosen, like the date and price columns", () => {
    for (const field of ["transaction_date", "vehicle_identifier", "litres", "unit_price", "total_cost"]) {
        assert.ok(PICKABLE_FIELDS.includes(field), field)
        assert.ok(FIELD_LABELS[field], `${field} has a label`)
    }
})

test("a date shared by every row needs the user's choice, sent exactly as previewed", () => {
    assert.equal(needsSingleDateChoice(dated), false)
    assert.equal(needsSingleDateChoice(null), false)
    const pending = { ...dated, single_date: "2026-08-31", single_date_confirmation_required: true, single_date_mode: null, ready_to_import: false }
    assert.equal(needsSingleDateChoice(pending), true)
    assert.equal(canConfirmImport(pending), false, "blocked until the user chooses")
    assert.equal(importOptions(pending).singleDateMode, null)
    const asDaily = { ...pending, single_date_confirmation_required: false, single_date_mode: "dated", ready_to_import: true }
    assert.equal(importOptions(asDaily).singleDateMode, "dated")
    assert.equal(importOptions(asDaily).reportingMonth, null)
    const asSummary = { ...asDaily, single_date_mode: "monthly_summary", import_mode: "monthly_summary", reporting_month: "2026-08" }
    assert.deepEqual([importOptions(asSummary).singleDateMode, importOptions(asSummary).reportingMonth], ["monthly_summary", "2026-08"])
    // The derived month needs no form value; an explicitly chosen month must match the preview
    assert.equal(canConfirmImport(asSummary, false, ""), true)
    assert.equal(canConfirmImport(asSummary, false, "2026-08"), true)
    assert.equal(canConfirmImport(asSummary, false, "2026-07"), false, "month changed, preview not yet refreshed")
})
