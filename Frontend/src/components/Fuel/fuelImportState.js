// Pure helpers for the fuel statement import flow (unit-tested in fuelImportState.test.js).
// The backend preview decides readiness (ready_to_import / blocking_reasons); these helpers only read it.

// Columns the user may have to choose explicitly: the date, the vehicle and litres (when the statement repeats a
// column group), and the two price columns
export const PICKABLE_FIELDS = ["transaction_date", "vehicle_identifier", "litres", "unit_price", "total_cost"]

export const FIELD_LABELS = {
    transaction_date: "Transaction date",
    vehicle_identifier: "Vehicle / plate",
    litres: "Litres",
    unit_price: "Unit price per litre",
    total_cost: "Total cost (AED)",
}

// The preview's mapping without unmapped (null/empty) fields: what the import is sent
export function mappingForImport(preview) {
    const mapping = preview?.active_mapping || preview?.suggested_mapping || {}
    return Object.fromEntries(Object.entries(mapping).filter(([, header]) => typeof header === "string" && header.trim()))
}

// No date column is mapped, so the statement is a monthly summary and needs a reporting month
export function needsReportingMonth(preview) {
    if (!preview) return false
    const mapping = preview.active_mapping || preview.suggested_mapping || {}
    return !mapping.transaction_date
}

// A complete reporting month as the backend expects it ("YYYY-MM"). Browsers without a month picker show a text box,
// whose partial input is kept in the form but not sent to the preview
export function isReportingMonth(value) {
    return /^\d{4}-(0[1-9]|1[0-2])$/.test(String(value ?? ""))
}

// Every row carries one identical date: the user must say whether it is a monthly reporting date or real refuel dates
export function needsSingleDateChoice(preview) {
    return Boolean(preview?.single_date)
}

// The month in the form is the one the preview was computed for (always true for dated statements; for a single-date
// statement confirmed as a monthly summary, only an explicitly chosen month is checked)
export function reportingMonthInSync(preview, reportingMonth) {
    const singleDateMonth = preview?.single_date_mode === "monthly_summary" && Boolean(reportingMonth)
    if (!needsReportingMonth(preview) && !singleDateMonth) return true
    return String(reportingMonth ?? "") === String(preview.reporting_month ?? "")
}

// Column choices are shown when the backend asks for confirmation, or when there is no date column
export function showColumnChoices(preview) {
    return Boolean(preview && ((preview.mapping_confirmation_required || []).length > 0 || needsReportingMonth(preview)))
}

// Confirm stays disabled until the backend reports the import would be accepted as previewed, and (when the form's
// reporting month is given) until the preview was computed for that month
export function canConfirmImport(preview, importing = false, reportingMonth = undefined) {
    return Boolean(preview && preview.ready_to_import === true && !importing
        && (reportingMonth === undefined || reportingMonthInSync(preview, reportingMonth)))
}

// Options sent with the import so it runs exactly as previewed (the replace count guards against changes in between)
export function importOptions(preview, { mappingConfirmed = false, extendToFullMonths = false } = {}) {
    const expected = preview?.replacement?.expected_replace_count
    return {
        reportingMonth: preview?.import_mode === "monthly_summary" ? preview.reporting_month : null,
        mappingConfirmed: Boolean(mappingConfirmed),
        extendToFullMonths: Boolean(extendToFullMonths),
        singleDateMode: needsSingleDateChoice(preview) ? preview.single_date_mode || null : null,
        expectedReplaceCount: Number.isInteger(expected) ? expected : null,
    }
}

// "2026-08-31" -> "31 Aug 2026" for the assigned reporting date (no time zone shift: plain calendar date)
export function formatReportingDate(isoDate) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(isoDate || ""))
    if (!m) return "—"
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1]}`
}

// A stored row from a monthly-summary import: its date is a reporting date, not a refuel
export function isMonthlySummaryRow(row) {
    return row?.date_basis === "reporting_month_end"
}

// Date cell for a transaction row: monthly summaries are labelled by their reporting month
export function transactionDateLabel(row) {
    if (!isMonthlySummaryRow(row)) return row?.transaction_date ?? "—"
    const day = String(row.transaction_date || "").slice(0, 10)
    return `Monthly summary · reporting date ${formatReportingDate(day)}`
}

// "2026-07" -> "Jul 2026": a monthly summary is labelled by its reporting month, never by a day
export function formatReportingMonth(month) {
    const m = /^(\d{4})-(\d{2})/.exec(String(month || ""))
    if (!m || Number(m[2]) < 1 || Number(m[2]) > 12) return "—"
    return formatReportingDate(`${m[1]}-${m[2]}-01`).replace(/^1 /, "")
}

// Date cell of a preview record the import would replace: a stored summary shows only its reporting month
export function replacementRecordDateLabel(record) {
    if (record?.date_basis !== "reporting_month_end") return String(record?.transaction_date || "—").slice(0, 16).replace("T", " ")
    return `Monthly summary ${formatReportingMonth(record.reporting_month || String(record.transaction_date || "").slice(0, 7))}`
}
