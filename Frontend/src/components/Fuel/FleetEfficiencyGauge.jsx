import { Gauge, Route, Info } from "lucide-react"

export default function FleetEfficiencyGauge({ efficiencyData, loading }) {
    if (loading) {
        return (
            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between animate-pulse">
                <div className="h-5 w-40 bg-slate-200 rounded mb-4" />
                <div className="h-28 bg-slate-100 rounded-xl mb-4" />
                <div className="h-16 bg-slate-100 rounded-xl" />
            </div>
        )
    }

    // No response (request failed or not loaded): show nothing rather than a row of zero counts.
    if (!efficiencyData) {
        return (
            <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col items-center justify-center text-center">
                <Info className="h-6 w-6 text-slate-400 mb-2" />
                <h3 className="text-sm font-bold text-slate-700">Efficiency telemetry unavailable</h3>
                <p className="text-[11px] text-slate-500 mt-1">No efficiency data was returned for this period.</p>
            </div>
        )
    }

    const telemetryUnknown = efficiencyData?.vehicles_telemetry_unknown || 0
    const eff = efficiencyData?.fuel_efficiency_l_per_100km
    const costPerKm = efficiencyData?.fuel_cost_per_km
    const totalDist = efficiencyData?.total_distance_km
    const trackedVehicles = efficiencyData?.matched_vehicle_count || 0
    const vehiclesWithDist = efficiencyData?.vehicles_with_distance || 0
    const vehiclesWithoutDist = efficiencyData?.vehicles_without_distance || 0
    const unmatchedVehicles = efficiencyData?.unmatched_vehicle_count || 0

    // Neutral visual scale calculation (0 to 60 L/100km typical fleet operational spectrum)
    const gaugeValue = eff ? Math.min(Math.max(eff, 0), 60) : 0
    const gaugePercent = Math.min((gaugeValue / 60) * 100, 100)

    return (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between">
            {/* Header */}
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                <div className="flex items-center gap-2">
                    <div className="rounded-xl bg-[#33758F]/10 p-1.5 text-[#33758F]">
                        <Gauge className="h-4 w-4" />
                    </div>
                    <div>
                        <h3 className="text-sm font-bold text-slate-900">
                            Fleet Efficiency Telemetry
                        </h3>
                        <p className="text-[11px] text-slate-500">
                            Weighted L/100 km & distance coverage
                        </p>
                    </div>
                </div>
                <div className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-bold text-slate-600">
                    Weighted
                </div>
            </div>

            {/* Main Visual Display */}
            <div className="my-4 space-y-3">
                <div className="flex items-baseline justify-between">
                    <div>
                        <div className="text-3xl font-black tracking-tight text-slate-900">
                            {eff !== null && eff !== undefined ? eff.toFixed(1) : "—"}
                            <span className="text-sm font-bold text-slate-400 ml-1.5">L / 100 km</span>
                        </div>
                        <div className="text-xs font-semibold text-slate-500 mt-0.5">
                            {costPerKm !== null && costPerKm !== undefined
                                ? `AED ${costPerKm.toFixed(2)} per kilometer`
                                : "Awaiting telemetry"}
                        </div>
                    </div>

                    <div className="text-right">
                        <div className="text-sm font-bold text-slate-700 flex items-center justify-end gap-1">
                            <Route className="h-3.5 w-3.5 text-sky-600" />
                            {totalDist ? `${Math.round(totalDist).toLocaleString()} km` : "—"}
                        </div>
                        <div className="text-[10px] text-slate-400">Total Tracked Distance</div>
                    </div>
                </div>

                {/* Neutral Performance Progress Bar */}
                <div className="space-y-1">
                    <div className="h-3 w-full rounded-full bg-slate-100 overflow-hidden relative border border-slate-200/60">
                        <div
                            className="h-full rounded-full bg-gradient-to-r from-[#33758F] to-[#0284C7] transition-all duration-500"
                            style={{ width: `${gaugePercent}%` }}
                        />
                    </div>
                    <div className="flex justify-between text-[10px] text-slate-400 font-medium">
                        <span>0 L/100 km</span>
                        <span>30 L/100 km</span>
                        <span>60 L/100 km</span>
                    </div>
                </div>
            </div>

            {/* Telemetry Tracking Status Grid */}
            <div className="border-t border-slate-100 pt-3 space-y-2 text-xs">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                    Telemetry Coverage
                </div>

                <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
                        <div className="flex items-center justify-between">
                            <span className="text-[11px] text-slate-500 font-medium">GPS Tracked</span>
                            <span className="font-bold text-slate-900">{trackedVehicles}</span>
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                            Active Wialon units
                        </div>
                    </div>

                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
                        <div className="flex items-center justify-between">
                            <span className="text-[11px] text-slate-500 font-medium">With Distance</span>
                            <span className="font-bold text-emerald-600">{vehiclesWithDist}</span>
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                            Trips recorded
                        </div>
                    </div>

                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
                        <div className="flex items-center justify-between">
                            <span className="text-[11px] text-slate-500 font-medium">No Distance</span>
                            <span className="font-bold text-amber-600">{vehiclesWithoutDist}</span>
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                            Tracked, 0 km
                        </div>
                    </div>

                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-2.5">
                        <div className="flex items-center justify-between">
                            <span className="text-[11px] text-slate-500 font-medium">Untracked</span>
                            <span className="font-bold text-slate-500">{unmatchedVehicles}</span>
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                            Fuel-only vehicles
                        </div>
                    </div>
                </div>
                {telemetryUnknown > 0 && (
                    <p className="mt-2 text-[10px] text-amber-700">
                        {telemetryUnknown} tracked vehicle{telemetryUnknown === 1 ? "" : "s"} with unknown Wialon distance (excluded from L/100 km)
                    </p>
                )}
            </div>
        </div>
    )
}
