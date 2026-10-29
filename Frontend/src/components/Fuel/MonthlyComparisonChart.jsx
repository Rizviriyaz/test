import { useEffect, useMemo, useState } from "react"
import { ResponsiveContainer, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid, Tooltip } from "recharts"
import { AlertTriangle } from "lucide-react"
import SectionCard from "../Common/SectionCard"
import { ErrorState, EmptyState } from "../Common/StateViews"
import { getEfficiencyTrend } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/useFuelFilters"
import { getUAETodayStr } from "../../utils/datetime"
import { alignMonths, comparisonWindow, isCurrentMonth, monthLabel } from "./fuelPeriod"
import { compactTick, formatMeasurement, isKnownNumber, trendNotice } from "./telemetryFormat"
import { TREND_STATUS_LABELS } from "./fuelLabels"
import { AXIS_LINE, GRID_STROKE, SERIES_COLOR, TICK, TOOLTIP_CURSOR, TOOLTIP_STYLE } from "../Common/chartTheme"

const METRICS = [
    { id: "litres", label: "Fuel", unit: "L", decimals: 0 },
    { id: "cost", label: "Cost", unit: "AED", decimals: 0 },
    { id: "distance_km", label: "Distance", unit: "km", decimals: 0 },
    { id: "fuel_efficiency_l_per_100km", label: "L/100 km", unit: "L/100 km", decimals: 1 },
]

const segBtn = (active) =>
    `rounded px-2.5 py-1 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
        active ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]" : "text-slate-600 hover:text-slate-900"
    }`

const value = (row, key) => (row && isKnownNumber(row[key]) ? row[key] : null)

// Table cell text: a month without a row is "—" (no records), an unknown ratio says why when the API does
function cellText(row, key, decimals) {
    if (!row) return { text: "—", title: "No records returned for this month" }
    const v = value(row, key)
    if (v !== null) return { text: formatMeasurement(v, decimals) }
    if (key === "fuel_efficiency_l_per_100km" || key === "km_per_l" || key === "fuel_cost_per_km") {
        return { text: "—", title: TREND_STATUS_LABELS[row.efficiency_status] || "Unavailable" }
    }
    return { text: "—", title: "Unavailable" }
}

/**
 * Selected month and the five before it, from the monthly efficiency trend (fuel, cost, distance, efficiency).
 * `enabled` false defers the request (shown as loading, never as the previous window's data).
 */
