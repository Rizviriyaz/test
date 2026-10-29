import { useState } from "react"
import { AlertTriangle, CalendarRange, Info, Replace } from "lucide-react"
import {
    FIELD_LABELS,
    PICKABLE_FIELDS,
    formatReportingDate,
    formatReportingMonth,
    needsReportingMonth,
    needsSingleDateChoice,
    replacementRecordDateLabel,
    showColumnChoices,
} from "./fuelImportState"

const fmt = (n, digits = 2) =>
    Number.isFinite(Number(n)) ? Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }) : "—"

/**
 * Import choices and checks shown under the statement preview: explicit column choices (date, unit price, total
 * cost), the reporting month for statements without a date column, what a date shared by every row means, warnings,
 * why the import is blocked, the vehicle-months skipped because a monthly summary is kept, and the exact stored rows
 * the import would replace. Every change re-runs the backend preview.
 */
export default function FuelImportControls({
    preview,
    disabled,
    maxMonth,
    reportingMonth,
    mappingConfirmed,
    extendToFullMonths,
    singleDateMode,
    onMappingChange,
    onConfirmMapping,
    onMonthChange,
    onExtendChange,
    onSingleDateChange,
}) {
    const [showRecords, setShowRecords] = useState(false)
    if (!preview) return null

    const mapping = preview.active_mapping || preview.suggested_mapping || {}
    const columns = (preview.detected_columns || []).filter((h) => h && String(h).trim())
    const pending = preview.mapping_confirmation_required || []
    const monthly = preview.import_mode === "monthly_summary"
    const replacement = preview.replacement
    const records = replacement?.records || []
    const replacing = replacement?.rows_to_replace > 0
    const conflicts = replacement?.partial_month_conflicts || []
    const keptSummaries = preview.monthly_summary_scopes_kept || []
    const singleDateChoice = singleDateMode || preview.single_date_mode || ""

    return (
        <div className="space-y-3">
            {showColumnChoices(preview) && (
                <fieldset disabled={disabled} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
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
                <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs">
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
                        {preview.monthly_summary_table
                            ? "No date column: rows are stored as totals for this month, not refuels. Each row needs its total cost."
                            : "No date column: rows are dated on the last day of this month (a reporting date, not a refuel date)."}
                    </p>
                </div>
            )}

            {needsSingleDateChoice(preview) && (
                <fieldset disabled={disabled} className={`rounded-lg border px-3 py-2 text-xs ${preview.single_date_confirmation_required ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
                    <legend className="px-1 font-semibold text-slate-800">
                        Every row is dated {formatReportingDate(preview.single_date)}
                    </legend>
                    <p className="mb-2 text-slate-600">
                        {preview.single_date_confirmation_required
                            ? "Treated as a monthly summary. The date is not a month end: select the month it reports."
                            : "Treated as a monthly summary of that month. Choose individual refuels only if each row is a separate refuel."}
                    </p>
                    <label className="flex items-start gap-2 text-slate-700">
                        <input type="radio" name="single-date-mode" className="mt-0.5" disabled={!preview.monthly_summary_enabled} checked={singleDateChoice === "monthly_summary"} onChange={() => onSingleDateChange("monthly_summary")} />
                        <span>Monthly summary: each row is a vehicle&apos;s monthly total{!preview.monthly_summary_enabled && " (not enabled on this server)"}</span>
                    </label>
                    <label className="mt-1 flex items-start gap-2 text-slate-700">
                        <input type="radio" name="single-date-mode" className="mt-0.5" checked={singleDateChoice === "dated"} onChange={() => onSingleDateChange("dated")} />
                        <span>Individual refuels on that day (daily transactions)</span>
                    </label>
                    {singleDateChoice === "monthly_summary" && (
                        <label className="mt-2 flex flex-wrap items-center gap-2 text-slate-700">
                            <CalendarRange className="h-4 w-4 text-slate-400" />
                            <span>Reporting month</span>
                            <input
                                type="month"
                                max={maxMonth}
                                value={reportingMonth || preview.reporting_month || ""}
                                placeholder="YYYY-MM"
                                onChange={(e) => onMonthChange(e.target.value)}
                                className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-800 focus:border-brand focus:outline-hidden"
                            />
                        </label>
                    )}
                </fieldset>
            )}

            {keptSummaries.length > 0 && (
                <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900" role="status">
                    <div className="flex items-start gap-2 font-semibold">
                        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-600" />
                        <span>{preview.rows_skipped_monthly_summary} row(s) will not be imported: a monthly summary is already stored for these vehicle-months and is kept</span>
                    </div>
                    <ul className="mt-1 max-h-28 space-y-0.5 overflow-y-auto pl-5">
                        {keptSummaries.map((g) => (
                            <li key={`${g.vehicle_identifier}-${g.month}`}>
                                {g.vehicle_identifier} · {g.month}: summary {fmt(g.summary_litres, 1)} L, AED {fmt(g.summary_cost)} kept; {g.rows_skipped} daily row(s) skipped
                            </li>
                        ))}
                    </ul>
                    <p className="mt-1 text-sky-800">Other vehicles and months in this file are imported.</p>
                </div>
            )}

            {monthly && (preview.monthly_summary_table || preview.assigned_transaction_date) && (
                <div className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-900" role="status">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-600" />
                    <span>
                        {preview.monthly_summary_table
                            ? <>Stored as <strong>{formatReportingMonth(preview.reporting_month)}</strong> totals, without a refuelling date.</>
                            : <>Rows dated <strong>{formatReportingDate(preview.assigned_transaction_date)}</strong> as the reporting date, not a refuelling date.</>}
                        {" "}Counted only in whole-month ranges.
                    </span>
                </div>
            )}

            {monthly && preview.partial_month && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="status">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                    <span>This month has not ended, so the totals may be incomplete.</span>
                </div>
            )}

            {(preview.validation_warnings || []).length > 0 && (
                <ul className="space-y-0.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                    {preview.validation_warnings.slice(0, 6).map((w) => <li key={w}>{w}</li>)}
                </ul>
            )}

            {replacement && (replacing || replacement.rows_unchanged > 0 || conflicts.length > 0 || extendToFullMonths) && (
                <div className={`rounded-lg border px-3 py-2 text-xs ${replacing ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`} role={replacing ? "alert" : undefined}>
                    <div className={`flex items-start gap-2 font-semibold ${replacing ? "text-amber-900" : "text-slate-800"}`}>
                        <Replace className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${replacing ? "text-amber-600" : "text-slate-400"}`} />
                        <span>
                            {replacing
                                ? `Replaces ${replacement.rows_to_replace} stored row(s): ${fmt(replacement.litres_to_replace, 1)} L, AED ${fmt(replacement.cost_to_replace)}`
                                : "Replaces no stored rows"}
                        </span>
                    </div>
                    <p className={`mt-1 ${replacing ? "text-amber-900/80" : "text-slate-600"}`}>
                        {replacement.vehicles_in_file} vehicle(s) in this file, {replacement.period_from} to {replacement.period_to} only.
                        {replacement.rows_unchanged > 0 && ` ${replacement.rows_unchanged} identical row(s) already stored and kept.`}
                        {replacing && " Replaced rows stay in the database, hidden from the dashboard."}
                    </p>
                    {records.length > 0 && (
                        <button type="button" onClick={() => setShowRecords((v) => !v)} aria-expanded={showRecords} className="mt-1 text-[11px] font-medium text-brand underline underline-offset-2 cursor-pointer">
                            {showRecords ? "Hide rows" : `View rows (${replacement.records_truncated ? `${records.length} of ${replacement.rows_to_replace}` : records.length})`}
                        </button>
                    )}
                    {showRecords && records.length > 0 && (
                        <div className="mt-1.5 max-h-48 overflow-auto rounded-md border border-slate-200 bg-white">
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
                                    {records.map((r) => (
                                        <tr key={`${r.record_type || "transaction"}-${r.id ?? r.summary_id}`} className="border-t border-slate-100">
                                            <td className="px-2 py-1 text-slate-800">{r.vehicle_identifier}</td>
                                            <td className="px-2 py-1 tabular-nums text-slate-700">
                                                {replacementRecordDateLabel(r)}
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
                    {(conflicts.length > 0 || extendToFullMonths) && preview.import_mode === "dated" && (
                        <label className="mt-2 flex items-start gap-2 text-slate-700">
                            <input type="checkbox" className="mt-0.5" disabled={disabled} checked={extendToFullMonths} onChange={(e) => onExtendChange(e.target.checked)} />
                            <span>This statement covers whole calendar months: replace each vehicle&apos;s daily rows in those months (monthly summaries are kept).</span>
                        </label>
                    )}
                </div>
            )}

            {!preview.ready_to_import && (preview.blocking_reasons || []).length > 0 && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="alert">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                    <ul className="space-y-0.5">
                        {preview.blocking_reasons.map((b) => <li key={b}>{b}</li>)}
                    </ul>
                </div>
            )}
        </div>
    )
}
