import { useState, useMemo } from "react"
import {
    AlertTriangle,
    AlertCircle,
    Flame,
    Clock,
    Zap,
    Scale,
    Search,
    ChevronRight,
    ArrowUpRight,
    ShieldAlert,
    HelpCircle,
    Info,
    CheckCircle2,
    RefreshCw,
    Sliders,
    Layers,
} from "lucide-react"

function formatNumber(val, decimals = 1) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })
}

function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    })
}

const CATEGORY_TABS = [
    { id: "all", label: "All Findings" },
    { id: "excessive_idling", label: "Excessive Idling", icon: Clock },
    { id: "rapid_refuel", label: "Rapid Refuels (<24h)", icon: Zap },
    { id: "volume_spike", label: "Volume Spikes", icon: Flame },
    { id: "odometer_inconsistency", label: "Odometer Issues", icon: Scale },
    { id: "duplicate_candidate", label: "Potential Duplicates", icon: Layers },
    { id: "unmatched_vehicle", label: "Unmatched GPS", icon: AlertTriangle },
]

// Info items for identifiers excluded from KPIs (unmatched / ambiguous / cross-import duplicates)
function isExcludedIdentifierItem(item) {
    const id = String(item?.id || "")
    return item?.anomaly_type === "unmatched_vehicle" || id.startsWith("dupx-") || id.startsWith("unmatched-")
}

