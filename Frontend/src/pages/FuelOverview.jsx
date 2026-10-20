import { useState, useEffect } from "react"
import { Link } from "react-router-dom"
import { Award, ArrowRight, AlertCircle, RefreshCw, Layers } from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { useFuelFilters } from "../context/FuelFilterContext"
import { getFleetEfficiency, getVehicleEfficiencyList } from "../api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import KpiCards from "../components/Fuel/KpiCards"
import FuelTrendChart from "../components/Fuel/FuelTrendChart"
import FuelTypeDonut from "../components/Fuel/FuelTypeDonut"
import FleetEfficiencyGauge from "../components/Fuel/FleetEfficiencyGauge"
import VehicleStatusBadge from "../components/Fuel/VehicleStatusBadge"
import ExcludedFuelPanel from "../components/Fuel/ExcludedFuelPanel"

export default function FuelOverview() {
    const { queryParams, period, periodLabel, setPeriod } = useFuelFilters()
    const [efficiency, setEfficiency] = useState(null)
    const [topVehicles, setTopVehicles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        let active = true
        async function loadOverviewData() {
            try {
                setLoading(true)
                setError(null)
                const [effRes, vehRes] = await Promise.allSettled([
                    getFleetEfficiency(queryParams),
                    getVehicleEfficiencyList({
                        page: 1,
                        page_size: 5,
                        sort_by: "litres",
                        sort_order: "desc",
                        ...queryParams,
                    }),
                ])

                if (!active) return

                if (effRes.status === "fulfilled") {
                    setEfficiency(effRes.value)
                } else {
                    // Never leave the previous period's KPIs on screen next to the error
                    setEfficiency(null)
                    setError(effRes.reason?.message || "Failed to load fleet efficiency metrics.")
                }

                if (vehRes.status === "fulfilled" && Array.isArray(vehRes.value?.data)) {
                    setTopVehicles(vehRes.value.data)
                } else {
                    setTopVehicles([])
                    if (effRes.status === "fulfilled") {
                        setError(vehRes.reason?.message || "Failed to load the top vehicles list.")
                    }
                }
            } catch (err) {
                if (!active) return
                console.error("Overview error:", err)
                setError(err.message || "Failed to load dashboard data.")
            } finally {
                if (active) setLoading(false)
            }
        }

        loadOverviewData()
        return () => { active = false }
    }, [queryParams])

    // "No data" only when nothing was fetched at all; rows excluded from KPIs are shown in the excluded panel
    const hasNoData = !loading && efficiency?.excluded_scope?.all_transaction_count === 0

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-white text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex-1 overflow-y-auto px-6 lg:px-8 py-8">
                        {/* 1. Header with Global Filters */}
                        <FuelHeader
                            title="ATS Fleet Fuel Performance"
                            subtitle={`Fleet Fuel Performance · ${periodLabel}`}
                        />

                        {/* Error Message */}
                        {error && (
                            <div className="mb-6 flex items-center gap-3 rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
                                <AlertCircle className="h-5 w-5 shrink-0 text-rose-600" />
                                <span>{error}</span>
                            </div>
                        )}

                        {/* Empty State Banner if no records for selected period */}
                        {hasNoData && (
                            <div className="mb-6 rounded-2xl border border-amber-200 bg-amber-50/60 p-5 shadow-xs">
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                                    <div className="flex items-center gap-3">
                                        <div className="rounded-xl bg-amber-100 p-2 text-amber-700">
                                            <AlertCircle className="h-5 w-5" />
                                        </div>
                                        <div>
                                            <h4 className="text-xs font-bold text-amber-900">
                                                No Fuel Transactions Recorded for {periodLabel}
                                            </h4>
                                            <p className="text-[11px] text-amber-700 mt-0.5">
                                                The selected filter returned 0 refuels. Switch to All Available Data or specify a custom date range.
                                            </p>
                                        </div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setPeriod("all")}
                                        className="inline-flex items-center gap-1.5 rounded-xl bg-amber-700 px-3.5 py-2 text-xs font-bold text-white hover:bg-amber-800 transition-colors shadow-2xs cursor-pointer self-start sm:self-auto"
                                    >
                                        <Layers className="h-3.5 w-3.5" />
                                        <span>View All Historical Records</span>
                                    </button>
                                </div>
                            </div>
                        )}

                        {/* 2. Primary 4 KPI Cards */}
                        <KpiCards efficiencyData={efficiency} loading={loading} />

                        {/* Transactions excluded from KPIs (no single Wialon unit, suspected re-imports) */}
                        <ExcludedFuelPanel />

                        {/* 3. Main Fleet Trend Chart */}
                        <FuelTrendChart />

                        {/* 4. Fuel Type Breakdown & Fleet Efficiency Gauge */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
                            <FuelTypeDonut />
                            <FleetEfficiencyGauge efficiencyData={efficiency} loading={loading} />
                        </div>

                        {/* 5. Top Vehicle Consumption Preview */}
                        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
                            <div className="flex items-center justify-between pb-4 border-b border-slate-100">
                                <div className="flex items-center gap-2">
                                    <div className="rounded-xl bg-[#33758F]/10 p-1.5 text-[#33758F]">
                                        <Award className="h-4 w-4" />
                                    </div>
                                    <div>
                                        <h3 className="text-sm font-bold text-slate-900">
                                            Vehicle Performance Highlights
                                        </h3>
                                        <p className="text-[11px] text-slate-500">
                                            Leading fuel consumers for the selected period
                                        </p>
                                    </div>
                                </div>
                                <Link
                                    to="/fuel/ranking"
                                    className="inline-flex items-center gap-1.5 text-xs font-bold text-[#33758F] hover:text-[#285d73] transition-colors"
                                >
                                    <span>Full Rankings</span>
                                    <ArrowRight className="h-3.5 w-3.5" />
                                </Link>
                            </div>

                            <div className="pt-3 divide-y divide-slate-100">
                                {loading ? (
                                    <div className="py-6 text-center text-xs text-slate-400">
                                        Loading vehicle rankings…
                                    </div>
                                ) : topVehicles.length === 0 ? (
                                    <div className="py-6 text-center text-xs text-slate-400">
                                        No vehicles found for this period.
                                    </div>
                                ) : (
                                    topVehicles.map((v, idx) => (
                                        <div
                                            key={v.vehicle_identifier}
                                            className="flex flex-col sm:flex-row sm:items-center justify-between py-3 gap-2"
                                        >
                                            <div className="flex items-center gap-3">
                                                <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-slate-100 text-[11px] font-bold text-slate-600">
                                                    #{idx + 1}
                                                </span>
                                                <div>
                                                    <span className="text-xs font-black tracking-wide text-slate-900">
                                                        {v.vehicle_identifier}
                                                    </span>
                                                    {v.wialon_unit_name && (
                                                        <span className="ml-2 text-[11px] text-slate-400 font-medium">
                                                             ({v.wialon_unit_name})
                                                        </span>
                                                    )}
                                                </div>
                                                <VehicleStatusBadge status={v.status} />
                                            </div>

                                            <div className="flex items-center gap-4 text-xs font-semibold">
                                                <div className="text-right">
                                                    <span className="text-slate-900 font-bold">
                                                        {Number(v.litres || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} L
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Fuel</span>
                                                </div>
                                                <div className="text-right">
                                                    <span className="text-emerald-700 font-bold">
                                                        AED {Number(v.total_cost || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Cost</span>
                                                </div>
                                                <div className="text-right min-w-[70px]">
                                                    <span className="text-sky-700 font-bold">
                                                        {v.distance_km !== null ? `${Number(v.distance_km).toLocaleString()} km` : "—"}
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Distance</span>
                                                </div>
                                                <div className="text-right min-w-[75px]">
                                                    <span className="text-[#33758F] font-bold">
                                                        {v.fuel_efficiency_l_per_100km !== null ? `${v.fuel_efficiency_l_per_100km.toFixed(1)} L/100km` : "—"}
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Efficiency</span>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </div>
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
