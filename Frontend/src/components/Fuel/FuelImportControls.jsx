import { useState } from "react"
import { AlertTriangle, CalendarRange, Info, Replace } from "lucide-react"
import { FIELD_LABELS, PICKABLE_FIELDS, formatReportingDate, needsReportingMonth, showColumnChoices } from "./fuelImportState"

const fmt = (n, digits = 2) =>
    Number.isFinite(Number(n)) ? Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—"

/**
 * Import choices and checks shown under the statement preview: explicit column choices (date, unit price, total
 * cost), the reporting month for statements without a date column, warnings, why the import is blocked, and the
 * exact stored rows the import would replace (latest import wins). Every change re-runs the backend preview.
 */
export default function FuelImportControls({
    preview,
    disabled,
    maxMonth,
    reportingMonth,
    mappingConfirmed,
    extendToFullMonths,
    onMappingChange,
    onConfirmMapping,
    onMonthChange,
    onExtendChange,
}) {
    const [showAllRecords, setShowAllRecords] = useState(false)
    if (!preview) return null

    const mapping = preview.active_mapping || preview.suggested_mapping || {}
    const columns = (preview.detected_columns || []).filter((h) => h && String(h).trim())
    const pending = preview.mapping_confirmation_required || []
    const monthly = preview.import_mode === "monthly_summary"
    const replacement = preview.replacement
    const records = replacement?.records || []
    const shownRecords = showAllRecords ? records : records.slice(0, 8)
    const conflicts = replacement?.partial_month_conflicts || []

    return (
        <div className="space-y-3">
            {showColumnChoices(preview) && (
                <fieldset disabled={disabled} className="rounded-lg border border-slate-200 bg-white p-3 text-xs">
                    <legend className="px-1 font-semibold text-slate-800">Columns</legend>
                    <div className="grid gap-2 sm:grid-cols-3">
                        {PICKABLE_FIELDS.map((field) => (
                            <label key={field} className="block">
                                <span className={`mb-1 block text-[11px] ${pending.includes(field) ? "font-semibold text-amber-700" : "text-slate-500"}`}>
                                    {FIELD_LABELS[field]}{pending.includes(field) && " · confirm"}
                                </span>
                                <select
                                    value={mapping[field] || ""}
                                    onChange={(e) => onMappingChange(field, e.target.value || null)}
                                    className="h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-800 focus:border-brand focus:outline-hidden"
                                >
                                    <option value="">{field === "transaction_date" ? "No date column (monthly summary)" : "Not in this file"}</option>
                                    {columns.map((h) => <option key={h} value={h}>{h}</option>)}
                                </select>
                            </label>
                        ))}
                    </div>
                    {pending.length > 0 && (
                        <label className="mt-2 flex items-start gap-2 text-slate-700">
                            <input type="checkbox" className="mt-0.5" checked={mappingConfirmed} onChange={(e) => onConfirmMapping(e.target.checked)} />
                            <span>These columns are correct. The unit price is per litre and the total cost is the amount charged.</span>
                        </label>
                    )}
                </fieldset>
            )}

            {needsReportingMonth(preview) && (
                <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs">
                    <label className="flex flex-wrap items-center gap-2 font-semibold text-slate-800">
                        <CalendarRange className="h-4 w-4 text-slate-400" />
                        <span>Reporting month</span>
                        <input
                            type="month"
                            disabled={disabled || !preview.monthly_summary_enabled}
                            max={maxMonth}
                            value={reportingMonth ?? preview.reporting_month ?? ""}
                            placeholder="YYYY-MM"
                            onChange={(e) => onMonthChange(e.target.value)}
                            className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs font-normal text-slate-800 focus:border-brand focus:outline-hidden"
                        />
                    </label>
                    <p className="mt-1.5 text-slate-600">
                        This statement has no transaction date column, so it is treated as a monthly summary. Every accepted row
                        is dated on the last day of the selected month. That is a reporting date, not when the vehicle refuelled.
                    </p>
                </div>
            )}

            {monthly && preview.assigned_transaction_date && (
                <div className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900" role="status">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-600" />
                    <span>
                        Monthly summary for <strong>{preview.reporting_month}</strong>. Assigned reporting date{" "}
                        <strong>{formatReportingDate(preview.assigned_transaction_date)}</strong>. These rows count only in whole-month
                        ranges and are never shown as daily refuelling.
                        {preview.partial_month && " This month has not ended, so the totals may be incomplete."}
                    </span>
                </div>
            )}

            {(preview.validation_warnings || []).length > 0 && (
                <ul className="space-y-0.5 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                    {preview.validation_warnings.slice(0, 6).map((w) => <li key={w}>{w}</li>)}
                </ul>
            )}

            {replacement && (
                <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs">
                    <div className="flex items-center gap-2 font-semibold text-slate-800">
                        <Replace className="h-4 w-4 text-slate-400" />
                        <span>
                            {replacement.rows_to_replace > 0
                                ? `Replaces ${replacement.rows_to_replace} stored row(s): ${fmt(replacement.litres_to_replace, 1)} L, AED ${fmt(replacement.cost_to_replace)}`
                                : "Replaces no stored rows"}
                        </span>
                    </div>
                    <p className="mt-1 text-slate-600">
                        Period {replacement.period_from} to {replacement.period_to}, for the {replacement.vehicles_in_file} vehicle(s) in this
                        file only. {replacement.rows_unchanged > 0 && `${replacement.rows_unchanged} identical row(s) are already stored and kept. `}
                        Replaced rows are kept in the database and hidden from the dashboard; other vehicles and dates are not changed.
                    </p>
                    {records.length > 0 && (
                        <div className="mt-2 max-h-48 overflow-auto rounded-md border border-slate-100">
                            <table className="w-full text-left text-[11px]">
                                <thead className="sticky top-0 bg-slate-50 text-slate-500">
                                    <tr>
                                        <th className="px-2 py-1 font-medium">Vehicle</th>
                                        <th className="px-2 py-1 font-medium">Date</th>
                                        <th className="px-2 py-1 font-medium text-right">Litres</th>
                                        <th className="px-2 py-1 font-medium text-right">Cost</th>
                                        <th className="px-2 py-1 font-medium">Import</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {shownRecords.map((r) => (
                                        <tr key={r.id} className="border-t border-slate-100">
                                            <td className="px-2 py-1 text-slate-800">{r.vehicle_identifier}</td>
                                            <td className="px-2 py-1 tabular-nums text-slate-700">
                                                {r.date_basis === "reporting_month_end" ? `Monthly summary ${String(r.transaction_date).slice(0, 7)}` : String(r.transaction_date).slice(0, 16).replace("T", " ")}
                                            </td>
                                            <td className="px-2 py-1 text-right tabular-nums">{fmt(r.litres, 1)}</td>
                                            <td className="px-2 py-1 text-right tabular-nums">{fmt(r.total_cost)}</td>
                                            <td className="px-2 py-1 text-slate-500">#{r.import_id}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                    {records.length > 8 && (
                        <button type="button" onClick={() => setShowAllRecords((v) => !v)} className="mt-1 text-[11px] font-medium text-brand underline underline-offset-2 cursor-pointer">
                            {showAllRecords ? "Show fewer" : `Show all ${records.length}${replacement.records_truncated ? ` of ${replacement.rows_to_replace}` : ""}`}
                        </button>
                    )}
                    {(conflicts.length > 0 || extendToFullMonths) && preview.import_mode === "dated" && (
                        <label className="mt-2 flex items-start gap-2 text-slate-700">
                            <input type="checkbox" className="mt-0.5" disabled={disabled} checked={extendToFullMonths} onChange={(e) => onExtendChange(e.target.checked)} />
                            <span>This statement covers whole calendar months. Replace each vehicle&apos;s full months, including monthly summaries.</span>
                        </label>
                    )}
                </div>
            )}

            {!preview.ready_to_import && (preview.blocking_reasons || []).length > 0 && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900" role="alert">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                    <ul className="space-y-0.5">
                        {preview.blocking_reasons.map((b) => <li key={b}>{b}</li>)}
                    </ul>
                </div>
            )}
        </div>
    )
}