export default function FuelWasteIntelligence({
    wasteData,
    loading,
    error,
    onSelectVehicle,
    idleThreshold = 5.0,
    onIdleThresholdChange,
}) {
    const [selectedTab, setSelectedTab] = useState("all")
    const [searchQuery, setSearchQuery] = useState("")

    const anomalies = wasteData?.anomalies || []

    const filteredAnomalies = useMemo(() => {
        return anomalies.filter((a) => {
            const matchesTab = selectedTab === "all" || a.anomaly_type === selectedTab
            const matchesSearch = !searchQuery ||
                a.vehicle_identifier?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                a.title?.toLowerCase().includes(searchQuery.toLowerCase()) ||
                a.description?.toLowerCase().includes(searchQuery.toLowerCase())
            return matchesTab && matchesSearch
        })
    }, [anomalies, selectedTab, searchQuery])

    if (loading) {
        return (
            <div className="mb-8 rounded-3xl border border-slate-200/80 bg-white p-6 shadow-xs animate-pulse">
                <div className="h-6 w-64 bg-slate-200 rounded mb-4" />
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
                    <div className="h-20 bg-slate-100 rounded-2xl" />
                    <div className="h-20 bg-slate-100 rounded-2xl" />
                    <div className="h-20 bg-slate-100 rounded-2xl" />
                </div>
                <div className="space-y-3">
                    <div className="h-16 bg-slate-100 rounded-xl" />
                    <div className="h-16 bg-slate-100 rounded-xl" />
                </div>
            </div>
        )
    }

    if (error) {
        return (
            <div className="mb-8 rounded-3xl border border-rose-200 bg-rose-50/50 p-6 shadow-xs text-center">
                <AlertCircle className="mx-auto h-8 w-8 text-rose-500 mb-2" />
                <h3 className="text-sm font-bold text-rose-900">Fuel Waste Intelligence Unavailable</h3>
                <p className="text-xs text-rose-600 mt-1 max-w-md mx-auto">{error}</p>
            </div>
        )
    }

    if (!wasteData) {
        return (
            <div className="mb-8 rounded-3xl border border-slate-200/80 bg-slate-50/50 p-6 shadow-xs text-center">
                <Info className="mx-auto h-8 w-8 text-slate-400 mb-2" />
                <h3 className="text-sm font-bold text-slate-700">No Fuel Waste Report Available</h3>
                <p className="text-xs text-slate-500 mt-1 max-w-md mx-auto">
                    Select a date period or adjust filters to generate fuel waste and anomaly analytics.
                </p>
            </div>
        )
    }

    const totalAnomalies = wasteData?.total_anomalies_count || 0
    // No vehicle with known idle hours -> the idle figures are unavailable, not a measured zero.
    const idleDataAvailable = wasteData?.fleet_estimated_idle_fuel_litres != null
    const telematicsPartial = (wasteData?.telematics_status || "complete") !== "complete"
    const dataWarnings = Array.isArray(wasteData?.data_warnings) ? wasteData.data_warnings : []
    const idleWasteLitres = wasteData?.total_idle_waste_litres || 0
    const idleWasteCost = wasteData?.total_idle_waste_cost || 0
    const idleVehicleCount = wasteData?.excessive_idle_vehicles_count || 0
    const perDay = (wasteData?.idle_threshold_mode || "per_day") === "per_day"
    const effectiveThreshold = wasteData?.effective_idle_threshold_hours
    const periodDays = wasteData?.period_days
    const refuelAnomalies = wasteData?.refuel_anomalies_count || 0
    const dataQualityCount = wasteData?.data_quality_issues_count || 0

    return (
        <section className="mb-6 rounded-2xl border border-slate-200/80 bg-white p-4.5 sm:p-5 shadow-xs">
            {/* ─── HEADER & CONTROLS ────────────────────────────────────────── */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-3.5 border-b border-slate-100">
                <div className="flex items-center gap-2.5">
                    <div className="rounded-xl bg-amber-500/10 p-2 text-amber-700 border border-amber-500/20">
                        <ShieldAlert className="h-4.5 w-4.5" />
                    </div>
                    <div>
                        <div className="flex items-center gap-2">
                            <h2 className="text-sm font-bold text-slate-900">
                                Fuel Waste & Anomaly Intelligence
                            </h2>
                            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-black text-amber-800">
                                {totalAnomalies} Findings
                            </span>
                        </div>
                    </div>
                </div>

                {/* Idle Threshold Selector */}
                {onIdleThresholdChange && (
                    <div className="flex items-center gap-2 bg-slate-50 p-1.5 px-3 rounded-2xl border border-slate-200 self-start md:self-auto text-xs">
                        <Sliders className="h-3.5 w-3.5 text-slate-400" />
                        <span className="text-[11px] font-bold text-slate-500">Idle Alert Threshold:</span>
                        <select
                            value={idleThreshold}
                            onChange={(e) => onIdleThresholdChange(parseFloat(e.target.value))}
                            className="rounded-lg bg-white border border-slate-200 px-2 py-1 text-xs font-bold text-slate-700 shadow-2xs cursor-pointer focus:outline-hidden"
                        >
                            <option value={3.0}>≥ 3.0 h{perDay ? "/day" : ""}</option>
                            <option value={5.0}>≥ 5.0 h{perDay ? "/day" : ""} (Standard)</option>
                            <option value={10.0}>≥ 10.0 h{perDay ? "/day" : ""}</option>
                            <option value={15.0}>≥ 15.0 h{perDay ? "/day" : ""} (Severe)</option>
                        </select>
                    </div>
                )}
            </div>

            {telematicsPartial && (
                <div role="status" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                    <span className="font-bold">Partial Wialon telematics:</span> idle figures cover only vehicles whose idle hours were reported.
                    {dataWarnings.length > 0 && (
                        <ul className="mt-1 list-disc pl-4">
                            {dataWarnings.slice(0, 5).map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                    )}
                </div>
            )}

            {/* ─── SUMMARY CARDS ────────────────────────────────────────────── */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 my-4">
                {/* 1. Idling Fuel Waste */}
                <div className="rounded-2xl border border-rose-200/80 bg-gradient-to-br from-rose-50/50 via-white to-white p-3.5 shadow-2xs">
                    <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-rose-800">
                            Idle Waste Above Threshold
                        </span>
                        <span className="rounded-md bg-rose-100 px-1.5 py-0.5 text-[9px] font-bold text-rose-800">
                            {idleDataAvailable ? `${idleVehicleCount} Vehicles Exceeding` : "Idle data unavailable"}
                        </span>
                    </div>
                    <div className="flex items-baseline gap-1.5">
                        {idleDataAvailable ? (
                            <>
                                <span className="text-xl font-black text-rose-900">
                                    AED {formatCurrency(idleWasteCost)}
                                </span>
                                <span className="text-xs font-bold text-rose-700">
                                    ({formatNumber(idleWasteLitres, 1)} L){telematicsPartial ? " · partial" : ""}
                                </span>
                            </>
                        ) : (
                            <span className="text-xl font-black text-rose-900">—</span>
                        )}
                    </div>
                    <p className="mt-1 text-[10px] text-rose-600">
                        Estimated idle fuel of vehicles idling at or above
                        {effectiveThreshold != null
                            ? ` ${formatNumber(effectiveThreshold, 1)} h${periodDays ? ` over ${periodDays} day${periodDays === 1 ? "" : "s"}` : ""}`
                            : " the threshold"}
                    </p>
                </div>

                {/* 2. Refuelling Pattern Anomalies */}
                <div className="rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50/50 via-white to-white p-3.5 shadow-2xs">
                    <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-amber-800">
                            Refuelling Pattern Flags
                        </span>
                        <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[9px] font-bold text-amber-800">
                            {refuelAnomalies} Events
                        </span>
                    </div>
                    <div className="flex items-baseline gap-1.5">
                        <span className="text-xl font-black text-amber-900">
                            {refuelAnomalies}
                        </span>
                        <span className="text-xs font-bold text-amber-700">Anomalous Transactions</span>
                    </div>
                    <p className="mt-1 text-[10px] text-amber-600">
                        Rapid consecutive refills (&lt;24h) and high volume dispensed spikes
                    </p>
                </div>

                {/* 3. Data Integrity & Coverage */}
                <div className="rounded-2xl border border-slate-200 bg-gradient-to-br from-slate-50/70 via-white to-white p-3.5 shadow-2xs">
                    <div className="flex items-center justify-between mb-1">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-slate-600">
                            Data Quality & Safeguards
                        </span>
                        <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[9px] font-bold text-slate-700">
                            {dataQualityCount} Items
                        </span>
                    </div>
                    <div className="flex items-baseline gap-1.5">
                        <span className="text-xl font-black text-slate-900">
                            {dataQualityCount}
                        </span>
                        <span className="text-xs font-bold text-slate-500">Inconsistencies</span>
                    </div>
                    <p className="mt-1 text-[10px] text-slate-500">
                        Odometer regressions, candidate duplicates, and unmatched plates
                    </p>
                </div>
            </div>

            {/* ─── FILTER TABS & SEARCH BAR ─────────────────────────────────── */}
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 pt-1">
                <div className="flex flex-wrap items-center gap-1.5 overflow-x-auto pb-1">
                    {CATEGORY_TABS.map((tab) => {
                        const Icon = tab.icon
                        const isActive = selectedTab === tab.id
                        const count = tab.id === "all"
                            ? anomalies.length
                            : anomalies.filter((a) => a.anomaly_type === tab.id).length

                        return (
                            <button
                                key={tab.id}
                                type="button"
                                onClick={() => setSelectedTab(tab.id)}
                                className={`inline-flex items-center gap-1.5 rounded-xl px-2.5 py-1 text-xs font-bold transition-all cursor-pointer ${
                                    isActive
                                        ? "bg-[#33758F] text-white shadow-xs"
                                        : "bg-slate-100 text-slate-600 hover:bg-slate-200/70 hover:text-slate-900"
                                }`}
                            >
                                {Icon && <Icon className="h-3.5 w-3.5" />}
                                <span>{tab.label}</span>
                                <span
                                    className={`rounded-full px-1.5 py-0.2 text-[10px] font-mono ${
                                        isActive ? "bg-white/20 text-white" : "bg-slate-200 text-slate-700"
                                    }`}
                                >
                                    {count}
                                </span>
                            </button>
                        )
                    })}
                </div>

                <div className="relative min-w-[220px]">
                    <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
                    <input
                        type="text"
                        placeholder="Search vehicle or anomaly…"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full rounded-xl border border-slate-200 bg-slate-50/50 pl-9 pr-3 py-1.5 text-xs font-semibold text-slate-800 placeholder-slate-400 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                    />
                </div>
            </div>

            {/* ─── ANOMALY FINDINGS LIST ────────────────────────────────────── */}
            <div className="mt-3.5 space-y-2">
                {filteredAnomalies.length === 0 ? (
                    <div className="py-6 text-center text-xs text-slate-400 border border-dashed border-slate-200 rounded-2xl bg-slate-50/50">
                        <CheckCircle2 className="mx-auto h-5 w-5 text-emerald-500 mb-1" />
                        <span className="font-bold text-slate-700">No anomalies detected</span> in this category for the selected period.
                    </div>
                ) : (
                    filteredAnomalies.map((item) => {
                        const isCritical = item.severity === "critical"
                        const isWarning = item.severity === "warning"

                        return (
                            <div
                                key={item.id}
                                className={`group rounded-xl border p-2.5 sm:p-3 transition-all hover:shadow-xs ${
                                    isCritical
                                        ? "border-rose-200 bg-gradient-to-r from-rose-50/40 to-white"
                                        : isWarning
                                        ? "border-amber-200 bg-gradient-to-r from-amber-50/30 to-white"
                                        : "border-slate-200 bg-white"
                                }`}
                            >
                                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                                    <div className="space-y-1">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="font-mono text-xs font-black text-slate-900 bg-slate-100 px-2 py-0.5 rounded-md border border-slate-200">
                                                {item.vehicle_identifier}
                                            </span>

                                            <span
                                                className={`rounded-md px-1.5 py-0.5 text-[10px] font-black uppercase tracking-wider ${
                                                    isCritical
                                                        ? "bg-rose-100 text-rose-800 border border-rose-200"
                                                        : isWarning
                                                        ? "bg-amber-100 text-amber-800 border border-amber-200"
                                                        : "bg-slate-100 text-slate-700 border border-slate-200"
                                                }`}
                                            >
                                                {item.severity}
                                            </span>

                                            <span className="rounded-md bg-slate-50 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 border border-slate-200">
                                                [{item.finding_type.toUpperCase()}]
                                            </span>

                                            <h4 className="text-xs font-black text-slate-900">
                                                {item.title}
                                            </h4>
                                        </div>

                                        <p className="text-xs text-slate-600 leading-relaxed pl-0.5">
                                            {item.description}
                                        </p>

                                        <div className="flex flex-wrap items-center gap-4 text-[11px] text-slate-500 pt-1">
                                            {item.recorded_value && (
                                                <span>
                                                    <strong className="text-slate-700">Recorded:</strong> {item.recorded_value}
                                                </span>
                                            )}
                                            {item.threshold_or_baseline && (
                                                <span>
                                                    <strong className="text-slate-700">Baseline:</strong> {item.threshold_or_baseline}
                                                </span>
                                            )}
                                            {item.transaction_date && (
                                                <span>
                                                    <strong className="text-slate-700">Date:</strong> {item.transaction_date}
                                                </span>
                                            )}
                                        </div>
                                    </div>

                                    {/* Action button (not for identifiers excluded from KPIs: they have no single Wialon unit) */}
                                    {!isExcludedIdentifierItem(item) && <button
                                        type="button"
                                        onClick={() => onSelectVehicle?.(item.vehicle_identifier)}
                                        className="inline-flex items-center gap-1 self-start sm:self-center shrink-0 rounded-xl bg-slate-100 px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-[#33758F] hover:text-white transition-colors cursor-pointer"
                                    >
                                        <span>Inspect</span>
                                        <ArrowUpRight className="h-3.5 w-3.5" />
                                    </button>}
                                </div>
                            </div>
                        )
                    })
                )}
            </div>
        </section>
    )
}
