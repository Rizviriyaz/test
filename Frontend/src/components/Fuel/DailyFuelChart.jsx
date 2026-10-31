import { useEffect, useState } from "react"
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip } from "recharts"
import SectionCard from "../Common/SectionCard"
import { ErrorState, EmptyState } from "../Common/StateViews"
import { getEfficiencyTrend } from "../../api/fleetApi"
import { compactTick, formatMeasurement, isKnownNumber } from "./telemetryFormat"
import { AXIS_LINE, GRID_STROKE, SERIES_COLOR, TICK, TOOLTIP_CURSOR, TOOLTIP_STYLE } from "../Common/chartTheme"

const dayLabel = (period) => String(period || "").slice(8, 10)
const known = (v, digits, unit) => (isKnownNumber(v) ? `${formatMeasurement(v, digits)} ${unit}` : "—")

/**
 * Daily fuel of the matched fleet for one month, from the daily efficiency trend. Rendered by the page only when the
 * month's reporting scope holds dated rows (fuelPeriod.hasReportedDailyRows): the backend never spreads a monthly
 * summary over days, so only days with dated records have bars, and a day without a row is absent, not 0.
 */
export default function DailyFuelChart({ dateFrom, dateTo, enabled = true }) {
    const [state, setState] = useState({ loading: true, error: null, rows: [] })
    const [attempt, setAttempt] = useState(0)

    useEffect(() => {
        if (!enabled) return undefined
        let active = true
        // eslint-disable-next-line react-hooks/set-state-in-effect -- the request shows its loading state while it runs
        setState((s) => ({ ...s, loading: true, error: null }))
        getEfficiencyTrend({ group_by: "day", date_from: dateFrom, date_to: dateTo })
            .then((res) => {
                if (!active) return
                const rows = (Array.isArray(res?.data) ? res.data : []).filter((r) => Number(r?.transaction_count) > 0)
                setState({ loading: false, error: null, rows })
            })
            .catch((err) => {
                if (active) setState({ loading: false, error: err?.message || "Failed to load daily fuel.", rows: [] })
            })
        return () => { active = false }
    }, [enabled, dateFrom, dateTo, attempt])

    return (
        <SectionCard title="Daily Fuel">
            {state.loading || !enabled ? (
                <div className="h-[200px] animate-pulse rounded bg-slate-50 motion-reduce:animate-none" role="status" aria-label="Loading daily fuel" />
            ) : state.error ? (
                <ErrorState message={state.error} onRetry={() => setAttempt((n) => n + 1)} className="border-0" />
            ) : state.rows.length === 0 ? (
                <EmptyState title="No dated fuel records in this month" className="border-0" />
            ) : (
                <div className="h-[200px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={state.rows} margin={{ top: 4, right: 4, left: -8, bottom: 0 }}>
                            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
                            <XAxis dataKey="period" tickFormatter={dayLabel} tick={TICK} tickLine={false} axisLine={{ stroke: AXIS_LINE }} />
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={(v) => compactTick(v)} />
                            <Tooltip
                                cursor={TOOLTIP_CURSOR}
                                contentStyle={TOOLTIP_STYLE}
                                labelFormatter={(period) => period}
                                formatter={(v, _name, item) => [
                                    `${known(v, 1, "L")} · AED ${known(item?.payload?.cost, 0, "").trim()} · ${known(item?.payload?.distance_km, 0, "km")}`,
                                    "Fuel · Cost · Distance",
                                ]}
                            />
                            <Bar dataKey="litres" fill={SERIES_COLOR} radius={[3, 3, 0, 0]} maxBarSize={24} isAnimationActive={false} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            )}
        </SectionCard>
    )
}