export default function MonthlyComparisonChart({ endMonth, enabled = true }) {
    const { vehicle, fuelType } = useFuelFilters()
    const [metric, setMetric] = useState("litres")
    const [rows, setRows] = useState([])
    const [notice, setNotice] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [attempt, setAttempt] = useState(0)

    const range = useMemo(() => comparisonWindow(endMonth), [endMonth])

    useEffect(() => {
        if (!enabled) return undefined
        let active = true
        async function fetchComparison() {
            try {
                setLoading(true)
                setError(null)
                const res = await getEfficiencyTrend({
                    group_by: "month",
                    date_from: range.from,
                    date_to: range.to,
                    ...(vehicle ? { vehicle_identifier: vehicle } : {}),
                    ...(fuelType ? { fuel_type: fuelType } : {}),
                })
                if (!active) return
                setRows(Array.isArray(res?.data) ? res.data : [])
                setNotice(trendNotice(res))
            } catch (err) {
                if (!active) return
                setRows([])
                setNotice(null)
                setError(err.message || "Failed to load the monthly comparison.")
            } finally {
                if (active) setLoading(false)
            }
        }
        fetchComparison()
        return () => { active = false }
    }, [enabled, range, vehicle, fuelType, attempt])

    const today = getUAETodayStr()
    const aligned = alignMonths(range.months, rows).map(({ month, row }) => {
        const l100 = value(row, "fuel_efficiency_l_per_100km")
        return {
            month,
            row: row && { ...row, km_per_l: l100 && l100 > 0 ? 100 / l100 : null },
            label: `${monthLabel(month).slice(0, 3)}${isCurrentMonth(month, today) ? " (MTD)" : ""}`,
            chartValue: value(row, metric),
        }
    })
    const activeMetric = METRICS.find((m) => m.id === metric)
    const hasAny = aligned.some((a) => a.row)

    const tableRows = [
        { key: "litres", label: "Fuel (L)", decimals: 0 },
        { key: "cost", label: "Cost (AED)", decimals: 0 },
        { key: "distance_km", label: "Distance (km)", decimals: 0 },
        { key: "fuel_efficiency_l_per_100km", label: "L/100 km", decimals: 1 },
        { key: "km_per_l", label: "km/L", decimals: 2 },
        { key: "fuel_cost_per_km", label: "AED/km", decimals: 2 },
    ]

    return (
        <SectionCard
            title="6-Month Comparison"
            subtitle={`${monthLabel(range.months[0])} – ${monthLabel(endMonth)}`}
            actions={
                <div role="group" aria-label="Metric" className="inline-flex rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                    {METRICS.map((m) => (
                        <button key={m.id} type="button" onClick={() => setMetric(m.id)} aria-pressed={metric === m.id} className={segBtn(metric === m.id)}>
                            {m.label}
                        </button>
                    ))}
                </div>
            }
        >
            {loading || !enabled ? (
                <div className="h-[260px] animate-pulse rounded bg-slate-50 motion-reduce:animate-none" role="status" aria-label="Loading monthly comparison" />
            ) : error ? (
                <ErrorState message={error} onRetry={() => setAttempt((n) => n + 1)} className="border-0" />
            ) : !hasAny ? (
                <EmptyState title="No records in these six months" className="border-0" />
            ) : (
                <>
                    {notice && (
                        <p className="mb-3 flex items-start gap-1.5 text-[11px] text-amber-800" role="status" title={notice.warnings.join(" ")}>
                            <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
                            Wialon distance {notice.status}: months without reported distance are blank, not zero.
                        </p>
                    )}

                    <div className="h-[170px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={aligned} margin={{ top: 4, right: 4, left: -8, bottom: 0 }}>
                                <CartesianGrid vertical={false} stroke={GRID_STROKE} />
                                <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={{ stroke: AXIS_LINE }} />
                                <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v)} />
                                <Tooltip
                                    cursor={TOOLTIP_CURSOR}
                                    contentStyle={TOOLTIP_STYLE}
                                    labelFormatter={(_, payload) => monthLabel(payload?.[0]?.payload?.month || "", true)}
                                    formatter={(v) => [v === null || v === undefined ? "—" : `${formatMeasurement(v, activeMetric.decimals)} ${activeMetric.unit}`, activeMetric.label]}
                                />
                                <Bar dataKey="chartValue" radius={[3, 3, 0, 0]} maxBarSize={40} isAnimationActive={false}>
                                    {aligned.map((a) => (
                                        <Cell key={a.month} fill={SERIES_COLOR} fillOpacity={a.month === endMonth ? 1 : 0.4} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>

                    <div className="mt-3 overflow-x-auto">
                        <table className="w-full min-w-[520px] text-xs">
                            <thead>
                                <tr className="border-b border-slate-100 text-[11px] text-slate-500">
                                    <th scope="col" className="py-1.5 pr-3 text-left font-medium">Metric</th>
                                    {aligned.map((a) => (
                                        <th key={a.month} scope="col" className={`px-2 py-1.5 text-right font-medium ${a.month === endMonth ? "text-slate-900" : ""}`}>
                                            {a.label}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-50">
                                {tableRows.map((r) => (
                                    <tr key={r.key}>
                                        <th scope="row" className="py-1.5 pr-3 text-left font-normal text-slate-500">{r.label}</th>
                                        {aligned.map((a) => {
                                            const c = cellText(a.row, r.key, r.decimals)
                                            return (
                                                <td key={a.month} title={c.title} className={`px-2 py-1.5 text-right tabular-nums ${a.month === endMonth ? "font-semibold text-slate-900" : "text-slate-700"} ${c.text === "—" ? "text-slate-400" : ""}`}>
                                                    {c.text}
                                                </td>
                                            )
                                        })}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <p className="mt-2 text-[11px] text-slate-400">
                        Distance here: Wialon trips by start month. The Distance KPI uses the group trips report, so values can differ.
                    </p>
                </>
            )}
        </SectionCard>
    )
}
