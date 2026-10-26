import { useState, useMemo } from "react"
import { AlertTriangle, ArrowUpRight, Flame, Clock, Fuel, Search } from "lucide-react"

function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    })
}

export default function DrivingVsIdlingChart({
    efficiencyData,
    vehicles = [],
    loading,
    idleRate = 2.0,
    onSelectVehicle,
}) {
    const [viewMode, setViewMode] = useState("overview") // "overview" | "table"
    const [searchQuery, setSearchQuery] = useState("")
    const [tableSortBy, setTableSortBy] = useState("idle_fuel") // "idle_fuel" | "idle_cost" | "idle_hours" | "driving_hours" | "distance" | "efficiency"
    const [tableSortOrder, setTableSortOrder] = useState("desc")
    const [tablePage, setTablePage] = useState(1)
    const pageSize = 10
    const safeIdleRate = typeof idleRate === "number" && !isNaN(idleRate) ? idleRate : 2.0
    const drivingFuel = efficiencyData?.estimated_driving_fuel_litres
    const idleFuel = efficiencyData?.estimated_idle_fuel_litres
    const drivingHours = efficiencyData?.total_driving_hours
    const idleHours = efficiencyData?.total_idle_hours
    const drivingPct = efficiencyData?.estimated_driving_percentage
    const idlePct = efficiencyData?.estimated_idle_percentage
    const idleCost = efficiencyData?.estimated_idle_cost

    // Identify top idle wasters across vehicles
    const topIdleWasters = useMemo(() => {
        if (!vehicles || vehicles.length === 0) return []
        return [...vehicles]
            .filter((v) => v.estimated_idle_fuel_litres && v.estimated_idle_fuel_litres > 0)
            .sort((a, b) => (b.estimated_idle_fuel_litres || 0) - (a.estimated_idle_fuel_litres || 0))
            .slice(0, 5)
    }, [vehicles])

    const totalHours = (drivingHours || 0) + (idleHours || 0)
    const totalFuel = (drivingFuel || 0) + (idleFuel || 0)

    const hoursDrivingPct = totalHours > 0 ? ((drivingHours || 0) / totalHours) * 100 : (drivingPct || 0)
    const hoursIdlePct = totalHours > 0 ? ((idleHours || 0) / totalHours) * 100 : (idlePct || 0)

    const fuelDrivingPct = totalFuel > 0 ? ((drivingFuel || 0) / totalFuel) * 100 : (drivingPct || 0)
    const fuelIdlePct = totalFuel > 0 ? ((idleFuel || 0) / totalFuel) * 100 : (idlePct || 0)

    // Filter and sort vehicles for the per-vehicle comparison table
    const filteredVehicles = useMemo(() => {
        if (!vehicles) return []
        let result = vehicles.filter((v) => {
            const query = searchQuery.trim().toLowerCase()
            if (!query) return true
            return (
                (v.vehicle_identifier && v.vehicle_identifier.toLowerCase().includes(query)) ||
                (v.wialon_unit_name && v.wialon_unit_name.toLowerCase().includes(query))
            )
        })

        result.sort((a, b) => {
            let valA
            let valB
            switch (tableSortBy) {
                case "idle_fuel":
                    valA = a.estimated_idle_fuel_litres || 0
                    valB = b.estimated_idle_fuel_litres || 0
                    break
                case "idle_cost":
                    valA = a.estimated_idle_cost || 0
                    valB = b.estimated_idle_cost || 0
                    break
                case "idle_hours":
                    valA = a.idle_duration_hours || 0
                    valB = b.idle_duration_hours || 0
                    break
                case "driving_hours":
                    valA = a.driving_duration_hours || 0
                    valB = b.driving_duration_hours || 0
                    break
                case "distance":
                    valA = a.distance_km || 0
                    valB = b.distance_km || 0
                    break
                case "efficiency": {
                    const effA = a.fuel_efficiency_l_per_100km != null ? (100 / a.fuel_efficiency_l_per_100km) : 0
                    const effB = b.fuel_efficiency_l_per_100km != null ? (100 / b.fuel_efficiency_l_per_100km) : 0
                    valA = effA
                    valB = effB
                    break
                }
                default:
                    valA = a.estimated_idle_fuel_litres || 0
                    valB = b.estimated_idle_fuel_litres || 0
            }
            return tableSortOrder === "desc" ? valB - valA : valA - valB
        })

        return result
    }, [vehicles, searchQuery, tableSortBy, tableSortOrder])

    const totalTablePages = Math.max(1, Math.ceil(filteredVehicles.length / pageSize))
    const pagedVehicles = useMemo(() => {
        const start = (tablePage - 1) * pageSize
        return filteredVehicles.slice(start, start + pageSize)
    }, [filteredVehicles, tablePage, pageSize])

    if (loading) {
        return (
            <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-xs">
                <div className="mb-3 h-4 w-48 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    <div className="h-44 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                    <div className="h-44 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                </div>
            </div>
        )
    }

    return (
        <section aria-labelledby="driving-vs-idling-heading" className="rounded-lg border border-slate-200 bg-white p-4 shadow-xs">
            {/* Header & Controls */}
            <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between border-b border-slate-100 pb-3 mb-3.5">
                <div>
                    <div className="flex items-center gap-2">
                        <h3 id="driving-vs-idling-heading" className="type-section-title text-slate-900">
                            Driving vs. Idling Operational Analysis
                        </h3>
                        {idleCost !== null && idleCost !== undefined && idleCost > 0 && (
                            <span className="inline-flex items-center gap-1 rounded bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] font-semibold text-amber-900">
                                <Flame className="h-3 w-3 text-amber-600" />
                                <span>AED {formatCurrency(idleCost)} estimated loss</span>
                            </span>
                        )}
                    </div>
                    <p className="text-xs text-slate-500 mt-0.5">
                        Operational engine duration and estimated stationary fuel consumption modeling across fleet units
                    </p>
                </div>

                {/* View Mode Toggle: Overview vs Detailed Table */}
                <div role="group" aria-label="Driving vs idling view" className="inline-flex rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                    <button
                        type="button"
                        onClick={() => setViewMode("overview")}
                        aria-pressed={viewMode === "overview"}
                        className={`rounded px-2.5 py-1 transition-colors cursor-pointer ${
                            viewMode === "overview"
                                ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]"
                                : "text-slate-600 hover:text-slate-900"
                        }`}
                    >
                        Fleet Distribution
                    </button>
                    <button
                        type="button"
                        onClick={() => setViewMode("table")}
                        aria-pressed={viewMode === "table"}
                        className={`rounded px-2.5 py-1 transition-colors cursor-pointer ${
                            viewMode === "table"
                                ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]"
                                : "text-slate-600 hover:text-slate-900"
                        }`}
                    >
                        Per-Vehicle Comparison
                    </button>
                </div>
            </div>

            {/* View 1: Fleet Overview Visualizations & Top Wasters */}
            {viewMode === "overview" && (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
                    {/* ── Left Column: Comparative Distribution Bars ── */}
                    <div className="lg:col-span-7 flex flex-col justify-between space-y-3.5">
                        {/* Time Comparison Bar */}
                        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3.5">
                            <div className="flex items-center justify-between text-xs font-medium text-slate-700 mb-2">
                                <span className="flex items-center gap-1.5">
                                    <Clock className="h-3.5 w-3.5 text-slate-400" />
                                    <span className="font-semibold text-slate-900">Engine Duration Split</span>
                                    <span className="text-[10px] text-slate-400 font-mono">(Recorded Wialon)</span>
                                </span>
                                <span className="text-[11px] tabular-nums text-slate-600">
                                    {drivingHours != null ? `${Number(drivingHours).toFixed(1)}h driving` : "—"}
                                    {" · "}
                                    {idleHours != null ? `${Number(idleHours).toFixed(1)}h idling` : "—"}
                                </span>
                            </div>

                            {/* Stacked comparison bar */}
                            <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-200">
                                <div
                                    style={{ width: `${Math.max(hoursDrivingPct, 0)}%` }}
                                    className="bg-[#33758F] transition-[width] duration-300"
                                    title={`Driving: ${drivingHours != null ? Number(drivingHours).toFixed(1) + 'h' : '—'} (${Math.round(hoursDrivingPct)}%)`}
                                />
                                <div
                                    style={{ width: `${Math.max(hoursIdlePct, 0)}%` }}
                                    className="bg-[#D97706] transition-[width] duration-300"
                                    title={`Idling: ${idleHours != null ? Number(idleHours).toFixed(1) + 'h' : '—'} (${Math.round(hoursIdlePct)}%)`}
                                />
                            </div>

                            <div className="flex items-center justify-between mt-2 text-[11px]">
                                <span className="inline-flex items-center gap-1.5 text-[#33758F] font-medium">
                                    <span className="h-2 w-2 rounded-full bg-[#33758F]" />
                                    Active Driving Motion: {Math.round(hoursDrivingPct)}%
                                </span>
                                <span className="inline-flex items-center gap-1.5 text-[#D97706] font-medium">
                                    <span className="h-2 w-2 rounded-full bg-[#D97706]" />
                                    Stationary Idling: {Math.round(hoursIdlePct)}%
                                </span>
                            </div>
                        </div>

                        {/* Fuel Consumption Comparison Bar */}
                        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3.5">
                            <div className="flex items-center justify-between text-xs font-medium text-slate-700 mb-2">
                                <span className="flex items-center gap-1.5">
                                    <Fuel className="h-3.5 w-3.5 text-slate-400" />
                                    <span className="font-semibold text-slate-900">Estimated Fuel Allocation</span>
                                    <span className="font-mono text-[10px] text-slate-400">(Estimated @ {safeIdleRate.toFixed(1)} L/h)</span>
                                </span>
                                <span className="text-[11px] tabular-nums text-slate-600">
                                    {drivingFuel != null ? `${Math.round(drivingFuel).toLocaleString()} L driving` : "—"}
                                    {" · "}
                                    {idleFuel != null ? `${Math.round(idleFuel).toLocaleString()} L idle waste` : "—"}
                                </span>
                            </div>

                            {/* Stacked comparison bar */}
                            <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-200">
                                <div
                                    style={{ width: `${Math.max(fuelDrivingPct, 0)}%` }}
                                    className="bg-[#33758F] transition-[width] duration-300"
                                    title={`Driving Fuel: ${drivingFuel ? Math.round(drivingFuel) + ' L' : '—'} (${Math.round(fuelDrivingPct)}%)`}
                                />
                                <div
                                    style={{ width: `${Math.max(fuelIdlePct, 0)}%` }}
                                    className="bg-[#D97706] transition-[width] duration-300"
                                    title={`Idle Waste Fuel: ${idleFuel ? Math.round(idleFuel) + ' L' : '—'} (${Math.round(fuelIdlePct)}%)`}
                                />
                            </div>

                            <div className="mt-2 flex items-center justify-between text-[11px]">
                                <span className="inline-flex items-center gap-1.5 font-medium text-[#33758F]">
                                    <span className="h-2 w-2 rounded-full bg-[#33758F]" />
                                    Productive Driving Fuel: {Math.round(fuelDrivingPct)}%
                                </span>
                                <span className="inline-flex items-center gap-1.5 font-medium text-[#D97706]">
                                    <span className="h-2 w-2 rounded-full bg-[#D97706]" />
                                    Stationary Idle Waste: {Math.round(fuelIdlePct)}%
                                </span>
                            </div>
                        </div>

                        {/* Subtle footnote */}
                        <p className="pl-0.5 text-[11px] leading-normal text-slate-400">
                            Driving & idling durations are directly recorded by Wialon telematics. Idle fuel volume and financial cost are estimated using the active {safeIdleRate.toFixed(1)} L/h rate parameter.
                        </p>
                    </div>

                    {/* ── Right Column: Top 5 Highest Idle Fuel Wasters ── */}
                    <div className="lg:col-span-5 rounded-lg border border-slate-200 bg-white p-3.5">
                        <div className="flex items-center justify-between border-b border-slate-100 pb-2 mb-2">
                            <span className="text-xs font-semibold text-slate-900 flex items-center gap-1.5">
                                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                                Highest Idle Fuel Wasters
                            </span>
                            <span className="text-[11px] font-mono text-slate-400">Rate: {idleRate.toFixed(1)} L/h</span>
                        </div>

                        {topIdleWasters.length === 0 ? (
                            <p className="py-8 text-center text-xs text-slate-400">
                                No vehicles with recorded idle fuel waste in this period.
                            </p>
                        ) : (
                            <div className="divide-y divide-slate-100">
                                {topIdleWasters.map((v, idx) => (
                                    <div
                                        key={v.vehicle_identifier}
                                        className="flex items-center justify-between py-2 text-xs transition-colors hover:bg-slate-50/70 px-1 rounded"
                                    >
                                        <div className="flex items-center gap-2.5 min-w-0">
                                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-50 font-mono text-[10px] font-semibold text-amber-800">
                                                #{idx + 1}
                                            </span>
                                            <div className="min-w-0">
                                                <button
                                                    type="button"
                                                    onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                    className="font-mono text-xs font-semibold text-slate-900 hover:text-brand truncate text-left cursor-pointer"
                                                    title={`Inspect ${v.vehicle_identifier}`}
                                                >
                                                    {v.vehicle_identifier}
                                                </button>
                                                <span className="block text-[11px] text-slate-400 truncate">
                                                    {v.wialon_unit_name || `${v.idle_duration_hours ? v.idle_duration_hours.toFixed(1) + 'h idle' : '—'}`}
                                                </span>
                                            </div>
                                        </div>

                                        <div className="flex items-center gap-2.5 shrink-0 text-right">
                                            <div>
                                                <span className="block font-semibold tabular-nums text-amber-800">
                                                    {v.estimated_idle_fuel_litres ? `${v.estimated_idle_fuel_litres.toFixed(1)} L` : "—"}
                                                </span>
                                                <span className="block text-[10px] tabular-nums text-slate-500">
                                                    {v.estimated_idle_cost ? `AED ${Math.round(v.estimated_idle_cost).toLocaleString()}` : (v.idle_duration_hours ? `${v.idle_duration_hours.toFixed(1)} h` : "—")}
                                                </span>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                className="p-1 text-slate-400 hover:text-brand hover:bg-slate-100 rounded cursor-pointer"
                                                title={`Inspect ${v.vehicle_identifier}`}
                                                aria-label={`Inspect ${v.vehicle_identifier}`}
                                            >
                                                <ArrowUpRight className="h-3.5 w-3.5" />
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* View 2: Searchable Per-Vehicle Driving vs. Idling Comparison Table */}
            {viewMode === "table" && (
                <div className="space-y-3">
                    {/* Table Toolbar */}
                    <div className="flex flex-wrap items-center justify-between gap-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                            {/* Search box */}
                            <div className="relative">
                                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                                <input
                                    type="text"
                                    value={searchQuery}
                                    onChange={(e) => {
                                        setSearchQuery(e.target.value)
                                        setTablePage(1)
                                    }}
                                    placeholder="Search vehicle or unit…"
                                    className="h-8 w-52 rounded-md border border-slate-200 bg-white pl-8 pr-2.5 text-xs text-slate-800 placeholder-slate-400 focus:border-brand focus:outline-hidden"
                                />
                            </div>

                            {/* Sort Selector */}
                            <div className="flex items-center gap-1.5 text-xs">
                                <span className="text-slate-500">Sort:</span>
                                <select
                                    value={tableSortBy}
                                    onChange={(e) => setTableSortBy(e.target.value)}
                                    className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-800 cursor-pointer focus:border-brand focus:outline-hidden"
                                >
                                    <option value="idle_fuel">Idle Fuel Waste (L)</option>
                                    <option value="idle_cost">Idle Cost (AED)</option>
                                    <option value="idle_hours">Idling Duration (h)</option>
                                    <option value="driving_hours">Driving Duration (h)</option>
                                    <option value="distance">Distance (km)</option>
                                    <option value="efficiency">Fuel Efficiency (km/L)</option>
                                </select>

                                <button
                                    type="button"
                                    onClick={() => setTableSortOrder((o) => (o === "desc" ? "asc" : "desc"))}
                                    className="h-8 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
                                    title="Toggle sort direction"
                                >
                                    {tableSortOrder === "desc" ? "High → Low" : "Low → High"}
                                </button>
                            </div>
                        </div>

                        <span className="text-xs text-slate-500 tabular-nums">
                            {filteredVehicles.length} vehicles found
                        </span>
                    </div>

                    {/* Comparison Table */}
                    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
                        <table className="w-full min-w-[860px] text-left text-xs">
                            <thead className="border-b border-slate-200 bg-slate-50 text-[11px] text-slate-500">
                                <tr>
                                    <th className="min-w-[140px] px-3 py-2.5 font-medium">Vehicle</th>
                                    <th className="px-3 py-2.5 text-right font-medium">Driving Time</th>
                                    <th className="px-3 py-2.5 text-right font-medium">Idling Time</th>
                                    <th className="min-w-[160px] px-3 py-2.5 font-medium">Split</th>
                                    <th className="px-3 py-2.5 text-right font-medium">Est. Idle Fuel</th>
                                    <th className="px-3 py-2.5 text-right font-medium">Est. Idle Cost</th>
                                    <th className="px-3 py-2.5 text-right font-medium">Distance</th>
                                    <th className="px-3 py-2.5 text-right font-medium">Efficiency</th>
                                    <th className="w-16 px-3 py-2.5 text-center font-medium">Action</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 text-[11px]">
                                {pagedVehicles.length === 0 ? (
                                    <tr>
                                        <td colSpan={9} className="py-8 text-center text-slate-400">
                                            No vehicles matching search query.
                                        </td>
                                    </tr>
                                ) : (
                                    pagedVehicles.map((v) => {
                                        const drvH = v.driving_duration_hours !== null && v.driving_duration_hours !== undefined ? v.driving_duration_hours : null
                                        const idlH = v.idle_duration_hours !== null && v.idle_duration_hours !== undefined ? v.idle_duration_hours : null
                                        const totalH = (drvH || 0) + (idlH || 0)
                                        const drvPct = totalH > 0 && drvH !== null ? Math.round((drvH / totalH) * 100) : null
                                        const idlPct = totalH > 0 && idlH !== null ? (100 - drvPct) : null

                                        const kmPerL = v.distance_km && v.litres && v.litres > 0
                                            ? (v.distance_km / v.litres)
                                            : (v.fuel_efficiency_l_per_100km ? 100 / v.fuel_efficiency_l_per_100km : null)

                                        return (
                                            <tr key={v.vehicle_identifier} className="hover:bg-slate-50/80 transition-colors">
                                                <td className="px-3 py-2 whitespace-nowrap">
                                                    <button
                                                        type="button"
                                                        onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                        className="font-mono font-semibold text-slate-900 hover:text-brand cursor-pointer text-left block"
                                                    >
                                                        {v.vehicle_identifier}
                                                    </button>
                                                    {v.wialon_unit_name && (
                                                        <span className="text-[10px] text-slate-400 block truncate max-w-[140px]">
                                                            {v.wialon_unit_name}
                                                        </span>
                                                    )}
                                                </td>

                                                <td className="px-3 py-2 text-right tabular-nums">
                                                    <span className="font-medium text-slate-900">
                                                        {drvH != null ? `${Number(drvH).toFixed(1)} h` : "—"}
                                                    </span>
                                                </td>

                                                <td className="px-3 py-2 text-right tabular-nums">
                                                    <span className="font-medium text-amber-800">
                                                        {idlH != null ? `${Number(idlH).toFixed(1)} h` : "—"}
                                                    </span>
                                                </td>

                                                <td className="px-3 py-2">
                                                    {drvPct !== null && idlPct !== null ? (
                                                        <div className="space-y-0.5">
                                                            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 flex">
                                                                <div
                                                                    style={{ width: `${Math.max(drvPct, 0)}%` }}
                                                                    className="bg-[#33758F]"
                                                                />
                                                                <div
                                                                    style={{ width: `${Math.max(idlPct, 0)}%` }}
                                                                    className="bg-[#D97706]"
                                                                />
                                                            </div>
                                                            <div className="flex justify-between text-[9px] tabular-nums text-slate-500 font-mono">
                                                                <span>{drvPct}% drv</span>
                                                                <span>{idlPct}% idle</span>
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <span className="text-slate-400 text-[10px]">—</span>
                                                    )}
                                                </td>

                                                <td className="px-3 py-2 text-right tabular-nums font-semibold text-amber-800">
                                                    {v.estimated_idle_fuel_litres != null ? `${v.estimated_idle_fuel_litres.toFixed(1)} L` : "—"}
                                                </td>

                                                <td className="px-3 py-2 text-right tabular-nums font-medium text-slate-900">
                                                    {v.estimated_idle_cost != null ? `AED ${formatCurrency(v.estimated_idle_cost)}` : "—"}
                                                </td>

                                                <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                                                    {v.distance_km != null ? `${Math.round(v.distance_km).toLocaleString()} km` : "—"}
                                                </td>

                                                <td className="px-3 py-2 text-right tabular-nums">
                                                    {kmPerL != null ? (
                                                        <div>
                                                            <span className="block font-semibold text-slate-900">
                                                                {Number(kmPerL).toFixed(2)} km/L
                                                            </span>
                                                            <span className="block text-[10px] text-slate-400">
                                                                {v.fuel_efficiency_l_per_100km != null ? `${Number(v.fuel_efficiency_l_per_100km).toFixed(1)} L/100` : "—"}
                                                            </span>
                                                        </div>
                                                    ) : (
                                                        <span className="text-slate-400">—</span>
                                                    )}
                                                </td>

                                                <td className="px-3 py-2 text-center">
                                                    <button
                                                        type="button"
                                                        onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                        className="inline-flex h-6 items-center gap-0.5 rounded border border-slate-200 bg-white px-2 text-[10px] font-medium text-slate-700 hover:border-brand hover:text-brand cursor-pointer"
                                                        title={`Inspect ${v.vehicle_identifier}`}
                                                    >
                                                        <span>Inspect</span>
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

                    {/* Pagination */}
                    {totalTablePages > 1 && (
                        <div className="flex items-center justify-between text-xs text-slate-500 pt-1">
                            <span>
                                Page {tablePage} of {totalTablePages}
                            </span>
                            <div className="flex items-center gap-1.5">
                                <button
                                    type="button"
                                    disabled={tablePage <= 1}
                                    onClick={() => setTablePage((p) => Math.max(1, p - 1))}
                                    className="h-7 rounded border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 disabled:opacity-50 cursor-pointer"
                                >
                                    Previous
                                </button>
                                <button
                                    type="button"
                                    disabled={tablePage >= totalTablePages}
                                    onClick={() => setTablePage((p) => Math.min(totalTablePages, p + 1))}
                                    className="h-7 rounded border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 disabled:opacity-50 cursor-pointer"
                                >
                                    Next
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </section>
    )
}

