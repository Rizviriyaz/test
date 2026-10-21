import { useState, useId } from "react"
import {
    ResponsiveContainer,
    BarChart,
    Bar,
    LineChart,
    Line,
    AreaChart,
    Area,
    PieChart,
    Pie,
    Cell,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    Legend,
} from "recharts"
import {
    BarChart3,
    LineChart as LineChartIcon,
    AreaChart as AreaChartIcon,
    PieChart as PieChartIcon,
    Disc,
    AlignLeft,
    Check,
} from "lucide-react"

const DEFAULT_PALETTE = [
    "#F97316", // Amber / Orange
    "#3B82F6", // Blue
    "#10B981", // Emerald
    "#8B5CF6", // Purple
    "#EC4899", // Pink
    "#06B6D4", // Cyan
    "#F59E0B", // Yellow
    "#6366F1", // Indigo
    "#14B8A6", // Teal
    "#EF4444", // Red
]

const TYPE_CONFIG = {
    bar: { label: "Vertical Bar", icon: BarChart3 },
    "horizontal-bar": { label: "Horizontal Bar", icon: AlignLeft },
    line: { label: "Line", icon: LineChartIcon },
    area: { label: "Area", icon: AreaChartIcon },
    pie: { label: "Pie", icon: PieChartIcon },
    donut: { label: "Donut", icon: Disc },
}

