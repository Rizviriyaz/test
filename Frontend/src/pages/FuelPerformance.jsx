import { useState, useEffect, useRef, useCallback } from "react"
import { createPortal } from "react-dom"
import { Link } from "react-router-dom"
import {
    UploadCloud,
    AlertTriangle,
    FileSpreadsheet,
    CheckCircle2,
    X,
    Loader2,
    CalendarDays,
    CalendarRange,
    Info,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import { EmptyState, ErrorBanner } from "../components/Common/StateViews"
import { useFuelFilters } from "../context/useFuelFilters"
import {
    getFleetEfficiency,
    getVehicleEfficiencyList,
    previewFuelImport,
    executeFuelImport,
    getFuelTrend,
    getFuelDaily,
    clearClientCache,
} from "../api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import KpiCards from "../components/Fuel/KpiCards"
import DrivingVsIdlingChart from "../components/Fuel/DrivingVsIdlingChart"
import VehicleLeaderboard from "../components/Fuel/VehicleLeaderboard"
import VehicleFuelDetailModal from "../components/Fuel/VehicleFuelDetailModal"
import { excludedSummary, skippedRowsLabel } from "../components/Fuel/fuelLabels"
import FuelImportControls from "../components/Fuel/FuelImportControls"
import { canConfirmImport, importOptions, isReportingMonth, mappingForImport, showColumnChoices } from "../components/Fuel/fuelImportState"
import { dailySearch, datedDailyAvailability, datedTransactionCount, isCurrentMonth, latestMonthWithData, monthLabel } from "../components/Fuel/fuelPeriod"
import { fuelVehicleListParams } from "../utils/fleetRules"
import { getUAETodayStr } from "../utils/datetime"
import { TELEMATICS_RETRY_DELAY_MS, shouldRetryTelematics } from "../components/Fuel/telemetryFormat"

const NO_IMPORT_CHOICES = { mapping: null, reportingMonth: "", mappingConfirmed: false, extendToFullMonths: false }

export default function FuelPerformance() {
    const { queryParams, periodLabel, period, selectedMonth, setMonth, vehicle, fuelType } = useFuelFilters()

    // Configurable Telematics Idle Rate (Litres/Hour)
    const [idleRate, setIdleRate] = useState(2.0)
    const [idleThreshold] = useState(5.0)

    // Requests use the debounced rate, so dragging the slider does not fire a request per 0.1 L/h step
    const [idleRateQuery, setIdleRateQuery] = useState(2.0)
    useEffect(() => {
        const t = setTimeout(() => setIdleRateQuery(idleRate), 300)
        return () => clearTimeout(t)
    }, [idleRate])

    // Latest-request guards: a slower earlier response must never overwrite a newer one
    const efficiencyRequestIdRef = useRef(0)
    const rankingsRequestIdRef = useRef(0)
    const idleWastersRequestIdRef = useRef(0)

    // Fleet Efficiency & Overview Metrics
    const [efficiency, setEfficiency] = useState(null)
    const [loadingEfficiency, setLoadingEfficiency] = useState(true)
    const [overviewError, setOverviewError] = useState(null)


    // Fleet-wide idle fuel ranking (independent of the leaderboard's page, search, sort and status)
    const [idleWasters, setIdleWasters] = useState([])
    const [loadingIdleWasters, setLoadingIdleWasters] = useState(true)
    const [idleWastersError, setIdleWastersError] = useState(null)

    // Rankings & Leaderboard State
    const [sortBy, setSortBy] = useState("efficiency")
    const [sortOrder, setSortOrder] = useState("desc")
    const [statusFilter, setStatusFilter] = useState("")
    const [searchQuery, setSearchQuery] = useState("")
    const [page, setPage] = useState(1)
    const [pageSize] = useState(20)
    const [vehiclesData, setVehiclesData] = useState({ data: [], total: 0, page: 1 })
    const [loadingRankings, setLoadingRankings] = useState(true)
    const [rankingsError, setRankingsError] = useState(null)


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

    // 1. Fetch Fleet Efficiency Metrics
    // `silent`: a background refresh (telematics retry) keeps the figures on screen instead of a loading state
    const loadEfficiencyData = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++efficiencyRequestIdRef.current
        try {
            if (!silent) setLoadingEfficiency(true)
            setOverviewError(null)
            const res = await getFleetEfficiency({
                ...queryParams,
                idle_rate_lph: idleRateQuery,
            })
            if (requestId !== efficiencyRequestIdRef.current) return
            setEfficiency(res)
        } catch (err) {
            if (requestId !== efficiencyRequestIdRef.current) return
            setEfficiency(null)
            setOverviewError(err.message || "Failed to load fleet efficiency metrics.")
        } finally {
            if (requestId === efficiencyRequestIdRef.current) setLoadingEfficiency(false)
        }
    }, [queryParams, idleRateQuery])

    // 3. Fetch Vehicle Rankings
    const loadRankingsData = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++rankingsRequestIdRef.current
        try {
            if (!silent) setLoadingRankings(true)
            setRankingsError(null)
            const res = await getVehicleEfficiencyList({
                page,
                page_size: pageSize,
                sort_by: sortBy,
                sort_order: sortOrder,
                // Global filters first, so the local search box (and status/sort) is never overwritten by them. The
                // global vehicle filter is exact (vehicle_filter, as the KPI cards); the search box stays a substring.
                ...fuelVehicleListParams(queryParams, searchQuery),
                status: statusFilter || undefined,
                idle_rate_lph: idleRateQuery,
            })
            if (requestId !== rankingsRequestIdRef.current) return
            setVehiclesData(res || { data: [], total: 0, page: 1 })
        } catch (err) {
            if (requestId !== rankingsRequestIdRef.current) return
            setVehiclesData({ data: [], total: 0, page: 1 })
            setRankingsError(err.message || "Failed to load vehicle rankings.")
        } finally {
            if (requestId === rankingsRequestIdRef.current) setLoadingRankings(false)
        }
    }, [page, pageSize, sortBy, sortOrder, statusFilter, searchQuery, queryParams, idleRateQuery])

    // 4. Fetch the fleet-wide idle fuel ranking: period and global filters only, never the leaderboard's
    // page, search, sort or status
    const loadIdleWasters = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++idleWastersRequestIdRef.current
        try {
            if (!silent) setLoadingIdleWasters(true)
            setIdleWastersError(null)
            const res = await getVehicleEfficiencyList({
                page: 1,
                page_size: 5,
                sort_by: "estimated_idle_fuel",
                sort_order: "desc",
                // No status filter: vehicles with known idle hours but unknown distance (no_distance_data) carry the
                // same idle estimate as the KPI; unknown estimates are dropped below, never ranked as 0
                idle_rate_lph: idleRateQuery,
                ...fuelVehicleListParams(queryParams),
            })
            if (requestId !== idleWastersRequestIdRef.current) return
            // Without Wialon telematics there is no ranking: say so rather than "no idle fuel"
            if (res?.telematics_status === "unavailable") {
                setIdleWasters([])
                setIdleWastersError(res.data_warnings?.[0] || "Wialon telematics unavailable.")
                return
            }
            // Unknown estimates are not ranked
            setIdleWasters((res?.data || []).filter(
                (v) => v.estimated_idle_fuel_litres !== null && v.estimated_idle_fuel_litres !== undefined
            ))
        } catch (err) {
            if (requestId !== idleWastersRequestIdRef.current) return
            setIdleWasters([])
            setIdleWastersError(err.message || "Failed to load the idle fuel ranking.")
        } finally {
            if (requestId === idleWastersRequestIdRef.current) setLoadingIdleWasters(false)
        }
    }, [queryParams, idleRateQuery])

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadEfficiencyData()
        loadIdleWasters()
    }, [loadEfficiencyData, loadIdleWasters])

    // Wialon reports still running on the server (telematics_pending): ask again, silently and a bounded number of
    // times, so real distance, driving and idle values replace the placeholders. The server runs one report per period
    // and caches it, so a retry joins it instead of starting another. Counted per filter set.
    const telematicsKey = `${new URLSearchParams(queryParams).toString()}|${idleRateQuery}`
    const [telematicsRetry, setTelematicsRetry] = useState({ key: null, count: 0 })
    const telematicsRetries = telematicsRetry.key === telematicsKey ? telematicsRetry.count : 0
    const telematicsPending = !loadingEfficiency && shouldRetryTelematics(efficiency, telematicsRetries)
    useEffect(() => {
        if (!telematicsPending) return undefined
        const timer = setTimeout(() => {
            setTelematicsRetry({ key: telematicsKey, count: telematicsRetries + 1 })
            clearClientCache("/api/fuel/analytics/efficiency")
            loadEfficiencyData({ silent: true })
            loadRankingsData({ silent: true })
            loadIdleWasters({ silent: true })
        }, TELEMATICS_RETRY_DELAY_MS)
        return () => clearTimeout(timer)
    }, [telematicsPending, telematicsKey, telematicsRetries, loadEfficiencyData, loadRankingsData, loadIdleWasters])

    const [pageFilters, setPageFilters] = useState(queryParams)
    if (pageFilters !== queryParams) {
        setPageFilters(queryParams)
        setPage(1)
    }

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadRankingsData()
    }, [loadRankingsData])

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
        updateImportChoices({ mapping, mappingConfirmed: false, ...(field === "transaction_date" && header ? { reportingMonth: "" } : {}) })
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
    // "View Daily Transactions" is offered only when /analytics/daily reports dated (transaction-level) rows for the month
    // (see datedDailyAvailability); it is checked only for a month with dated rows in the KPI scope
    const checkDaily = monthMode && !loadingEfficiency && (datedTransactionCount(scope) || 0) > 0
    const dailyKey = checkDaily ? new URLSearchParams(queryParams).toString() : null
    const [dailyCheck, setDailyCheck] = useState({ key: null, available: false })
    useEffect(() => {
        if (!dailyKey) return undefined
        let active = true
        getFuelDaily(queryParams)
            .then((res) => { if (active) setDailyCheck({ key: dailyKey, available: datedDailyAvailability(res).available }) })
            .catch(() => { if (active) setDailyCheck({ key: dailyKey, available: false }) })
        return () => { active = false }
    }, [dailyKey, queryParams])
    const dailyEligible = checkDaily && dailyCheck.key === dailyKey && dailyCheck.available

    // When the selected month is empty, find the latest month with matched fuel data (fuel-only monthly trend; no
    // Wialon reports). Months whose rows are all unmatched are not offered: their KPIs would be empty too.
    const lookupLatest = hasNoData && monthMode
    useEffect(() => {
        if (!lookupLatest) return undefined
        let active = true
        // eslint-disable-next-line react-hooks/set-state-in-effect -- the lookup shows its loading state while it runs
        setLatestMonth({ status: "loading", month: null })
        getFuelTrend({ group_by: "month", ...(vehicle ? { vehicle_identifier: vehicle } : {}), ...(fuelType ? { fuel_type: fuelType } : {}) })
            .then((res) => {
                if (!active) return
                const month = latestMonthWithData(res?.data, getUAETodayStr())
                setLatestMonth({ status: month ? "found" : "none", month })
            })
            .catch(() => {
                if (active) setLatestMonth({ status: "error", month: null })
            })
        return () => { active = false }
    }, [lookupLatest, vehicle, fuelType])

    // Concise data notices, most relevant first
    const notices = []
    if (monthMode && isCurrentMonth(selectedMonth, today)) {
        notices.push({ key: "mtd", tone: "info", text: `${monthLabel(selectedMonth)} is in progress: month-to-date figures.` })
    }
    if (summaryRows > 0) {
        notices.push({ key: "summary", tone: "info", text: `Includes ${summaryRows} monthly-summary row(s): statement totals, not dated refuels.` })
    }
    const summaryRangeWarnings = (Array.isArray(scope?.warnings) ? scope.warnings : []).filter((w) => /monthly-summary/i.test(w))
    summaryRangeWarnings.forEach((w, i) => notices.push({ key: `range-${i}`, tone: "warn", text: w }))
    const partialSummaries = excludedSummary(scope)?.partialSummaries || 0
    if (partialSummaries > 0 && summaryRangeWarnings.length === 0) {
        notices.push({ key: "partial", tone: "warn", text: `${partialSummaries} monthly-summary row(s) excluded: the range does not cover their whole month.` })
    }
    // Unit-months with both a monthly summary and dated rows (no confirmed full-month coverage): excluded, never summed
    const overlapConflicts = (Array.isArray(scope?.overlap_conflicts) ? scope.overlap_conflicts : [])
        .filter((c) => c?.resolution === "excluded_coverage_unconfirmed")
    if (overlapConflicts.length > 0) {
        notices.push({
            key: "overlap",
            tone: "warn",
            text: `${overlapConflicts.length} vehicle-month(s) excluded: monthly-summary and dated rows overlap without confirmed full-month coverage.`,
            title: overlapConflicts.map((c) => `${c.wialon_unit_name || c.wialon_unit_id} ${c.month}`).join("\n"),
        })
    }
    const telematicsWarnings = Array.isArray(efficiency?.data_warnings) ? efficiency.data_warnings : []
    // "unavailable": Wialon reports failed or did not answer in time; fuel figures are shown, telematics values are "—"
    if (efficiency?.telematics_status === "partial" || efficiency?.telematics_status === "unavailable") {
        notices.push({
            key: "telematics",
            tone: "warn",
            text: telematicsPending
                ? "Wialon reports are still running. Distance, driving and idle values will appear automatically."
                : `Wialon telematics ${efficiency.telematics_status === "unavailable" ? "unavailable" : "incomplete"}${telematicsWarnings[0] ? `: ${telematicsWarnings[0]}` : "."}`,
            title: telematicsWarnings.join("\n"),
        })
    }

    return (
        <AppShell>
            <FuelHeader
                title="Fuel Performance"
                subtitle={periodLabel}
                showFilters={true}
                idleRate={idleRate}
                setIdleRate={setIdleRate}
                onOpenImport={openImportModal}
                actions={dailyEligible ? (
                    <Link
                        to={`/fuel/daily?${dailySearch(selectedMonth, vehicle)}`}
                        className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-brand/40 bg-white px-2.5 text-xs font-medium text-brand transition-colors hover:bg-brand-light focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                    >
                        <CalendarRange className="h-3.5 w-3.5" />
                        View Daily Transactions
                    </Link>
                ) : null}
            />

            <div className="space-y-5">
                {overviewError && (
                    <ErrorBanner message={overviewError} onRetry={loadEfficiencyData} />
                )}

                {notices.length > 0 && (
                    <ul role="status" className="space-y-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-xs">
                        {notices.map((n) => (
                            <li key={n.key} className={`flex items-start gap-2 ${n.tone === "warn" ? "text-amber-900" : "text-slate-600"}`} title={n.title}>
                                {n.tone === "warn"
                                    ? <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
                                    : <Info className="mt-px h-3.5 w-3.5 shrink-0 text-slate-400" />}
                                <span className="min-w-0">{n.text}</span>
                            </li>
                        ))}
                    </ul>
                )}

                {hasNoData ? (
                    <EmptyState
                        icon={CalendarDays}
                        title={`No fuel data for ${periodLabel}`}
                        message={vehicle || fuelType ? `Filters: ${[vehicle, fuelType].filter(Boolean).join(" · ")}` : undefined}
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
            </div>

            {/* ─── IMPORT DIALOG ─────────────────────────────────── */}
            {isImportModalOpen && createPortal(
                <div
                    className="fixed inset-0 z-[9999] flex items-center justify-center overflow-y-auto bg-slate-900/40 p-4"
                    onClick={() => setIsImportModalOpen(false)}
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="fuel-import-title"
                        className="my-8 w-full max-w-2xl rounded-xl border border-slate-200 bg-white p-5 shadow-dialog sm:p-6"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Modal Header */}
                        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                            <div>
                                <h3 id="fuel-import-title" className="text-[15px] font-semibold text-slate-900">
                                    Fuel Data Management
                                </h3>
                                <p className="text-xs text-slate-500">
                                    Import fuel statement (.xlsx)
                                </p>
                            </div>

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

                        <div>
                                {/* Upload Result Alert */}
                                {importResult && (
                                    <div className={`my-3 rounded-lg border p-4 text-xs ${
                                        importResult.status === "completed"
                                            ? "border-emerald-200 bg-emerald-50/80"
                                            : importResult.status === "failed"
                                                ? "border-rose-200 bg-rose-50/80"
                                                : "border-amber-200 bg-amber-50/80"
                                    }`}>
                                        <div className="flex items-center gap-2 font-semibold text-slate-800 mb-1">
                                            {importResult.status === "completed"
                                                ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                                                : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                                            <span>
                                                {importResult.status === "completed"
                                                    ? "Import complete"
                                                    : importResult.status === "failed"
                                                        ? "Import Failed"
                                                        : "Imported with errors"}
                                            </span>
                                        </div>
                                        <p className="text-slate-700">
                                            {importResult.rows_imported} transactions imported • {importResult.rows_duplicate} duplicates skipped • {importResult.rows_failed} failed
                                            {importResult.rows_unverified > 0 && ` • ${importResult.rows_unverified} unverified`}
                                            {importResult.rows_not_processed > 0 && ` • ${importResult.rows_not_processed} not processed`}
                                            {skippedRowsLabel(importResult) && ` • ${skippedRowsLabel(importResult)}`}
                                            {importResult.committed && importResult.rows_replaced > 0 && ` • ${importResult.rows_replaced} older row(s) replaced`}
                                            {importResult.rows_unchanged > 0 && ` • ${importResult.rows_unchanged} already stored unchanged`}
                                        </p>
                                        {importResult.import_mode === "monthly_summary" && (
                                            <p className="mt-1 text-slate-600">
                                                Monthly summary {importResult.reporting_month}: rows dated {importResult.assigned_transaction_date} as the reporting date, not a refuelling date.
                                            </p>
                                        )}
                                        {importResult.error_message && (
                                            <p className="mt-1 text-rose-700">{importResult.error_message}</p>
                                        )}
                                    </div>
                                )}

                                {/* Preview / Import Error */}
                                {(previewError || importError) && (
                                    <div role="alert" className="my-3 flex items-center gap-3 rounded-lg border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
                                        <AlertTriangle className="h-4 w-4 shrink-0 text-rose-600" />
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
                                        className={`my-4 rounded-xl border border-dashed p-8 text-center cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
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
                                        <FileSpreadsheet className="mx-auto mb-2 h-8 w-8 text-slate-400" />
                                        <p className="text-[13px] font-medium text-slate-800">
                                            Drop the Excel fuel statement here, or <span className="text-brand underline underline-offset-2">choose a file</span>
                                        </p>
                                        <span className="text-[11px] text-slate-400 mt-1 block">Supported format: .xlsx</span>
                                    </div>
                                )}

                                {/* Preview Loading */}
                                {previewLoading && (
                                    <div className="my-8 text-center text-xs text-slate-500">
                                        <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                                        Reading the statement…
                                    </div>
                                )}

                                {/* Preview Result */}
                                {previewData && (
                                    <div className="my-3 space-y-3">
                                        <div className="rounded-lg border border-slate-200 bg-slate-50/50 p-3 text-xs">
                                            <div className="flex items-center justify-between font-semibold text-slate-800 mb-2">
                                                <span>{previewData.file_name}</span>
                                                <span className="text-emerald-700">
                                                    {previewData.rows_fuel ?? previewData.rows_candidate} {previewData.rows_fuel != null ? "fuel transactions detected" : "valid rows detected"}
                                                </span>
                                            </div>
                                            <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
                                                <div className="rounded-md bg-white p-2 border border-slate-200">
                                                    <span className="text-slate-400 block">Total Rows</span>
                                                    <span className="font-semibold tabular-nums text-slate-800">{previewData.rows_found}</span>
                                                </div>
                                                {previewData.rows_fuel != null && (
                                                    <>
                                                        <div className="rounded-md bg-white p-2 border border-slate-200">
                                                            <span className="text-slate-400 block">Fuel Purchases</span>
                                                            <span className="font-semibold tabular-nums text-emerald-700">{previewData.rows_fuel}</span>
                                                        </div>
                                                        <div className="rounded-md bg-white p-2 border border-slate-200">
                                                            <span className="text-slate-400 block">Invalid</span>
                                                            <span className={`font-semibold tabular-nums ${previewData.rows_invalid > 0 ? "text-rose-600" : "text-slate-500"}`}>{previewData.rows_invalid}</span>
                                                        </div>
                                                        <div className="rounded-md bg-white p-2 border border-slate-200">
                                                            <span className="text-slate-400 block">Non-fuel Skipped</span>
                                                            <span className="font-semibold tabular-nums text-slate-500">{previewData.rows_non_fuel ?? 0}</span>
                                                        </div>
                                                    </>
                                                )}
                                                <div className="rounded-md bg-white p-2 border border-slate-200">
                                                    <span className="text-slate-400 block">{previewData.rows_fuel != null ? "Blank/Summary" : "Ignored"}</span>
                                                    <span className="font-semibold tabular-nums text-slate-500">
                                                        {previewData.rows_fuel != null ? previewData.rows_ignored - (previewData.rows_non_fuel ?? 0) : previewData.rows_ignored}
                                                    </span>
                                                </div>
                                                <div className="rounded-md bg-white p-2 border border-slate-200">
                                                    <span className="text-slate-400 block">Columns</span>
                                                    <span className="font-semibold tabular-nums text-slate-900">{Object.keys(previewData.suggested_mapping || {}).length}</span>
                                                </div>
                                            </div>
                                        </div>

                                        {previewData.invalid_row_details?.length > 0 && (
                                            <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
                                                <p className="font-semibold mb-1">{previewData.rows_invalid} row(s) will fail validation and not be imported:</p>
                                                <ul className="space-y-0.5">
                                                    {previewData.invalid_row_details.slice(0, 5).map((d) => (
                                                        <li key={d.source_row}>Row {d.source_row}: {d.reason}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        )}

                                        <FuelImportControls
                                            preview={previewData}
                                            disabled={importing || previewLoading}
                                            maxMonth={getUAETodayStr().slice(0, 7)}
                                            reportingMonth={importChoices.reportingMonth}
                                            mappingConfirmed={importChoices.mappingConfirmed}
                                            extendToFullMonths={importChoices.extendToFullMonths}
                                            onMappingChange={handleMappingChange}
                                            onConfirmMapping={(checked) => updateImportChoices({ mapping: previewData.active_mapping, mappingConfirmed: checked })}
                                            onMonthChange={(month) => updateImportChoices({ reportingMonth: month }, month === "" || isReportingMonth(month))}
                                            onExtendChange={(checked) => updateImportChoices({ extendToFullMonths: checked })}
                                        />

                                        {previewData.missing_required_fields?.length > 0 && !showColumnChoices(previewData) && (
                                            <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                                                Required columns not recognised: {previewData.missing_required_fields.join(", ")}. Rename these headers in the statement and upload it again.
                                            </p>
                                        )}

                                        <div className="flex items-center justify-end gap-2 pt-2">
                                            <button
                                                type="button"
                                                disabled={importing}
                                                onClick={() => setPreviewData(null)}
                                                className="h-8 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
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
                                    </div>
                                )}
                        </div>
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
