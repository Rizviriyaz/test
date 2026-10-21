import { useState, useEffect, useMemo, useRef, useCallback } from "react"
import { createPortal } from "react-dom"
import {
    UploadCloud,
    Award,
    ShieldAlert,
    Search,
    ChevronLeft,
    ChevronRight,
    AlertTriangle,
    Layers,
    FileSpreadsheet,
    CheckCircle2,
    X,
    Loader2,
    History,
    RefreshCw,
    Sliders,
    ArrowUpRight,
} from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
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
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-white text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex-1 overflow-y-auto px-6 lg:px-8 py-8">
                        {/* ─── HEADER & GLOBAL CONTROLS ───────────────────────────── */}
                        <FuelHeader
                            title="Fuel Performance Dashboard"
                            subtitle={`Fleet Fuel Performance · ${periodLabel}`}
                            showFilters={true}
                            actions={
                                <button
                                    type="button"
                                    onClick={openImportModal}
                                    className="inline-flex items-center gap-2 rounded-xl bg-[#33758F] px-3.5 py-2 text-xs font-bold text-white hover:bg-[#285d73] transition-colors shadow-xs cursor-pointer shrink-0"
                                >
                                    <UploadCloud className="h-4 w-4" />
                                    <span>Import Fuel Data</span>
                                </button>
                            }
                        />

                        {/* ─── TELEMATICS IDLE RATE COEFFICIENT CONTROLS ──────────── */}
                        <div className="mb-5 rounded-2xl border border-slate-200/90 bg-white px-4 py-2.5 shadow-xs">
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                                <div className="flex items-center gap-2.5">
                                    <div className="rounded-xl bg-amber-500/10 p-1.5 text-amber-700 border border-amber-500/20 shrink-0">
                                        <Sliders className="h-4 w-4" />
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs font-bold text-slate-800">Idle Burn Rate</span>
                                        <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-black text-amber-900">
                                            {idleRate.toFixed(1)} L/h
                                        </span>
                                        <span className="text-[11px] text-slate-400 hidden lg:inline">
                                            Stationary engine burn coefficient
                                        </span>
                                    </div>
                                </div>

                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-[11px] font-bold text-slate-400">Presets:</span>
                                    {[
                                        { label: "1.5 L/h", val: 1.5 },
                                        { label: "2.0 (Std)", val: 2.0 },
                                        { label: "2.5 L/h", val: 2.5 },
                                        { label: "3.0 (AC)", val: 3.0 },
                                    ].map((preset) => (
                                        <button
                                            key={preset.val}
                                            type="button"
                                            onClick={() => setIdleRate(preset.val)}
                                            className={`rounded-lg px-2.5 py-1 text-xs font-bold transition-all cursor-pointer ${
                                                idleRate === preset.val
                                                    ? "bg-[#33758F] text-white shadow-xs"
                                                    : "bg-slate-100 text-slate-600 hover:bg-slate-200/70"
                                            }`}
                                        >
                                            {preset.label}
                                        </button>
                                    ))}

                                    <div className="flex items-center gap-1.5 ml-1 pl-2 border-l border-slate-200">
                                        <input
                                            type="range"
                                            min="0.5"
                                            max="5.0"
                                            step="0.1"
                                            value={idleRate}
                                            onChange={(e) => setIdleRate(parseFloat(e.target.value))}
                                            className="w-20 accent-[#33758F] cursor-pointer"
                                            title={`Idle rate: ${idleRate.toFixed(1)} L/h`}
                                        />
                                        <span className="font-mono text-xs font-bold text-slate-700 w-8 text-right">
                                            {idleRate.toFixed(1)}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Error Message */}
                        {overviewError && (
                            <div className="mb-6 flex items-center gap-3 rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
                                <AlertTriangle className="h-5 w-5 shrink-0 text-rose-600" />
                                <span>{overviewError}</span>
                            </div>
                        )}

                        {/* Empty State Banner if no records for selected period */}
                        {hasNoData && (
                            <div className="mb-6 rounded-2xl border border-amber-200 bg-amber-50/60 p-5 shadow-xs">
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                    <div className="flex items-center gap-3">
                                        <div className="rounded-xl bg-amber-100 p-2 text-amber-700">
                                            <AlertTriangle className="h-5 w-5" />
                                        </div>
                                        <div>
                                            <h4 className="text-xs font-bold text-amber-900">
                                                No Fuel Transactions Recorded for {periodLabel}
                                            </h4>
                                            <p className="text-[11px] text-amber-700 mt-0.5">
                                                The selected filter returned 0 refuels. Switch to All Available Data or specify a custom date range.
                                            </p>
                                        </div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setPeriod("all")}
                                        className="inline-flex items-center gap-1.5 rounded-xl bg-amber-700 px-3.5 py-2 text-xs font-bold text-white hover:bg-amber-800 transition-colors shadow-2xs cursor-pointer self-start sm:self-auto"
                                    >
                                        <Layers className="h-3.5 w-3.5" />
                                        <span>View All Historical Records</span>
                                    </button>
                                </div>
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

                        {/* ─── 4. FUEL TYPE BREAKDOWN & FLEET EFFICIENCY GAUGE ─────── */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
                            <FuelTypeDonut />
                            <FleetEfficiencyGauge efficiencyData={efficiency} loading={loadingEfficiency} />
                        </div>

                        {/* ─── 5. VEHICLES NEEDING ATTENTION (OUTLIERS) ────────────── */}
                        {attentionVehicles.length > 0 && (
                            <section className="mb-6">
                                <div className="rounded-2xl border border-amber-200/80 bg-gradient-to-r from-amber-50/60 to-rose-50/40 p-4 shadow-xs">
                                    <div className="flex items-center gap-2 mb-2.5">
                                        <div className="rounded-xl bg-amber-500/10 p-1.5 text-amber-700">
                                            <ShieldAlert className="h-4 w-4" />
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <h2 className="text-sm font-bold text-slate-900">
                                                Vehicles Needing Attention
                                            </h2>
                                            <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800">
                                                Relative Efficiency Outliers
                                            </span>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
                                        {attentionVehicles.map((v) => (
                                            <div
                                                key={v.vehicle_identifier}
                                                onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                className="rounded-xl border border-amber-200 bg-white/90 p-3 shadow-2xs hover:shadow-xs transition-shadow cursor-pointer"
                                            >
                                                <div className="flex items-center justify-between mb-1.5">
                                                    <span className="font-mono text-xs font-black text-slate-900">
                                                        {v.vehicle_identifier}
                                                    </span>
                                                    <span className="rounded bg-amber-100 px-1 py-0.2 text-[9px] font-bold text-amber-800">
                                                        Highest L/100km
                                                    </span>
                                                </div>

                                                <div className="flex items-baseline justify-between mb-0.5">
                                                    <span className="text-[11px] text-slate-500">Consumption:</span>
                                                    <span className="text-xs font-black text-amber-700">
                                                        {v.fuel_efficiency_l_per_100km?.toFixed(1)} L/100km
                                                    </span>
                                                </div>

                                                <div className="flex items-baseline justify-between text-[11px] text-slate-500">
                                                    <span>Cost / km:</span>
                                                    <span className="font-bold text-slate-800">
                                                        {v.fuel_cost_per_km != null ? `AED ${fixedOrDash(v.fuel_cost_per_km)}` : "—"}
                                                    </span>
                                                </div>

                                                <div className="mt-1.5 pt-1.5 border-t border-slate-100 flex items-center justify-between text-[10px] text-slate-400">
                                                    <span>{Math.round(v.litres).toLocaleString()} L</span>
                                                    <span>{v.distance_km != null ? `${Math.round(v.distance_km).toLocaleString()} km` : "— km"}</span>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </section>
                        )}

                        {/* ─── 6. VEHICLE PERFORMANCE RANKINGS ─────────────────────── */}
                        <section className="rounded-2xl border border-slate-200/80 bg-white p-4.5 sm:p-5 shadow-xs mb-6">
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-slate-100">
                                <div className="flex items-center gap-2">
                                    <div className="rounded-xl bg-[#33758F]/10 p-1.5 text-[#33758F]">
                                        <Award className="h-4 w-4" />
                                    </div>
                                    <h3 className="text-sm font-bold text-slate-900">
                                        Vehicle Performance Rankings
                                    </h3>
                                </div>
                            </div>

                            {/* Ranking Controls */}
                            <div className="my-3 flex flex-col lg:flex-row lg:items-center justify-between gap-2.5">
                                <div className="relative flex-1 max-w-sm">
                                    <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
                                    <input
                                        type="text"
                                        placeholder="Search vehicle plate number…"
                                        value={searchQuery}
                                        onChange={(e) => {
                                            setSearchQuery(e.target.value)
                                            setPage(1)
                                        }}
                                        className="w-full rounded-xl border border-slate-200 bg-slate-50/50 pl-9 pr-3 py-1.5 text-xs font-semibold text-slate-800 placeholder-slate-400 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                                    />
                                </div>

                                <div className="flex flex-wrap items-center gap-2">
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">
                                            Rank By:
                                        </span>
                                        <select
                                            value={sortBy}
                                            onChange={(e) => {
                                                setSortBy(e.target.value)
                                                setPage(1)
                                            }}
                                            className="rounded-xl border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 shadow-2xs cursor-pointer"
                                        >
                                            {SORT_OPTIONS.map((opt) => (
                                                <option key={opt.value} value={opt.value}>
                                                    {opt.label}
                                                </option>
                                            ))}
                                        </select>
                                    </div>

                                    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 text-xs font-semibold">
                                        <button
                                            type="button"
                                            onClick={() => setSortOrder("desc")}
                                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                                sortOrder === "desc"
                                                    ? "bg-white text-slate-900 shadow-xs font-bold"
                                                    : "text-slate-600 hover:text-slate-900"
                                            }`}
                                        >
                                            {sortOrderLabels(sortBy).desc}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setSortOrder("asc")}
                                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                                sortOrder === "asc"
                                                    ? "bg-white text-slate-900 shadow-xs font-bold"
                                                    : "text-slate-600 hover:text-slate-900"
                                            }`}
                                        >
                                            {sortOrderLabels(sortBy).asc}
                                        </button>
                                    </div>

                                    <select
                                        value={statusFilter}
                                        onChange={(e) => {
                                            setStatusFilter(e.target.value)
                                            setPage(1)
                                        }}
                                        className="rounded-xl border border-slate-200 bg-white px-3 py-1 text-xs font-semibold text-slate-700 shadow-2xs cursor-pointer"
                                    >
                                        <option value="">All Matched Vehicles</option>
                                        <option value="matched">Active Telemetry</option>
                                        <option value="no_distance_data">Zero Distance in Period</option>
                                    </select>
                                </div>
                            </div>

                            {/* Compact High-Density Table */}
                            <div className="overflow-x-auto rounded-xl border border-slate-200 mt-2">
                                <table className="w-full text-left text-xs">
                                    <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-500 border-b border-slate-200">
                                        <tr>
                                            <th className="px-3.5 py-2.5">Rank & Vehicle</th>
                                            <th className="px-3 py-2.5 text-right">Fuel (L & AED)</th>
                                            <th className="px-3 py-2.5 text-right">Distance</th>
                                            <th className="px-3 py-2.5 text-right">Efficiency</th>
                                            <th className="px-3 py-2.5 text-right">Driving</th>
                                            <th className="px-3 py-2.5 text-right">Idling</th>
                                            <th className="px-3.5 py-2.5 min-w-[130px]">Relative</th>
                                            <th className="px-3 py-2.5 text-right">Action</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 bg-white">
                                        {loadingRankings ? (
                                            <tr>
                                                <td colSpan={8} className="py-12 text-center text-xs text-slate-400">
                                                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-[#33758F] mb-1.5" />
                                                    Loading vehicle performance rankings…
                                                </td>
                                            </tr>
                                        ) : rankingsError ? (
                                            <tr>
                                                <td colSpan={8} role="alert" className="py-8 text-center text-xs text-rose-600">
                                                    {rankingsError}
                                                </td>
                                            </tr>
                                        ) : vehiclesData.data.length === 0 ? (
                                            <tr>
                                                <td colSpan={8} className="py-8 text-center text-xs text-slate-400">
                                                    No vehicles found matching the filter criteria.
                                                </td>
                                            </tr>
                                        ) : (
                                            vehiclesData.data.map((v, idx) => {
                                                const rankNum = (page - 1) * pageSize + idx + 1
                                                const isGold = rankNum === 1
                                                const isSilver = rankNum === 2
                                                const isBronze = rankNum === 3

                                                let currentMetricVal = v.litres || 0
                                                if (sortBy === "cost") currentMetricVal = v.total_cost || 0
                                                if (sortBy === "distance") currentMetricVal = v.distance_km || 0
                                                if (sortBy === "efficiency") currentMetricVal = v.fuel_efficiency_l_per_100km || 0
                                                if (sortBy === "cost_per_km") currentMetricVal = v.fuel_cost_per_km || 0
                                                if (sortBy === "transactions") currentMetricVal = v.transaction_count || 0

                                                const progressPercent = Math.min(Math.max((currentMetricVal / maxSortMetric) * 100, 4), 100)

                                                return (
                                                    <tr
                                                        key={v.vehicle_identifier}
                                                        className={`hover:bg-slate-50/80 transition-colors ${
                                                            isGold ? "bg-amber-50/20" : isSilver ? "bg-slate-50/30" : isBronze ? "bg-orange-50/15" : ""
                                                        }`}
                                                    >
                                                        {/* 1. Rank & Vehicle Plate */}
                                                        <td className="px-3.5 py-2.5 whitespace-nowrap">
                                                            <div className="flex items-center gap-2.5">
                                                                <span
                                                                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-[10px] font-black ${
                                                                        isGold
                                                                            ? "bg-amber-100 text-amber-800 border border-amber-300"
                                                                            : isSilver
                                                                            ? "bg-slate-200 text-slate-800 border border-slate-300"
                                                                            : isBronze
                                                                            ? "bg-amber-800/10 text-amber-900 border border-amber-800/20"
                                                                            : "bg-slate-100 text-slate-600"
                                                                    }`}
                                                                >
                                                                    {isGold ? "🥇" : isSilver ? "🥈" : isBronze ? "🥉" : `#${rankNum}`}
                                                                </span>
                                                                <div>
                                                                    <div className="flex items-center gap-1.5">
                                                                        <button
                                                                            type="button"
                                                                            onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                                            className="font-mono text-xs font-black text-slate-900 hover:text-[#33758F] transition-colors cursor-pointer"
                                                                        >
                                                                            {v.vehicle_identifier}
                                                                        </button>
                                                                        <VehicleStatusBadge status={v.status} />
                                                                        {v.estimation_status && (
                                                                            <span className="rounded px-1 text-[9px] font-bold text-slate-500 bg-slate-100 border border-slate-200">
                                                                                [{v.estimation_status.toUpperCase()}]
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                    {v.wialon_unit_name && (
                                                                        <span className="text-[10px] text-slate-400 block">
                                                                            {v.wialon_unit_name}
                                                                        </span>
                                                                    )}
                                                                </div>
                                                            </div>
                                                        </td>

                                                        {/* 2. Fuel Purchased & Cost */}
                                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                                            <span className="font-bold text-slate-900 block">
                                                                {Math.round(v.litres).toLocaleString()} L
                                                            </span>
                                                            <span className="text-[10px] text-slate-400 block">
                                                                AED {Math.round(v.total_cost || 0).toLocaleString()}
                                                            </span>
                                                        </td>

                                                        {/* 3. Distance */}
                                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                                            <span className="font-bold text-sky-700 block">
                                                                {v.distance_km !== null ? `${Math.round(v.distance_km).toLocaleString()} km` : "—"}
                                                            </span>
                                                            <span className="text-[10px] text-slate-400 block">
                                                                {v.transaction_count} refuels
                                                            </span>
                                                        </td>

                                                        {/* 4. Efficiency */}
                                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                                            <span className="font-bold text-[#33758F] block">
                                                                {v.fuel_efficiency_l_per_100km !== null ? `${v.fuel_efficiency_l_per_100km.toFixed(1)} L/100` : "—"}
                                                            </span>
                                                            <span className="text-[10px] text-slate-400 block">
                                                                {v.fuel_cost_per_km !== null ? `AED ${fixedOrDash(v.fuel_cost_per_km)}/km` : "—"}
                                                            </span>
                                                        </td>

                                                        {/* 5. Driving */}
                                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                                            <span className="font-bold text-sky-900 block">
                                                                {v.driving_duration_hours !== null && v.driving_duration_hours !== undefined ? `${v.driving_duration_hours.toFixed(1)} hrs` : "—"}
                                                            </span>
                                                            <span className="text-[10px] text-sky-700 block">
                                                                {v.estimated_driving_fuel_litres !== null && v.estimated_driving_fuel_litres !== undefined ? `${v.estimated_driving_fuel_litres.toFixed(1)} L` : "In motion"}
                                                            </span>
                                                        </td>

                                                        {/* 6. Idling */}
                                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                                            <span className="font-bold text-amber-900 block">
                                                                {v.idle_duration_hours !== null && v.idle_duration_hours !== undefined ? `${v.idle_duration_hours.toFixed(1)} hrs` : "—"}
                                                            </span>
                                                            <span className="text-[10px] text-rose-700 font-semibold block">
                                                                {v.estimated_idle_fuel_litres !== null && v.estimated_idle_fuel_litres !== undefined ? `${v.estimated_idle_fuel_litres.toFixed(1)} L waste` : "—"}
                                                            </span>
                                                        </td>

                                                        {/* 7. Relative Performance */}
                                                        <td className="px-3.5 py-2.5 min-w-[130px]">
                                                            <div className="flex items-center justify-between text-[10px] font-bold text-slate-500 mb-0.5">
                                                                <span className="font-mono text-slate-700">{Math.round(progressPercent)}%</span>
                                                            </div>
                                                            <div className="h-1.5 w-full rounded-full bg-slate-100 overflow-hidden border border-slate-200/50">
                                                                <div
                                                                    className="h-full rounded-full bg-gradient-to-r from-[#33758F] to-[#0284C7] transition-all duration-300"
                                                                    style={{ width: `${progressPercent}%` }}
                                                                />
                                                            </div>
                                                        </td>

                                                        {/* 8. Action */}
                                                        <td className="px-3 py-2.5 text-right whitespace-nowrap">
                                                            <button
                                                                type="button"
                                                                onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                                className="inline-flex items-center gap-1 rounded-lg bg-slate-100 hover:bg-[#33758F] hover:text-white px-2.5 py-1 text-xs font-bold text-slate-700 transition-colors cursor-pointer"
                                                            >
                                                                <span>Inspect</span>
                                                                <ArrowUpRight className="h-3 w-3" />
                                                            </button>
                                                        </td>
                                                    </tr>
                                                )
                                            })
                                        )}
                                    </tbody>
                                </table>
                            </div>

                            {/* Pagination */}
                            {vehiclesData.total > 0 && (
                                <div className="mt-3 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pt-2.5 border-t border-slate-100">
                                    <span className="text-xs font-semibold text-slate-500">
                                        Showing {(page - 1) * pageSize + 1} to {Math.min(page * pageSize, vehiclesData.total)} of {vehiclesData.total} vehicles
                                    </span>

                                    <div className="flex items-center gap-2">
                                        <button
                                            type="button"
                                            disabled={page <= 1}
                                            onClick={() => setPage((p) => Math.max(1, p - 1))}
                                            className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-2xs"
                                        >
                                            <ChevronLeft className="h-3.5 w-3.5" />
                                            Previous
                                        </button>
                                        <span className="px-2 text-xs font-bold text-slate-700">
                                            Page {page} of {totalPages}
                                        </span>
                                        <button
                                            type="button"
                                            disabled={page >= totalPages}
                                            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                                            className="inline-flex items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer shadow-2xs"
                                        >
                                            Next
                                            <ChevronRight className="h-3.5 w-3.5" />
                                        </button>
                                    </div>
                                </div>
                            )}
                        </section>

                        {/* ─── IMPORT MODAL ────────────────────────────────────────── */}
                        {isImportModalOpen && createPortal(
                            <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/50 backdrop-blur-xs p-4 overflow-y-auto" onClick={() => setIsImportModalOpen(false)}>
                                <div className="w-full max-w-2xl rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl my-8" onClick={(e) => e.stopPropagation()}>
                                    <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                                        <div className="flex items-center gap-2.5">
                                            <div className="rounded-xl bg-[#33758F]/10 p-2 text-[#33758F]">
                                                <UploadCloud className="h-5 w-5" />
                                            </div>
                                            <div>
                                                <h3 className="text-base font-bold text-slate-900">
                                                    Import Fuel Data (.xlsx)
                                                </h3>
                                                <p className="text-xs text-slate-500">
                                                    Upload Excel statement with automated column preview
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
                                            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                        >
                                            <X className="h-5 w-5" />
                                        </button>
                                    </div>

                                    {/* Upload Result Alert */}
                                    {importResult && (
                                        <div className={`my-4 rounded-xl border p-4 text-xs ${
                                            importResult.status === "completed"
                                                ? "border-emerald-200 bg-emerald-50/80"
                                                : importResult.status === "failed"
                                                    ? "border-rose-200 bg-rose-50/80"
                                                    : "border-amber-200 bg-amber-50/80"
                                        }`}>
                                            <div className="flex items-center gap-2 font-bold text-slate-800 mb-1">
                                                {importResult.status === "completed"
                                                    ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                                                    : <AlertTriangle className="h-4 w-4 text-amber-600" />}
                                                <span>
                                                    {importResult.status === "completed"
                                                        ? "Import Successful!"
                                                        : importResult.status === "failed"
                                                            ? "Import Failed"
                                                            : "Import Completed With Errors"}
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
                                        <div role="alert" className="my-4 flex items-center gap-3 rounded-xl border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
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
                                            className={`my-5 rounded-2xl border-2 border-dashed p-8 text-center cursor-pointer transition-colors ${
                                                isDragging
                                                    ? "border-[#33758F] bg-[#33758F]/5"
                                                    : "border-slate-200 hover:border-[#33758F]/50 bg-slate-50/50"
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
                                            <FileSpreadsheet className="mx-auto h-10 w-10 text-[#33758F] mb-2" />
                                            <p className="text-sm font-bold text-slate-800">
                                                Drag & drop Excel fuel file here, or <span className="text-[#33758F] underline">Choose File</span>
                                            </p>
                                            <span className="text-[11px] text-slate-400 mt-1 block">Supported format: .xlsx</span>
                                        </div>
                                    )}

                                    {/* Preview Loading */}
                                    {previewLoading && (
                                        <div className="my-8 text-center text-xs text-slate-500">
                                            <Loader2 className="mx-auto h-7 w-7 animate-spin text-[#33758F] mb-2" />
                                            Parsing Excel structure & schema…
                                        </div>
                                    )}

                                    {/* Preview Result */}
                                    {previewData && (
                                        <div className="my-4 space-y-3">
                                            <div className="rounded-xl border border-slate-200 bg-slate-50/50 p-3 text-xs">
                                                <div className="flex items-center justify-between font-bold text-slate-800 mb-2">
                                                    <span>{previewData.file_name}</span>
                                                    <span className="text-emerald-700">{previewData.rows_candidate} valid rows detected</span>
                                                </div>
                                                <div className="grid grid-cols-3 gap-2 text-center text-[11px]">
                                                    <div className="bg-white rounded-lg p-2 border border-slate-100">
                                                        <span className="text-slate-400 block">Total Rows</span>
                                                        <span className="font-bold text-slate-800">{previewData.rows_found}</span>
                                                    </div>
                                                    <div className="bg-white rounded-lg p-2 border border-slate-100">
                                                        <span className="text-slate-400 block">Ignored</span>
                                                        <span className="font-bold text-slate-500">{previewData.rows_ignored}</span>
                                                    </div>
                                                    <div className="bg-white rounded-lg p-2 border border-slate-100">
                                                        <span className="text-slate-400 block">Columns</span>
                                                        <span className="font-bold text-[#33758F]">{Object.keys(previewData.suggested_mapping || {}).length}</span>
                                                    </div>
                                                </div>
                                            </div>

                                            {previewData.missing_required_fields?.length > 0 && (
                                                <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                                                    Required columns not recognised: {previewData.missing_required_fields.join(", ")}. Rename these headers in the statement and upload it again.
                                                </p>
                                            )}

                                            <div className="flex items-center justify-end gap-2 pt-2">
                                                <button
                                                    type="button"
                                                    disabled={importing}
                                                    onClick={() => setPreviewData(null)}
                                                    className="rounded-xl border border-slate-200 px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
                                                >
                                                    Cancel
                                                </button>
                                                <button
                                                    type="button"
                                                    disabled={importing || previewData.missing_required_fields?.length > 0}
                                                    onClick={handleConfirmImport}
                                                    className="inline-flex items-center gap-1.5 rounded-xl bg-[#33758F] px-4 py-2 text-xs font-bold text-white hover:bg-[#285d73] shadow-xs disabled:opacity-50 disabled:cursor-not-allowed"
                                                >
                                                    {importing ? (
                                                        <>
                                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                            <span>Importing to Database…</span>
                                                        </>
                                                    ) : (
                                                        <>
                                                            <UploadCloud className="h-3.5 w-3.5" />
                                                            <span>Confirm Import</span>
                                                        </>
                                                    )}
                                                </button>
                                            </div>
                                        </div>
                                    )}

                                    {/* Import History Table */}
                                    <div className="mt-6 pt-4 border-t border-slate-100">
                                        <div className="flex items-center justify-between mb-2">
                                            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-400 flex items-center gap-1.5">
                                                <History className="h-3.5 w-3.5" />
                                                Recent Imports Log
                                            </h4>
                                            <button
                                                type="button"
                                                onClick={loadImportHistory}
                                                className="text-slate-400 hover:text-slate-700 p-1 rounded"
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
                                                    <div key={h.id} className="py-2.5 flex items-center justify-between">
                                                        <div>
                                                            <span className="font-bold text-slate-800">{h.file_name}</span>
                                                            <span className="text-[10px] text-slate-400 block">
                                                                {formatUAEDateTime(h.created_at)}
                                                            </span>
                                                        </div>
                                                        <div className="text-right">
                                                            <span className="font-bold text-emerald-700">
                                                                {h.rows_imported} imported
                                                            </span>
                                                            <span className="text-[10px] text-slate-400 block">
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
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
