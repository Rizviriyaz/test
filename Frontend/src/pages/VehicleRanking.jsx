import { useState, useEffect, useMemo } from "react"
import { Link } from "react-router-dom"
import {
    Award,
    ArrowUpDown,
    AlertTriangle,
    Search,
    ChevronLeft,
    ChevronRight,
    TrendingDown,
    Fuel,
    DollarSign,
    Route,
    Gauge,
    ShieldAlert,
    ExternalLink,
} from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { useFuelFilters } from "../context/FuelFilterContext"
import { getVehicleEfficiencyList } from "../../src/api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import VehicleStatusBadge from "../components/Fuel/VehicleStatusBadge"

const SORT_OPTIONS = [
    { label: "Fuel Volume (Litres)", value: "litres" },
    { label: "Fuel Expenditure (Cost)", value: "cost" },
    { label: "Distance Traveled (km)", value: "distance" },
    { label: "Fuel Efficiency (L/100km)", value: "efficiency" },
    { label: "Cost Per km (AED/km)", value: "cost_per_km" },
    { label: "Refuel Count (Transactions)", value: "transactions" },
    { label: "Vehicle Identifier", value: "vehicle_identifier" },
]

export default function VehicleRanking() {
    const { queryParams, periodLabel } = useFuelFilters()

    const [sortBy, setSortBy] = useState("litres")
    const [sortOrder, setSortOrder] = useState("desc")
    const [statusFilter, setStatusFilter] = useState("")
    const [searchQuery, setSearchQuery] = useState("")
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(25)

    const [vehiclesData, setVehiclesData] = useState({ data: [], total: 0, page: 1 })
    const [attentionVehicles, setAttentionVehicles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    // Fetch ranked vehicle list from backend
    useEffect(() => {
        let active = true
        async function fetchRanking() {
            try {
                setLoading(true)
                setError(null)
                const res = await getVehicleEfficiencyList({
                    page,
                    page_size: pageSize,
                    sort_by: sortBy,
                    sort_order: sortOrder,
                    status: statusFilter || undefined,
                    vehicle_identifier: searchQuery || undefined,
                    ...queryParams,
                })
                if (!active) return
                setVehiclesData(res || { data: [], total: 0, page: 1 })
            } catch (err) {
                if (!active) return
                console.error("Failed to load vehicle rankings:", err)
                setError(err.message || "Failed to load vehicle rankings.")
            } finally {
                if (active) setLoading(false)
            }
        }

        fetchRanking()
        return () => { active = false }
    }, [page, pageSize, sortBy, sortOrder, statusFilter, searchQuery, queryParams])

    // Load outlier vehicles needing attention
    useEffect(() => {
        let active = true
        async function fetchAttention() {
            try {
                // Fetch worst efficiency (highest L/100km)
                const worstEffRes = await getVehicleEfficiencyList({
                    page: 1,
                    page_size: 4,
                    sort_by: "efficiency",
                    sort_order: "desc",
                    status: "matched", // only those with calculated efficiency
                    ...queryParams,
                })
                if (!active) return
                setAttentionVehicles(worstEffRes?.data || [])
            } catch (err) {
                console.warn("Failed to fetch attention outliers:", err)
            }
        }
        fetchAttention()
        return () => { active = false }
    }, [queryParams])

    const totalPages = Math.max(1, Math.ceil((vehiclesData.total || 0) / pageSize))

    // Calculate maximum metric in current dataset for relative performance bar
    const maxSortMetric = useMemo(() => {
        if (!vehiclesData.data || vehiclesData.data.length === 0) return 1
        const vals = vehiclesData.data.map(v => {
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

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-[#F8FAFC] text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex-1 overflow-y-auto px-6 lg:px-8 py-8">
                        {/* Header */}
                        <FuelHeader
                            title="Vehicle Performance & Ranking"
                            subtitle={`Fleet Vehicle Ranking · ${periodLabel}`}
                        />

                        {/* Error Message */}
                        {error && (
                            <div className="mb-6 flex items-center gap-3 rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
                                <AlertTriangle className="h-5 w-5 shrink-0 text-rose-600" />
                                <span>{error}</span>
                            </div>
                        )}

                        {/* ─── SECTION 1: VEHICLES NEEDING ATTENTION ──────────────── */}
                        {attentionVehicles.length > 0 && (
                            <section className="mb-6">
                                <div className="rounded-2xl border border-amber-200/80 bg-gradient-to-r from-amber-50/60 to-rose-50/40 p-5 shadow-xs">
                                    <div className="flex items-center gap-2 mb-3">
                                        <div className="rounded-xl bg-amber-500/10 p-1.5 text-amber-700">
                                            <ShieldAlert className="h-4 w-4" />
                                        </div>
                                        <div>
                                            <h2 className="text-sm font-bold text-slate-900">
                                                Vehicles Needing Attention (Relative Efficiency Outliers)
                                            </h2>
                                            <p className="text-[11px] text-slate-600">
                                                Tracked vehicles recording highest relative fuel consumption per 100 km
                                            </p>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mt-3">
                                        {attentionVehicles.map((v) => (
                                            <div
                                                key={v.vehicle_identifier}
                                                className="rounded-xl border border-amber-200 bg-white/90 p-3.5 shadow-2xs hover:shadow-xs transition-shadow"
                                            >
                                                <div className="flex items-center justify-between mb-2">
                                                    <span className="font-mono text-xs font-black text-slate-900">
                                                        {v.vehicle_identifier}
                                                    </span>
                                                    <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800">
                                                        High Burn
                                                    </span>
                                                </div>

                                                <div className="flex items-baseline justify-between mb-1">
                                                    <span className="text-[11px] text-slate-500">Consumption:</span>
                                                    <span className="text-sm font-black text-amber-700">
                                                        {v.fuel_efficiency_l_per_100km?.toFixed(1)} L/100km
                                                    </span>
                                                </div>

                                                <div className="flex items-baseline justify-between text-[11px] text-slate-500">
                                                    <span>Cost / km:</span>
                                                    <span className="font-bold text-slate-800">
                                                        AED {v.fuel_cost_per_km?.toFixed(2)}
                                                    </span>
                                                </div>

                                                <div className="mt-2 pt-2 border-t border-slate-100 flex items-center justify-between text-[10px] text-slate-400">
                                                    <span>{Math.round(v.litres).toLocaleString()} L used</span>
                                                    <span>{Math.round(v.distance_km || 0).toLocaleString()} km</span>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            </section>
                        )}

                        {/* ─── SECTION 2: RANKING CONTROLS ──────────────────────────── */}
                        <div className="rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs mb-6">
                            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                                {/* Search query input */}
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
                                        className="w-full rounded-xl border border-slate-200 bg-slate-50/50 pl-9 pr-3 py-2 text-xs font-semibold text-slate-800 placeholder-slate-400 focus:border-[#1C5E7A] focus:bg-white focus:outline-hidden"
                                    />
                                </div>

                                {/* Dynamic Sorting Controls */}
                                <div className="flex flex-wrap items-center gap-2.5">
                                    {/* Rank By Field */}
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
                                            className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-2xs focus:border-[#1C5E7A] cursor-pointer"
                                        >
                                            {SORT_OPTIONS.map((opt) => (
                                                <option key={opt.value} value={opt.value}>
                                                    {opt.label}
                                                </option>
                                            ))}
                                        </select>
                                    </div>

                                    {/* Order Direction */}
                                    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 text-xs font-semibold">
                                        <button
                                            type="button"
                                            onClick={() => setSortOrder("desc")}
                                            className={`rounded-lg px-2.5 py-1.5 transition-colors cursor-pointer ${
                                                sortOrder === "desc"
                                                    ? "bg-white text-slate-900 shadow-xs font-bold"
                                                    : "text-slate-600 hover:text-slate-900"
                                            }`}
                                        >
                                            Highest First (Best)
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setSortOrder("asc")}
                                            className={`rounded-lg px-2.5 py-1.5 transition-colors cursor-pointer ${
                                                sortOrder === "asc"
                                                    ? "bg-white text-slate-900 shadow-xs font-bold"
                                                    : "text-slate-600 hover:text-slate-900"
                                            }`}
                                        >
                                            Lowest First
                                        </button>
                                    </div>

                                    {/* Status Filter */}
                                    <select
                                        value={statusFilter}
                                        onChange={(e) => {
                                            setStatusFilter(e.target.value)
                                            setPage(1)
                                        }}
                                        className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-2xs cursor-pointer"
                                    >
                                        <option value="">All Statuses</option>
                                        <option value="matched">Tracked GPS</option>
                                        <option value="no_distance_data">No Distance Telemetry</option>
                                        <option value="not_tracked">Untracked</option>
                                        <option value="ambiguous">Ambiguous</option>
                                    </select>
                                </div>
                            </div>
                        </div>

                        {/* ─── SECTION 3: RANKED VEHICLE CARDS / ROWS ──────────────── */}
                        <div className="space-y-3 mb-6">
                            {loading ? (
                                <div className="space-y-3">
                                    {[1, 2, 3, 4, 5].map((i) => (
                                        <div
                                            key={i}
                                            className="animate-pulse rounded-2xl border border-slate-200 bg-white p-4 shadow-xs flex items-center justify-between"
                                        >
                                            <div className="flex items-center gap-3">
                                                <div className="h-7 w-7 rounded-xl bg-slate-200" />
                                                <div className="h-4 w-32 bg-slate-200 rounded" />
                                            </div>
                                            <div className="h-6 w-60 bg-slate-100 rounded" />
                                        </div>
                                    ))}
                                </div>
                            ) : vehiclesData.data.length === 0 ? (
                                <div className="rounded-2xl border border-slate-200 bg-white p-12 text-center text-slate-400">
                                    <Award className="mx-auto h-8 w-8 text-slate-300 mb-2" />
                                    <p className="text-sm font-bold text-slate-700">No vehicle records found.</p>
                                    <p className="text-xs text-slate-400 mt-1">Try adjusting the filter criteria or date range.</p>
                                </div>
                            ) : (
                                vehiclesData.data.map((v, idx) => {
                                    const rankNum = (page - 1) * pageSize + idx + 1
                                    const isGold = rankNum === 1
                                    const isSilver = rankNum === 2
                                    const isBronze = rankNum === 3

                                    // Metric value for progress bar
                                    let currentMetricVal = v.litres || 0
                                    if (sortBy === "cost") currentMetricVal = v.total_cost || 0
                                    if (sortBy === "distance") currentMetricVal = v.distance_km || 0
                                    if (sortBy === "efficiency") currentMetricVal = v.fuel_efficiency_l_per_100km || 0
                                    if (sortBy === "cost_per_km") currentMetricVal = v.fuel_cost_per_km || 0
                                    if (sortBy === "transactions") currentMetricVal = v.transaction_count || 0

                                    const progressPercent = Math.min(Math.max((currentMetricVal / maxSortMetric) * 100, 4), 100)

                                    return (
                                        <div
                                            key={v.vehicle_identifier}
                                            className={`rounded-2xl border bg-white p-4 shadow-xs transition-all hover:shadow-md ${
                                                isGold
                                                    ? "border-amber-300/80 bg-gradient-to-r from-amber-50/20 to-white"
                                                    : isSilver
                                                    ? "border-slate-300/80 bg-gradient-to-r from-slate-50/30 to-white"
                                                    : isBronze
                                                    ? "border-amber-700/30 bg-gradient-to-r from-orange-50/20 to-white"
                                                    : "border-slate-200/80"
                                            }`}
                                        >
                                            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                                                {/* Left: Rank Medal & Plate & Status */}
                                                <div className="flex items-center gap-3 min-w-[240px]">
                                                    <span
                                                        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-xs font-black shadow-2xs ${
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
                                                        <div className="flex items-center gap-2">
                                                            <Link
                                                                to={`/fuel/vehicles?select=${encodeURIComponent(v.vehicle_identifier)}`}
                                                                className="font-mono text-sm font-black text-slate-900 hover:text-[#1C5E7A] transition-colors"
                                                            >
                                                                {v.vehicle_identifier}
                                                            </Link>
                                                            <VehicleStatusBadge status={v.status} />
                                                        </div>
                                                        {v.wialon_unit_name && (
                                                            <span className="text-[11px] text-slate-400 font-medium">
                                                                GPS Unit: {v.wialon_unit_name}
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>

                                                {/* Center: Visual Performance Bar */}
                                                <div className="flex-1 max-w-xs px-2">
                                                    <div className="flex items-center justify-between text-[11px] font-bold text-slate-500 mb-1">
                                                        <span>Relative Performance</span>
                                                        <span className="font-mono text-slate-700">{Math.round(progressPercent)}%</span>
                                                    </div>
                                                    <div className="h-2.5 w-full rounded-full bg-slate-100 overflow-hidden border border-slate-200/50">
                                                        <div
                                                            className="h-full rounded-full bg-gradient-to-r from-[#1C5E7A] to-[#0284C7] transition-all duration-300"
                                                            style={{ width: `${progressPercent}%` }}
                                                        />
                                                    </div>
                                                </div>

                                                {/* Right: Key Telemetry Grid */}
                                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-right text-xs shrink-0">
                                                    <div className="rounded-xl bg-slate-50/60 p-2">
                                                        <span className="block text-[10px] font-bold text-slate-400 uppercase">Efficiency</span>
                                                        <span className="font-bold text-[#1C5E7A]">
                                                            {v.fuel_efficiency_l_per_100km !== null ? `${v.fuel_efficiency_l_per_100km.toFixed(1)} L/100km` : "—"}
                                                        </span>
                                                    </div>

                                                    <div className="rounded-xl bg-slate-50/60 p-2">
                                                        <span className="block text-[10px] font-bold text-slate-400 uppercase">Cost / km</span>
                                                        <span className="font-bold text-slate-700">
                                                            {v.fuel_cost_per_km !== null ? `AED ${v.fuel_cost_per_km.toFixed(2)}` : "—"}
                                                        </span>
                                                    </div>

                                                    <div className="rounded-xl bg-slate-50/60 p-2">
                                                        <span className="block text-[10px] font-bold text-slate-400 uppercase">Fuel</span>
                                                        <span className="font-bold text-slate-900">
                                                            {Math.round(v.litres).toLocaleString()} L
                                                        </span>
                                                    </div>

                                                    <div className="rounded-xl bg-slate-50/60 p-2">
                                                        <span className="block text-[10px] font-bold text-slate-400 uppercase">Distance</span>
                                                        <span className="font-bold text-sky-700">
                                                            {v.distance_km !== null ? `${Math.round(v.distance_km).toLocaleString()} km` : "—"}
                                                        </span>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    )
                                })
                            )}
                        </div>

                        {/* ─── SECTION 4: PAGINATION CONTROLS ───────────────────────── */}
                        {vehiclesData.total > 0 && (
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-2xl border border-slate-200/80 bg-white px-5 py-3.5 shadow-xs">
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
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
