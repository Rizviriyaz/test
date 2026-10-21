import { useState, useEffect, useMemo } from "react"
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip } from "recharts"
import { Loader2 } from "lucide-react"
import { getFuelTypes } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/useFuelFilters"
import { categoricalColor } from "../Common/chartTheme"

function CustomTooltip({ active, payload }) {
    if (!active || !payload || !payload.length) return null
    const data = payload[0]?.payload
    return (
        <div className="space-y-0.5 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-xs shadow-pop">
            <div className="flex items-center gap-2 border-b border-slate-100 pb-1 mb-1">
                <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: data.color }} />
                <span className="font-medium text-slate-900">{data.name}</span>
            </div>
            <div className="flex items-center justify-between gap-4">
                <span className="text-slate-500">Volume:</span>
                <span className="font-medium tabular-nums text-slate-900">{data.value.toLocaleString()} L</span>
            </div>
            <div className="flex items-center justify-between gap-4">
                <span className="text-slate-500">Expenditure:</span>
                <span className="font-medium tabular-nums text-slate-900">AED {data.cost.toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between gap-4">
                <span className="text-slate-500">Fleet Share:</span>
                <span className="font-medium tabular-nums text-slate-900">{data.percentage}%</span>
            </div>
        </div>
    )
}

export default function FuelTypeDonut() {
    const { queryParams } = useFuelFilters()
    const [fuelTypes, setFuelTypes] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    useEffect(() => {
        let active = true
        async function fetchTypes() {
            try {
                setLoading(true)
                setError(null)
                const res = await getFuelTypes({
                    date_from: queryParams.date_from,
                    date_to: queryParams.date_to,
                    vehicle_identifier: queryParams.vehicle_identifier,
                })
                if (!active) return
                setFuelTypes(Array.isArray(res?.data) ? res.data : [])
            } catch (err) {
                if (!active) return
                console.error("Failed to fetch fuel types breakdown:", err)
                setError(err.message || "Failed to load fuel types.")
            } finally {
                if (active) setLoading(false)
            }
        }

        fetchTypes()
        return () => { active = false }
    }, [queryParams])

    const totalLitres = useMemo(() => {
        return fuelTypes.reduce((sum, item) => sum + (Number(item.litres) || 0), 0)
    }, [fuelTypes])

    const chartData = useMemo(() => {
        return fuelTypes.map((item, idx) => {
            const litres = Number(item.litres) || 0
            const pct = totalLitres > 0 ? (litres / totalLitres) * 100 : 0
            return {
                name: item.fuel_type || "Unknown",
                value: litres,
                cost: Number(item.cost) || 0,
                transactions: item.transactions || 0,
                percentage: Math.round(pct * 10) / 10,
                color: categoricalColor(idx),
            }
        })
    }, [fuelTypes, totalLitres])

    return (
        <div className="flex flex-col justify-between rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-baseline justify-between gap-2">
                <h3 className="type-section-title text-slate-900">Fuel products</h3>
                <span className="text-xs text-slate-500">
                    {chartData.length} {chartData.length === 1 ? "type" : "types"}
                </span>
            </div>

            <div className="my-3 flex-1 min-h-[190px] flex items-center justify-center">
                {loading ? (
                    <div className="flex flex-col items-center justify-center text-slate-400">
                        <Loader2 className="mb-1 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                        <span className="text-xs">Loading breakdown…</span>
                    </div>
                ) : error ? (
                    <div role="alert" className="text-xs text-rose-700">{error}</div>
                ) : chartData.length === 0 ? (
                    <div className="text-xs text-slate-400">No fuel products recorded in period</div>
                ) : (
                    <div className="h-[180px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                                <Tooltip content={<CustomTooltip />} />
                                <Pie
                                    data={chartData}
                                    cx="50%"
                                    cy="50%"
                                    innerRadius={48}
                                    outerRadius={74}
                                    paddingAngle={0}
                                    stroke="#fff"
                                    strokeWidth={2}
                                    dataKey="value"
                                >
                                    {chartData.map((entry) => (
                                        <Cell key={entry.name} fill={entry.color} />
                                    ))}
                                </Pie>
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* Compact Dynamic Legend */}
            <div className="divide-y divide-slate-100 border-t border-slate-100">
                {chartData.map((item) => (
                    <div key={item.name} className="flex items-center justify-between py-1.5 text-xs">
                        <div className="flex items-center gap-2 min-w-0">
                            <span
                                className="h-2 w-2 shrink-0 rounded-[2px]"
                                style={{ backgroundColor: item.color }}
                            />
                            <span className="truncate text-slate-700">
                                {item.name}
                            </span>
                        </div>
                        <div className="flex items-center gap-2.5 shrink-0 text-right">
                            <span className="tabular-nums text-slate-500">
                                {Number(item.value).toLocaleString(undefined, { maximumFractionDigits: 0 })} L
                            </span>
                            <span className="w-11 text-right font-semibold tabular-nums text-slate-900">
                                {item.percentage}%
                            </span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}
