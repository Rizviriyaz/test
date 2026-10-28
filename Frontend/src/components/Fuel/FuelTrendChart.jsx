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
import { Calendar, Loader2, AlertTriangle } from "lucide-react"
import { getEfficiencyTrend } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/useFuelFilters"
import { compactTick, formatMeasurement, formatTrendPoint, trendNotice } from "./telemetryFormat"
import { TREND_STATUS_LABELS } from "./fuelLabels"
import { AXIS_LINE, CATEGORICAL, GRID_STROKE, SERIES_COLOR, TICK, TOOLTIP_CURSOR } from "../Common/chartTheme"

function CustomTooltip({ active, payload }) {
    if (!active || !payload || !payload.length) return null
    const point = payload[0]?.payload
    const row = (label, value) => (
        <div className="flex items-center justify-between gap-6">
            <span className="text-slate-500">{label}</span>
            <span className="font-medium tabular-nums text-slate-900">{value}</span>
        </div>
    )
    return (
        <div className="min-w-[220px] space-y-0.5 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-xs shadow-pop">
            <p className="mb-1 border-b border-slate-100 pb-1 font-medium text-slate-900">{point?.period}</p>
            {row("Fuel purchased", `${Number(point?.litres || 0).toLocaleString()} L`)}
            {row("Fuel cost", `AED ${Number(point?.cost || 0).toLocaleString()}`)}
            {row("Distance", formatMeasurement(point?.distance_km, 1, "km"))}
            <div className="mt-1 border-t border-slate-100 pt-1">
                {row(
                    "Purchase-based L/100 km",
                    point?.fuel_efficiency !== null && point?.fuel_efficiency !== undefined
                        ? point.fuel_efficiency
                        : (TREND_STATUS_LABELS[point?.efficiency_status] || "—"),
                )}
                {point?.cumulative_efficiency !== null && point?.cumulative_efficiency !== undefined &&
                    row("Cumulative", `${point.cumulative_efficiency} L/100 km`)}
            </div>
            <p className="pt-0.5 text-[11px] text-slate-400">{point?.transaction_count || 0} transactions</p>
        </div>
    )
}

const METRICS = [
    { id: "fuel", label: "Fuel (L)" },
    { id: "cost", label: "Cost (AED)" },
    { id: "distance", label: "Distance (km)" },
    { id: "combined", label: "Fuel & distance" },
    { id: "efficiency", label: "L/100 km" },
]

const segBtn = (active) =>
    `rounded px-2.5 py-1 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
        active ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]" : "text-slate-600 hover:text-slate-900"
    }`