export default function AnalyticsChart({
    title,
    subtitle,
    data = [],
    dataKey = "value",
    nameKey = "label",
    availableTypes = ["bar", "horizontal-bar", "line", "area", "pie", "donut"],
    defaultType = "bar",
    unit = "",
    color = "#F97316",
    height = 280,
    onItemClick = null,
    emptyMessage = "No data available for this chart",
}) {
    const [chartType, setChartType] = useState(defaultType)
    const [dropdownOpen, setDropdownOpen] = useState(false)
    const gradientId = useId()

    // Validate current chartType against availableTypes
    const activeType = availableTypes.includes(chartType)
        ? chartType
        : availableTypes[0] || "bar"

    const CustomTooltip = ({ active, payload, label }) => {
        if (active && payload && payload.length) {
            const currentItem = payload[0]
            const val = currentItem.value
            const name = label || currentItem.name || currentItem.payload?.[nameKey] || ""
            return (
                <div className="rounded-xl border border-slate-100 bg-white/95 p-3 shadow-lg backdrop-blur-sm">
                    <p className="text-xs font-semibold text-slate-700">{name}</p>
                    <p className="mt-1 text-sm font-bold text-slate-900">
                        {val == null ? "Unknown" : <>{val} <span className="text-xs font-normal text-slate-500">{unit}</span></>}
                    </p>
                </div>
            )
        }
        return null
    }

    const renderChartContent = () => {
        if (!data || data.length === 0) {
            return (
                <div className="flex h-full w-full items-center justify-center text-xs text-slate-400">
                    {emptyMessage}
                </div>
            )
        }

        switch (activeType) {
            case "horizontal-bar":
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                            layout="vertical"
                            data={data}
                            margin={{ top: 10, right: 20, left: 40, bottom: 5 }}
                            onClick={onItemClick}
                        >
                            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" horizontal={false} />
                            <XAxis
                                type="number"
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v) => `${v}${unit ? " " + unit : ""}`}
                            />
                            <YAxis
                                type="category"
                                dataKey={nameKey}
                                stroke="#64748B"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                width={85}
                            />
                            <Tooltip content={<CustomTooltip />} />
                            <Bar
                                dataKey={dataKey}
                                fill={color}
                                radius={[0, 6, 6, 0]}
                                cursor={onItemClick ? "pointer" : "default"}
                            >
                                {data.map((_, index) => (
                                    <Cell
                                        key={`cell-${index}`}
                                        fill={DEFAULT_PALETTE[index % DEFAULT_PALETTE.length]}
                                    />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                )

            case "line":
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart
                            data={data}
                            margin={{ top: 10, right: 20, left: 0, bottom: 5 }}
                            onClick={onItemClick}
                        >
                            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
                            <XAxis
                                dataKey={nameKey}
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v) => `${v}${unit ? " " + unit : ""}`}
                            />
                            <Tooltip content={<CustomTooltip />} />
                            <Line
                                type="monotone"
                                dataKey={dataKey}
                                stroke={color}
                                strokeWidth={2.5}
                                dot={{ r: 3, fill: color, strokeWidth: 1.5, stroke: "#fff" }}
                                activeDot={{ r: 6, fill: color, stroke: "#fff", strokeWidth: 2 }}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                )

            case "area":
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart
                            data={data}
                            margin={{ top: 10, right: 20, left: 0, bottom: 5 }}
                            onClick={onItemClick}
                        >
                            <defs>
                                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor={color} stopOpacity={0.4} />
                                    <stop offset="95%" stopColor={color} stopOpacity={0.0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
                            <XAxis
                                dataKey={nameKey}
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v) => `${v}${unit ? " " + unit : ""}`}
                            />
                            <Tooltip content={<CustomTooltip />} />
                            <Area
                                type="monotone"
                                dataKey={dataKey}
                                stroke={color}
                                strokeWidth={2.5}
                                fillOpacity={1}
                                fill={`url(#${gradientId})`}
                            />
                        </AreaChart>
                    </ResponsiveContainer>
                )

            case "pie":
            case "donut":
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <PieChart margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
                            <Tooltip content={<CustomTooltip />} />
                            <Pie
                                data={data}
                                dataKey={dataKey}
                                nameKey={nameKey}
                                cx="50%"
                                cy="50%"
                                innerRadius={activeType === "donut" ? 50 : 0}
                                outerRadius={78}
                                paddingAngle={activeType === "donut" ? 3 : 0}
                                cursor={onItemClick ? "pointer" : "default"}
                                onClick={onItemClick}
                            >
                                {data.map((_, index) => (
                                    <Cell
                                        key={`pie-cell-${index}`}
                                        fill={DEFAULT_PALETTE[index % DEFAULT_PALETTE.length]}
                                    />
                                ))}
                            </Pie>
                            <Legend
                                verticalAlign="bottom"
                                height={36}
                                iconType="circle"
                                formatter={(val) => (
                                    <span className="text-[11px] font-medium text-slate-600">
                                        {val}
                                    </span>
                                )}
                            />
                        </PieChart>
                    </ResponsiveContainer>
                )

            case "bar":
            default:
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart
                            data={data}
                            margin={{ top: 10, right: 10, left: -10, bottom: 5 }}
                            onClick={onItemClick}
                        >
                            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
                            <XAxis
                                dataKey={nameKey}
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                stroke="#94A3B8"
                                fontSize={11}
                                tickLine={false}
                                axisLine={false}
                                tickFormatter={(v) => `${v}${unit ? " " + unit : ""}`}
                            />
                            <Tooltip content={<CustomTooltip />} />
                            <Bar
                                dataKey={dataKey}
                                fill={color}
                                radius={[6, 6, 0, 0]}
                                cursor={onItemClick ? "pointer" : "default"}
                            >
                                {data.map((_, index) => (
                                    <Cell
                                        key={`bar-cell-${index}`}
                                        fill={DEFAULT_PALETTE[index % DEFAULT_PALETTE.length]}
                                    />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                )
        }
    }

    const ActiveIcon = TYPE_CONFIG[activeType]?.icon || BarChart3

    return (
        <div className="flex min-w-0 flex-col justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-xs sm:p-5">
            {/* Chart Header with Interactive Type Switcher */}
            <div className="flex items-start justify-between gap-2 pb-3">
                <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold text-slate-900">{title}</h3>
                    {subtitle && (
                        <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>
                    )}
                </div>

                {/* Chart Type Selector Dropdown */}
                {availableTypes && availableTypes.length > 1 && (
                    <div className="relative">
                        <button
                            type="button"
                            onClick={() => setDropdownOpen(!dropdownOpen)}
                            className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 shadow-2xs transition hover:border-slate-300 hover:text-slate-900 cursor-pointer"
                            title="Change visualization type"
                            aria-haspopup="listbox"
                            aria-expanded={dropdownOpen}
                        >
                            <ActiveIcon className="h-3.5 w-3.5 text-[#33758F]" />
                            <span className="capitalize">{TYPE_CONFIG[activeType]?.label || activeType}</span>
                        </button>

                        {dropdownOpen && (
                            <>
                                <div
                                    className="fixed inset-0 z-40"
                                    onClick={() => setDropdownOpen(false)}
                                />
                                <div className="absolute right-0 z-50 mt-1 w-40 rounded-xl border border-slate-200 bg-white py-1 shadow-lg animate-in fade-in zoom-in-95 duration-100">
                                    <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                                        Chart Type
                                    </div>
                                    {availableTypes.map((typeKey) => {
                                        const cfg = TYPE_CONFIG[typeKey] || { label: typeKey, icon: BarChart3 }
                                        const Icon = cfg.icon
                                        const isSelected = typeKey === activeType
                                        return (
                                            <button
                                                key={typeKey}
                                                type="button"
                                                onClick={() => {
                                                    setChartType(typeKey)
                                                    setDropdownOpen(false)
                                                }}
                                                className={`flex w-full items-center justify-between px-3 py-1.5 text-xs transition cursor-pointer ${
                                                    isSelected
                                                        ? "bg-[#F0F6F9] font-semibold text-[#33758F]"
                                                        : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                                                }`}
                                            >
                                                <div className="flex items-center gap-2">
                                                    <Icon className={`h-3.5 w-3.5 ${isSelected ? "text-[#33758F]" : "text-slate-400"}`} />
                                                    <span>{cfg.label}</span>
                                                </div>
                                                {isSelected && <Check className="h-3.5 w-3.5 text-[#33758F]" />}
                                            </button>
                                        )
                                    })}
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>

            {/* Chart Body */}
            <div style={{ height: `${height}px` }} className="w-full pt-2">
                {renderChartContent()}
            </div>
        </div>
    )
}
