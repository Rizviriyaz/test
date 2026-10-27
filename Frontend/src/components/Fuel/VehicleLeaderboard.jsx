import { useState } from "react"
import {
    ArrowUpRight,
    Loader2,
    ChevronDown,
    ChevronUp,
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
    const [expandedIds, setExpandedIds] = useState(new Set())

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

    const toggleExpand = (id, e) => {
        e?.stopPropagation?.()
        setExpandedIds((prev) => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    }

    return (
        <section aria-labelledby="leaderboard-heading" className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
            {/* ─── Leaderboard Header & Controls ─────────────────────────── */}
            <div className="flex flex-col gap-2.5 border-b border-slate-200 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
                <div className="flex items-center gap-2">
                    <h2 id="leaderboard-heading" className="type-section-title text-slate-900">
                        Vehicle Performance Leaderboard
                    </h2>
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 tabular-nums">
                        {totalVehicles} {totalVehicles === 1 ? "vehicle" : "vehicles"}
                    </span>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    {/* Search Vehicle Identifier */}
                    <SearchInput
                        value={searchQuery}
                        onChange={(val) => {
                            setSearchQuery(val)
                            setPage(1)
                        }}
                        placeholder="Search vehicle…"
                        className="w-full sm:w-44"
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

            {/* ─── Compact Table ─────────────────────────────────────────── */}
            <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                    <thead className="border-b border-slate-200 bg-slate-50 text-[11px] text-slate-500">
                        <tr>
                            <th scope="col" className="w-12 px-3 py-2 text-center font-medium">#</th>
                            <th scope="col" className="px-3 py-2 font-medium">Vehicle</th>
                            <th scope="col" className="px-3 py-2 text-right font-medium">Distance</th>
                            <th scope="col" className="px-3 py-2 text-right font-medium">Fuel Consumed</th>
                            <th scope="col" className="px-3 py-2 text-right font-medium">Efficiency (L/100 km)</th>
                            <th scope="col" className="w-24 px-3 py-2 text-center font-medium">
                                <span className="sr-only">Actions</span>
                            </th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {loading ? (
                            <tr>
                                <td colSpan={6} role="status" className="py-12 text-center text-xs text-slate-500">
                                    <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                                    Loading rankings…
                                </td>
                            </tr>
                        ) : error ? (
                            <tr>
                                <td colSpan={6} role="alert" className="py-10 text-center text-xs text-rose-700">
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
                                <td colSpan={6} className="py-10 text-center text-xs text-slate-500">
                                    No vehicles found matching current filters.
                                </td>
                            </tr>
                        ) : (
                            vehicles.map((v, idx) => {
                                const rankNum = (page - 1) * pageSize + idx + 1
                                const kmPerL = getKmPerL(v)
                                const isExpanded = expandedIds.has(v.vehicle_identifier)

                                // Honest data status
                                const hasValidEfficiency = v.fuel_efficiency_l_per_100km !== null && v.fuel_efficiency_l_per_100km !== undefined
                                const isZeroDistance = v.status === "no_distance_data" || v.distance_km === 0
                                const isUntracked = v.status === "not_tracked"

                                const isTopRanked = sortBy === "efficiency" && rankNum <= 3 && hasValidEfficiency

                                return (
                                    <tr key={v.vehicle_identifier} className="group transition-colors hover:bg-slate-50/70">
                                        {/* Rank # */}
                                        <td className="px-3 py-2 text-center font-mono text-[11px] text-slate-400 tabular-nums">
                                            {isTopRanked ? (
                                                <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-emerald-50 text-[10px] font-bold text-emerald-700">
                                                    {rankNum}
                                                </span>
                                            ) : (
                                                rankNum
                                            )}
                                        </td>

                                        {/* Vehicle Name / Identifier */}
                                        <td className="px-3 py-2">
                                            <div className="flex items-center gap-1.5 flex-wrap">
                                                <button
                                                    type="button"
                                                    onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                    className="font-mono text-xs font-semibold text-slate-900 hover:text-brand cursor-pointer"
                                                    title={`Inspect ${v.vehicle_identifier}`}
                                                >
                                                    {v.vehicle_identifier}
                                                </button>
                                                <VehicleStatusBadge status={v.status} />
                                                {v.wialon_unit_name && (
                                                    <span className="text-[11px] text-slate-400 truncate max-w-[130px]" title={v.wialon_unit_name}>
                                                        ({v.wialon_unit_name})
                                                    </span>
                                                )}
                                            </div>
                                        </td>

                                        {/* Distance Travelled (Recorded) */}
                                        <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                                            <span className="font-semibold text-slate-900">
                                                {v.distance_km !== null && v.distance_km !== undefined
                                                    ? `${Math.round(v.distance_km).toLocaleString()} km`
                                                    : "—"}
                                            </span>
                                        </td>

                                        {/* Fuel Consumed / Purchased (Recorded) */}
                                        <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                                            <span className="font-semibold text-slate-900">
                                                {v.litres !== null && v.litres !== undefined
                                                    ? `${Math.round(v.litres).toLocaleString()} L`
                                                    : "—"}
                                            </span>
                                        </td>

                                        {/* Fuel Efficiency (L/100 km primary) */}
                                        <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                                            {hasValidEfficiency ? (
                                                <div className="inline-flex items-baseline gap-1.5 justify-end">
                                                    <span className="font-semibold text-slate-900 text-xs">
                                                        {v.fuel_efficiency_l_per_100km?.toFixed(1)} L/100 km
                                                    </span>
                                                    {kmPerL !== null && (
                                                        <span className="text-[11px] text-slate-400">
                                                            ({kmPerL.toFixed(2)} km/L)
                                                        </span>
                                                    )}
                                                </div>
                                            ) : (
                                                <span className="font-medium text-slate-400">
                                                    {v.efficiency_suppressed_reason === "insufficient_distance"
                                                        ? "Insufficient km"
                                                        : isZeroDistance
                                                            ? "Zero distance"
                                                            : isUntracked
                                                                ? "No Wialon unit"
                                                                : "—"}
                                                </span>
                                            )}
                                        </td>

                                        {/* Actions: Expand & Inspect */}
                                        <td className="px-3 py-2 text-center whitespace-nowrap">
                                            <div className="inline-flex items-center gap-1">
                                                <button
                                                    type="button"
                                                    onClick={(e) => toggleExpand(v.vehicle_identifier, e)}
                                                    className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                                                    title={isExpanded ? "Hide secondary details" : "Show secondary details"}
                                                    aria-label={isExpanded ? "Hide details" : "Show details"}
                                                >
                                                    {isExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                                    className="inline-flex h-6 items-center gap-0.5 rounded border border-slate-200 bg-white px-1.5 text-[11px] font-medium text-slate-700 transition-colors hover:border-brand hover:text-brand cursor-pointer"
                                                    title={`Inspect full records for ${v.vehicle_identifier}`}
                                                >
                                                    Inspect
                                                    <ArrowUpRight className="h-3 w-3 text-slate-400" />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                )
                            })
                        )}
                    </tbody>
                </table>
            </div>

            {/* ─── Expandable Details Section for Expanded Rows ───────────── */}
            {expandedIds.size > 0 && (
                <div className="border-t border-slate-200 bg-slate-50/50 px-4 py-2.5">
                    <div className="flex items-center justify-between text-[11px] font-medium text-slate-600 mb-2">
                        <span>Expanded vehicle details ({expandedIds.size})</span>
                        <button
                            type="button"
                            onClick={() => setExpandedIds(new Set())}
                            className="text-brand hover:underline cursor-pointer"
                        >
                            Collapse all
                        </button>
                    </div>
                    <div className="space-y-2">
                        {vehicles.filter((v) => expandedIds.has(v.vehicle_identifier)).map((v) => {
                            const drvH = v.driving_duration_hours !== null && v.driving_duration_hours !== undefined ? v.driving_duration_hours : null
                            const idlH = v.idle_duration_hours !== null && v.idle_duration_hours !== undefined ? v.idle_duration_hours : null
                            const totalH = (drvH || 0) + (idlH || 0)
                            const drvPct = totalH > 0 && drvH !== null ? Math.round((drvH / totalH) * 100) : null
                            const idlPct = totalH > 0 && idlH !== null && drvPct !== null ? (100 - drvPct) : null

                            return (
                                <div key={v.vehicle_identifier} className="rounded-lg border border-slate-200 bg-white p-3 text-xs shadow-2xs">
                                    <div className="flex items-center justify-between border-b border-slate-100 pb-1.5 mb-2">
                                        <div className="flex items-center gap-2">
                                            <span className="font-mono font-semibold text-slate-900">{v.vehicle_identifier}</span>
                                            {v.wialon_unit_name && <span className="text-[11px] text-slate-400">({v.wialon_unit_name})</span>}
                                            <VehicleStatusBadge status={v.status} />
                                        </div>
                                        <button
                                            type="button"
                                            onClick={(e) => toggleExpand(v.vehicle_identifier, e)}
                                            className="text-[11px] text-slate-400 hover:text-slate-700 cursor-pointer"
                                        >
                                            Close
                                        </button>
                                    </div>
                                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6 text-[11px]">
                                        <div>
                                            <span className="text-slate-400 block">Driving Time</span>
                                            <span className="font-semibold text-slate-800 tabular-nums">
                                                {drvH != null ? `${Number(drvH).toFixed(1)} h` : "—"}
                                            </span>
                                            {v.estimated_driving_fuel_litres != null && (
                                                <span className="text-slate-400 block text-[10px]">{Math.round(v.estimated_driving_fuel_litres)} L driving</span>
                                            )}
                                        </div>
                                        <div>
                                            <span className="text-slate-400 block">Idling Time</span>
                                            <span className="font-semibold text-slate-800 tabular-nums">
                                                {idlH != null ? `${Number(idlH).toFixed(1)} h` : "—"}
                                            </span>
                                        </div>
                                        <div>
                                            <span className="text-slate-400 block">Idle Waste (Est.)</span>
                                            <span className="font-semibold text-amber-800 tabular-nums">
                                                {v.estimated_idle_fuel_litres != null ? `${Number(v.estimated_idle_fuel_litres).toFixed(1)} L` : "—"}
                                            </span>
                                            {v.estimated_idle_cost != null && (
                                                <span className="text-slate-400 block text-[10px]">AED {Math.round(v.estimated_idle_cost).toLocaleString()}</span>
                                            )}
                                        </div>
                                        <div>
                                            <span className="text-slate-400 block">Total Spend</span>
                                            <span className="font-semibold text-slate-800 tabular-nums">
                                                AED {Math.round(v.total_cost || 0).toLocaleString()}
                                            </span>
                                            {v.fuel_cost_per_km != null && (
                                                <span className="text-slate-400 block text-[10px]">AED {fixedOrDash(v.fuel_cost_per_km)}/km</span>
                                            )}
                                        </div>
                                        <div>
                                            <span className="text-slate-400 block">Transactions</span>
                                            <span className="font-semibold text-slate-800 tabular-nums">
                                                {v.transaction_count != null ? `${v.transaction_count} refuels` : "—"}
                                            </span>
                                        </div>
                                        <div>
                                            <span className="text-slate-400 block">Motion Split</span>
                                            {drvPct !== null && idlPct !== null ? (
                                                <span className="font-semibold text-slate-800 tabular-nums">
                                                    {drvPct}% drive · {idlPct}% idle
                                                </span>
                                            ) : (
                                                <span className="text-slate-400">—</span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}

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
