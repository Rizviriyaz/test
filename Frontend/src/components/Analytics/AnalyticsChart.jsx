import { useState } from "react"
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
    ChevronDown,
} from "lucide-react"
import {
    AXIS_LINE,
    CATEGORY_TICK,
    GRID_STROKE,
    SERIES_COLOR,
    TICK,
    TOOLTIP_CURSOR,
    categoricalColor,
} from "../Common/chartTheme"

const TYPE_CONFIG = {
    bar: { label: "Vertical bar", icon: BarChart3 },
    "horizontal-bar": { label: "Horizontal bar", icon: AlignLeft },
    line: { label: "Line", icon: LineChartIcon },
    area: { label: "Area", icon: AreaChartIcon },
    pie: { label: "Pie", icon: PieChartIcon },
    donut: { label: "Donut", icon: Disc },
}

/**
 * Single-measure chart with a switchable form. Bars, lines and areas use one color
 * (`color`, brand teal by default); only pie/donut slices take the categorical palette,
 * in data order, with a legend.
 */
export default function AnalyticsChart({
    title,
    subtitle,
    data = [],
    dataKey = "value",
    nameKey = "label",
    availableTypes = ["bar", "horizontal-bar", "line", "area", "pie", "donut"],
    defaultType = "bar",
    unit = "",
    color = SERIES_COLOR,
    height = 280,
    onItemClick = null,
    emptyMessage = "No data available for this chart",
}) {
    const [chartType, setChartType] = useState(defaultType)
    const [dropdownOpen, setDropdownOpen] = useState(false)

    // Validate current chartType against availableTypes
    const activeType = availableTypes.includes(chartType)
        ? chartType
        : availableTypes[0] || "bar"

    const tickFormatter = (v) => `${v}${unit ? " " + unit : ""}`
    const pointer = onItemClick ? "pointer" : "default"

    const CustomTooltip = ({ active, payload, label }) => {
        if (active && payload && payload.length) {
            const currentItem = payload[0]
            const val = currentItem.value
            const name = currentItem.payload?.fullLabel || label || currentItem.name || currentItem.payload?.[nameKey] || ""
            return (
                <div className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 shadow-pop">
                    <p className="text-[11px] text-slate-500">{name}</p>
                    <p className="text-[13px] font-semibold tabular-nums text-slate-900">
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
                        <BarChart layout="vertical" data={data} margin={{ top: 4, right: 20, left: 8, bottom: 4 }} onClick={onItemClick}>
                            <CartesianGrid stroke={GRID_STROKE} horizontal={false} />
                            <XAxis type="number" tick={TICK} tickLine={false} axisLine={false} tickFormatter={tickFormatter} />
                            <YAxis type="category" dataKey={nameKey} tick={CATEGORY_TICK} tickLine={false} axisLine={false} width={92} />
                            <Tooltip cursor={TOOLTIP_CURSOR} content={<CustomTooltip />} />
                            <Bar dataKey={dataKey} fill={color} radius={[0, 3, 3, 0]} maxBarSize={16} cursor={pointer} />
                        </BarChart>
                    </ResponsiveContainer>
                )

            case "line":
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 4 }} onClick={onItemClick}>
                            <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                            <XAxis dataKey={nameKey} tick={TICK} tickLine={false} axisLine={{ stroke: AXIS_LINE }} />
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={tickFormatter} />
                            <Tooltip cursor={{ stroke: AXIS_LINE }} content={<CustomTooltip />} />
                            <Line
                                type="monotone"
                                dataKey={dataKey}
                                stroke={color}
                                strokeWidth={2}
                                dot={data.length <= 31 ? { r: 2.5, fill: color, strokeWidth: 0 } : false}
                                activeDot={{ r: 4.5, fill: color, stroke: "#fff", strokeWidth: 2 }}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                )

            case "area":
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 4 }} onClick={onItemClick}>
                            <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                            <XAxis dataKey={nameKey} tick={TICK} tickLine={false} axisLine={{ stroke: AXIS_LINE }} />
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={tickFormatter} />
                            <Tooltip cursor={{ stroke: AXIS_LINE }} content={<CustomTooltip />} />
                            <Area
                                type="monotone"
                                dataKey={dataKey}
                                stroke={color}
                                strokeWidth={2}
                                fill={color}
                                fillOpacity={0.1}
                                activeDot={{ r: 4.5, fill: color, stroke: "#fff", strokeWidth: 2 }}
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
                                cy="45%"
                                innerRadius={activeType === "donut" ? 52 : 0}
                                outerRadius={80}
                                paddingAngle={0}
                                stroke="#fff"
                                strokeWidth={2}
                                cursor={pointer}
                                onClick={onItemClick}
                            >
                                {data.map((_, index) => (
                                    <Cell key={`pie-cell-${index}`} fill={categoricalColor(index)} />
                                ))}
                            </Pie>
                            <Legend
                                verticalAlign="bottom"
                                height={36}
                                iconType="square"
                                iconSize={8}
                                formatter={(val) => <span className="text-[11px] text-slate-600">{val}</span>}
                            />
                        </PieChart>
                    </ResponsiveContainer>
                )

            case "bar":
            default:
                return (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 4 }} onClick={onItemClick}>
                            <CartesianGrid stroke={GRID_STROKE} vertical={false} />
                            <XAxis dataKey={nameKey} tick={TICK} tickLine={false} axisLine={{ stroke: AXIS_LINE }} />
                            <YAxis tick={TICK} tickLine={false} axisLine={false} tickFormatter={tickFormatter} />
                            <Tooltip cursor={TOOLTIP_CURSOR} content={<CustomTooltip />} />
                            <Bar dataKey={dataKey} fill={color} radius={[3, 3, 0, 0]} maxBarSize={32} cursor={pointer} />
                        </BarChart>
                    </ResponsiveContainer>
                )
        }
    }

    const ActiveIcon = TYPE_CONFIG[activeType]?.icon || BarChart3

    return (
        <div className="flex min-w-0 flex-col rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-start justify-between gap-2 pb-2">
                <div className="min-w-0">
                    <h3 className="truncate text-[13px] font-semibold text-slate-900">{title}</h3>
                    {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
                </div>

                {availableTypes && availableTypes.length > 1 && (
                    <div className="relative">
                        <button
                            type="button"
                            onClick={() => setDropdownOpen(!dropdownOpen)}
                            onKeyDown={(e) => { if (e.key === "Escape") setDropdownOpen(false) }}
                            className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-600 transition-colors hover:border-slate-300 hover:text-slate-900 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
                            title="Change visualization type"
                            aria-haspopup="listbox"
                            aria-expanded={dropdownOpen}
                            aria-label={`Chart type: ${TYPE_CONFIG[activeType]?.label || activeType}`}
                        >
                            <ActiveIcon className="h-3.5 w-3.5 text-slate-500" />
                            <span className="hidden sm:inline">{TYPE_CONFIG[activeType]?.label || activeType}</span>
                            <ChevronDown className="h-3 w-3 text-slate-400" />
                        </button>

                        {dropdownOpen && (
                            <>
                                <div className="fixed inset-0 z-40" onClick={() => setDropdownOpen(false)} />
                                <div role="listbox" className="absolute right-0 z-50 mt-1 w-40 rounded-md border border-slate-200 bg-white p-1 shadow-pop">
                                    {availableTypes.map((typeKey) => {
                                        const cfg = TYPE_CONFIG[typeKey] || { label: typeKey, icon: BarChart3 }
                                        const Icon = cfg.icon
                                        const isSelected = typeKey === activeType
                                        return (
                                            <button
                                                key={typeKey}
                                                type="button"
                                                role="option"
                                                aria-selected={isSelected}
                                                onClick={() => {
                                                    setChartType(typeKey)
                                                    setDropdownOpen(false)
                                                }}
                                                className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-xs transition-colors cursor-pointer ${
                                                    isSelected ? "font-medium text-slate-900" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                                                }`}
                                            >
                                                <span className="flex items-center gap-2">
                                                    <Icon className="h-3.5 w-3.5 text-slate-400" />
                                                    {cfg.label}
                                                </span>
                                                {isSelected && <Check className="h-3.5 w-3.5 text-brand" />}
                                            </button>
                                        )
                                    })}
                                </div>
                            </>
                        )}
                    </div>
                )}
            </div>

            <div style={{ height: `${height}px` }} className="w-full">
                {renderChartContent()}
            </div>
        </div>
    )
}
