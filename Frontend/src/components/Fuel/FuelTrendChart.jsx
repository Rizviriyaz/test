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
import { TrendingUp, Calendar, Loader2 } from "lucide-react"
import { getEfficiencyTrend } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/FuelFilterContext"

export default function FuelTrendChart() {
    const { queryParams } = useFuelFilters()
    const [interval, setInterval] = useState("day") // 'day' | 'week' | 'month'
    const [metric, setMetric] = useState("fuel") // 'fuel' | 'cost' | 'distance' | 'combined'
    const [trendData, setTrendData] = useState([])
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
            } catch (err) {
                if (!active) return
                console.error("Failed to fetch fuel trend:", err)
                setError(err.message || "Failed to load trend analytics.")
            } finally {
                if (active) setLoading(false)
            }
        }

        fetchTrend()
        return () => { active = false }
    }, [interval, queryParams])

    const formattedData = trendData.map((d) => ({
        ...d,
        displayPeriod: d.period ? d.period.slice(5) : "", // e.g. '08-15'
        litres: Math.round((d.litres || 0) * 10) / 10,
        cost: Math.round(d.cost || 0),
        distance_km: Math.round((d.distance_km || 0) * 10) / 10,
        fuel_efficiency: d.fuel_efficiency_l_per_100km ? Math.round(d.fuel_efficiency_l_per_100km * 10) / 10 : null,
    }))

    const CustomTooltip = ({ active, payload, label }) => {
        if (!active || !payload || !payload.length) return null
        const point = payload[0]?.payload
        return (
            <div className="rounded-xl border border-slate-200 bg-white/95 p-3 shadow-lg backdrop-blur-xs text-xs space-y-1">
                <p className="font-bold text-slate-800 border-b border-slate-100 pb-1 mb-1">
                    Period: {point?.period}
                </p>
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Fuel Consumed:</span>
                    <span className="font-bold text-[#1C5E7A]">{Number(point?.litres || 0).toLocaleString()} L</span>
                </div>
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Fuel Cost:</span>
                    <span className="font-bold text-emerald-600">AED {Number(point?.cost || 0).toLocaleString()}</span>
                </div>
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Distance Traveled:</span>
                    <span className="font-bold text-sky-600">{Number(point?.distance_km || 0).toLocaleString()} km</span>
                </div>
                {point?.fuel_efficiency && (
                    <div className="flex items-center justify-between gap-4 pt-1 border-t border-slate-100">
                        <span className="text-slate-500">Efficiency:</span>
                        <span className="font-bold text-slate-700">{point.fuel_efficiency} L/100 km</span>
                    </div>
                )}
                <div className="text-[10px] text-slate-400">
                    {point?.transaction_count || 0} refuel transactions
                </div>
            </div>
        )
    }

    return (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs mb-6">
            {/* Header with Title and Interactive Controls */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-slate-100">
                <div className="flex items-center gap-2.5">
                    <div className="rounded-xl bg-[#1C5E7A]/10 p-2 text-[#1C5E7A]">
                        <TrendingUp className="h-4 w-4" />
                    </div>
                    <div>
                        <h2 className="text-sm font-bold text-slate-900">
                            Fleet Consumption & Mileage Trends
                        </h2>
                        <p className="text-[11px] text-slate-500">
                            Chronological analysis of fuel consumption, cost, and GPS distance
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
                                    ? "bg-white text-[#1C5E7A] shadow-xs font-bold"
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

            {/* Chart Area */}
            <div className="pt-4 h-[320px] w-full">
                {loading ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-400">
                        <Loader2 className="h-6 w-6 animate-spin text-[#1C5E7A] mb-2" />
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
                                    <stop offset="5%" stopColor="#1C5E7A" stopOpacity={0.4} />
                                    <stop offset="95%" stopColor="#1C5E7A" stopOpacity={0.0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k L`} />
                            <Tooltip content={<CustomTooltip />} />
                            <Area type="monotone" dataKey="litres" stroke="#1C5E7A" strokeWidth={2.5} fillOpacity={1} fill="url(#fuelGradient)" name="Fuel (Litres)" />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : metric === "cost" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
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
                            <YAxis tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k km`} />
                            <Tooltip content={<CustomTooltip />} />
                            <Area type="monotone" dataKey="distance_km" stroke="#0284C7" strokeWidth={2.5} fillOpacity={1} fill="url(#distanceGradient)" name="Distance (km)" />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : (
                    /* Combined Multi-Axis View */
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={formattedData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                            <XAxis dataKey="displayPeriod" tick={{ fontSize: 11, fill: "#64748B" }} tickLine={false} axisLine={{ stroke: "#E2E8F0" }} />
                            <YAxis yAxisId="left" tick={{ fontSize: 11, fill: "#1C5E7A" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k L`} />
                            <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: "#0284C7" }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k km`} />
                            <Tooltip content={<CustomTooltip />} />
                            <Legend wrapperStyle={{ fontSize: 11, paddingTop: 10 }} />
                            <Bar yAxisId="left" dataKey="litres" fill="#1C5E7A" radius={[4, 4, 0, 0]} name="Fuel (Litres)" />
                            <Bar yAxisId="right" dataKey="distance_km" fill="#38BDF8" radius={[4, 4, 0, 0]} name="Distance (km)" />
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    )
}
