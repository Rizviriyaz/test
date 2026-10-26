import { useState, useEffect, useRef, useCallback } from "react"
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
    BarChart3,
    Truck,
    Flame,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import { ErrorBanner } from "../components/Common/StateViews"
import { useFuelFilters } from "../context/useFuelFilters"
import {
    getFleetEfficiency,
    getVehicleEfficiencyList,
    previewFuelImport,
    executeFuelImport,
    getFuelImports,
    reconcileFuelImport,
    getFuelWaste,
    clearClientCache,
} from "../api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import KpiCards from "../components/Fuel/KpiCards"
import DrivingVsIdlingChart from "../components/Fuel/DrivingVsIdlingChart"
import VehicleLeaderboard from "../components/Fuel/VehicleLeaderboard"
import FuelTrendChart from "../components/Fuel/FuelTrendChart"
import FuelTypeDonut from "../components/Fuel/FuelTypeDonut"
import FleetEfficiencyGauge from "../components/Fuel/FleetEfficiencyGauge"
import FuelWasteIntelligence from "../components/Fuel/FuelWasteIntelligence"
import VehicleFuelDetailModal from "../components/Fuel/VehicleFuelDetailModal"
import ExcludedFuelPanel from "../components/Fuel/ExcludedFuelPanel"
import { fixedOrDash, skippedRowsLabel } from "../components/Fuel/fuelLabels"
import { fuelVehicleListParams } from "../utils/fleetRules"
import { formatUAEDateTime } from "../utils/datetime"

