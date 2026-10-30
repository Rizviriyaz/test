import { useState, useEffect, useRef } from "react"
import { createPortal } from "react-dom"
import {
    UploadCloud,
    AlertTriangle,
    FileSpreadsheet,
    CheckCircle2,
    X,
    Loader2,
    CalendarDays,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import { EmptyState, ErrorBanner } from "../components/Common/StateViews"
import { useFuelFilters } from "../context/useFuelFilters"
import {
    previewFuelImport,
    executeFuelImport,
    getFuelTrend,
    clearClientCache,
} from "../api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import KpiCards from "../components/Fuel/KpiCards"
import DrivingVsIdlingChart from "../components/Fuel/DrivingVsIdlingChart"
import VehicleLeaderboard from "../components/Fuel/VehicleLeaderboard"
import VehicleFuelDetailModal from "../components/Fuel/VehicleFuelDetailModal"
import { skippedRowsLabel } from "../components/Fuel/fuelLabels"
import FuelImportControls from "../components/Fuel/FuelImportControls"
import DataCoverage from "../components/Fuel/DataCoverage"
import { canConfirmImport, formatReportingDate, formatReportingMonth, importOptions, isReportingMonth, mappingForImport, showColumnChoices } from "../components/Fuel/fuelImportState"
import { latestMonthWithData, monthLabel } from "../components/Fuel/fuelPeriod"
import FuelNoticeList from "../components/Fuel/FuelNoticeList"
import { fuelScopeNotices } from "../components/Fuel/fuelNotices"
import useFuelEfficiencyData from "../components/Fuel/useFuelEfficiencyData"
import { getUAETodayStr } from "../utils/datetime"

const NO_IMPORT_CHOICES = { mapping: null, reportingMonth: "", mappingConfirmed: false, extendToFullMonths: false, singleDateMode: "" }

// Reporting period of a previewed statement, read from the preview only; null when the preview does not state one
function previewPeriodLabel(preview) {
    if (preview?.import_mode === "monthly_summary" && preview.reporting_month) {
        return formatReportingMonth(preview.reporting_month)
    }
    const from = String(preview?.replacement?.period_from || "").slice(0, 10)
    const to = String(preview?.replacement?.period_to || "").slice(0, 10)
    if (from && to) {
        return from === to ? formatReportingDate(from) : `${formatReportingDate(from)} – ${formatReportingDate(to)}`
    }
    if (preview?.single_date) return formatReportingDate(preview.single_date)
    return null
}

export default function FuelPerformance() {
    const { queryParams, periodLabel, period, selectedMonth, setMonth, vehicle, setVehicle } = useFuelFilters()

    const {
        idleRate, setIdleRate,
        efficiency, loadingEfficiency, overviewError, loadEfficiencyData,
        idleWasters, loadingIdleWasters, idleWastersError, loadIdleWasters,
        vehiclesData, loadingRankings, rankingsError, loadRankingsData,
        sortBy, setSortBy, sortOrder, setSortOrder, statusFilter, setStatusFilter,
        searchQuery, setSearchQuery, page, setPage, pageSize,
        telematicsPending, telematicsRetries,
    } = useFuelEfficiencyData(queryParams)
    const [idleThreshold] = useState(5.0)

    // Latest month with data, looked up only when the selected month is empty: idle | loading | found | none | error
    const [latestMonth, setLatestMonth] = useState({ status: "idle", month: null })

    // Vehicle Detail Modal State
    const [selectedVehicleDetail, setSelectedVehicleDetail] = useState(null)

    // Excel Import Modal State
    const [isImportModalOpen, setIsImportModalOpen] = useState(false)
    const [selectedFile, setSelectedFile] = useState(null)
    const [isDragging, setIsDragging] = useState(false)
    const [previewLoading, setPreviewLoading] = useState(false)
    const [previewData, setPreviewData] = useState(null)
    const [previewError, setPreviewError] = useState(null)
    // User choices re-sent with every preview and with the import: column mapping, reporting month, confirmations
    const [importChoices, setImportChoices] = useState(NO_IMPORT_CHOICES)
    const [importing, setImporting] = useState(false)
    const [importResult, setImportResult] = useState(null)
    const [importError, setImportError] = useState(null)

    const fileInputRef = useRef(null)

    useEffect(() => {
        if (!isImportModalOpen) return undefined
        const onKey = (e) => { if (e.key === "Escape") setIsImportModalOpen(false) }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [isImportModalOpen])

    const openImportModal = () => {
        setIsImportModalOpen(true)
    }

    const handleFileChosen = async (file) => {
        if (!file) return
        if (!file.name.toLowerCase().endsWith(".xlsx")) {
            setPreviewError("Unsupported file type. Please upload an Excel spreadsheet (.xlsx).")
            return
        }

        setSelectedFile(file)
        setPreviewData(null)
        setImportResult(null)
        setImportError(null)
        setImportChoices(NO_IMPORT_CHOICES)
        runPreview(file, NO_IMPORT_CHOICES)
    }

    // Re-runs the backend preview whenever the file, a column choice or the reporting month changes
    const runPreview = async (file, choices) => {
        setPreviewError(null)
        try {
            setPreviewLoading(true)
            const preview = await previewFuelImport(file, choices)
            setPreviewData(preview)
        } catch (err) {
            console.error("Preview error:", err)
            setPreviewError(err.message || "Failed to parse Excel file for preview.")
        } finally {
            setPreviewLoading(false)
        }
    }

    const updateImportChoices = (changes, refreshPreview = true) => {
        if (!selectedFile || !previewData) return
        const next = { ...importChoices, ...changes }
        setImportChoices(next)
        if (refreshPreview) runPreview(selectedFile, next)
    }

    const handleMappingChange = (field, header) => {
        const mapping = { ...(previewData.active_mapping || previewData.suggested_mapping || {}), [field]: header }
        // Choosing a column is an explicit decision; a new choice must be confirmed again
        updateImportChoices({ mapping, mappingConfirmed: false, ...(field === "transaction_date" ? { singleDateMode: "", ...(header ? { reportingMonth: "" } : {}) } : {}) })
    }

    const handleConfirmImport = async () => {
        if (!selectedFile || !previewData) return

        try {
            setImporting(true)
            setImportError(null)
            const result = await executeFuelImport(selectedFile, mappingForImport(previewData), importOptions(previewData, importChoices))
            setImportResult(result)
            setPreviewData(null)
            setSelectedFile(null)
            setImportChoices(NO_IMPORT_CHOICES)
            clearClientCache("/api/fuel/")
            loadEfficiencyData()
            loadRankingsData()
            loadIdleWasters()
        } catch (err) {
            console.error("Import execution failed:", err)
            setImportError(err.message || "An error occurred while importing fuel statement.")
        } finally {
            setImporting(false)
        }
    }

    const hasNoData = !loadingEfficiency && efficiency?.excluded_scope?.all_transaction_count === 0
    const scope = efficiency?.excluded_scope
    const today = getUAETodayStr()
    const monthMode = period === "month"
    const summaryRows = Number(scope?.monthly_summary_rows) || 0
    // Monthly summaries are the only fuel reporting source: dated statement rows in the period are counted, never shown
    const datedNotReported = Number(scope?.dated_not_reported?.transaction_count) || 0

    // When the selected month is empty, find the latest month with matched fuel data (fuel-only monthly trend; no
    // Wialon reports). Months whose rows are all unmatched are not offered: their KPIs would be empty too.
    const lookupLatest = hasNoData && monthMode
    useEffect(() => {
        if (!lookupLatest) return undefined
        let active = true
        // eslint-disable-next-line react-hooks/set-state-in-effect -- the lookup shows its loading state while it runs
        setLatestMonth({ status: "loading", month: null })
        getFuelTrend({ group_by: "month", ...(vehicle ? { vehicle_identifier: vehicle } : {}) })
            .then((res) => {
                if (!active) return
                const month = latestMonthWithData(res?.data, getUAETodayStr())
                setLatestMonth({ status: month ? "found" : "none", month })
            })
            .catch(() => {
                if (active) setLatestMonth({ status: "error", month: null })
            })
        return () => { active = false }
    }, [lookupLatest, vehicle])

    const notices = fuelScopeNotices({ efficiency, selectedMonth, today, monthMode, telematicsPending })

    return (
        <AppShell>
            <FuelHeader
                showFilters={true}
                idleRate={idleRate}
                setIdleRate={setIdleRate}
                onOpenImport={openImportModal}
            />

            <div className="space-y-3 sm:space-y-4">
                {overviewError && (
                    <ErrorBanner message={overviewError} onRetry={loadEfficiencyData} />
                )}

                <FuelNoticeList notices={notices} />

                {hasNoData ? (
                    <EmptyState
                        icon={CalendarDays}
                        title={`No fuel data for ${periodLabel}`}
                        message={[
                            vehicle ? `Filter: ${vehicle}` : null,
                            datedNotReported > 0
                                ? `${datedNotReported.toLocaleString("en-US")} dated transaction row(s) exist, but fuel is reported from monthly summaries only.`
                                : null,
                        ].filter(Boolean).join(" ") || undefined}
                        action={monthMode ? (
                            latestMonth.status === "loading" ? (
                                <span className="text-xs text-slate-400">Finding the latest month with data…</span>
                            ) : latestMonth.status === "found" && latestMonth.month !== selectedMonth ? (
                                <button
                                    type="button"
                                    onClick={() => setMonth(latestMonth.month)}
                                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-brand px-3 text-xs font-medium text-white transition-colors hover:bg-brand-hover cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                >
                                    Jump to latest month with data ({monthLabel(latestMonth.month)})
                                </button>
                            ) : latestMonth.status === "error" ? (
                                <span className="text-xs text-slate-500">The latest month with data could not be determined.</span>
                            ) : latestMonth.status === "none" ? (
                                <span className="text-xs text-slate-500">No matched fuel data for these filters.</span>
                            ) : null
                        ) : null}
                    />
                ) : (
                    <>
                        {/* Monthly overview: 8 KPI categories */}
                        <KpiCards
                            efficiencyData={efficiency}
                            loading={loadingEfficiency}
                            idleRate={idleRate}
                        />

                        {/* Driving vs idling, beside the fleet-wide idle fuel ranking */}
                        <DrivingVsIdlingChart
                            efficiencyData={efficiency}
                            loading={loadingEfficiency}
                            idleRate={idleRate}
                            idleWasters={idleWasters}
                            wastersLoading={loadingIdleWasters}
                            wastersError={idleWastersError}
                            onRetryWasters={loadIdleWasters}
                            onSelectVehicle={(vId) => setSelectedVehicleDetail(vId)}
                        />

                        <VehicleLeaderboard
                            vehiclesData={vehiclesData}
                            loading={loadingRankings}
                            error={rankingsError}
                            onRetry={loadRankingsData}
                            sortBy={sortBy}
                            setSortBy={setSortBy}
                            sortOrder={sortOrder}
                            setSortOrder={setSortOrder}
                            searchQuery={searchQuery}
                            setSearchQuery={setSearchQuery}
                            statusFilter={statusFilter}
                            setStatusFilter={setStatusFilter}
                            page={page}
                            setPage={setPage}
                            pageSize={pageSize}
                            onSelectVehicle={(vId) => setSelectedVehicleDetail(vId)}
                            monthlySummaryRows={summaryRows}
                        />
                    </>
                )}

                {/* Data coverage: fleet vehicles without fuel records, and matched vehicles without distance */}
                <DataCoverage
                    queryParams={queryParams}
                    vehicleFilter={vehicle}
                    onClearVehicle={() => setVehicle("")}
                    refreshKey={`${telematicsRetries}|${importResult?.import_id ?? ""}`}
                />
            </div>

            {/* ─── IMPORT DIALOG ─────────────────────────────────── */}
            {isImportModalOpen && createPortal(
                <div
                    className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/40 p-3 sm:p-4"
                    onClick={() => setIsImportModalOpen(false)}
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="fuel-import-title"
                        className="flex max-h-[calc(100dvh-1.5rem)] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-dialog sm:max-h-[calc(100dvh-2rem)]"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Modal Header */}
                        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
                            <h3 id="fuel-import-title" className="text-[15px] font-semibold text-slate-900">
                                Import Fuel Statement
                            </h3>
                            <button
                                type="button"
                                onClick={() => {
                                    setIsImportModalOpen(false)
                                    setPreviewData(null)
                                    setSelectedFile(null)
                                    setImportResult(null)
                                }}
                                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                                aria-label="Close"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>

                        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
                            {/* Upload Result Alert */}
                            {importResult && (
                                <div className={`rounded-lg border px-3 py-2.5 text-xs ${
                                    importResult.status === "completed"
                                        ? "border-emerald-200 bg-emerald-50/80"
                                        : importResult.status === "failed"
                                            ? "border-rose-200 bg-rose-50/80"
                                            : "border-amber-200 bg-amber-50/80"
                                }`}>
                                    <div className="flex items-center gap-2 font-semibold text-slate-800">
                                        {importResult.status === "completed"
                                            ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                                            : <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />}
                                        <span>
                                            {importResult.status === "completed"
                                                ? "Import complete"
                                                : importResult.status === "failed"
                                                    ? "Import Failed"
                                                    : "Imported with errors"}
                                        </span>
                                    </div>
                                    <p className="mt-1 text-slate-700">
                                        {importResult.rows_imported} transactions imported
                                        {importResult.rows_duplicate > 0 && ` • ${importResult.rows_duplicate} duplicates skipped`}
                                        {importResult.rows_failed > 0 && ` • ${importResult.rows_failed} failed`}
                                        {importResult.rows_unverified > 0 && ` • ${importResult.rows_unverified} unverified`}
                                        {importResult.rows_not_processed > 0 && ` • ${importResult.rows_not_processed} not processed`}
                                        {skippedRowsLabel(importResult) && ` • ${skippedRowsLabel(importResult)}`}
                                        {importResult.committed && importResult.rows_replaced > 0 && ` • ${importResult.rows_replaced} older row(s) replaced`}
                                        {importResult.rows_unchanged > 0 && ` • ${importResult.rows_unchanged} already stored unchanged`}
                                        {importResult.rows_skipped_monthly_summary > 0 && ` • ${importResult.rows_skipped_monthly_summary} skipped (monthly summary kept)`}
                                    </p>
                                    {importResult.import_mode === "monthly_summary" && (
                                        <p className="mt-1 text-slate-600">
                                            {importResult.monthly_summary_table
                                                ? `Monthly summary ${importResult.reporting_month}: stored as monthly totals, without a refuelling date.`
                                                : `Monthly summary ${importResult.reporting_month}: dated ${importResult.assigned_transaction_date} as the reporting date, not a refuelling date.`}
                                        </p>
                                    )}
                                    {importResult.error_message && (
                                        <p className="mt-1 text-rose-700">{importResult.error_message}</p>
                                    )}
                                </div>
                            )}

                            {/* Preview / Import Error */}
                            {(previewError || importError) && (
                                <div role="alert" className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50/80 px-3 py-2.5 text-xs font-semibold text-rose-700">
                                    <AlertTriangle className="mt-px h-4 w-4 shrink-0 text-rose-600" />
                                    <span>{previewError || importError}</span>
                                </div>
                            )}

                            {/* Upload Area */}
                            {!previewData && !previewLoading && (
                                <div
                                    onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
                                    onDragLeave={() => setIsDragging(false)}
                                    onDrop={(e) => {
                                        e.preventDefault()
                                        setIsDragging(false)
                                        if (e.dataTransfer.files?.length) handleFileChosen(e.dataTransfer.files[0])
                                    }}
                                    onClick={() => fileInputRef.current?.click()}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                            e.preventDefault()
                                            fileInputRef.current?.click()
                                        }
                                    }}
                                    role="button"
                                    tabIndex={0}
                                    className={`rounded-xl border border-dashed px-4 py-6 text-center cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                        isDragging
                                            ? "border-brand bg-brand/5"
                                            : "border-slate-200 hover:border-brand/50 bg-slate-50/50"
                                    }`}
                                >
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        accept=".xlsx"
                                        className="hidden"
                                        onChange={(e) => {
                                            if (e.target.files?.length) handleFileChosen(e.target.files[0])
                                        }}
                                    />
                                    <FileSpreadsheet className="mx-auto mb-2 h-7 w-7 text-slate-400" />
                                    <p className="text-[13px] font-medium text-slate-800">
                                        Drop the Excel fuel statement here, or <span className="text-brand underline underline-offset-2">choose a file</span>
                                    </p>
                                    <span className="mt-1 block text-[11px] text-slate-400">.xlsx only</span>
                                </div>
                            )}

                            {/* Preview Loading */}
                            {previewLoading && (
                                <div className="py-8 text-center text-xs text-slate-500" role="status">
                                    <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                                    Reading the statement…
                                </div>
                            )}

                            {/* Preview Result: only what the decision needs */}
                            {previewData && (
                                <>
                                    <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200 text-xs">
                                        <div className="flex items-center gap-2 px-3 py-2">
                                            <FileSpreadsheet className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
                                            <dt className="sr-only">File</dt>
                                            <dd className="min-w-0 truncate font-medium text-slate-900" title={previewData.file_name}>{previewData.file_name}</dd>
                                        </div>
                                        <div className="flex items-baseline justify-between gap-3 px-3 py-2">
                                            <dt className="text-slate-500">{previewData.rows_fuel != null ? "Fuel transactions" : "Rows detected"}</dt>
                                            <dd className="text-sm font-semibold tabular-nums text-slate-900">
                                                {(previewData.rows_fuel ?? previewData.rows_candidate ?? 0).toLocaleString()}
                                            </dd>
                                        </div>
                                        {previewPeriodLabel(previewData) && (
                                            <div className="flex items-baseline justify-between gap-3 px-3 py-2">
                                                <dt className="text-slate-500">Reporting period</dt>
                                                <dd className="text-right font-medium tabular-nums text-slate-900">
                                                    {previewPeriodLabel(previewData)}
                                                    {previewData.import_mode === "monthly_summary" && <span className="font-normal text-slate-500"> · monthly summary</span>}
                                                </dd>
                                            </div>
                                        )}
                                        {previewData.rows_invalid > 0 && (
                                            <div className="flex items-baseline justify-between gap-3 px-3 py-2">
                                                <dt className="text-rose-700">Invalid rows (not imported)</dt>
                                                <dd className="font-semibold tabular-nums text-rose-700">{previewData.rows_invalid.toLocaleString()}</dd>
                                            </div>
                                        )}
                                    </dl>

                                    {previewData.invalid_row_details?.length > 0 && (
                                        <ul className="space-y-0.5 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800" role="alert">
                                            {previewData.invalid_row_details.slice(0, 5).map((d) => (
                                                <li key={d.source_row}>Row {d.source_row}: {d.reason}</li>
                                            ))}
                                            {previewData.rows_invalid > 5 && (
                                                <li className="text-rose-700/80">…and {previewData.rows_invalid - 5} more</li>
                                            )}
                                        </ul>
                                    )}

                                    {previewData.missing_required_fields?.length > 0 && !showColumnChoices(previewData) && (
                                        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800" role="alert">
                                            Required columns not recognised: {previewData.missing_required_fields.join(", ")}. Rename these headers and upload again.
                                        </p>
                                    )}

                                    <FuelImportControls
                                        preview={previewData}
                                        disabled={importing || previewLoading}
                                        maxMonth={getUAETodayStr().slice(0, 7)}
                                        reportingMonth={importChoices.reportingMonth}
                                        mappingConfirmed={importChoices.mappingConfirmed}
                                        extendToFullMonths={importChoices.extendToFullMonths}
                                        singleDateMode={importChoices.singleDateMode}
                                        onMappingChange={handleMappingChange}
                                        onConfirmMapping={(checked) => updateImportChoices({ mapping: previewData.active_mapping, mappingConfirmed: checked })}
                                        onMonthChange={(month) => updateImportChoices({ reportingMonth: month }, month === "" || isReportingMonth(month))}
                                        onExtendChange={(checked) => updateImportChoices({ extendToFullMonths: checked })}
                                        onSingleDateChange={(mode) => updateImportChoices({ singleDateMode: mode, reportingMonth: "" })}
                                    />
                                </>
                            )}
                        </div>

                        {previewData && (
                            <div className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-100 px-4 py-3">
                                <button
                                    type="button"
                                    disabled={importing}
                                    onClick={() => setPreviewData(null)}
                                    className="h-8 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="button"
                                    disabled={previewLoading || !canConfirmImport(previewData, importing, importChoices.reportingMonth)}
                                    onClick={handleConfirmImport}
                                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-brand px-3 text-xs font-medium text-white hover:bg-brand-hover cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                    {importing ? (
                                        <>
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                            <span>Importing…</span>
                                        </>
                                    ) : (
                                        <>
                                            <UploadCloud className="h-3.5 w-3.5" />
                                            <span>Confirm import</span>
                                        </>
                                    )}
                                </button>
                            </div>
                        )}
                    </div>
                </div>,
                document.body
            )}

            {/* Vehicle Fuel Detail Drill-Down Modal */}
            {selectedVehicleDetail && (
                <VehicleFuelDetailModal
                    vehicleIdentifier={selectedVehicleDetail}
                    onClose={() => setSelectedVehicleDetail(null)}
                    dateFrom={queryParams.date_from || efficiency?.excluded_scope?.period?.date_from}
                    dateTo={queryParams.date_to || efficiency?.excluded_scope?.period?.date_to}
                    idleRate={idleRate}
                    idleThreshold={idleThreshold}
                    idleThresholdMode="per_day"
                />
            )}
        </AppShell>
    )
}
