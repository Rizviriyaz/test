import { useState, useEffect } from "react"
import {
    ResponsiveContainer,
    BarChart,
    Bar,
    LineChart,
    Line,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend,
    AreaChart,
    Area,
} from "recharts"
import { TrendingUp, Calendar, Loader2, AlertTriangle } from "lucide-react"
import { getEfficiencyTrend } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/useFuelFilters"
import { compactTick, formatMeasurement, formatTrendPoint, trendNotice } from "./telemetryFormat"
import { TREND_STATUS_LABELS } from "./fuelLabels"

function CustomTooltip({ active, payload }) {
    if (!active || !payload || !payload.length) return null
    const point = payload[0]?.payload
    return (
        <div className="rounded-xl border border-slate-200 bg-white/95 p-3 shadow-lg backdrop-blur-xs text-xs space-y-1">
            <p className="font-bold text-slate-800 border-b border-slate-100 pb-1 mb-1">
                Period: {point?.period}
            </p>
            <div className="flex items-center justify-between gap-4">
                <span className="text-slate-500">Fuel Purchased:</span>
                <span className="font-bold text-[#33758F]">{Number(point?.litres || 0).toLocaleString()} L</span>
            </div>
            <div className="flex items-center justify-between gap-4">
                <span className="text-slate-500">Fuel Cost:</span>
                <span className="font-bold text-emerald-600">AED {Number(point?.cost || 0).toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between gap-4">
                <span className="text-slate-500">Distance Traveled:</span>
                <span className="font-bold text-sky-600">{formatMeasurement(point?.distance_km, 1, "km")}</span>
            </div>
            <div className="flex items-center justify-between gap-4 pt-1 border-t border-slate-100">
                <span className="text-slate-500">Purchase-based L/100 km:</span>
                <span className="font-bold text-slate-700">
                    {point?.fuel_efficiency !== null && point?.fuel_efficiency !== undefined
                        ? point.fuel_efficiency
                        : (TREND_STATUS_LABELS[point?.efficiency_status] || "—")}
                </span>
            </div>
            {point?.cumulative_efficiency !== null && point?.cumulative_efficiency !== undefined && (
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Cumulative:</span>
                    <span className="font-bold text-indigo-700">{point.cumulative_efficiency} L/100 km</span>
                </div>
            )}
            <div className="text-[10px] text-slate-400">
                {point?.transaction_count || 0} refuel transactions
            </div>
        </div>
    )
}

export default function FuelTrendChart() {
    const { queryParams } = useFuelFilters()
    const [interval, setInterval] = useState("day") // 'day' | 'week' | 'month'
    const [metric, setMetric] = useState("fuel") // 'fuel' | 'cost' | 'distance' | 'combined'
    const [trendData, setTrendData] = useState([])
    const [notice, setNotice] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        let active = true
        async function fetchTrend() {
            try {
                setLoading(true)
                setError(null)
                const res = await getEfficiencyTrend({
                    group_by: interval,
                    ...queryParams,
                })
                if (!active) return
                setTrendData(Array.isArray(res?.data) ? res.data : [])
                setNotice(trendNotice(res))
            } catch (err) {
                if (!active) return
                console.error("Failed to fetch fuel trend:", err)
                setError(err.message || "Failed to load trend analytics.")
                setNotice(null)
            } finally {
                if (active) setLoading(false)
            }
        }

        fetchTrend()
        return () => { active = false }
    }, [interval, queryParams])

    const formattedData = trendData.map(formatTrendPoint)

    return (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs mb-6">
            {/* Header with Title and Interactive Controls */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-slate-100">
                <div className="flex items-center gap-2.5">
                    <div className="rounded-xl bg-[#33758F]/10 p-2 text-[#33758F]">
                        <TrendingUp className="h-4 w-4" />
                    </div>
                    <div>
                        <h2 className="text-sm font-bold text-slate-900">
                            Fuel Purchased & Wialon Distance Trends
                        </h2>
                        <p className="text-[11px] text-slate-500">
                            Fuel purchased (recorded) · Wialon trip distance per period (recorded) · L/100 km = purchased ÷ distance (derived, not measured)
                        </p>
                    </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    {/* Metric Selector */}
                    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 text-xs font-semibold">
                        <button
                            type="button"
                            onClick={() => setMetric("fuel")}
                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                metric === "fuel"
                                    ? "bg-white text-[#33758F] shadow-xs font-bold"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                        >
                            Fuel (L)
                        </button>
                        <button
                            type="button"
                            onClick={() => setMetric("cost")}
                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                metric === "cost"
                                    ? "bg-white text-emerald-700 shadow-xs font-bold"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                        >
                            Cost (AED)
                        </button>
                        <button
                            type="button"
                            onClick={() => setMetric("distance")}
                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                metric === "distance"
                                    ? "bg-white text-sky-700 shadow-xs font-bold"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                        >
                            Distance (km)
                        </button>
                        <button
                            type="button"
                            onClick={() => setMetric("combined")}
                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                metric === "combined"
                                    ? "bg-white text-indigo-700 shadow-xs font-bold"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                        >
                            Combined
                        </button>
                        <button
                            type="button"
                            onClick={() => setMetric("efficiency")}
                            className={`rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                metric === "efficiency"
                                    ? "bg-white text-[#33758F] shadow-xs font-bold"
                                    : "text-slate-600 hover:text-slate-900"
                            }`}
                        >
                            L/100 km
                        </button>
                    </div>

                    {/* Grouping Interval Selector */}
                    <div className="inline-flex rounded-xl bg-slate-100 p-0.5 text-xs font-semibold">
                        {["day", "week", "month"].map((mode) => (
                            <button
                                key={mode}
                                type="button"
                                onClick={() => setInterval(mode)}
                                className={`capitalize rounded-lg px-2.5 py-1 transition-colors cursor-pointer ${
                                    interval === mode
                                        ? "bg-white text-slate-900 shadow-xs font-bold"
                                        : "text-slate-600 hover:text-slate-900"
                                }`}
                            >
                                {mode}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {!loading && !error && notice && (
                <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50/70 p-2.5 text-[11px] text-amber-900 flex items-start gap-2" role="status">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
                    <div className="space-y-0.5">
                        <span className="font-bold block">
                            Wialon telemetry {notice.status}: periods without reported distance are left blank, not zero.
                        </span>
                        {notice.unitsFailed > 0 && (
                            <span className="block">{notice.unitsFailed} unit(s) had no complete Wialon trip report; their distance is excluded.</span>
                        )}
                        {notice.mismatches > 0 && (
                            <span className="block">{notice.mismatches} unit(s) differ between trip and group reports; shown unadjusted.</span>
                        )}
                        {notice.warnings.map((w) => (
                            <span key={w} className="block">{w}</span>
                        ))}
                    </div>
                </div>
            )}

            {/* Chart Area */}
            <div className="pt-4 h-[320px] w-full">
                {loading ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-400">
                        <Loader2 className="h-6 w-6 animate-spin text-[#33758F] mb-2" />
                        <span className="text-xs font-semibold">Loading fleet trend telemetry…</span>
                    </div>
                ) : error ? (
                    <div className="h-full flex items-center justify-center text-rose-500 text-xs font-semibold">
                        {error}
                    </div>
                ) : formattedData.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-400 text-xs">
                        <Calendar className="h-8 w-8 text-slate-300 mb-2" />
                        <span>No chronological records found for the selected period.</span>
                        <span className="text-[11px] text-slate-400 mt-1">Try expanding the date range in Global Filters.</span>
                    </div>
                ) : metric === "fuel" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <defs>
                                <linearGradient id="fuelGradient" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#33758F" stopOpacity={0.4} />
                                    <stop offset="95%" stopColor="#33758F" stopOpacity={0.0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "L")} />
                            <Tooltip content={<CustomTooltip />} />
                            <Area type="monotone" dataKey="litres" stroke="#33758F" strokeWidth={2.5} fillOpacity={1} fill="url(#fuelGradient)" name="Fuel (Litres)" />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : metric === "cost" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v)} />
                            <Tooltip content={<CustomTooltip />} />
                            <Bar dataKey="cost" fill="#10B981" radius={[6, 6, 0, 0]} name="Cost (AED)" />
                        </BarChart>
                    </ResponsiveContainer>
                ) : metric === "distance" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <defs>
                                <linearGradient id="distanceGradient" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#0284C7" stopOpacity={0.35} />
                                    <stop offset="95%" stopColor="#0284C7" stopOpacity={0.0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "km")} />
                            <Tooltip content={<CustomTooltip />} />
                            <Area type="monotone" dataKey="distance_km" connectNulls={false} stroke="#0284C7" strokeWidth={2.5} fillOpacity={1} fill="url(#distanceGradient)" name="Distance (km)" />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : metric === "efficiency" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}`} />
                            <Tooltip content={<CustomTooltip />} />
                            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 10 }} />
                            <Line type="monotone" dataKey="fuel_efficiency" connectNulls={false} stroke="#33758F" strokeWidth={2} dot={{ r: 3 }} name="Per period (L/100 km)" />
                            <Line type="monotone" dataKey="cumulative_efficiency" connectNulls={false} stroke="#6366F1" strokeWidth={2} strokeDasharray="5 4" dot={false} name="Cumulative (L/100 km)" />
                        </LineChart>
                    </ResponsiveContainer>
                ) : (
                    /* Combined Multi-Axis View */
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis yAxisId="left" tick={{ fontSize: 11, fill: "#33758F" }} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "L")} />
                            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: "#0284C7" }} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "km")} />
                            <Tooltip content={<CustomTooltip />} />
                            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 10 }} />
                            <Bar yAxisId="left" dataKey="litres" fill="#33758F" radius={[4, 4, 0, 0]} name="Fuel (Litres)" />
                            <Bar yAxisId="right" dataKey="distance_km" fill="#38BDF8" radius={[4, 4, 0, 0]} name="Distance (km)" />
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    )
}
