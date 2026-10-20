import { useState, useEffect, useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Truck,
    Search,
    Fuel,
    DollarSign,
    Route,
    Gauge,
    Receipt,
    Calendar,
    AlertCircle,
    TrendingUp,
    MapPin,
    User,
    Loader2,
} from "lucide-react"
import {
    ResponsiveContainer,
    AreaChart,
    Area,
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
} from "recharts"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { useFuelFilters } from "../context/FuelFilterContext"
import {
    getVehicleEfficiencyList,
    getEfficiencyTrend,
    getFuelTransactions,
} from "../api/fleetApi"
import { formatTrendPoint } from "../components/Fuel/telemetryFormat"
import { fixedOrDash, fuelTypeLabel, isRefundAdjustment } from "../components/Fuel/fuelLabels"
import FuelHeader from "../components/Fuel/FuelHeader"
import VehicleStatusBadge from "../components/Fuel/VehicleStatusBadge"

export default function FuelVehicles() {
    const [searchParams, setSearchParams] = useSearchParams()
    const { queryParams, periodLabel } = useFuelFilters()

    const [vehicleList, setVehicleList] = useState([])
    const [selectedVehicle, setSelectedVehicle] = useState(null)
    const [vehicleSearch, setVehicleSearch] = useState("")
    const [loadingList, setLoadingList] = useState(true)
    const [listError, setListError] = useState(null)

    // Vehicle specific telemetry and transactions
    const [vehicleTrend, setVehicleTrend] = useState([])
    const [transactions, setTransactions] = useState([])
    const [transactionsTotal, setTransactionsTotal] = useState(0)
    const [loadingDetails, setLoadingDetails] = useState(false)
    const [detailError, setDetailError] = useState(null)
    const [trendMetric, setTrendMetric] = useState("fuel") // 'fuel' | 'cost' | 'distance'
    const [trendInterval, setTrendInterval] = useState("day")

    // 1. Fetch all vehicles for the searchable list
    useEffect(() => {
        let active = true
        async function loadVehicles() {
            try {
                setLoadingList(true)
                setListError(null)
                const res = await getVehicleEfficiencyList({
                    page: 1,
                    page_size: 250,
                    sort_by: "litres",
                    sort_order: "desc",
                    ...queryParams,
                })
                if (!active) return
                const list = res?.data || []
                setVehicleList(list)

                // Check URL query param for default selection
                const initialPlate = searchParams.get("select")
                const byId = (id) => (id ? list.find((v) => v.vehicle_identifier.toLowerCase() === id.toLowerCase()) : null)
                // Re-resolve the selection from the new list so its KPI cards match the current period;
                // a vehicle absent from the new period falls back to the first one (or none).
                setSelectedVehicle((prev) => byId(initialPlate) || byId(prev?.vehicle_identifier) || list[0] || null)
            } catch (err) {
                if (!active) return
                setVehicleList([])
                setSelectedVehicle(null)
                setListError(err?.message || "Could not load vehicles.")
            } finally {
                if (active) setLoadingList(false)
            }
        }

        loadVehicles()
        return () => { active = false }
    }, [queryParams])

    // 2. Fetch specific vehicle trend & transactions on vehicle selection
    useEffect(() => {
        if (!selectedVehicle?.vehicle_identifier) return
        let active = true

        async function loadVehicleTelemetry() {
            try {
                setLoadingDetails(true)
                setDetailError(null)
                // Never show the previous vehicle's rows under the newly selected vehicle.
                setVehicleTrend([])
                setTransactions([])
                setTransactionsTotal(0)
                const [trendRes, txRes] = await Promise.allSettled([
                    // Global filters first, so the selected vehicle is never overwritten by them
                    getEfficiencyTrend({
                        ...queryParams,
                        vehicle_identifier: selectedVehicle.vehicle_identifier,
                        group_by: trendInterval,
                    }),
                    getFuelTransactions({
                        ...queryParams,
                        vehicle_identifier: selectedVehicle.vehicle_identifier,
                        page: 1,
                        page_size: 15,
                    }),
                ])

                if (!active) return

                const failures = []
                if (trendRes.status === "fulfilled" && Array.isArray(trendRes.value?.data)) {
                    setVehicleTrend(trendRes.value.data)
                } else {
                    failures.push("trend")
                }
                if (txRes.status === "fulfilled" && Array.isArray(txRes.value?.transactions)) {
                    setTransactions(txRes.value.transactions)
                    setTransactionsTotal(Number(txRes.value.total) || txRes.value.transactions.length)
                } else {
                    failures.push("transactions")
                }
                if (failures.length) {
                    const reason = [trendRes, txRes].find((r) => r.status === "rejected")?.reason?.message
                    setDetailError(`Could not load vehicle ${failures.join(" and ")}${reason ? `: ${reason}` : "."}`)
                }
            } catch (err) {
                if (active) setDetailError(err?.message || "Could not load vehicle details.")
            } finally {
                if (active) setLoadingDetails(false)
            }
        }

        loadVehicleTelemetry()
        return () => { active = false }
    }, [selectedVehicle?.vehicle_identifier, trendInterval, queryParams])

    const filteredVehicles = useMemo(() => {
        if (!vehicleSearch.trim()) return vehicleList
        const q = vehicleSearch.toLowerCase().trim()
        return vehicleList.filter((v) =>
            v.vehicle_identifier.toLowerCase().includes(q) ||
            (v.wialon_unit_name && v.wialon_unit_name.toLowerCase().includes(q))
        )
    }, [vehicleList, vehicleSearch])

    // Unknown Wialon distance stays null (a gap), never 0 km
    const formattedTrendData = vehicleTrend.map(formatTrendPoint)

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-white text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex-1 overflow-y-auto px-6 lg:px-8 py-8">
                        {/* Header */}
                        <FuelHeader
                            title="Vehicle Telemetry & Performance"
                            subtitle={`Individual Vehicle Analytics · ${periodLabel}`}
                        />

                        {/* Split layout: Vehicle List + Detailed View */}
                        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
                            {/* ─── LEFT PANE: Searchable Vehicle Selector (4 cols) ──── */}
                            <div className="lg:col-span-4 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs flex flex-col h-[750px]">
                                <div className="pb-3 border-b border-slate-100">
                                    <h3 className="text-sm font-bold text-slate-900 mb-2 flex items-center gap-2">
                                        <Truck className="h-4 w-4 text-[#33758F]" />
                                        Fleet Vehicles ({filteredVehicles.length})
                                    </h3>
                                    <div className="relative">
                                        <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
                                        <input
                                            type="text"
                                            placeholder="Filter by plate number…"
                                            value={vehicleSearch}
                                            onChange={(e) => setVehicleSearch(e.target.value)}
                                            className="w-full rounded-xl border border-slate-200 bg-slate-50/50 pl-9 pr-3 py-2 text-xs font-semibold text-slate-800 placeholder-slate-400 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                                        />
                                    </div>
                                </div>

                                {/* Vehicle list items */}
                                <div className="flex-1 overflow-y-auto divide-y divide-slate-100 py-2 pr-1 space-y-1">
                                    {loadingList ? (
                                        <div className="py-12 text-center text-xs text-slate-400">
                                            <Loader2 className="mx-auto h-5 w-5 animate-spin text-[#33758F] mb-2" />
                                            Loading vehicles…
                                        </div>
                                    ) : listError ? (
                                        <div role="alert" className="py-12 px-3 text-center text-xs text-rose-600">
                                            {listError}
                                        </div>
                                    ) : filteredVehicles.length === 0 ? (
                                        <div className="py-12 text-center text-xs text-slate-400">
                                            No vehicles match your search
                                        </div>
                                    ) : (
                                        filteredVehicles.map((v) => {
                                            const isSelected = selectedVehicle?.vehicle_identifier === v.vehicle_identifier
                                            return (
                                                <button
                                                    key={v.vehicle_identifier}
                                                    type="button"
                                                    onClick={() => {
                                                        setSelectedVehicle(v)
                                                        setSearchParams({ select: v.vehicle_identifier })
                                                    }}
                                                    className={`w-full text-left rounded-xl p-3 transition-colors cursor-pointer ${
                                                        isSelected
                                                            ? "bg-[#33758F]/10 border border-[#33758F]/30"
                                                            : "hover:bg-slate-50 border border-transparent"
                                                    }`}
                                                >
                                                    <div className="flex items-center justify-between mb-1">
                                                        <span className="font-mono text-xs font-black text-slate-900">
                                                            {v.vehicle_identifier}
                                                        </span>
                                                        <VehicleStatusBadge status={v.status} />
                                                    </div>
                                                    <div className="flex items-center justify-between text-[11px] text-slate-500">
                                                        <span>{Math.round(v.litres).toLocaleString()} L</span>
                                                        <span className="font-bold text-slate-700">
                                                            {v.fuel_efficiency_l_per_100km !== null ? `${v.fuel_efficiency_l_per_100km.toFixed(1)} L/100km` : "—"}
                                                        </span>
                                                    </div>
                                                </button>
                                            )
                                        })
                                    )}
                                </div>
                            </div>

                            {/* ─── RIGHT PANE: Selected Vehicle Performance (8 cols) ── */}
                            <div className="lg:col-span-8 space-y-6">
                                {selectedVehicle ? (
                                    <>
                                        {/* Vehicle Header Card */}
                                        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
                                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-4 border-b border-slate-100">
                                                <div>
                                                    <div className="flex items-center gap-2">
                                                        <h2 className="font-mono text-xl font-black text-slate-900">
                                                            {selectedVehicle.vehicle_identifier}
                                                        </h2>
                                                        <VehicleStatusBadge status={selectedVehicle.status} />
                                                    </div>
                                                    <p className="text-xs text-slate-500 mt-1">
                                                        {selectedVehicle.wialon_unit_name
                                                            ? `Resolved GPS Unit: ${selectedVehicle.wialon_unit_name} (ID: ${selectedVehicle.wialon_unit_id})`
                                                            : "Fuel transaction record with untracked GPS telemetry"}
                                                    </p>
                                                </div>

                                                <div className="rounded-xl bg-slate-100/80 px-3 py-1.5 text-right text-xs">
                                                    <span className="text-[10px] uppercase font-bold text-slate-400 block">Period</span>
                                                    <span className="font-bold text-slate-700">{periodLabel}</span>
                                                </div>
                                            </div>

                                            {/* 6 Key Telemetry Cards */}
                                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 pt-4">
                                                {/* Fuel */}
                                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                                        Fuel Consumed
                                                    </span>
                                                    <div className="text-lg font-black text-slate-900">
                                                        {Number(selectedVehicle.litres).toLocaleString(undefined, { maximumFractionDigits: 1 })}
                                                        <span className="text-xs font-bold text-slate-400 ml-1">L</span>
                                                    </div>
                                                </div>

                                                {/* Cost */}
                                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                                        Fuel Expenditure
                                                    </span>
                                                    <div className="text-lg font-black text-emerald-700">
                                                        AED {Number(selectedVehicle.total_cost).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                                    </div>
                                                </div>

                                                {/* Distance */}
                                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                                        Distance Traveled
                                                    </span>
                                                    <div className="text-lg font-black text-sky-700">
                                                        {selectedVehicle.distance_km !== null
                                                            ? `${Number(selectedVehicle.distance_km).toLocaleString(undefined, { maximumFractionDigits: 1 })} km`
                                                            : "—"}
                                                    </div>
                                                </div>

                                                {/* Efficiency */}
                                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                                        Fuel Efficiency
                                                    </span>
                                                    <div className="text-lg font-black text-[#33758F]">
                                                        {selectedVehicle.fuel_efficiency_l_per_100km !== null
                                                            ? `${selectedVehicle.fuel_efficiency_l_per_100km.toFixed(1)} L/100km`
                                                            : "—"}
                                                    </div>
                                                </div>

                                                {/* Cost Per Km */}
                                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                                        Cost Per km
                                                    </span>
                                                    <div className="text-lg font-black text-slate-800">
                                                        {selectedVehicle.fuel_cost_per_km !== null
                                                            ? `AED ${selectedVehicle.fuel_cost_per_km.toFixed(2)}`
                                                            : "—"}
                                                    </div>
                                                </div>

                                                {/* Refuel Count */}
                                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                                        Transactions
                                                    </span>
                                                    <div className="text-lg font-black text-slate-900">
                                                        {selectedVehicle.transaction_count}
                                                        <span className="text-xs font-bold text-slate-400 ml-1">refuels</span>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Telemetry Charts Over Time */}
                                        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
                                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-4 border-b border-slate-100">
                                                <div>
                                                    <h3 className="text-sm font-bold text-slate-900">
                                                        Telemetry Trends Over Time
                                                    </h3>
                                                    <p className="text-[11px] text-slate-500">
                                                        Historical progression for {selectedVehicle.vehicle_identifier}
                                                    </p>
                                                </div>

                                                <div className="flex items-center gap-2">
                                                    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 text-xs font-semibold">
                                                        <button
                                                            type="button"
                                                            onClick={() => setTrendMetric("fuel")}
                                                            className={`rounded-lg px-2.5 py-1 cursor-pointer transition-colors ${
                                                                trendMetric === "fuel" ? "bg-white text-[#33758F] font-bold shadow-xs" : "text-slate-600"
                                                            }`}
                                                        >
                                                            Fuel (L)
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => setTrendMetric("distance")}
                                                            className={`rounded-lg px-2.5 py-1 cursor-pointer transition-colors ${
                                                                trendMetric === "distance" ? "bg-white text-sky-700 font-bold shadow-xs" : "text-slate-600"
                                                            }`}
                                                        >
                                                            Distance (km)
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => setTrendMetric("cost")}
                                                            className={`rounded-lg px-2.5 py-1 cursor-pointer transition-colors ${
                                                                trendMetric === "cost" ? "bg-white text-emerald-700 font-bold shadow-xs" : "text-slate-600"
                                                            }`}
                                                        >
                                                            Cost (AED)
                                                        </button>
                                                    </div>

                                                    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 text-xs font-semibold">
                                                        {["day", "week"].map((mode) => (
                                                            <button
                                                                key={mode}
                                                                type="button"
                                                                onClick={() => setTrendInterval(mode)}
                                                                className={`capitalize rounded-lg px-2 py-1 cursor-pointer transition-colors ${
                                                                    trendInterval === mode ? "bg-white text-slate-900 font-bold shadow-xs" : "text-slate-600"
                                                                }`}
                                                            >
                                                                {mode}
                                                            </button>
                                                        ))}
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="pt-4 h-[240px] w-full">
                                                {loadingDetails ? (
                                                    <div className="h-full flex items-center justify-center text-slate-400 text-xs">
                                                        <Loader2 className="h-5 w-5 animate-spin text-[#33758F] mr-2" />
                                                        Loading vehicle telemetry…
                                                    </div>
                                                ) : formattedTrendData.length === 0 ? (
                                                    <div className="h-full flex items-center justify-center text-slate-400 text-xs">
                                                        No telemetry points recorded in this period.
                                                    </div>
                                                ) : trendMetric === "fuel" ? (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <AreaChart data={formattedTrendData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                                                            <defs>
                                                                <linearGradient id="vFuelGrad" x1="0" y1="0" x2="0" y2="1">
                                                                    <stop offset="5%" stopColor="#33758F" stopOpacity={0.4} />
                                                                    <stop offset="95%" stopColor="#33758F" stopOpacity={0.0} />
                                                                </linearGradient>
                                                            </defs>
                                                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 10, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                                                            <YAxis tick={{ fontSize: 10, fill: "#64748B" }} tickLine={false} axisLine={false} />
                                                            <Tooltip />
                                                            <Area type="monotone" dataKey="litres" stroke="#33758F" strokeWidth={2} fill="url(#vFuelGrad)" name="Fuel (L)" />
                                                        </AreaChart>
                                                    </ResponsiveContainer>
                                                ) : trendMetric === "distance" ? (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <AreaChart data={formattedTrendData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                                                            <defs>
                                                                <linearGradient id="vDistGrad" x1="0" y1="0" x2="0" y2="1">
                                                                    <stop offset="5%" stopColor="#0284C7" stopOpacity={0.4} />
                                                                    <stop offset="95%" stopColor="#0284C7" stopOpacity={0.0} />
                                                                </linearGradient>
                                                            </defs>
                                                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 10, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                                                            <YAxis tick={{ fontSize: 10, fill: "#64748B" }} tickLine={false} axisLine={false} />
                                                            <Tooltip />
                                                            <Area type="monotone" dataKey="distance_km" stroke="#0284C7" strokeWidth={2} fill="url(#vDistGrad)" name="Distance (km)" />
                                                        </AreaChart>
                                                    </ResponsiveContainer>
                                                ) : (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <BarChart data={formattedTrendData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                                                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 10, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                                                            <YAxis tick={{ fontSize: 10, fill: "#64748B" }} tickLine={false} axisLine={false} />
                                                            <Tooltip />
                                                            <Bar dataKey="cost" fill="#10B981" radius={[4, 4, 0, 0]} name="Cost (AED)" />
                                                        </BarChart>
                                                    </ResponsiveContainer>
                                                )}
                                            </div>
                                        </div>

                                        {/* Recent Transactions List */}
                                        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
                                            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                                                <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                    <Receipt className="h-4 w-4 text-[#33758F]" />
                                                    Recent Refuel Transactions ({transactionsTotal})
                                                </h3>
                                                <span className="text-[11px] text-slate-400 font-medium">
                                                    {transactionsTotal > transactions.length ? `Latest ${transactions.length} shown` : "Logged Receipts"}
                                                </span>
                                            </div>

                                            {detailError && (
                                                <div role="alert" className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                                                    {detailError}
                                                </div>
                                            )}

                                            <div className="divide-y divide-slate-100 pt-2">
                                                {loadingDetails ? (
                                                    <div className="py-6 flex items-center justify-center text-xs text-slate-400">
                                                        <Loader2 className="h-4 w-4 animate-spin text-[#33758F] mr-2" />
                                                        Loading transactions…
                                                    </div>
                                                ) : detailError && transactions.length === 0 ? null : transactions.length === 0 ? (
                                                    <div className="py-6 text-center text-xs text-slate-400">
                                                        No transactions recorded for this period.
                                                    </div>
                                                ) : (
                                                    transactions.map((tx) => (
                                                        <div key={tx.id} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
                                                            <div>
                                                                <div className="font-bold text-slate-800 flex items-center gap-2">
                                                                    <span>{tx.transaction_date?.slice(0, 10)}</span>
                                                                    <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600 font-semibold">
                                                                        {fuelTypeLabel(tx.fuel_type)}
                                                                    </span>
                                                                    {isRefundAdjustment(tx) && (
                                                                        <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-800 font-semibold">
                                                                            Refund adjustment
                                                                        </span>
                                                                    )}
                                                                </div>
                                                                <div className="text-[11px] text-slate-500 mt-0.5 flex items-center gap-3">
                                                                    {tx.station_name && (
                                                                        <span className="flex items-center gap-1">
                                                                            <MapPin className="h-3 w-3 text-slate-400" />
                                                                            {tx.station_name}
                                                                        </span>
                                                                    )}
                                                                    {tx.driver_name && (
                                                                        <span className="flex items-center gap-1">
                                                                            <User className="h-3 w-3 text-slate-400" />
                                                                            {tx.driver_name}
                                                                        </span>
                                                                    )}
                                                                </div>
                                                            </div>

                                                            <div className="flex items-center gap-4 text-right">
                                                                <div>
                                                                    <span className="font-bold text-slate-900">{tx.litres} L</span>
                                                                    <span className="block text-[10px] text-slate-400">
                                                                        {tx.unit_price != null ? `@ AED ${fixedOrDash(tx.unit_price)}/L` : "Unit price not recorded"}
                                                                    </span>
                                                                </div>
                                                                <div className="min-w-[70px]">
                                                                    <span className="font-black text-emerald-700">
                                                                        {tx.total_cost != null ? `AED ${fixedOrDash(tx.total_cost)}` : "—"}
                                                                    </span>
                                                                    <span className="block text-[10px] text-slate-400">Total</span>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    ))
                                                )}
                                            </div>
                                        </div>
                                    </>
                                ) : (
                                    <div className="rounded-2xl border border-slate-200 bg-white p-16 text-center text-slate-400">
                                        <Truck className="mx-auto h-10 w-10 text-slate-300 mb-2" />
                                        <p className="text-sm font-bold text-slate-700">Select a vehicle to inspect performance</p>
                                    </div>
                                )}
                            </div>
                        </div>
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
