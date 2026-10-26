import { useState } from "react"
import {
    ArrowUpRight,
    Loader2,
} from "lucide-react"
import SearchInput from "../Common/SearchInput"
import Pagination from "../Common/Pagination"
import VehicleStatusBadge from "./VehicleStatusBadge"
import { fixedOrDash, sortOrderLabels } from "./fuelLabels"

const SORT_OPTIONS = [
    { label: "Fuel Efficiency (L/100km)", value: "efficiency" },
    { label: "Fuel Volume (Litres)", value: "litres" },
    { label: "Fuel Expenditure (AED)", value: "cost" },
    { label: "Distance Traveled (km)", value: "distance" },
    { label: "Cost Per km (AED/km)", value: "cost_per_km" },
    { label: "Refuel Count (Tx)", value: "transactions" },
    { label: "Vehicle Identifier", value: "vehicle_identifier" },
]

export default function VehicleLeaderboard({
    vehiclesData,
    loading,
    error,
    onRetry,
    sortBy,
    setSortBy,
    sortOrder,
    setSortOrder,
    searchQuery,
    setSearchQuery,
    statusFilter,
    setStatusFilter,
    page,
    setPage,
    pageSize,
    onSelectVehicle,
}) {
    const [viewMode, setViewMode] = useState("all") // "all" | "efficiency" | "idling"

    const vehicles = vehiclesData?.data || []
    const totalVehicles = vehiclesData?.total || 0
    const totalPages = Math.max(1, Math.ceil(totalVehicles / pageSize))

    // Helper to calculate km/L accurately
    const getKmPerL = (v) => {
        if (v.distance_km && v.litres && v.litres > 0) {
            return v.distance_km / v.litres
        }
        if (v.fuel_efficiency_l_per_100km && v.fuel_efficiency_l_per_100km > 0) {
            return 100 / v.fuel_efficiency_l_per_100km
        }
        return null
    }

    return (
        <section aria-labelledby="leaderboard-heading" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
            {/* ─── Leaderboard Header & Controls ─────────────────────────── */}
            <div className="flex flex-col gap-3 border-b border-slate-200 px-4 py-3.5 lg:flex-row lg:items-center lg:justify-between">
                <div>
                    <div className="flex items-center gap-2">
                        <h2 id="leaderboard-heading" className="type-section-title text-slate-900">
                            Vehicle Performance Leaderboard
                        </h2>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 tabular-nums">
                            {totalVehicles} {totalVehicles === 1 ? "vehicle" : "vehicles"}
                        </span>
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">
                        Ranked fuel efficiency & per-vehicle driving vs. idling comparison
                    </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    {/* View Mode Switcher */}
                    <div role="group" aria-label="Table view mode" className="inline-flex rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                        {[
                            { id: "all", label: "Complete" },
                            { id: "efficiency", label: "Efficiency" },
                            { id: "idling", label: "Driving vs. Idling" },
                        ].map((tab) => (
                            <button
                                key={tab.id}
                                type="button"
                                onClick={() => setViewMode(tab.id)}
                                aria-pressed={viewMode === tab.id}
                                className={`rounded px-2.5 py-1 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                    viewMode === tab.id
                                        ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]"
                                        : "text-slate-600 hover:text-slate-900"
                                }`}
                            >
                                {tab.label}
                            </button>
                        ))}
                    </div>

                    {/* Search Plate */}
                    <SearchInput
                        value={searchQuery}
                        onChange={(val) => {
                            setSearchQuery(val)
                            setPage(1)
                        }}
                        placeholder="Search vehicle…"
                        className="w-full sm:w-40"
                    />

                    {/* Rank By Field */}
                    <label className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15">
                        <span className="text-xs text-slate-500 hidden sm:inline">Rank by</span>
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

                    {/* Sort Order (High/Low) */}
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

                    {/* Status Filter */}
                    <select
                        value={statusFilter}
                        onChange={(e) => {
                            setStatusFilter(e.target.value)
                            setPage(1)
                        }}
                        aria-label="Filter by telemetry status"
                        className="h-8 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-800 cursor-pointer focus:border-brand focus:outline-hidden"
                    >
                        <option value="">All Vehicles</option>
                        <option value="matched">Active Telemetry</option>
                        <option value="no_distance_data">Zero Distance</option>
                    </select>
                </div>
            </div>

            {/* ─── Table ─────────────────────────────────────────────────── */}
            <div className="overflow-x-auto">
                <table className="w-full min-w-[980px] text-left text-xs">
                    <thead className="border-b border-slate-200 bg-slate-50 text-[11px] text-slate-500">
                        <tr>
                            <th scope="col" className="w-12 px-3 py-2.5 text-center font-medium">#</th>
                            <th scope="col" className="px-3 py-2.5 font-medium min-w-[170px]">Vehicle</th>
                            <th scope="col" className="px-3 py-2.5 text-right font-medium">Distance</th>
                            <th scope="col" className="px-3 py-2.5 text-right font-medium">Fuel Purchased</th>
                            <th scope="col" className="px-3 py-2.5 text-right font-medium min-w-[130px]">
                                Efficiency (km/L)
                            </th>
                            {(viewMode === "all" || viewMode === "idling") && (
                                <>
                                    <th scope="col" className="px-3 py-2.5 text-right font-medium">Driving Time</th>
                                    <th scope="col" className="px-3 py-2.5 text-right font-medium">Idling Time</th>
                                    <th scope="col" className="px-3 py-2.5 font-medium min-w-[180px]">
                                        Driving vs. Idling Split
                                    </th>
                                    <th scope="col" className="px-3 py-2.5 text-right font-medium">Idle Fuel Waste</th>
                                </>
                            )}
                            <th scope="col" className="px-3 py-2.5 text-right font-medium">Fuel Cost</th>
                            <th scope="col" className="px-3 py-2.5 text-center font-medium w-16">
                                <span className="sr-only">Actions</span>
                            </th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {loading ? (
                            <tr>
                                <td colSpan={11} role="status" className="py-16 text-center text-xs text-slate-500">
                                    <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                                    Loading vehicle performance leaderboard…
                                </td>
                            </tr>
                        ) : error ? (
                            <tr>
                                <td colSpan={11} role="alert" className="py-12 text-center text-xs text-rose-700">
                                    {error}
                                    {onRetry && (
                                        <button
                                            type="button"
                                            onClick={onRetry}
                                            className="ml-2 font-medium text-brand underline-offset-2 hover:underline cursor-pointer"
                                        >
                                            Retry
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ) : vehicles.length === 0 ? (
                            <tr>
                                <td colSpan={11} className="py-12 text-center text-xs text-slate-500">
                                    No vehicles found matching the current filters.
                                </td>
                            </tr>
                        ) : (
                            vehicles.map((v, idx) => {
                                const rankNum = (page - 1) * pageSize + idx + 1
                                const kmPerL = getKmPerL(v)

                                // Honest data status
                                const hasValidEfficiency = v.fuel_efficiency_l_per_100km !== null && v.fuel_efficiency_l_per_100km !== undefined
                                const isZeroDistance = v.status === "no_distance_data" || v.distance_km === 0
                                const isUntracked = v.status === "not_tracked"

                                // Time and fuel calculations for comparison bars
                                const drvH = v.driving_duration_hours !== null && v.driving_duration_hours !== undefined ? v.driving_duration_hours : null
                                const idlH = v.idle_duration_hours !== null && v.idle_duration_hours !== undefined ? v.idle_duration_hours : null
                                const totalH = (drvH || 0) + (idlH || 0)

                                const drvPct = totalH > 0 && drvH !== null
                                    ? Math.round((drvH / totalH) * 100)
                                    : (v.estimated_driving_percentage ? Math.round(v.estimated_driving_percentage) : null)
                                const idlPct = totalH > 0 && idlH !== null
                                    ? (100 - drvPct)
                                    : (v.estimated_idle_percentage ? Math.round(v.estimated_idle_percentage) : null)

                                // Top / Bottom performer indicator (when sorted by efficiency)
                                const isTopRanked = sortBy === "efficiency" && rankNum <= 3 && hasValidEfficiency
                                const isHighIdle = v.estimated_idle_fuel_litres && v.estimated_idle_fuel_litres > 20

                                const sub = "block text-[11px] text-slate-400 font-normal"

                                return (
                                    <tr key={v.vehicle_identifier} className="transition-colors hover:bg-slate-50/70">
                                        {/* Rank # */}
                                        <td className="px-3 py-2.5 text-center font-mono text-[11px] text-slate-400 tabular-nums">
                                            {isTopRanked ? (
                                                <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-50 text-[10px] font-bold text-emerald-700">
                                                    {rankNum}
                                                </span>
                                            ) : (
                                                rankNum
                                            )}
                                        </td>

                                        {/* Vehicle Identifier + Wialon Name + Status */}
                                        <td className="whitespace-nowrap px-3 py-2.5">
                                            <div className="flex items-center gap-2">
                                                <button
                                                    type="button"
                                                    onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                    className="font-mono text-xs font-semibold text-slate-900 transition-colors hover:text-brand cursor-pointer"
                                                    title={`Inspect ${v.vehicle_identifier}`}
                                                >
                                                    {v.vehicle_identifier}
                                                </button>
                                                <VehicleStatusBadge status={v.status} />
                                                {isTopRanked && (
                                                    <span className="rounded bg-emerald-50 border border-emerald-200 px-1 py-0.5 text-[10px] font-medium text-emerald-800">
                                                        Top Performer
                                                    </span>
                                                )}
                                                {isZeroDistance && (
                                                    <span className="rounded bg-slate-100 border border-slate-200 px-1 py-0.5 text-[10px] text-slate-500">
                                                        Zero Distance
                                                    </span>
                                                )}
                                            </div>
                                            {v.wialon_unit_name && (
                                                <span className={sub}>{v.wialon_unit_name}</span>
                                            )}
                                        </td>

                                        {/* Distance Travelled */}
                                        <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                            <span className="block font-semibold text-slate-900">
                                                {v.distance_km !== null && v.distance_km !== undefined
                                                    ? `${Math.round(v.distance_km).toLocaleString()} km`
                                                    : "—"}
                                            </span>
                                            <span className={sub}>
                                                {v.transaction_count != null ? `${v.transaction_count} refuels` : "—"}
                                            </span>
                                        </td>

                                        {/* Fuel Purchased */}
                                        <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                            <span className="block font-semibold text-slate-900">
                                                {v.litres !== null && v.litres !== undefined
                                                    ? `${Math.round(v.litres).toLocaleString()} L`
                                                    : "—"}
                                            </span>
                                            <span className={sub}>
                                                AED {Math.round(v.total_cost || 0).toLocaleString()}
                                            </span>
                                        </td>

                                        {/* Fuel Efficiency (km/L and L/100km) */}
                                        <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                            {hasValidEfficiency ? (
                                                <>
                                                    <span className="block font-semibold text-slate-900 text-[13px]">
                                                        {kmPerL !== null ? `${kmPerL.toFixed(2)} km/L` : "—"}
                                                    </span>
                                                    <span className={sub}>
                                                        {v.fuel_efficiency_l_per_100km?.toFixed(1)} L/100 · AED {fixedOrDash(v.fuel_cost_per_km)}/km
                                                    </span>
                                                </>
                                            ) : (
                                                <div className="text-right">
                                                    <span className="font-semibold text-slate-400">—</span>
                                                    <span className="block text-[10px] text-slate-400">
                                                        {v.efficiency_suppressed_reason === "insufficient_distance"
                                                            ? "Insufficient km"
                                                            : isZeroDistance
                                                                ? "No movement"
                                                                : isUntracked
                                                                    ? "Untracked"
                                                                    : "No ratio"}
                                                    </span>
                                                </div>
                                            )}
                                        </td>

                                        {/* Driving & Idling Columns */}
                                        {(viewMode === "all" || viewMode === "idling") && (
                                            <>
                                                {/* Driving Duration */}
                                                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                                    <span className="block font-semibold text-slate-900">
                                                        {drvH != null ? `${Number(drvH).toFixed(1)} h` : "—"}
                                                    </span>
                                                    <span className={sub}>
                                                        {v.estimated_driving_fuel_litres != null
                                                            ? `${Math.round(v.estimated_driving_fuel_litres)} L`
                                                            : "In motion"}
                                                    </span>
                                                </td>

                                                {/* Idling Duration */}
                                                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                                    <span className="block font-semibold text-slate-900">
                                                        {idlH != null ? `${Number(idlH).toFixed(1)} h` : "—"}
                                                    </span>
                                                    <span className="block text-[11px] text-amber-700">
                                                        {v.estimated_idle_fuel_litres != null
                                                            ? `${Number(v.estimated_idle_fuel_litres).toFixed(1)} L`
                                                            : "—"}
                                                    </span>
                                                </td>

                                                {/* Driving vs Idling Comparison Bar for EVERY vehicle */}
                                                <td className="min-w-[180px] px-3 py-2.5">
                                                    {drvPct !== null && idlPct !== null ? (
                                                        <div className="space-y-1">
                                                            <div className="flex h-2 w-full overflow-hidden rounded-full bg-slate-100">
                                                                <div
                                                                    style={{ width: `${Math.max(drvPct, 0)}%` }}
                                                                    className="bg-[#33758F] transition-[width] duration-300"
                                                                    title={`Driving: ${drvH != null ? Number(drvH).toFixed(1) + 'h' : '—'} (${drvPct}%)`}
                                                                />
                                                                <div
                                                                    style={{ width: `${Math.max(idlPct, 0)}%` }}
                                                                    className="bg-[#D97706] transition-[width] duration-300"
                                                                    title={`Idling: ${idlH != null ? Number(idlH).toFixed(1) + 'h' : '—'} (${idlPct}%)`}
                                                                />
                                                            </div>
                                                            <div className="flex items-center justify-between text-[10px] tabular-nums">
                                                                <span className="text-[#33758F] font-medium">{drvPct}% driving</span>
                                                                <span className="text-[#D97706] font-medium">{idlPct}% idle</span>
                                                            </div>
                                                        </div>
                                                    ) : (
                                                        <span className="text-slate-400 text-[11px]">Telemetry unavailable</span>
                                                    )}
                                                </td>

                                                {/* Estimated Idle Fuel Waste */}
                                                <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                                    <span className={`block font-semibold ${isHighIdle ? "text-amber-800" : "text-slate-900"}`}>
                                                        {v.estimated_idle_fuel_litres != null
                                                            ? `${Number(v.estimated_idle_fuel_litres).toFixed(1)} L`
                                                            : "—"}
                                                    </span>
                                                    <span className={sub}>
                                                        {v.estimated_idle_cost != null
                                                            ? `AED ${Math.round(v.estimated_idle_cost).toLocaleString()}`
                                                            : "—"}
                                                    </span>
                                                </td>
                                            </>
                                        )}

                                        {/* Fuel Cost (AED) */}
                                        <td className="whitespace-nowrap px-3 py-2.5 text-right tabular-nums">
                                            <span className="block font-semibold text-slate-900">
                                                AED {Math.round(v.total_cost || 0).toLocaleString()}
                                            </span>
                                            <span className={sub}>
                                                {v.litres > 0 && v.total_cost
                                                    ? `AED ${(v.total_cost / v.litres).toFixed(2)}/L`
                                                    : "—"}
                                            </span>
                                        </td>

                                        {/* Inspect Action */}
                                        <td className="whitespace-nowrap px-3 py-2.5 text-center">
                                            <button
                                                type="button"
                                                onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                className="inline-flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-700 transition-colors hover:border-brand hover:text-brand hover:bg-slate-50 cursor-pointer"
                                                title={`Inspect full calculation and records for ${v.vehicle_identifier}`}
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

            {/* ─── Pagination ────────────────────────────────────────────── */}
            {totalVehicles > 0 && (
                <Pagination
                    page={page}
                    totalPages={totalPages}
                    onPageChange={setPage}
                    start={(page - 1) * pageSize + 1}
                    end={Math.min(page * pageSize, totalVehicles)}
                    total={totalVehicles}
                    noun="vehicles"
                />
            )}
        </section>
    )
}
