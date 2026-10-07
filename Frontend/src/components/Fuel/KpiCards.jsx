import { Droplets, DollarSign, Route, Gauge } from "lucide-react"

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

export default function KpiCards({ efficiencyData, loading }) {
    if (loading) {
        return (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
                {[1, 2, 3, 4].map((i) => (
                    <div
                        key={i}
                        className="animate-pulse rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs"
                    >
                        <div className="flex items-center justify-between mb-3">
                            <div className="h-3 w-24 bg-slate-200 rounded" />
                            <div className="h-8 w-8 bg-slate-100 rounded-xl" />
                        </div>
                        <div className="h-7 w-32 bg-slate-200 rounded mb-2" />
                        <div className="h-3 w-20 bg-slate-100 rounded" />
                    </div>
                ))}
            </div>
        )
    }

    const totalLitres = efficiencyData?.total_litres
    const totalCost = efficiencyData?.total_cost
    const totalDistance = efficiencyData?.total_distance_km
    const efficiency = efficiencyData?.fuel_efficiency_l_per_100km
    const costPerKm = efficiencyData?.fuel_cost_per_km
    const trackedCount = efficiencyData?.vehicles_with_distance || efficiencyData?.matched_vehicle_count || 0
    const totalVehicles = efficiencyData?.vehicle_count || 0

    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            {/* 1. Fuel Consumed */}
            <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                <div className="flex items-center justify-between mb-2.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                        Fuel Consumed
                    </span>
                    <div className="rounded-xl bg-amber-50 p-2 text-amber-600 border border-amber-100">
                        <Droplets className="h-4 w-4" />
                    </div>
                </div>
                <div className="flex items-baseline gap-1.5">
                    <span className="text-2xl font-black tracking-tight text-slate-900">
                        {formatNumber(totalLitres, 1)}
                    </span>
                    <span className="text-xs font-bold text-slate-400">Litres</span>
                </div>
                <div className="mt-2 flex items-center text-[11px] text-slate-500">
                    <span>{efficiencyData?.transaction_count ? `${formatCurrency(efficiencyData.transaction_count)} refuels logged` : "No transactions logged"}</span>
                </div>
            </div>

            {/* 2. Fuel Cost */}
            <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                <div className="flex items-center justify-between mb-2.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                        Fuel Expenditure
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
                <div className="mt-2 flex items-center text-[11px] text-slate-500">
                    <span>
                        {totalLitres && totalCost 
                            ? `AED ${formatNumber(totalCost / totalLitres, 2)} / Litre avg`
                            : "Fuel cost total"}
                    </span>
                </div>
            </div>

            {/* 3. Distance */}
            <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                <div className="flex items-center justify-between mb-2.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                        Distance Traveled
                    </span>
                    <div className="rounded-xl bg-sky-50 p-2 text-sky-600 border border-sky-100">
                        <Route className="h-4 w-4" />
                    </div>
                </div>
                <div className="flex items-baseline gap-1.5">
                    <span className="text-2xl font-black tracking-tight text-slate-900">
                        {totalDistance !== null && totalDistance !== undefined
                            ? formatNumber(totalDistance, 1)
                            : "—"}
                    </span>
                    <span className="text-xs font-bold text-slate-400">km</span>
                </div>
                <div className="mt-2 flex items-center text-[11px] text-slate-500">
                    <span>
                        {trackedCount > 0 
                            ? `${trackedCount} of ${totalVehicles} vehicles tracked`
                            : "No GPS distance recorded"}
                    </span>
                </div>
            </div>

            {/* 4. Fleet Efficiency */}
            <div className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4.5 shadow-xs hover:shadow-md transition-shadow">
                <div className="flex items-center justify-between mb-2.5">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                        Fleet Efficiency
                    </span>
                    <div className="rounded-xl bg-[#1C5E7A]/10 p-2 text-[#1C5E7A] border border-[#1C5E7A]/20">
                        <Gauge className="h-4 w-4" />
                    </div>
                </div>
                <div className="flex items-baseline gap-1.5">
                    <span className="text-2xl font-black tracking-tight text-slate-900">
                        {efficiency !== null && efficiency !== undefined
                            ? formatNumber(efficiency, 1)
                            : "—"}
                    </span>
                    <span className="text-xs font-bold text-slate-400">L / 100 km</span>
                </div>
                <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                    <span>
                        {costPerKm !== null && costPerKm !== undefined
                            ? `AED ${formatNumber(costPerKm, 2)} / km`
                            : "Weighted calculation"}
                    </span>
                    <span className="text-[10px] font-bold text-[#1C5E7A] bg-[#1C5E7A]/5 px-1.5 py-0.5 rounded">
                        Weighted
                    </span>
                </div>
            </div>
        </div>
    )
}
