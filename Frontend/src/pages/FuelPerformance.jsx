import { useState, useEffect, useMemo, useRef, useCallback } from "react"
import { createPortal } from "react-dom"
import {
    UploadCloud,
    AlertTriangle,
    FileSpreadsheet,
    CheckCircle2,
    X,
    Loader2,
    History,
    RefreshCw,
    ArrowUpRight,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import SearchInput from "../components/Common/SearchInput"
import Pagination from "../components/Common/Pagination"
import { ErrorBanner } from "../components/Common/StateViews"
import { useFuelFilters } from "../context/useFuelFilters"
import {
    getFleetEfficiency,
    getVehicleEfficiencyList,
    previewFuelImport,
    executeFuelImport,
    getFuelImports,
    getFuelWaste,
    clearClientCache,
} from "../api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import KpiCards from "../components/Fuel/KpiCards"
import FuelTrendChart from "../components/Fuel/FuelTrendChart"
import FuelTypeDonut from "../components/Fuel/FuelTypeDonut"
import FleetEfficiencyGauge from "../components/Fuel/FleetEfficiencyGauge"
import VehicleStatusBadge from "../components/Fuel/VehicleStatusBadge"
import FuelWasteIntelligence from "../components/Fuel/FuelWasteIntelligence"
import VehicleFuelDetailModal from "../components/Fuel/VehicleFuelDetailModal"
import ExcludedFuelPanel from "../components/Fuel/ExcludedFuelPanel"
import { fixedOrDash, sortOrderLabels } from "../components/Fuel/fuelLabels"
import { formatUAEDateTime } from "../utils/datetime"

const SORT_OPTIONS = [
    { label: "Fuel Volume (Litres)", value: "litres" },
    { label: "Fuel Expenditure (Cost)", value: "cost" },
    { label: "Distance Traveled (km)", value: "distance" },
    { label: "Fuel Efficiency (L/100km)", value: "efficiency" },
    { label: "Cost Per km (AED/km)", value: "cost_per_km" },
    { label: "Refuel Count (Transactions)", value: "transactions" },
    { label: "Vehicle Identifier", value: "vehicle_identifier" },
]

export default function FuelPerformance() {
    const { queryParams, periodLabel, setPeriod } = useFuelFilters()

    // Configurable Telematics Idle Rate (Litres/Hour)
    const [idleRate, setIdleRate] = useState(2.0)
    const [idleThreshold, setIdleThreshold] = useState(5.0)
    // Requests use the debounced rate, so dragging the slider does not fire a request per 0.1 L/h step
    const [idleRateQuery, setIdleRateQuery] = useState(2.0)
    useEffect(() => {
        const t = setTimeout(() => setIdleRateQuery(idleRate), 300)
        return () => clearTimeout(t)
    }, [idleRate])
    // Latest-request guards: a slower earlier response must never overwrite a newer one
    const efficiencyRequestIdRef = useRef(0)
    const rankingsRequestIdRef = useRef(0)
    const attentionRequestIdRef = useRef(0)

    // Fleet Efficiency & Overview Metrics
    const [efficiency, setEfficiency] = useState(null)
    const [loadingEfficiency, setLoadingEfficiency] = useState(true)
    const [overviewError, setOverviewError] = useState(null)

    // Fuel Waste & Anomalies
    const [wasteData, setWasteData] = useState(null)
    const [loadingWaste, setLoadingWaste] = useState(true)
    const [wasteError, setWasteError] = useState(null)
    const wasteRequestIdRef = useRef(0)

    // Rankings & Outliers State
    const [sortBy, setSortBy] = useState("litres")
    const [sortOrder, setSortOrder] = useState("desc")
    const [statusFilter, setStatusFilter] = useState("")
    const [searchQuery, setSearchQuery] = useState("")
    const [page, setPage] = useState(1)
    const [pageSize] = useState(20)
    const [vehiclesData, setVehiclesData] = useState({ data: [], total: 0, page: 1 })
    const [loadingRankings, setLoadingRankings] = useState(true)
    const [rankingsError, setRankingsError] = useState(null)
    const [attentionVehicles, setAttentionVehicles] = useState([])

    // Vehicle Detail Modal State
    const [selectedVehicleDetail, setSelectedVehicleDetail] = useState(null)

    // Excel Import Modal State
    const [isImportModalOpen, setIsImportModalOpen] = useState(false)
    const [selectedFile, setSelectedFile] = useState(null)
    const [isDragging, setIsDragging] = useState(false)
    const [previewLoading, setPreviewLoading] = useState(false)
    const [previewData, setPreviewData] = useState(null)
    const [previewError, setPreviewError] = useState(null)
    const [importing, setImporting] = useState(false)
    const [importResult, setImportResult] = useState(null)
    const [dataVersion, setDataVersion] = useState(0)
    const [importError, setImportError] = useState(null)
    const [history, setHistory] = useState([])
    const [loadingHistory, setLoadingHistory] = useState(false)
    const [historyError, setHistoryError] = useState(null)

    const fileInputRef = useRef(null)

    // 1. Fetch Fleet Efficiency Metrics
    const loadEfficiencyData = useCallback(async () => {
        const requestId = ++efficiencyRequestIdRef.current
        try {
            setLoadingEfficiency(true)
            setOverviewError(null)
            const res = await getFleetEfficiency({
                ...queryParams,
                idle_rate_lph: idleRateQuery,
            })
            if (requestId !== efficiencyRequestIdRef.current) return
            setEfficiency(res)
        } catch (err) {
            if (requestId !== efficiencyRequestIdRef.current) return
            // Never leave the previous period's KPIs on screen next to the error
            setEfficiency(null)
            setOverviewError(err.message || "Failed to load fleet efficiency metrics.")
        } finally {
            if (requestId === efficiencyRequestIdRef.current) setLoadingEfficiency(false)
        }
    }, [queryParams, idleRateQuery])

    // 2. Fetch Fuel Waste & Anomaly Intelligence
    const loadWasteData = useCallback(async () => {
        const requestId = ++wasteRequestIdRef.current
        try {
            setLoadingWaste(true)
            setWasteError(null)
            const res = await getFuelWaste({
                ...queryParams,
                idle_rate_lph: idleRateQuery,
                idle_threshold_hours: idleThreshold,
            })
            if (requestId !== wasteRequestIdRef.current) return
            setWasteData(res)
            setWasteError(null)
        } catch (err) {
            if (requestId !== wasteRequestIdRef.current) return
            console.error("Waste intelligence load error:", err)
            setWasteData(null)
            setWasteError(err.message || "Failed to load fuel waste intelligence report.")
        } finally {
            if (requestId === wasteRequestIdRef.current) {
                setLoadingWaste(false)
            }
        }
    }, [queryParams, idleRateQuery, idleThreshold])

    // 3. Fetch Vehicle Rankings
    const loadRankingsData = useCallback(async () => {
        const requestId = ++rankingsRequestIdRef.current
        try {
            setLoadingRankings(true)
            setRankingsError(null)
            const res = await getVehicleEfficiencyList({
                page,
                page_size: pageSize,
                sort_by: sortBy,
                sort_order: sortOrder,
                // Global filters first, so the local search box (and status/sort) is never overwritten by them
                ...queryParams,
                status: statusFilter || undefined,
                vehicle_identifier: searchQuery || queryParams.vehicle_identifier || undefined,
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

    // 4. Fetch Vehicles Needing Attention Outliers
    const loadAttentionData = useCallback(async () => {
        const requestId = ++attentionRequestIdRef.current
        try {
            const res = await getVehicleEfficiencyList({
                page: 1,
                page_size: 4,
                sort_by: "efficiency",
                sort_order: "desc",
                status: "matched",
                idle_rate_lph: idleRateQuery,
                ...queryParams,
            })
            if (requestId !== attentionRequestIdRef.current) return
            // Only vehicles with a purchase-based ratio (suppressed ratios are null, never ranked)
            setAttentionVehicles((res?.data || []).filter((v) => v.fuel_efficiency_l_per_100km !== null && v.fuel_efficiency_l_per_100km !== undefined))
        } catch (err) {
            if (requestId !== attentionRequestIdRef.current) return
            // Optional panel: hide it rather than keep the previous period's outliers
            setAttentionVehicles([])
            console.warn("Attention load error:", err)
        }
    }, [queryParams, idleRateQuery])

    // Efficiency/attention URLs do not include the threshold, so a threshold change is served from the client cache
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadEfficiencyData()
        loadWasteData()
        loadAttentionData()
    }, [loadEfficiencyData, loadWasteData, loadAttentionData, idleThreshold])

    // A new global period/filter starts from the first page (the old page may not exist any more).
    // Adjusted during render, so the rankings effect below never requests the stale page.
    const [pageFilters, setPageFilters] = useState(queryParams)
    if (pageFilters !== queryParams) {
        setPageFilters(queryParams)
        setPage(1)
    }

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadRankingsData()
    }, [loadRankingsData])

    // Load Import History when modal is opened
    const loadImportHistory = async () => {
        try {
            setLoadingHistory(true)
            setHistoryError(null)
            const res = await getFuelImports(10, 0)
            setHistory(Array.isArray(res?.imports) ? res.imports : [])
        } catch (err) {
            // An outage must not read as "no import records"
            setHistory([])
            setHistoryError(err?.message || "Could not load import history.")
        } finally {
            setLoadingHistory(false)
        }
    }

    useEffect(() => {
        if (!isImportModalOpen) return undefined
        const onKey = (e) => { if (e.key === "Escape") setIsImportModalOpen(false) }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [isImportModalOpen])

    const openImportModal = () => {
        setIsImportModalOpen(true)
        loadImportHistory()
    }

    const handleFileChosen = async (file) => {
        if (!file) return
        if (!file.name.toLowerCase().endsWith(".xlsx")) {
            setPreviewError("Unsupported file type. Please upload an Excel spreadsheet (.xlsx).")
            return
        }

        setSelectedFile(file)
        setPreviewError(null)
        setPreviewData(null)
        setImportResult(null)
        setImportError(null)

        try {
            setPreviewLoading(true)
            const preview = await previewFuelImport(file)
            setPreviewData(preview)
        } catch (err) {
            console.error("Preview error:", err)
            setPreviewError(err.message || "Failed to parse Excel file for preview.")
        } finally {
            setPreviewLoading(false)
        }
    }

    const handleConfirmImport = async () => {
        if (!selectedFile || !previewData) return

        try {
            setImporting(true)
            setImportError(null)
            const result = await executeFuelImport(selectedFile, previewData.suggested_mapping)
            setImportResult(result)
            setPreviewData(null)
            setSelectedFile(null)
            // New rows: drop cached fuel responses so every panel reloads fresh data
            clearClientCache("/api/fuel/")
            setDataVersion((n) => n + 1)
            loadImportHistory()
            loadEfficiencyData()
            loadWasteData()
            loadRankingsData()
        } catch (err) {
            console.error("Import execution failed:", err)
            setImportError(err.message || "An error occurred while importing fuel statement.")
        } finally {
            setImporting(false)
        }
    }

    const totalPages = Math.max(1, Math.ceil((vehiclesData.total || 0) / pageSize))

    const maxSortMetric = useMemo(() => {
        if (!vehiclesData.data || vehiclesData.data.length === 0) return 1
        const vals = vehiclesData.data.map((v) => {
            if (sortBy === "litres") return v.litres || 0
            if (sortBy === "cost") return v.total_cost || 0
            if (sortBy === "distance") return v.distance_km || 0
            if (sortBy === "efficiency") return v.fuel_efficiency_l_per_100km || 0
            if (sortBy === "cost_per_km") return v.fuel_cost_per_km || 0
            if (sortBy === "transactions") return v.transaction_count || 0
            return 100
        })
        return Math.max(...vals, 1)
    }, [vehiclesData.data, sortBy])

    // "No data" only when nothing was fetched at all; rows excluded from KPIs are shown in the excluded panel
    const hasNoData = !loadingEfficiency && efficiency?.excluded_scope?.all_transaction_count === 0

    return (
        <AppShell>
            {/* ─── HEADER & GLOBAL CONTROLS ───────────────────────────── */}
            <FuelHeader
                title="Fuel & Performance"
                subtitle={periodLabel}
                showFilters={true}
                actions={
                    <button
                        type="button"
                        onClick={openImportModal}
                        className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-brand px-3 text-xs font-medium text-white transition-colors hover:bg-brand-hover cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:ring-offset-1"
                    >
                        <UploadCloud className="h-3.5 w-3.5" />
                        <span>Import fuel data</span>
                    </button>
                }
            />

            {/* ─── IDLE BURN RATE (drives every estimated figure below) ── */}
            <div className="mb-5 flex flex-col gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs text-slate-600">
                    <span className="font-medium text-slate-900">Idle burn rate</span>
                    <span className="hidden sm:inline"> · used for every estimated figure on this page</span>
                </p>
                <div className="flex flex-wrap items-center gap-2">
                    <div role="group" aria-label="Idle burn rate presets" className="inline-flex rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                        {[
                            { label: "1.5", val: 1.5 },
                            { label: "2.0 std", val: 2.0 },
                            { label: "2.5", val: 2.5 },
                            { label: "3.0 AC", val: 3.0 },
                        ].map((preset) => (
                            <button
                                key={preset.val}
                                type="button"
                                onClick={() => setIdleRate(preset.val)}
                                aria-pressed={idleRate === preset.val}
                                className={`rounded px-2.5 py-1 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                    idleRate === preset.val
                                        ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]"
                                        : "text-slate-600 hover:text-slate-900"
                                }`}
                            >
                                {preset.label}
                            </button>
                        ))}
                    </div>
                    <label className="flex items-center gap-2">
                        <input
                            type="range"
                            min="0.5"
                            max="5.0"
                            step="0.1"
                            value={idleRate}
                            onChange={(e) => setIdleRate(parseFloat(e.target.value))}
                            className="w-24 accent-brand cursor-pointer"
                            title={`Idle rate: ${idleRate.toFixed(1)} L/h`}
                            aria-label="Idle burn rate (litres per hour)"
                        />
                        <span className="w-14 text-right font-mono text-xs font-medium text-slate-900">
                            {idleRate.toFixed(1)} L/h
                        </span>
                    </label>
                </div>
            </div>

            {/* Error Message */}
            {overviewError && (
                <ErrorBanner message={overviewError} onRetry={loadEfficiencyData} className="mb-5" />
            )}

            {/* Empty state when the period has no records */}
            {hasNoData && (
                <div className="mb-5 flex flex-col gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-start gap-2">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                        <div>
                            <h4 className="text-xs font-medium text-amber-950">
                                No fuel transactions for {periodLabel}
                            </h4>
                            <p className="mt-0.5 text-xs text-amber-800">
                                Choose all historical data or a custom date range.
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setPeriod("all")}
                        className="inline-flex h-8 items-center gap-1.5 self-start rounded-md border border-amber-300 bg-white px-3 text-xs font-medium text-amber-900 transition-colors hover:bg-amber-100 cursor-pointer sm:self-auto"
                    >
                        View all data
                    </button>
                </div>
            )}

            {/* ─── 1. COMPACT KPI CARDS (RECORDED & ESTIMATED) ─────────── */}
            <KpiCards
                efficiencyData={efficiency}
                loading={loadingEfficiency}
                idleRate={idleRate}
            />

            {/* Transactions excluded from KPIs (no single Wialon unit, suspected re-imports) */}
            <ExcludedFuelPanel key={`excluded-${dataVersion}`} />

            {/* ─── 2. FUEL WASTE & ANOMALY INTELLIGENCE ─────────────────── */}
            <FuelWasteIntelligence
                wasteData={wasteData}
                loading={loadingWaste}
                error={wasteError}
                onSelectVehicle={(vId) => setSelectedVehicleDetail(vId)}
                idleThreshold={idleThreshold}
                onIdleThresholdChange={setIdleThreshold}
            />

            {/* ─── 3. MAIN FUEL / DISTANCE / COST TREND CHART ──────────── */}
            <FuelTrendChart key={`trend-${dataVersion}`} />

            {/* ─── 4. FUEL TYPE BREAKDOWN & FLEET EFFICIENCY ──────────── */}
            <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
                <FuelTypeDonut />
                <FleetEfficiencyGauge efficiencyData={efficiency} loading={loadingEfficiency} />
            </div>

            {/* ─── 5. VEHICLES NEEDING ATTENTION (OUTLIERS) ────────────── */}
            {attentionVehicles.length > 0 && (
                <section className="mb-5" aria-labelledby="fuel-attention-heading">
                    <h2 id="fuel-attention-heading" className="mb-2 type-section-title text-slate-900">
                        Needs attention <span className="font-normal text-slate-500">· highest L/100 km in period</span>
                    </h2>
                    <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-2 lg:grid-cols-4">
                        {attentionVehicles.map((v) => (
                            <button
                                type="button"
                                key={v.vehicle_identifier}
                                onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                className="bg-white px-4 py-3 text-left transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
                            >
                                <span className="block truncate font-mono text-xs font-semibold text-slate-900">
                                    {v.vehicle_identifier}
                                </span>
                                <span className="mt-1 flex items-baseline gap-1">
                                    <span className="text-xl font-semibold tabular-nums text-amber-800">
                                        {v.fuel_efficiency_l_per_100km?.toFixed(1)}
                                    </span>
                                    <span className="text-xs text-slate-500">L/100 km</span>
                                </span>
                                <span className="mt-0.5 block text-xs tabular-nums text-slate-500">
                                    {v.fuel_cost_per_km != null ? `AED ${fixedOrDash(v.fuel_cost_per_km)}/km` : "—"}
                                    {" · "}{Math.round(v.litres).toLocaleString()} L
                                    {" · "}{v.distance_km != null ? `${Math.round(v.distance_km).toLocaleString()} km` : "— km"}
                                </span>
                            </button>
                        ))}
                    </div>
                </section>
            )}

            {/* ─── 6. VEHICLE PERFORMANCE RANKINGS ─────────────────────── */}
            <section className="mb-5 overflow-hidden rounded-lg border border-slate-200 bg-white">
                <div className="flex flex-col gap-2.5 border-b border-slate-200 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
                    <h2 className="type-section-title text-slate-900">Vehicle rankings</h2>

                    <div className="flex flex-wrap items-center gap-2">
                        <SearchInput
                            value={searchQuery}
                            onChange={(val) => {
                                setSearchQuery(val)
                                setPage(1)
                            }}
                            placeholder="Search plate…"
                            className="w-full sm:w-44"
                        />
                        <label className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15">
                            <span className="text-xs text-slate-500">Rank by</span>
                            <select
                                value={sortBy}
                                onChange={(e) => {
                                    setSortBy(e.target.value)
                                    setPage(1)
                                }}
                                aria-label="Rank by"
                                className="cursor-pointer bg-transparent text-xs font-medium text-slate-800 outline-none"
                            >
                                {SORT_OPTIONS.map((opt) => (
                                    <option key={opt.value} value={opt.value}>
                                        {opt.label}
                                    </option>
                                ))}
                            </select>
                        </label>

                        <div role="group" aria-label="Sort order" className="inline-flex h-8 rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                            {["desc", "asc"].map((order) => (
                                <button
                                    key={order}
                                    type="button"
                                    onClick={() => setSortOrder(order)}
                                    aria-pressed={sortOrder === order}
                                    className={`rounded px-2.5 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                        sortOrder === order
                                            ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]"
                                            : "text-slate-600 hover:text-slate-900"
                                    }`}
                                >
                                    {sortOrderLabels(sortBy)[order]}
                                </button>
                            ))}
                        </div>

                        <select
                            value={statusFilter}
                            onChange={(e) => {
                                setStatusFilter(e.target.value)
                                setPage(1)
                            }}
                            aria-label="Filter by telemetry status"
                            className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-800 cursor-pointer focus:border-brand focus:outline-hidden"
                        >
                            <option value="">All Matched Vehicles</option>
                            <option value="matched">Active Telemetry</option>
                            <option value="no_distance_data">Zero Distance in Period</option>
                        </select>
                    </div>
                </div>

                <div className="overflow-x-auto">
                    <table className="w-full min-w-[920px] text-left text-xs">
                        <thead className="border-b border-slate-200 bg-slate-50 text-[11px] text-slate-500">
                            <tr>
                                <th scope="col" className="w-10 px-4 py-2 text-right font-medium">#</th>
                                <th scope="col" className="px-3 py-2 font-medium">Vehicle</th>
                                <th scope="col" className="px-3 py-2 text-right font-medium">Fuel</th>
                                <th scope="col" className="px-3 py-2 text-right font-medium">Distance</th>
                                <th scope="col" className="px-3 py-2 text-right font-medium">Efficiency</th>
                                <th scope="col" className="px-3 py-2 text-right font-medium">Driving</th>
                                <th scope="col" className="px-3 py-2 text-right font-medium">Idling</th>
                                <th scope="col" className="min-w-[120px] px-3 py-2 font-medium">Relative</th>
                                <th scope="col" className="px-4 py-2"><span className="sr-only">Action</span></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {loadingRankings ? (
                                <tr>
                                    <td colSpan={9} role="status" className="py-12 text-center text-xs text-slate-500">
                                        <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                                        Loading vehicle rankings…
                                    </td>
                                </tr>
                            ) : rankingsError ? (
                                <tr>
                                    <td colSpan={9} role="alert" className="py-10 text-center text-xs text-rose-700">
                                        {rankingsError}
                                        <button
                                            type="button"
                                            onClick={loadRankingsData}
                                            className="ml-2 font-medium text-brand underline-offset-2 hover:underline cursor-pointer"
                                        >
                                            Retry
                                        </button>
                                    </td>
                                </tr>
                            ) : vehiclesData.data.length === 0 ? (
                                <tr>
                                    <td colSpan={9} className="py-10 text-center text-xs text-slate-500">
                                        No vehicles match the current filters.
                                    </td>
                                </tr>
                            ) : (
                                vehiclesData.data.map((v, idx) => {
                                    const rankNum = (page - 1) * pageSize + idx + 1

                                    let currentMetricVal = v.litres || 0
                                    if (sortBy === "cost") currentMetricVal = v.total_cost || 0
                                    if (sortBy === "distance") currentMetricVal = v.distance_km || 0
                                    if (sortBy === "efficiency") currentMetricVal = v.fuel_efficiency_l_per_100km || 0
                                    if (sortBy === "cost_per_km") currentMetricVal = v.fuel_cost_per_km || 0
                                    if (sortBy === "transactions") currentMetricVal = v.transaction_count || 0

                                    const progressPercent = Math.min(Math.max((currentMetricVal / maxSortMetric) * 100, 4), 100)
                                    const sub = "block text-[11px] text-slate-500"

                                    return (
                                        <tr key={v.vehicle_identifier} className="transition-colors hover:bg-slate-50">
                                            <td className="px-4 py-2 text-right tabular-nums text-slate-400">{rankNum}</td>
                                            <td className="whitespace-nowrap px-3 py-2">
                                                <div className="flex items-center gap-2">
                                                    <button
                                                        type="button"
                                                        onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                        className="font-mono text-xs font-semibold text-slate-900 transition-colors hover:text-brand cursor-pointer"
                                                    >
                                                        {v.vehicle_identifier}
                                                    </button>
                                                    <VehicleStatusBadge status={v.status} />
                                                    {v.estimation_status && (
                                                        <span className="rounded-sm border border-slate-200 px-1 text-[10px] text-slate-500">
                                                            {v.estimation_status}
                                                        </span>
                                                    )}
                                                </div>
                                                {v.wialon_unit_name && <span className={sub}>{v.wialon_unit_name}</span>}
                                            </td>
                                            <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                                                <span className="block font-semibold text-slate-900">{Math.round(v.litres).toLocaleString()} L</span>
                                                <span className={sub}>AED {Math.round(v.total_cost || 0).toLocaleString()}</span>
                                            </td>
                                            <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                                                <span className="block font-semibold text-slate-900">
                                                    {v.distance_km !== null ? `${Math.round(v.distance_km).toLocaleString()} km` : "—"}
                                                </span>
                                                <span className={sub}>{v.transaction_count} refuels</span>
                                            </td>
                                            <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                                                <span className="block font-semibold text-slate-900">
                                                    {v.fuel_efficiency_l_per_100km !== null ? `${v.fuel_efficiency_l_per_100km.toFixed(1)} L/100` : "—"}
                                                </span>
                                                <span className={sub}>
                                                    {v.fuel_cost_per_km !== null ? `AED ${fixedOrDash(v.fuel_cost_per_km)}/km` : "—"}
                                                </span>
                                            </td>
                                            <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                                                <span className="block font-semibold text-slate-900">
                                                    {v.driving_duration_hours !== null && v.driving_duration_hours !== undefined ? `${v.driving_duration_hours.toFixed(1)} h` : "—"}
                                                </span>
                                                <span className={sub}>
                                                    {v.estimated_driving_fuel_litres !== null && v.estimated_driving_fuel_litres !== undefined ? `${v.estimated_driving_fuel_litres.toFixed(1)} L` : "In motion"}
                                                </span>
                                            </td>
                                            <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                                                <span className="block font-semibold text-slate-900">
                                                    {v.idle_duration_hours !== null && v.idle_duration_hours !== undefined ? `${v.idle_duration_hours.toFixed(1)} h` : "—"}
                                                </span>
                                                <span className="block text-[11px] text-amber-800">
                                                    {v.estimated_idle_fuel_litres !== null && v.estimated_idle_fuel_litres !== undefined ? `${v.estimated_idle_fuel_litres.toFixed(1)} L waste` : "—"}
                                                </span>
                                            </td>
                                            <td className="min-w-[120px] px-3 py-2">
                                                <div className="flex items-center gap-2">
                                                    <div className="h-1 flex-1 overflow-hidden rounded-full bg-slate-100">
                                                        <div className="h-full rounded-full bg-brand" style={{ width: `${progressPercent}%` }} />
                                                    </div>
                                                    <span className="w-8 text-right text-[11px] tabular-nums text-slate-500">{Math.round(progressPercent)}%</span>
                                                </div>
                                            </td>
                                            <td className="whitespace-nowrap px-4 py-2 text-right">
                                                <button
                                                    type="button"
                                                    onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                    className="inline-flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 cursor-pointer"
                                                >
                                                    Inspect
                                                    <ArrowUpRight className="h-3 w-3 text-slate-400" />
                                                </button>
                                            </td>
                                        </tr>
                                    )
                                })
                            )}
                        </tbody>
                    </table>
                </div>

                {vehiclesData.total > 0 && (
                    <Pagination
                        page={page}
                        totalPages={totalPages}
                        onPageChange={setPage}
                        start={(page - 1) * pageSize + 1}
                        end={Math.min(page * pageSize, vehiclesData.total)}
                        total={vehiclesData.total}
                        noun="vehicles"
                    />
                )}
            </section>

            {/* ─── IMPORT MODAL ────────────────────────────────────────── */}
            {isImportModalOpen && createPortal(
                <div className="fixed inset-0 z-[9999] flex items-center justify-center overflow-y-auto bg-slate-900/40 p-4" onClick={() => setIsImportModalOpen(false)}>
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="fuel-import-title"
                        className="my-8 w-full max-w-2xl rounded-xl border border-slate-200 bg-white p-5 shadow-dialog sm:p-6"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                            <div className="flex items-center gap-2.5">
                                <div>
                                    <h3 id="fuel-import-title" className="text-[15px] font-semibold text-slate-900">
                                        Import fuel data
                                    </h3>
                                    <p className="text-xs text-slate-500">
                                        Excel statement (.xlsx)
                                    </p>
                                </div>
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

                        {/* Upload Result Alert */}
                        {importResult && (
                            <div className={`my-4 rounded-lg border p-4 text-xs ${
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
                                </p>
                                {importResult.error_message && (
                                    <p className="mt-1 text-rose-700">{importResult.error_message}</p>
                                )}
                            </div>
                        )}

                        {/* Preview / Import Error */}
                        {(previewError || importError) && (
                            <div role="alert" className="my-4 flex items-center gap-3 rounded-lg border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
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
                                className={`my-5 rounded-lg border border-dashed p-8 text-center cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
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
                            <div className="my-4 space-y-3">
                                <div className="rounded-lg border border-slate-200 bg-slate-50/50 p-3 text-xs">
                                    <div className="flex items-center justify-between font-semibold text-slate-800 mb-2">
                                        <span>{previewData.file_name}</span>
                                        <span className="text-emerald-700">{previewData.rows_candidate} valid rows detected</span>
                                    </div>
                                    <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
                                        <div className="rounded-md bg-white p-2 border border-slate-200">
                                            <span className="text-slate-400 block">Total Rows</span>
                                            <span className="font-semibold tabular-nums text-slate-800">{previewData.rows_found}</span>
                                        </div>
                                        <div className="rounded-md bg-white p-2 border border-slate-200">
                                            <span className="text-slate-400 block">Ignored</span>
                                            <span className="font-semibold tabular-nums text-slate-500">{previewData.rows_ignored}</span>
                                        </div>
                                        <div className="rounded-md bg-white p-2 border border-slate-200">
                                            <span className="text-slate-400 block">Columns</span>
                                            <span className="font-semibold tabular-nums text-slate-900">{Object.keys(previewData.suggested_mapping || {}).length}</span>
                                        </div>
                                    </div>
                                </div>

                                {previewData.missing_required_fields?.length > 0 && (
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
                                        disabled={importing || previewData.missing_required_fields?.length > 0}
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

                        {/* Import History Table */}
                        <div className="mt-6 pt-4 border-t border-slate-100">
                            <div className="flex items-center justify-between mb-2">
                                <h4 className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
                                    <History className="h-3.5 w-3.5 text-slate-400" />
                                    Recent imports
                                </h4>
                                <button
                                    type="button"
                                    onClick={loadImportHistory}
                                    className="rounded p-1 text-slate-400 hover:text-slate-700 cursor-pointer"
                                    aria-label="Reload import history"
                                >
                                    <RefreshCw className="h-3 w-3" />
                                </button>
                            </div>

                            <div className="max-h-48 overflow-y-auto divide-y divide-slate-100 text-xs">
                                {loadingHistory ? (
                                    <div className="py-4 text-center text-slate-400">Loading history…</div>
                                ) : historyError ? (
                                    <div role="alert" className="py-4 text-center text-rose-600">{historyError}</div>
                                ) : history.length === 0 ? (
                                    <div className="py-4 text-center text-slate-400">No import records found.</div>
                                ) : (
                                    history.map((h) => (
                                        <div key={h.id} className="flex items-center justify-between gap-3 py-2.5">
                                            <div className="min-w-0">
                                                <span className="block truncate font-semibold text-slate-800">{h.file_name}</span>
                                                <span className="text-[11px] text-slate-500 block">
                                                    {formatUAEDateTime(h.created_at)}
                                                </span>
                                            </div>
                                            <div className="shrink-0 text-right">
                                                <span className="font-semibold text-emerald-700">
                                                    {h.rows_imported} imported
                                                </span>
                                                <span className="text-[11px] text-slate-500 block">
                                                    {h.status}
                                                </span>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
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
                    // Same period as the fleet KPIs (with "All", the fleet's own data range)
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
