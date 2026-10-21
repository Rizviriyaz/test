import { useState, useMemo } from "react"
import {
    AlertCircle,
    Search,
    ArrowUpRight,
    Info,
    CheckCircle2,
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
    { id: "excessive_idling", label: "Excessive Idling" },
    { id: "rapid_refuel", label: "Rapid Refuels (<24h)" },
    { id: "volume_spike", label: "Volume Spikes" },
    { id: "odometer_inconsistency", label: "Odometer Issues" },
    { id: "duplicate_candidate", label: "Potential Duplicates" },
    { id: "unmatched_vehicle", label: "Unmatched GPS" },
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

    const anomalies = useMemo(() => wasteData?.anomalies || [], [wasteData])

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
            <div role="status" aria-label="Loading fuel waste report" className="mb-5 rounded-lg border border-slate-200 bg-white p-4">
                <div className="mb-4 h-4 w-56 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
                    {[0, 1, 2].map((i) => <div key={i} className="h-16 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />)}
                </div>
                <div className="space-y-2">
                    {[0, 1].map((i) => <div key={i} className="h-12 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />)}
                </div>
            </div>
        )
    }

    if (error) {
        return (
            <div role="alert" className="mb-5 rounded-lg border border-slate-200 bg-white p-6 text-center">
                <AlertCircle className="mx-auto mb-2 h-5 w-5 text-rose-600" />
                <h3 className="type-section-title text-slate-900">Fuel waste report unavailable</h3>
                <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">{error}</p>
            </div>
        )
    }

    if (!wasteData) {
        return (
            <div className="mb-5 rounded-lg border border-slate-200 bg-white p-6 text-center">
                <Info className="mx-auto mb-2 h-5 w-5 text-slate-300" />
                <h3 className="text-[13px] font-medium text-slate-800">No fuel waste report available</h3>
                <p className="mx-auto mt-1 max-w-md text-xs text-slate-500">
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

    const summary = [
        {
            label: "Idle waste above threshold",
            value: idleDataAvailable ? `AED ${formatCurrency(idleWasteCost)}` : "—",
            detail: idleDataAvailable ? `${formatNumber(idleWasteLitres, 1)} L${telematicsPartial ? " · partial" : ""} · ${idleVehicleCount} vehicles` : "Idle data unavailable",
            note: `Estimated idle fuel of vehicles idling at or above${effectiveThreshold != null
                ? ` ${formatNumber(effectiveThreshold, 1)} h${periodDays ? ` over ${periodDays} day${periodDays === 1 ? "" : "s"}` : ""}`
                : " the threshold"}`,
            tone: idleDataAvailable && idleWasteCost > 0 ? "text-amber-800" : "text-slate-900",
        },
        {
            label: "Refuelling pattern flags",
            value: refuelAnomalies,
            detail: "anomalous transactions",
            note: "Rapid consecutive refills (<24h) and high-volume spikes",
            tone: refuelAnomalies > 0 ? "text-amber-800" : "text-slate-900",
        },
        {
            label: "Data quality",
            value: dataQualityCount,
            detail: "inconsistencies",
            note: "Odometer regressions, candidate duplicates, unmatched plates",
            tone: "text-slate-900",
        },
    ]

    const severityStyle = (sev) =>
        sev === "critical"
            ? { rule: "before:bg-status-offline", text: "text-rose-700", label: "Critical" }
            : sev === "warning"
                ? { rule: "before:bg-status-idling", text: "text-amber-800", label: "Warning" }
                : { rule: "before:bg-transparent", text: "text-slate-500", label: sev ? sev.charAt(0).toUpperCase() + sev.slice(1) : "Info" }

    return (
        <section className="mb-5 overflow-hidden rounded-lg border border-slate-200 bg-white">
            <div className="flex flex-col justify-between gap-3 border-b border-slate-200 px-4 py-3 md:flex-row md:items-center">
                <h2 className="type-section-title text-slate-900">
                    Fuel waste & anomalies <span className="font-normal tabular-nums text-slate-500">· {totalAnomalies} findings</span>
                </h2>

                {onIdleThresholdChange && (
                    <label className="inline-flex h-8 items-center gap-1.5 self-start rounded-md border border-slate-200 bg-white px-2.5 text-xs focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15 md:self-auto">
                        <span className="text-slate-500">Idle alert threshold</span>
                        <select
                            value={idleThreshold}
                            onChange={(e) => onIdleThresholdChange(parseFloat(e.target.value))}
                            className="cursor-pointer bg-transparent font-medium text-slate-800 outline-none"
                        >
                            <option value={3.0}>≥ 3.0 h{perDay ? "/day" : ""}</option>
                            <option value={5.0}>≥ 5.0 h{perDay ? "/day" : ""} (Standard)</option>
                            <option value={10.0}>≥ 10.0 h{perDay ? "/day" : ""}</option>
                            <option value={15.0}>≥ 15.0 h{perDay ? "/day" : ""} (Severe)</option>
                        </select>
                    </label>
                )}
            </div>

            {telematicsPartial && (
                <div role="status" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
                    <span className="font-medium">Partial Wialon telematics:</span> idle figures cover only vehicles whose idle hours were reported.
                    {dataWarnings.length > 0 && (
                        <ul className="mt-1 list-disc pl-4">
                            {dataWarnings.slice(0, 5).map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                    )}
                </div>
            )}

            <dl className="grid grid-cols-1 gap-px border-b border-slate-200 bg-slate-200 sm:grid-cols-3">
                {summary.map((item) => (
                    <div key={item.label} className="bg-white px-4 py-3">
                        <dt className="text-xs font-medium text-slate-500">{item.label}</dt>
                        <dd className="mt-1 flex items-baseline gap-1.5">
                            <span className={`text-xl font-semibold tabular-nums ${item.tone}`}>{item.value}</span>
                            <span className="text-xs text-slate-500">{item.detail}</span>
                        </dd>
                        <dd className="mt-0.5 text-[11px] text-slate-400">{item.note}</dd>
                    </div>
                ))}
            </dl>

            <div className="flex flex-col justify-between gap-2 px-4 pt-3 lg:flex-row lg:items-center">
                <div role="group" aria-label="Finding category" className="flex flex-wrap items-center gap-1">
                    {CATEGORY_TABS.map((tab) => {
                        const isActive = selectedTab === tab.id
                        const count = tab.id === "all"
                            ? anomalies.length
                            : anomalies.filter((a) => a.anomaly_type === tab.id).length

                        return (
                            <button
                                key={tab.id}
                                type="button"
                                onClick={() => setSelectedTab(tab.id)}
                                aria-pressed={isActive}
                                className={`inline-flex h-7 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                    isActive
                                        ? "border-brand/50 bg-brand-light font-medium text-slate-900"
                                        : "border-transparent text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                                }`}
                            >
                                {tab.label}
                                <span className={`tabular-nums ${isActive ? "text-brand" : "text-slate-400"}`}>{count}</span>
                            </button>
                        )
                    })}
                </div>

                <div className="relative min-w-[220px]">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" aria-hidden="true" />
                    <input
                        type="search"
                        placeholder="Search vehicle or anomaly…"
                        aria-label="Search vehicle or anomaly"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="h-8 w-full rounded-md border border-slate-200 bg-white pl-8 pr-3 text-xs text-slate-800 placeholder-slate-400 focus:border-brand focus:outline-hidden focus:ring-2 focus:ring-brand/15"
                    />
                </div>
            </div>

            <div className="mt-3 border-t border-slate-200">
                {filteredAnomalies.length === 0 ? (
                    <p className="flex items-center justify-center gap-2 px-4 py-6 text-xs text-slate-500">
                        <CheckCircle2 className="h-4 w-4 text-status-moving" />
                        No anomalies in this category for the selected period.
                    </p>
                ) : (
                    <ul className="max-h-[520px] divide-y divide-slate-100 overflow-y-auto">
                        {filteredAnomalies.map((item) => {
                            const sev = severityStyle(item.severity)
                            return (
                                <li
                                    key={item.id}
                                    className={`relative flex flex-col justify-between gap-3 px-4 py-3 before:absolute before:inset-y-0 before:left-0 before:w-0.5 sm:flex-row sm:items-center ${sev.rule}`}
                                >
                                    <div className="min-w-0 space-y-1">
                                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                                            <span className="font-mono text-xs font-semibold text-slate-900">{item.vehicle_identifier}</span>
                                            <span className={`text-xs font-medium ${sev.text}`}>{sev.label}</span>
                                            <span className="text-[11px] text-slate-400">{item.finding_type}</span>
                                        </div>
                                        <h4 className="text-[13px] font-medium text-slate-900">{item.title}</h4>
                                        <p className="text-xs leading-relaxed text-slate-600">{item.description}</p>
                                        <dl className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-[11px] text-slate-500">
                                            {item.recorded_value && (
                                                <div className="flex gap-1"><dt>Recorded</dt><dd className="font-medium text-slate-800">{item.recorded_value}</dd></div>
                                            )}
                                            {item.threshold_or_baseline && (
                                                <div className="flex gap-1"><dt>Baseline</dt><dd className="font-medium text-slate-800">{item.threshold_or_baseline}</dd></div>
                                            )}
                                            {item.transaction_date && (
                                                <div className="flex gap-1"><dt>Date</dt><dd className="font-medium tabular-nums text-slate-800">{item.transaction_date}</dd></div>
                                            )}
                                        </dl>
                                    </div>

                                    {/* No action for identifiers excluded from KPIs: they have no single Wialon unit */}
                                    {!isExcludedIdentifierItem(item) && (
                                        <button
                                            type="button"
                                            onClick={() => onSelectVehicle?.(item.vehicle_identifier)}
                                            className="inline-flex h-7 shrink-0 items-center gap-1 self-start rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 cursor-pointer sm:self-center"
                                        >
                                            Inspect
                                            <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                                        </button>
                                    )}
                                </li>
                            )
                        })}
                    </ul>
                )}
            </div>
        </section>
    )
}
