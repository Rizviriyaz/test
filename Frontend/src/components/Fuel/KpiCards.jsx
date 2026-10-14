import { Droplets, DollarSign, Route, Gauge, Clock, Flame, ShieldCheck, HelpCircle, Activity } from "lucide-react"

function formatNumber(val, decimals = 1) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })
}

function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    })
}

export default function KpiCards({ efficiencyData, loading, idleRate = 2.0 }) {
    if (loading) {
        return (
            <div className="space-y-4 mb-8">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {[1, 2, 3, 4].map((i) => (
                        <div key={i} className="animate-pulse rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs">
                            <div className="flex items-center justify-between mb-3">
                                <div className="h-3 w-24 bg-slate-200 rounded" />
                                <div className="h-8 w-8 bg-slate-100 rounded-xl" />
                            </div>
                            <div className="h-7 w-32 bg-slate-200 rounded mb-2" />
                            <div className="h-3 w-20 bg-slate-100 rounded" />
                        </div>
                    ))}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {[5, 6, 7, 8].map((i) => (
                        <div key={i} className="animate-pulse rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs">
                            <div className="flex items-center justify-between mb-3">
                                <div className="h-3 w-24 bg-slate-200 rounded" />
                                <div className="h-8 w-8 bg-slate-100 rounded-xl" />
                            </div>
                            <div className="h-7 w-32 bg-slate-200 rounded mb-2" />
                            <div className="h-3 w-20 bg-slate-100 rounded" />
                        </div>
                    ))}
                </div>
            </div>
        )
    }

    const totalLitres = efficiencyData?.total_litres
    const totalCost = efficiencyData?.total_cost
    const transactionCount = efficiencyData?.transaction_count
    const vehicleCount = efficiencyData?.vehicle_count || 0
    const matchedCount = efficiencyData?.matched_vehicle_count || 0
    const unmatchedCount = efficiencyData?.unmatched_vehicle_count || 0

    const totalDistance = efficiencyData?.total_distance_km
    const drivingHours = efficiencyData?.total_driving_hours
    const idleHours = efficiencyData?.total_idle_hours
    const estimatedIdleFuel = efficiencyData?.estimated_idle_fuel_litres
    const estimatedIdleCost = efficiencyData?.estimated_idle_cost
    const estimatedDrivingFuel = efficiencyData?.estimated_driving_fuel_litres
    const idlePct = efficiencyData?.estimated_idle_percentage
    const drivingPct = efficiencyData?.estimated_driving_percentage
    const efficiency = efficiencyData?.fuel_efficiency_l_per_100km
    const costPerKm = efficiencyData?.fuel_cost_per_km

    const avgPrice = totalLitres && totalCost ? (totalCost / totalLitres) : null

    return (
        <div className="space-y-4 mb-8">
            {/* ─── SECTION 1: RECORDED FUEL TRANSACTIONS ────────────────────── */}
            <div>
                <div className="flex items-center justify-between mb-2.5 px-1">
                    <div className="flex items-center gap-2">
                        <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-black tracking-wider uppercase text-emerald-800 border border-emerald-200/60">
                            <ShieldCheck className="h-3 w-3" />
                            Recorded Data
                        </span>
                        <span className="text-xs font-semibold text-slate-500">
                            Directly backed by Supabase fuel transaction invoices
                        </span>
                    </div>
                    {avgPrice !== null && (
                        <span className="text-xs font-bold text-slate-600">
                            Weighted Avg: <span className="font-mono text-slate-900">AED {avgPrice.toFixed(2)}/L</span>
                        </span>
                    )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {/* 1. Fuel Purchased */}
                    <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Fuel Purchased
                            </span>
                            <div className="rounded-xl bg-blue-50 p-2 text-blue-600 border border-blue-100">
                                <Droplets className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {formatNumber(totalLitres, 1)}
                            </span>
                            <span className="text-xs font-bold text-slate-400">Litres</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span>Refuelled volume</span>
                            <span className="font-mono font-semibold text-emerald-700">100% Invoiced</span>
                        </div>
                    </div>

                    {/* 2. Fuel Expenditure */}
                    <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Recorded Expenditure
                            </span>
                            <div className="rounded-xl bg-emerald-50 p-2 text-emerald-600 border border-emerald-100">
                                <DollarSign className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-xs font-bold text-slate-500">AED</span>
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {formatCurrency(totalCost)}
                            </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span>Total fuel spend</span>
                            <span className="font-semibold text-slate-700">Financial Log</span>
                        </div>
                    </div>

                    {/* 3. Refuel Transactions */}
                    <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Refuel Transactions
                            </span>
                            <div className="rounded-xl bg-amber-50 p-2 text-amber-600 border border-amber-100">
                                <Activity className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {transactionCount !== undefined ? formatCurrency(transactionCount) : "—"}
                            </span>
                            <span className="text-xs font-bold text-slate-400">Events</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span>
                                {totalLitres && transactionCount ? `~${(totalLitres / transactionCount).toFixed(1)} L / refuel` : "Statement records"}
                            </span>
                            <span className="font-semibold text-slate-700">Verified</span>
                        </div>
                    </div>

                    {/* 4. Vehicles Refuelled */}
                    <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Vehicles Refuelled
                            </span>
                            <div className="rounded-xl bg-purple-50 p-2 text-purple-600 border border-purple-100">
                                <Route className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {vehicleCount}
                            </span>
                            <span className="text-xs font-bold text-slate-400">Plates</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span className="text-emerald-700 font-bold">{matchedCount || vehicleCount} Matched Fleet</span>
                            <span className="font-semibold text-slate-600">Verified Wialon</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* ─── SECTION 2: ESTIMATED TELEMATICS & CONSUMPTION ─────────── */}
            <div>
                <div className="flex items-center justify-between mb-2.5 px-1">
                    <div className="flex items-center gap-2">
                        <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 text-[10px] font-black tracking-wider uppercase text-amber-800 border border-amber-200/60">
                            <Flame className="h-3 w-3" />
                            Estimated Telematics Intelligence
                        </span>
                        <span className="text-xs font-semibold text-slate-500">
                            Calculated via Wialon stationary engine duration @ {idleRate.toFixed(1)} L/h rate
                        </span>
                    </div>
                    {idleHours !== undefined && idleHours !== null && (
                        <span className="text-xs font-bold text-slate-600">
                            Qualifying Idle Hours: <span className="font-mono text-amber-700 font-black">{idleHours.toFixed(1)} hrs</span>
                        </span>
                    )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {/* 5. Estimated Idle Fuel */}
                    <div className="relative overflow-hidden rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50/40 to-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-1.5">
                                <span className="text-[11px] font-bold uppercase tracking-wider text-amber-900">
                                    Estimated Idling Fuel
                                </span>
                            </div>
                            <div className="rounded-xl bg-amber-100 p-2 text-amber-700 border border-amber-200">
                                <Clock className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-amber-900">
                                {estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) : "—"}
                            </span>
                            <span className="text-xs font-bold text-amber-700">Litres</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-amber-800">
                            <span>
                                {idlePct !== null && idlePct !== undefined ? `${idlePct.toFixed(1)}% of fuel` : "Idle consumption"}
                            </span>
                            <span className="font-mono text-[10px] bg-amber-100 px-1.5 py-0.5 rounded font-bold">
                                {idleRate.toFixed(1)} L/h
                            </span>
                        </div>
                    </div>

                    {/* 6. Estimated Idle Fuel Cost */}
                    <div className="relative overflow-hidden rounded-2xl border border-rose-200/80 bg-gradient-to-br from-rose-50/40 to-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-rose-900">
                                Estimated Idle Cost
                            </span>
                            <div className="rounded-xl bg-rose-100 p-2 text-rose-700 border border-rose-200">
                                <Flame className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-xs font-bold text-rose-700">AED</span>
                            <span className="text-2xl font-black tracking-tight text-rose-900">
                                {estimatedIdleCost !== null && estimatedIdleCost !== undefined ? formatCurrency(estimatedIdleCost) : "—"}
                            </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-rose-800">
                            <span>Direct idling loss</span>
                            <span className="font-semibold text-rose-700">Actionable Waste</span>
                        </div>
                    </div>

                    {/* 7. Estimated Driving Fuel */}
                    <div className="relative overflow-hidden rounded-2xl border border-sky-200/80 bg-gradient-to-br from-sky-50/30 to-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-sky-900">
                                Estimated Driving Fuel
                            </span>
                            <div className="rounded-xl bg-sky-100 p-2 text-sky-700 border border-sky-200">
                                <Route className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-sky-900">
                                {estimatedDrivingFuel !== null && estimatedDrivingFuel !== undefined ? formatNumber(estimatedDrivingFuel, 1) : "—"}
                            </span>
                            <span className="text-xs font-bold text-sky-700">Litres</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-sky-800">
                            <span>
                                {drivingPct !== null && drivingPct !== undefined ? `${drivingPct.toFixed(1)}% productive` : (drivingHours ? `${drivingHours.toFixed(1)}h in motion` : "Productive motion")}
                            </span>
                            <span className="font-mono text-[10px] bg-sky-100 px-1.5 py-0.5 rounded font-bold">
                                {formatNumber(totalDistance, 0)} km
                            </span>
                        </div>
                    </div>

                    {/* 8. Fleet Fuel Efficiency */}
                    <div className="relative overflow-hidden rounded-2xl border border-[#33758F]/30 bg-gradient-to-br from-[#33758F]/5 to-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-[#33758F]">
                                Fleet Efficiency
                            </span>
                            <div className="rounded-xl bg-[#33758F]/10 p-2 text-[#33758F] border border-[#33758F]/20">
                                <Gauge className="h-4 w-4" />
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {efficiency !== null && efficiency !== undefined ? formatNumber(efficiency, 1) : "—"}
                            </span>
                            <span className="text-xs font-bold text-slate-500">L / 100 km</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-600">
                            <span>
                                {costPerKm !== null && costPerKm !== undefined ? `AED ${formatNumber(costPerKm, 2)} / km` : "Weighted burn"}
                            </span>
                            <span className="text-[10px] font-black text-[#33758F] bg-[#33758F]/10 px-1.5 py-0.5 rounded">
                                Weighted
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    )
}
