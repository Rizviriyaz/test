import { useState, useEffect, useMemo } from "react"
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip } from "recharts"
import { PieChart as PieIcon, Loader2 } from "lucide-react"
import { getFuelTypes } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/FuelFilterContext"

const PALETTE = [
    "#1C5E7A", // Deep Petrol Blue
    "#0284C7", // Bright Sky Blue
    "#10B981", // Emerald
    "#F59E0B", // Amber
    "#8B5CF6", // Violet
    "#EC4899", // Rose
]

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
                color: PALETTE[idx % PALETTE.length],
            }
        })
    }, [fuelTypes, totalLitres])

    const CustomTooltip = ({ active, payload }) => {
        if (!active || !payload || !payload.length) return null
        const data = payload[0]?.payload
        return (
            <div className="rounded-xl border border-slate-200 bg-white/95 p-3 shadow-lg backdrop-blur-xs text-xs space-y-1">
                <div className="flex items-center gap-2 border-b border-slate-100 pb-1 mb-1">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: data.color }} />
                    <span className="font-bold text-slate-800">{data.name}</span>
                </div>
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Volume:</span>
                    <span className="font-bold text-slate-800">{data.value.toLocaleString()} L</span>
                </div>
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Expenditure:</span>
                    <span className="font-bold text-emerald-600">AED {data.cost.toLocaleString()}</span>
                </div>
                <div className="flex items-center justify-between gap-4">
                    <span className="text-slate-500">Fleet Share:</span>
                    <span className="font-bold text-[#1C5E7A]">{data.percentage}%</span>
                </div>
            </div>
        )
    }

    return (
        <div className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                <div className="flex items-center gap-2">
                    <div className="rounded-xl bg-[#1C5E7A]/10 p-1.5 text-[#1C5E7A]">
                        <PieIcon className="h-4 w-4" />
                    </div>
                    <h3 className="text-sm font-bold text-slate-900">
                        Fuel Product Breakdown
                    </h3>
                </div>
                <span className="text-[11px] font-semibold text-slate-400">
                    {chartData.length} Types
                </span>
            </div>

            <div className="my-3 flex-1 min-h-[190px] flex items-center justify-center">
                {loading ? (
                    <div className="flex flex-col items-center justify-center text-slate-400">
                        <Loader2 className="h-5 w-5 animate-spin text-[#1C5E7A] mb-1" />
                        <span className="text-xs">Loading breakdown…</span>
                    </div>
                ) : error ? (
                    <div className="text-xs text-rose-500">{error}</div>
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
                                    paddingAngle={3}
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
            <div className="space-y-2 border-t border-slate-100 pt-3">
                {chartData.map((item) => (
                    <div key={item.name} className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2 min-w-0">
                            <span
                                className="h-2.5 w-2.5 rounded-full shrink-0"
                                style={{ backgroundColor: item.color }}
                            />
                            <span className="font-semibold text-slate-700 truncate">
                                {item.name}
                            </span>
                        </div>
                        <div className="flex items-center gap-2.5 shrink-0 text-right">
                            <span className="text-slate-500 font-medium">
                                {Number(item.value).toLocaleString(undefined, { maximumFractionDigits: 0 })} L
                            </span>
                            <span className="font-bold text-slate-900 w-11 text-right">
                                {item.percentage}%
                            </span>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}