export default function FuelPerformance() {
    const { queryParams, periodLabel, setPeriod } = useFuelFilters()

    // Main Tabs State: 'overview' | 'vehicles' | 'waste'
    const [activeTab, setActiveTab] = useState("overview")

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
    const wasteRequestIdRef = useRef(0)

    // Fleet Efficiency & Overview Metrics
    const [efficiency, setEfficiency] = useState(null)
    const [loadingEfficiency, setLoadingEfficiency] = useState(true)
    const [overviewError, setOverviewError] = useState(null)

    // Fuel Waste & Anomalies
    const [wasteData, setWasteData] = useState(null)
    const [loadingWaste, setLoadingWaste] = useState(true)
    const [wasteError, setWasteError] = useState(null)

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
    const [attentionVehicles, setAttentionVehicles] = useState([])
    const [bestVehicles, setBestVehicles] = useState([])

    // Vehicle Detail Modal State
    const [selectedVehicleDetail, setSelectedVehicleDetail] = useState(null)

    // Excel Import Modal State
    const [isImportModalOpen, setIsImportModalOpen] = useState(false)
    const [importModalTab, setImportModalTab] = useState("upload") // "upload" | "history"
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
    const [reconcilingId, setReconcilingId] = useState(null)
    const [reconcileError, setReconcileError] = useState(null)

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

    // 4. Fetch Vehicles Efficiency Outliers (Attention Required & Best Performers)
    const loadAttentionData = useCallback(async () => {
        const requestId = ++attentionRequestIdRef.current
        try {
            const [worstRes, bestRes] = await Promise.all([
                getVehicleEfficiencyList({
                    page: 1,
                    page_size: 4,
                    sort_by: "efficiency",
                    sort_order: "desc",
                    status: "matched",
                    idle_rate_lph: idleRateQuery,
                    ...fuelVehicleListParams(queryParams),
                }),
                getVehicleEfficiencyList({
                    page: 1,
                    page_size: 4,
                    sort_by: "efficiency",
                    sort_order: "asc",
                    status: "matched",
                    idle_rate_lph: idleRateQuery,
                    ...fuelVehicleListParams(queryParams),
                }),
            ])
            if (requestId !== attentionRequestIdRef.current) return
            // Only vehicles with a purchase-based ratio (suppressed ratios are null, never ranked)
            setAttentionVehicles(
                (worstRes?.data || []).filter(
                    (v) => v.fuel_efficiency_l_per_100km !== null && v.fuel_efficiency_l_per_100km !== undefined
                )
            )
            setBestVehicles(
                (bestRes?.data || []).filter(
                    (v) => v.fuel_efficiency_l_per_100km !== null && v.fuel_efficiency_l_per_100km !== undefined
                )
            )
        } catch (err) {
            if (requestId !== attentionRequestIdRef.current) return
            setAttentionVehicles([])
            setBestVehicles([])
            console.warn("Attention load error:", err)
        }
    }, [queryParams, idleRateQuery])

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadEfficiencyData()
        loadWasteData()
        loadAttentionData()
    }, [loadEfficiencyData, loadWasteData, loadAttentionData, idleThreshold])

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

    const handleReconcile = async (importId) => {
        try {
            setReconcilingId(importId)
            setReconcileError(null)
            await reconcileFuelImport(importId)
            await loadImportHistory()
            setDataVersion((n) => n + 1)
        } catch (err) {
            setReconcileError(err?.message || "Could not reconcile the import.")
        } finally {
            setReconcilingId(null)
        }
    }

    const openImportModal = (tab = "upload") => {
        setImportModalTab(tab)
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
            clearClientCache("/api/fuel/")
            setDataVersion((n) => n + 1)
            loadImportHistory()
            loadEfficiencyData()
            loadWasteData()
            loadRankingsData()
        } catch (err) {
            console.error("Import execution failed:", err)
            setImportError(err.message || "An error occurred while importing fuel statement.")
            loadImportHistory()
        } finally {
            setImporting(false)
        }
    }

    const hasNoData = !loadingEfficiency && efficiency?.excluded_scope?.all_transaction_count === 0

    return (
        <AppShell>
            {/* ─── 1. COMPACT TOOLBAR HEADER ───────────────────────────── */}
            <FuelHeader
                title="Fuel & Performance"
                subtitle={periodLabel}
                showFilters={true}
                idleRate={idleRate}
                setIdleRate={setIdleRate}
                onOpenImport={() => openImportModal("upload")}
                onOpenHistory={() => openImportModal("history")}
            />

            {/* ─── 2. THREE MAIN TABS NAVIGATION ────────────────────────── */}
            <div className="flex items-center gap-6 border-b border-slate-200 mb-5">
                <button
                    type="button"
                    role="tab"
                    id="fuel-tab-overview"
                    aria-controls="panel-overview"
                    aria-selected={activeTab === "overview"}
                    onClick={() => setActiveTab("overview")}
                    className={`-mb-px flex items-center gap-2 border-b-2 py-3 text-xs font-semibold transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                        activeTab === "overview"
                            ? "border-brand text-slate-900 font-bold"
                            : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"
                    }`}
                >
                    <BarChart3 className="h-4 w-4" />
                    <span>Overview</span>
                </button>

                <button
                    type="button"
                    role="tab"
                    id="fuel-tab-vehicles"
                    aria-controls="panel-vehicles"
                    aria-selected={activeTab === "vehicles"}
                    onClick={() => setActiveTab("vehicles")}
                    className={`-mb-px flex items-center gap-2 border-b-2 py-3 text-xs font-semibold transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                        activeTab === "vehicles"
                            ? "border-brand text-slate-900 font-bold"
                            : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"
                    }`}
                >
                    <Truck className="h-4 w-4" />
                    <span>Vehicle Performance</span>
                    {vehiclesData?.total > 0 && (
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600 tabular-nums">
                            {vehiclesData.total}
                        </span>
                    )}
                </button>

                <button
                    type="button"
                    role="tab"
                    id="fuel-tab-waste"
                    aria-controls="panel-waste"
                    aria-selected={activeTab === "waste"}
                    onClick={() => setActiveTab("waste")}
                    className={`-mb-px flex items-center gap-2 border-b-2 py-3 text-xs font-semibold transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                        activeTab === "waste"
                            ? "border-brand text-slate-900 font-bold"
                            : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"
                    }`}
                >
                    <Flame className="h-4 w-4 text-amber-600" />
                    <span>Waste & Audits</span>
                    {wasteData?.total_anomalies_count !== undefined && wasteData?.total_anomalies_count > 0 && (
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-900 tabular-nums">
                            {wasteData.total_anomalies_count}
                        </span>
                    )}
                </button>
            </div>

            {/* Error Message */}
            {overviewError && (
                <ErrorBanner message={overviewError} onRetry={loadEfficiencyData} className="mb-4" />
            )}

            {/* Empty state when the period has no records */}
            {hasNoData && (
                <div className="mb-4 flex flex-col gap-3 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3 sm:flex-row sm:items-center sm:justify-between shadow-xs">
                    <div className="flex items-start gap-2.5">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                        <div>
                            <h4 className="text-xs font-semibold text-amber-950">
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

            {/* ═══════════════════════════════════════════════════════════════
                TAB 1: OVERVIEW
            ═══════════════════════════════════════════════════════════════ */}
            {activeTab === "overview" && (
                <div id="panel-overview" role="tabpanel" aria-labelledby="fuel-tab-overview" className="space-y-4">
                    {/* Telemetry coverage alert (if partial telematics) */}
                    {efficiency?.telemetry_status === "partial" && Array.isArray(efficiency?.data_warnings) && efficiency.data_warnings.length > 0 && (
                        <div className="flex items-start gap-2.5 rounded-xl border border-amber-200 bg-amber-50/80 p-3 text-xs text-amber-900 shadow-xs" role="status">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                            <div className="space-y-0.5">
                                <span className="font-semibold block">Wialon Telematics Coverage Notice</span>
                                {efficiency.data_warnings.slice(0, 3).map((w, idx) => (
                                    <span key={idx} className="block text-[11px] text-amber-800">{w}</span>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 8 Compact KPI Cards with transparent calculation modals */}
                    <KpiCards
                        efficiencyData={efficiency}
                        loading={loadingEfficiency}
                        idleRate={idleRate}
                    />

                    {/* Driving vs. Idling Visual Analysis & Top 5 Idle Wasters */}
                    <DrivingVsIdlingChart
                        efficiencyData={efficiency}
                        vehicles={vehiclesData.data}
                        loading={loadingEfficiency || loadingRankings}
                        idleRate={idleRate}
                        onSelectVehicle={(vId) => setSelectedVehicleDetail(vId)}
                    />

                    {/* Fuel Products & Fleet Efficiency Gauge Grid */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                        <FuelTypeDonut />
                        <FleetEfficiencyGauge efficiencyData={efficiency} loading={loadingEfficiency} />
                    </div>

                    {/* Fleet Fuel & Distance Trend Chart */}
                    <FuelTrendChart key={`trend-${dataVersion}`} />
                </div>
            )}

            {/* ═══════════════════════════════════════════════════════════════
                TAB 2: VEHICLE PERFORMANCE
            ═══════════════════════════════════════════════════════════════ */}
            {activeTab === "vehicles" && (
                <div id="panel-vehicles" role="tabpanel" aria-labelledby="fuel-tab-vehicles" className="space-y-4">
                    {/* Efficiency Spotlight Outliers */}
                    {(bestVehicles.length > 0 || attentionVehicles.length > 0) && (
                        <section className="space-y-3" aria-labelledby="efficiency-spotlight-heading">
                            <div className="flex items-center justify-between">
                                <div>
                                    <h2 id="efficiency-spotlight-heading" className="type-section-title text-slate-900">
                                        Vehicle Efficiency Spotlight
                                    </h2>
                                    <p className="text-xs text-slate-500 mt-0.5">
                                        Verified fuel efficiency performance benchmarked across active fleet units
                                    </p>
                                </div>
                            </div>

                            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                                {/* Best Performers */}
                                {bestVehicles.length > 0 && (
                                    <div className="rounded-xl border border-slate-200/70 bg-white p-3.5 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                                        <div className="flex items-center justify-between border-b border-slate-100 pb-2 mb-2.5">
                                            <span className="text-xs font-semibold text-emerald-800 flex items-center gap-1.5">
                                                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                                                Best-Performing Vehicles (Highest km/L)
                                            </span>
                                            <span className="text-[11px] text-slate-400">Top {bestVehicles.length}</span>
                                        </div>
                                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                            {bestVehicles.map((v) => {
                                                const kmPerL = v.distance_km && v.litres && v.litres > 0
                                                    ? (v.distance_km / v.litres)
                                                    : (v.fuel_efficiency_l_per_100km ? 100 / v.fuel_efficiency_l_per_100km : null)
                                                return (
                                                    <button
                                                        type="button"
                                                        key={v.vehicle_identifier}
                                                        onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                        className="rounded-lg border border-slate-100 bg-slate-50/60 p-2.5 text-left transition-colors hover:border-emerald-300 hover:bg-emerald-50/40 cursor-pointer"
                                                    >
                                                        <span className="block truncate font-mono text-xs font-semibold text-slate-900">
                                                            {v.vehicle_identifier}
                                                        </span>
                                                        <span className="mt-1 flex items-baseline gap-1">
                                                            <span className="text-base font-semibold tabular-nums text-emerald-700">
                                                                {kmPerL !== null ? `${kmPerL.toFixed(2)} km/L` : "—"}
                                                            </span>
                                                            <span className="text-[11px] text-slate-500">
                                                                ({v.fuel_efficiency_l_per_100km?.toFixed(1)} L/100)
                                                            </span>
                                                        </span>
                                                        <span className="mt-0.5 block text-[11px] tabular-nums text-slate-500 truncate">
                                                            {v.fuel_cost_per_km != null ? `AED ${fixedOrDash(v.fuel_cost_per_km)}/km` : "—"}
                                                            {" · "}{Math.round(v.distance_km || 0).toLocaleString()} km
                                                        </span>
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    </div>
                                )}

                                {/* Attention Required (Lowest Efficiency) */}
                                {attentionVehicles.length > 0 && (
                                    <div className="rounded-xl border border-slate-200/70 bg-white p-3.5 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                                        <div className="flex items-center justify-between border-b border-slate-100 pb-2 mb-2.5">
                                            <span className="text-xs font-semibold text-amber-900 flex items-center gap-1.5">
                                                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                                                Attention Required (Lowest Efficiency)
                                            </span>
                                            <span className="text-[11px] text-slate-400">Lowest {attentionVehicles.length}</span>
                                        </div>
                                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                            {attentionVehicles.map((v) => {
                                                const kmPerL = v.distance_km && v.litres && v.litres > 0
                                                    ? (v.distance_km / v.litres)
                                                    : (v.fuel_efficiency_l_per_100km ? 100 / v.fuel_efficiency_l_per_100km : null)
                                                return (
                                                    <button
                                                        type="button"
                                                        key={v.vehicle_identifier}
                                                        onClick={() => setSelectedVehicleDetail(v.vehicle_identifier)}
                                                        className="rounded-lg border border-slate-100 bg-slate-50/60 p-2.5 text-left transition-colors hover:border-amber-300 hover:bg-amber-50/40 cursor-pointer"
                                                    >
                                                        <span className="block truncate font-mono text-xs font-semibold text-slate-900">
                                                            {v.vehicle_identifier}
                                                        </span>
                                                        <span className="mt-1 flex items-baseline gap-1">
                                                            <span className="text-base font-semibold tabular-nums text-amber-800">
                                                                {kmPerL !== null ? `${kmPerL.toFixed(2)} km/L` : "—"}
                                                            </span>
                                                            <span className="text-[11px] text-slate-500">
                                                                ({v.fuel_efficiency_l_per_100km?.toFixed(1)} L/100)
                                                            </span>
                                                        </span>
                                                        <span className="mt-0.5 block text-[11px] tabular-nums text-slate-500 truncate">
                                                            {v.fuel_cost_per_km != null ? `AED ${fixedOrDash(v.fuel_cost_per_km)}/km` : "—"}
                                                            {" · "}{Math.round(v.distance_km || 0).toLocaleString()} km
                                                        </span>
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </section>
                    )}

                    {/* Unified Vehicle Performance Leaderboard */}
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
                    />
                </div>
            )}

            {/* ═══════════════════════════════════════════════════════════════
                TAB 3: WASTE & AUDITS
            ═══════════════════════════════════════════════════════════════ */}
            {activeTab === "waste" && (
                <div id="panel-waste" role="tabpanel" aria-labelledby="fuel-tab-waste" className="space-y-4">
                    {/* Transaction Matching & Excluded Fuel Audit Panel */}
                    <ExcludedFuelPanel key={`excluded-${dataVersion}`} />

                    {/* Operational Anomaly & Fuel Waste Intelligence */}
                    <FuelWasteIntelligence
                        wasteData={wasteData}
                        loading={loadingWaste}
                        error={wasteError}
                        onSelectVehicle={(vId) => setSelectedVehicleDetail(vId)}
                        idleThreshold={idleThreshold}
                        onIdleThresholdChange={setIdleThreshold}
                    />
                </div>
            )}

            {/* ─── IMPORT & HISTORY DIALOG ─────────────────────────────────── */}
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
                                    Import fuel statement (.xlsx) and review past reconciliation batches
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

                        {/* Modal Tabs: Upload Statement vs Recent Imports */}
                        <div className="flex items-center gap-4 border-b border-slate-100 my-3 text-xs font-medium">
                            <button
                                type="button"
                                onClick={() => setImportModalTab("upload")}
                                className={`pb-2.5 -mb-px border-b-2 transition-colors cursor-pointer ${
                                    importModalTab === "upload"
                                        ? "border-brand text-slate-900 font-semibold"
                                        : "border-transparent text-slate-500 hover:text-slate-800"
                                }`}
                            >
                                <span className="flex items-center gap-1.5">
                                    <UploadCloud className="h-3.5 w-3.5" />
                                    <span>Upload Statement</span>
                                </span>
                            </button>

                            <button
                                type="button"
                                onClick={() => {
                                    setImportModalTab("history")
                                    loadImportHistory()
                                }}
                                className={`pb-2.5 -mb-px border-b-2 transition-colors cursor-pointer ${
                                    importModalTab === "history"
                                        ? "border-brand text-slate-900 font-semibold"
                                        : "border-transparent text-slate-500 hover:text-slate-800"
                                }`}
                            >
                                <span className="flex items-center gap-1.5">
                                    <History className="h-3.5 w-3.5" />
                                    <span>Import History</span>
                                    {history.length > 0 && (
                                        <span className="rounded-full bg-slate-100 px-1.5 py-0.2 text-[10px] text-slate-600">
                                            {history.length}
                                        </span>
                                    )}
                                </span>
                            </button>
                        </div>

                        {/* Tab 1: Upload Statement */}
                        {importModalTab === "upload" && (
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
                                        </p>
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
                            </div>
                        )}

                        {/* Tab 2: Import History Table */}
                        {importModalTab === "history" && (
                            <div className="py-2">
                                <div className="flex items-center justify-between mb-2">
                                    <h4 className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
                                        <History className="h-3.5 w-3.5 text-slate-400" />
                                        Statement Import Batches
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

                                {reconcileError && (
                                    <div role="alert" className="mb-2 text-xs text-rose-600">{reconcileError}</div>
                                )}
                                <div className="max-h-72 overflow-y-auto divide-y divide-slate-100 text-xs">
                                    {loadingHistory ? (
                                        <div className="py-8 text-center text-slate-400">Loading history…</div>
                                    ) : historyError ? (
                                        <div role="alert" className="py-8 text-center text-rose-600">{historyError}</div>
                                    ) : history.length === 0 ? (
                                        <div className="py-8 text-center text-slate-400">No import records found.</div>
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
                                                    {skippedRowsLabel(h) && (
                                                        <span className="text-[11px] text-slate-400 block">{skippedRowsLabel(h)}</span>
                                                    )}
                                                    {h.status !== "completed" && (
                                                        <button
                                                            type="button"
                                                            onClick={() => handleReconcile(h.id)}
                                                            disabled={reconcilingId !== null}
                                                            title="Recount this import from the transactions stored in the database"
                                                            className="text-[11px] font-medium text-brand hover:underline disabled:opacity-50 cursor-pointer"
                                                        >
                                                            {reconcilingId === h.id ? "Reconciling…" : "Reconcile"}
                                                        </button>
                                                    )}
                                                </div>
                                            </div>
                                        ))
                                    )}
                                </div>
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
