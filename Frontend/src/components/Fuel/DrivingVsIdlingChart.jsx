import { useMemo } from "react"
import { AlertTriangle, Flame, Clock, Fuel, ArrowUpRight } from "lucide-react"

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
    // True when the vehicle list could not be loaded: its section says so instead of "no vehicles"
    vehiclesUnavailable = false,
    loading,
    idleRate = 2.0,
    onSelectVehicle,
}) {
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

    // Hour shares come from hours only; without hours they are 0 (shown as "—"), never the fuel percentages
    const hoursKnown = totalHours > 0
    const hoursDrivingPct = hoursKnown ? ((drivingHours || 0) / totalHours) * 100 : 0
    const hoursIdlePct = hoursKnown ? ((idleHours || 0) / totalHours) * 100 : 0

    const fuelDrivingPct = totalFuel > 0 ? ((drivingFuel || 0) / totalFuel) * 100 : (drivingPct || 0)
    const fuelIdlePct = totalFuel > 0 ? ((idleFuel || 0) / totalFuel) * 100 : (idlePct || 0)

    if (loading) {
        return (
            <div className="rounded-xl border border-slate-200/70 bg-white p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]" role="status" aria-label="Loading driving vs idling analytics">
                <div className="mb-3 h-4 w-48 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    <div className="h-44 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                    <div className="h-44 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                </div>
            </div>
        )
    }

    return (
        <section
            aria-labelledby="driving-vs-idling-heading"
            className="rounded-xl border border-slate-200/70 bg-white p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]"
        >
            {/* Header & High-level Loss indicator */}
            <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between border-b border-slate-100 pb-3 mb-3.5">
                <div>
                    <div className="flex items-center gap-2">
                        <h2 id="driving-vs-idling-heading" className="type-section-title text-slate-900">
                            Driving vs. Idling Operational Analysis
                        </h2>
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
            </div>

            {/* Fleet Overview Visualizations & Top Wasters */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 items-stretch">
                {/* ── Left Column: Comparative Distribution Bars ── */}
                <div className="lg:col-span-7 flex flex-col justify-between space-y-3.5">
                    {/* Time Comparison Bar */}
                    <div className="rounded-xl border border-slate-200/60 bg-slate-50/60 p-3.5">
                        <div className="flex items-center justify-between text-xs font-medium text-slate-700 mb-2">
                            <span className="flex items-center gap-1.5">
                                <Clock className="h-3.5 w-3.5 text-slate-400" />
                                <span className="font-semibold text-slate-900">Engine Duration Split</span>
                                <span className="text-[10px] text-slate-400 font-mono">(Recorded Wialon)</span>
                            </span>
                            <span className="text-[11px] tabular-nums text-slate-600 font-medium">
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
                                title={`Driving: ${drivingHours != null ? Number(drivingHours).toFixed(1) + 'h' : '—'} (${hoursKnown ? Math.round(hoursDrivingPct) + '%' : '—'})`}
                            />
                            <div
                                style={{ width: `${Math.max(hoursIdlePct, 0)}%` }}
                                className="bg-[#D97706] transition-[width] duration-300"
                                title={`Idling: ${idleHours != null ? Number(idleHours).toFixed(1) + 'h' : '—'} (${hoursKnown ? Math.round(hoursIdlePct) + '%' : '—'})`}
                            />
                        </div>

                        <div className="flex items-center justify-between mt-2 text-[11px]">
                            <span className="inline-flex items-center gap-1.5 text-[#33758F] font-medium">
                                <span className="h-2 w-2 rounded-full bg-[#33758F]" />
                                Active Driving Motion: {hoursKnown ? `${Math.round(hoursDrivingPct)}%` : "—"}
                            </span>
                            <span className="inline-flex items-center gap-1.5 text-[#D97706] font-medium">
                                <span className="h-2 w-2 rounded-full bg-[#D97706]" />
                                Stationary Idling: {hoursKnown ? `${Math.round(hoursIdlePct)}%` : "—"}
                            </span>
                        </div>
                    </div>

                    {/* Fuel Consumption Comparison Bar */}
                    <div className="rounded-xl border border-slate-200/60 bg-slate-50/60 p-3.5">
                        <div className="flex items-center justify-between text-xs font-medium text-slate-700 mb-2">
                            <span className="flex items-center gap-1.5">
                                <Fuel className="h-3.5 w-3.5 text-slate-400" />
                                <span className="font-semibold text-slate-900">Estimated Fuel Allocation</span>
                                <span className="font-mono text-[10px] text-slate-400">(Estimated @ {safeIdleRate.toFixed(1)} L/h)</span>
                            </span>
                            <span className="text-[11px] tabular-nums text-slate-600 font-medium">
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
                <div className="lg:col-span-5 rounded-xl border border-slate-200/70 bg-white p-3.5 shadow-xs flex flex-col justify-between">
                    <div>
                        <div className="flex items-center justify-between border-b border-slate-100 pb-2 mb-2">
                            <span className="text-xs font-semibold text-slate-900 flex items-center gap-1.5">
                                <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />
                                Highest Idle Fuel Wasters
                            </span>
                            <span className="text-[11px] font-mono text-slate-400">Rate: {idleRate.toFixed(1)} L/h</span>
                        </div>

                        {vehiclesUnavailable ? (
                            <p role="alert" className="py-8 text-center text-xs text-slate-500">
                                Vehicle rankings could not be loaded; idle wasters are unavailable.
                            </p>
                        ) : topIdleWasters.length === 0 ? (
                            <p className="py-8 text-center text-xs text-slate-400">
                                No vehicles with recorded idle fuel waste in this period.
                            </p>
                        ) : (
                            <div className="divide-y divide-slate-100">
                                {topIdleWasters.map((v, idx) => (
                                    <div
                                        key={v.vehicle_identifier}
                                        className="flex items-center justify-between py-2 text-xs transition-colors hover:bg-slate-50/70 px-1 rounded-lg"
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
            </div>
        </section>
    )
}