export default function FuelTrendChart() {
    const { queryParams } = useFuelFilters()
    const [interval, setInterval] = useState("day") // 'day' | 'week' | 'month'
    const [metric, setMetric] = useState("fuel") // 'fuel' | 'cost' | 'distance' | 'combined' | 'efficiency'
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

    const xAxis = (hide = false) => (
        <XAxis dataKey="displayPeriod" tick={TICK} tickLine={false} axisLine={{ stroke: AXIS_LINE }} hide={hide} />
    )
    const grid = <CartesianGrid vertical={false} stroke={GRID_STROKE} />
    const margin = { top: 8, right: 8, left: -8, bottom: 0 }

    return (
        <section className="mb-4 rounded-xl border border-slate-200/70 bg-white p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                    <h2 className="type-section-title text-slate-900">Fuel & Distance Trend</h2>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    <div role="group" aria-label="Metric" className="inline-flex flex-wrap rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                        {METRICS.map((m) => (
                            <button key={m.id} type="button" onClick={() => setMetric(m.id)} aria-pressed={metric === m.id} className={segBtn(metric === m.id)}>
                                {m.label}
                            </button>
                        ))}
                    </div>
                    <div role="group" aria-label="Interval" className="inline-flex rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                        {["day", "week", "month"].map((mode) => (
                            <button key={mode} type="button" onClick={() => setInterval(mode)} aria-pressed={interval === mode} className={`capitalize ${segBtn(interval === mode)}`}>
                                {mode}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {!loading && !error && notice && (
                <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="status">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                    <div className="space-y-0.5">
                        <span className="block font-medium">
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

            <div className="mt-4 h-[270px] w-full">
                {loading ? (
                    <div role="status" className="flex h-full flex-col items-center justify-center text-slate-500">
                        <Loader2 className="mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                        <span className="text-xs">Loading fleet trend telemetry…</span>
                    </div>
                ) : error ? (
                    <div role="alert" className="flex h-full items-center justify-center text-xs text-rose-700">
                        {error}
                    </div>
                ) : formattedData.length === 0 ? (
                    <div className="flex h-full flex-col items-center justify-center text-xs text-slate-500">
                        <Calendar className="mb-2 h-5 w-5 text-slate-300" />
                        <span>No records in the selected period.</span>
                        <span className="mt-0.5 text-slate-400">Try a wider date range.</span>
                    </div>
                ) : metric === "fuel" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={formattedData} margin={margin}>
                            {grid}
                            {xAxis()}
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "L")} />
                            <Tooltip cursor={{ stroke: AXIS_LINE }} content={<CustomTooltip />} />
                            <Area type="monotone" dataKey="litres" stroke={SERIES_COLOR} strokeWidth={2} fill={SERIES_COLOR} fillOpacity={0.1} name="Fuel (Litres)" />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : metric === "cost" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={formattedData} margin={margin}>
                            {grid}
                            {xAxis()}
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v)} />
                            <Tooltip cursor={TOOLTIP_CURSOR} content={<CustomTooltip />} />
                            <Bar dataKey="cost" fill={SERIES_COLOR} radius={[3, 3, 0, 0]} maxBarSize={32} name="Cost (AED)" />
                        </BarChart>
                    </ResponsiveContainer>
                ) : metric === "distance" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={formattedData} margin={margin}>
                            {grid}
                            {xAxis()}
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "km")} />
                            <Tooltip cursor={{ stroke: AXIS_LINE }} content={<CustomTooltip />} />
                            <Area type="monotone" dataKey="distance_km" connectNulls={false} stroke={SERIES_COLOR} strokeWidth={2} fill={SERIES_COLOR} fillOpacity={0.1} name="Distance (km)" />
                        </AreaChart>
                    </ResponsiveContainer>
                ) : metric === "efficiency" ? (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={formattedData} margin={margin}>
                            {grid}
                            {xAxis()}
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}`} />
                            <Tooltip cursor={{ stroke: AXIS_LINE }} content={<CustomTooltip />} />
                            <Legend iconType="plainline" wrapperStyle={{ fontSize: 11, paddingTop: 8, color: "#475569" }} />
                            <Line type="monotone" dataKey="fuel_efficiency" connectNulls={false} stroke={SERIES_COLOR} strokeWidth={2} dot={{ r: 2.5, strokeWidth: 0, fill: SERIES_COLOR }} name="Per period (L/100 km)" />
                            <Line type="monotone" dataKey="cumulative_efficiency" connectNulls={false} stroke={CATEGORICAL[1]} strokeWidth={2} strokeDasharray="5 4" dot={false} name="Cumulative (L/100 km)" />
                        </LineChart>
                    </ResponsiveContainer>
                ) : (
                    /* Fuel and distance on one time axis, each with its own scale (no dual y-axis) */
                    <div className="flex h-full flex-col">
                        <p className="text-[11px] font-medium text-slate-500">Fuel purchased (L)</p>
                        <div className="min-h-0 flex-1">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={formattedData} margin={margin} syncId="fuel-distance">
                                    {grid}
                                    {xAxis(true)}
                                    <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "L")} />
                                    <Tooltip cursor={TOOLTIP_CURSOR} content={<CustomTooltip />} />
                                    <Bar dataKey="litres" fill={SERIES_COLOR} radius={[3, 3, 0, 0]} maxBarSize={28} name="Fuel (Litres)" />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                        <p className="mt-2 text-[11px] font-medium text-slate-500">Wialon distance (km)</p>
                        <div className="min-h-0 flex-1">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={formattedData} margin={margin} syncId="fuel-distance">
                                    {grid}
                                    {xAxis()}
                                    <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v, "km")} />
                                    <Tooltip cursor={TOOLTIP_CURSOR} content={() => null} />
                                    <Bar dataKey="distance_km" fill={CATEGORICAL[1]} radius={[3, 3, 0, 0]} maxBarSize={28} name="Distance (km)" />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                )}
            </div>
        </section>
    )
}
