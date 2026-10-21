import { Info } from "lucide-react"

export default function FleetEfficiencyGauge({ efficiencyData, loading }) {
    if (loading) {
        return (
            <div className="flex flex-col rounded-lg border border-slate-200 bg-white p-4" role="status" aria-label="Loading efficiency">
                <div className="mb-4 h-4 w-40 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                <div className="mb-4 h-24 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                <div className="h-16 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
            </div>
        )
    }

    // No response (request failed or not loaded): show nothing rather than a row of zero counts.
    if (!efficiencyData) {
        return (
            <div className="flex flex-col items-center justify-center rounded-lg border border-slate-200 bg-white p-5 text-center">
                <Info className="mb-2 h-5 w-5 text-slate-300" />
                <h3 className="text-[13px] font-medium text-slate-800">Efficiency telemetry unavailable</h3>
                <p className="mt-1 text-xs text-slate-500">No efficiency data was returned for this period.</p>
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

    // Neutral visual scale (0 to 60 L/100km typical fleet operational spectrum)
    const gaugeValue = eff ? Math.min(Math.max(eff, 0), 60) : 0
    const gaugePercent = Math.min((gaugeValue / 60) * 100, 100)

    const coverage = [
        { label: "GPS tracked", note: "Active Wialon units", value: trackedVehicles },
        { label: "With distance", note: "Trips recorded", value: vehiclesWithDist },
        { label: "No distance", note: "Tracked, 0 km", value: vehiclesWithoutDist, warn: vehiclesWithoutDist > 0 },
        { label: "Untracked", note: "Fuel-only vehicles", value: unmatchedVehicles },
    ]

    return (
        <div className="flex flex-col rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-baseline justify-between gap-2">
                <h3 className="type-section-title text-slate-900">Fleet efficiency</h3>
                <span className="text-xs text-slate-500">Weighted L/100 km · distance coverage</span>
            </div>

            <div className="mt-4 flex items-end justify-between gap-4">
                <div>
                    <p className="flex items-baseline gap-1.5">
                        <span className="text-[28px] font-semibold leading-none tracking-tight tabular-nums text-slate-900">
                            {eff !== null && eff !== undefined ? eff.toFixed(1) : "—"}
                        </span>
                        <span className="text-xs text-slate-500">L/100 km</span>
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                        {costPerKm !== null && costPerKm !== undefined
                            ? `AED ${costPerKm.toFixed(2)} per km`
                            : "Awaiting telemetry"}
                    </p>
                </div>
                <div className="text-right">
                    <p className="text-[15px] font-semibold tabular-nums text-slate-900">
                        {totalDist ? `${Math.round(totalDist).toLocaleString()} km` : "—"}
                    </p>
                    <p className="text-xs text-slate-500">Tracked distance</p>
                </div>
            </div>

            <div className="mt-3">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-100" role="img" aria-label={`Efficiency on a 0 to 60 L/100 km scale: ${eff ? eff.toFixed(1) : "unknown"}`}>
                    <div
                        className="h-full rounded-full bg-brand transition-[width] duration-500 motion-reduce:transition-none"
                        style={{ width: `${gaugePercent}%` }}
                    />
                </div>
                <div className="mt-1 flex justify-between text-[11px] tabular-nums text-slate-400">
                    <span>0</span>
                    <span>30</span>
                    <span>60 L/100 km</span>
                </div>
            </div>

            <div className="mt-4 border-t border-slate-100 pt-3">
                <h4 className="mb-1 text-xs font-medium text-slate-500">Telemetry coverage</h4>
                <dl className="grid grid-cols-2 gap-x-6">
                    {coverage.map((c) => (
                        <div key={c.label} className="flex items-baseline justify-between gap-3 border-b border-slate-100 py-1.5">
                            <dt className="min-w-0 text-xs text-slate-600">
                                {c.label}
                                <span className="block truncate text-[11px] text-slate-400">{c.note}</span>
                            </dt>
                            <dd className={`text-[13px] font-semibold tabular-nums ${c.warn ? "text-amber-800" : "text-slate-900"}`}>{c.value}</dd>
                        </div>
                    ))}
                </dl>
                {telemetryUnknown > 0 && (
                    <p className="mt-2 text-[11px] text-amber-700">
                        {telemetryUnknown} tracked vehicle{telemetryUnknown === 1 ? "" : "s"} with unknown Wialon distance (excluded from L/100 km)
                    </p>
                )}
            </div>
        </div>
    )
}
