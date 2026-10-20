import { useState, useEffect } from "react"
import { createPortal } from "react-dom"
import {
    X,
    Flame,
    AlertTriangle,
    Info,
    Loader2,
    CheckCircle2,
    Fuel,
} from "lucide-react"
import { getVehicleFuelDetail } from "../../api/fleetApi"
import VehicleStatusBadge from "./VehicleStatusBadge"
import { formatMeasurement, resolveActivityHours, collectTelemetryNotice, isKnownNumber } from "./telemetryFormat"
import ClassificationBadge from "./ClassificationBadge"
import { fixedOrDash, fuelTypeLabel, isRefundAdjustment } from "./fuelLabels"

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

export default function VehicleFuelDetailModal({
    vehicleIdentifier,
    onClose,
    dateFrom,
    dateTo,
    idleRate = 2.0,
    idleThreshold = 5.0,
    idleThresholdMode = "per_day",
}) {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [activeTab, setActiveTab] = useState("overview") // "overview", "transactions", "activity", "anomalies"

    useEffect(() => {
        if (!vehicleIdentifier) return
        let active = true

        async function fetchDetail() {
            try {
                setLoading(true)
                setError(null)
                setData(null)
                const res = await getVehicleFuelDetail({
                    vehicle_identifier: vehicleIdentifier,
                    date_from: dateFrom,
                    date_to: dateTo,
                    idle_rate_lph: idleRate,
                    idle_threshold_hours: idleThreshold,
                    idle_threshold_mode: idleThresholdMode,
                })
                if (!active) return
                setData(res)
            } catch (err) {
                if (!active) return
                setError(err.message || "Failed to load vehicle fuel details.")
            } finally {
                if (active) setLoading(false)
            }
        }

        fetchDetail()
        return () => { active = false }
    }, [vehicleIdentifier, dateFrom, dateTo, idleRate, idleThreshold, idleThresholdMode])

    if (!vehicleIdentifier) return null

    const summary = data?.vehicle_summary
    const txns = data?.transactions || []
    const activity = data?.activity_breakdown || {}
    const anomalies = data?.anomalies || []
    const transparency = data?.calculation_transparency || {}
    const vc = data?.value_classification

    const totalLitres = summary?.litres || 0
    const totalCost = summary?.total_cost || 0
    const distanceKm = summary?.distance_km
    const { drivingHours, idleHours, engineHours } = resolveActivityHours(summary, data?.activity_breakdown)
    const telemetryNotice = data ? collectTelemetryNotice(summary, data.activity_breakdown) : null
    const estIdleFuel = summary?.estimated_idle_fuel_litres
    const estIdleCost = summary?.estimated_idle_cost
    const estDrivingFuel = summary?.estimated_driving_fuel_litres
    const idlePct = summary?.estimated_idle_percentage
    const drivingPct = summary?.estimated_driving_percentage
    const efficiency = summary?.fuel_efficiency_l_per_100km
    const costPerKm = summary?.fuel_cost_per_km

    return createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/60 backdrop-blur-xs p-3 sm:p-6 overflow-y-auto" onClick={onClose}>
            <div className="relative w-full max-w-4xl max-h-[90vh] flex flex-col rounded-3xl border border-slate-200 bg-white shadow-2xl overflow-hidden my-auto" onClick={(e) => e.stopPropagation()}>
                {/* ─── MODAL HEADER ────────────────────────────────────────── */}
                <div className="flex items-center justify-between px-6 py-4.5 border-b border-slate-100 bg-slate-50/70">
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-[#33758F]/10 p-2.5 text-[#33758F] border border-[#33758F]/20">
                            <Fuel className="h-5 w-5" />
                        </div>
                        <div>
                            <div className="flex items-center gap-2.5">
                                <h3 className="font-mono text-lg font-black tracking-tight text-slate-900">
                                    {vehicleIdentifier}
                                </h3>
                                <VehicleStatusBadge status={summary?.status || "matched"} />
                                {summary?.estimation_status && (
                                    <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600 border border-slate-200">
                                        [{summary.estimation_status.toUpperCase()}]
                                    </span>
                                )}
                            </div>
                            <p className="text-xs text-slate-500 mt-0.5">
                                {summary?.wialon_unit_name ? `Matched GPS Unit: ${summary.wialon_unit_name}` : "Vehicle Fuel Intelligence Drill-Down"}
                                {transparency.period_date_from && transparency.period_date_to ? ` • Period: ${transparency.period_date_from} to ${transparency.period_date_to}` : ""}
                            </p>
                        </div>
                    </div>

                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-xl p-2 text-slate-400 hover:bg-slate-200/60 hover:text-slate-700 transition-colors cursor-pointer"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                {/* ─── MODAL BODY ──────────────────────────────────────────── */}
                <div className="flex-1 overflow-y-auto p-6 space-y-6">
                    {loading ? (
                        <div className="py-16 text-center text-xs text-slate-500">
                            <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#33758F] mb-3" />
                            <span>Aggregating fuel transactions and Wialon activity telemetry…</span>
                        </div>
                    ) : error ? (
                        <div className="rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700 flex items-center gap-3">
                            <AlertTriangle className="h-5 w-5 shrink-0 text-rose-600" />
                            <span>{error}</span>
                        </div>
                    ) : (
                        <>
                            {/* Navigation Sub-Tabs */}
                            <div className="flex items-center gap-2 border-b border-slate-100 pb-3">
                                {[
                                    { id: "overview", label: "Performance Overview" },
                                    { id: "transactions", label: `Transactions (${txns.length})` },
                                    { id: "activity", label: "Driving & Idling Activity" },
                                    { id: "anomalies", label: `Anomalies (${anomalies.length})` },
                                ].map((t) => (
                                    <button
                                        key={t.id}
                                        type="button"
                                        onClick={() => setActiveTab(t.id)}
                                        className={`rounded-xl px-3.5 py-1.5 text-xs font-bold transition-all cursor-pointer ${
                                            activeTab === t.id
                                                ? "bg-[#33758F] text-white shadow-xs"
                                                : "bg-slate-100 text-slate-600 hover:bg-slate-200/70"
                                        }`}
                                    >
                                        {t.label}
                                    </button>
                                ))}
                            </div>

                            {telemetryNotice && (
                                <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-3 text-[11px] text-amber-900 flex items-start gap-2" role="status">
                                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
                                    <div className="space-y-0.5">
                                        <span className="font-bold block">
                                            Wialon telemetry {telemetryNotice.status}: values shown as "—" were not reported.
                                        </span>
                                        {telemetryNotice.warnings.map((w) => (
                                            <span key={w} className="block">{w}</span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* ─── TAB 1: OVERVIEW ─────────────────────────────────── */}
                            {activeTab === "overview" && (
                                <div className="space-y-6">
                                    {/* 4 Core Vehicle KPIs */}
                                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                                        <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1 flex items-center gap-1.5">
                                                Fuel Purchased <ClassificationBadge classification={vc} field="litres" />
                                            </span>
                                            <span className="text-xl font-black text-slate-900 block">
                                                {formatNumber(totalLitres, 1)} <span className="text-xs font-semibold text-slate-400">L</span>
                                            </span>
                                            <span className="text-[11px] text-slate-500 mt-1 block">
                                                {txns.length} refuel transactions
                                            </span>
                                        </div>

                                        <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1 flex items-center gap-1.5">
                                                Total Expenditure <ClassificationBadge classification={vc} field="total_cost" />
                                            </span>
                                            <span className="text-xl font-black text-slate-900 block">
                                                AED {formatCurrency(totalCost)}
                                            </span>
                                            <span className="text-[11px] text-slate-500 mt-1 block">
                                                {totalLitres > 0 ? `AED ${(totalCost / totalLitres).toFixed(2)} / L avg` : "—"}
                                            </span>
                                        </div>

                                        <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1 flex items-center gap-1.5">
                                                Distance Traveled <ClassificationBadge classification={vc} field="distance_km" />
                                            </span>
                                            <span className="text-xl font-black text-sky-900 block">
                                                {distanceKm !== null && distanceKm !== undefined ? `${formatNumber(distanceKm, 1)}` : "—"} <span className="text-xs font-semibold text-slate-400">km</span>
                                            </span>
                                            <span className="text-[11px] text-slate-500 mt-1 block">
                                                Wialon trip report
                                            </span>
                                        </div>

                                        <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1 flex items-center gap-1.5">
                                                Purchase-based Efficiency <ClassificationBadge classification={vc} field="fuel_efficiency_l_per_100km" />
                                            </span>
                                            <span className="text-xl font-black text-[#33758F] block">
                                                {efficiency !== null && efficiency !== undefined ? `${efficiency.toFixed(1)}` : "—"} <span className="text-xs font-semibold text-slate-400">L/100km</span>
                                            </span>
                                            <span className="text-[11px] text-slate-500 mt-1 block">
                                                {costPerKm !== null && costPerKm !== undefined ? `AED ${costPerKm.toFixed(2)}/km` : "—"}
                                            </span>
                                        </div>
                                    </div>

                                    {/* Driving vs Idling Breakdown Panel */}
                                    <div className="rounded-2xl border border-amber-200/80 bg-gradient-to-r from-amber-50/40 via-white to-sky-50/40 p-5 shadow-2xs">
                                        <div className="flex items-center justify-between mb-3">
                                            <div className="flex items-center gap-2">
                                                <Flame className="h-4 w-4 text-amber-700" />
                                                <h4 className="text-xs font-black uppercase tracking-wider text-slate-900">
                                                    Estimated Driving vs Idling Fuel Segregation
                                                </h4>
                                            </div>
                                            <span className="rounded-md bg-amber-100 px-2 py-0.5 text-[10px] font-black text-amber-900">
                                                Rate: {idleRate.toFixed(1)} L/h
                                            </span>
                                        </div>

                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                            {/* Driving Segment */}
                                            <div className="rounded-xl border border-sky-200 bg-white/90 p-4 shadow-2xs">
                                                <div className="flex items-center justify-between mb-2">
                                                    <span className="text-xs font-bold text-sky-900">Productive Driving Fuel</span>
                                                    <span className="text-xs font-black text-sky-700">
                                                        {drivingPct !== null && drivingPct !== undefined ? `${drivingPct.toFixed(1)}%` : "—"}
                                                    </span>
                                                </div>
                                                <span className="text-2xl font-black text-sky-900">
                                                    {estDrivingFuel !== null && estDrivingFuel !== undefined ? `${formatNumber(estDrivingFuel, 1)} L` : "—"}
                                                </span>
                                                <div className="mt-2 text-[11px] text-slate-500 flex items-center justify-between">
                                                    <span>Moving duration:</span>
                                                    <span className="font-bold text-slate-800">{formatMeasurement(drivingHours, 1, "hrs")}</span>
                                                </div>
                                            </div>

                                            {/* Idling Segment */}
                                            <div className="rounded-xl border border-rose-200 bg-white/90 p-4 shadow-2xs">
                                                <div className="flex items-center justify-between mb-2">
                                                    <span className="text-xs font-bold text-rose-900">Estimated Idling Waste</span>
                                                    <span className="text-xs font-black text-rose-700">
                                                        {idlePct !== null && idlePct !== undefined ? `${idlePct.toFixed(1)}%` : "—"}
                                                    </span>
                                                </div>
                                                <div className="flex items-baseline justify-between">
                                                    <span className="text-2xl font-black text-rose-900">
                                                        {estIdleFuel !== null && estIdleFuel !== undefined ? `${formatNumber(estIdleFuel, 1)} L` : "—"}
                                                    </span>
                                                    <span className="text-sm font-bold text-rose-700">
                                                        AED {formatCurrency(estIdleCost)}
                                                    </span>
                                                </div>
                                                <div className="mt-2 text-[11px] text-slate-500 flex items-center justify-between">
                                                    <span>Qualifying idle time:</span>
                                                    <span className="font-bold text-slate-800">{formatMeasurement(idleHours, 1, "hrs")}</span>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Activity ratio bar */}
                                        {totalLitres > 0 && estIdleFuel !== null && estIdleFuel !== undefined && (
                                            <div className="mt-4 pt-3 border-t border-slate-200/60">
                                                <div className="flex items-center justify-between text-[11px] font-bold text-slate-600 mb-1.5">
                                                    <span>Fuel Segregation Distribution</span>
                                                    <span>{formatMeasurement(drivingPct, 1, "%")} Driving • {formatMeasurement(idlePct, 1, "%")} Idling</span>
                                                </div>
                                                <div className="h-3 w-full rounded-full bg-slate-100 overflow-hidden flex border border-slate-200/50">
                                                    <div
                                                        className="h-full bg-sky-500 transition-all"
                                                        style={{ width: `${isKnownNumber(drivingPct) ? drivingPct : 0}%` }}
                                                        title={`Driving: ${formatMeasurement(drivingPct, 1, "%")}`}
                                                    />
                                                    <div
                                                        className="h-full bg-rose-500 transition-all"
                                                        style={{ width: `${isKnownNumber(idlePct) ? idlePct : 0}%` }}
                                                        title={`Idling: ${formatMeasurement(idlePct, 1, "%")}`}
                                                    />
                                                </div>
                                            </div>
                                        )}
                                    </div>

                                    {/* Transparency & Assumptions Note */}
                                    <div className="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 text-xs space-y-2">
                                        <div className="flex items-center gap-1.5 font-bold text-slate-800">
                                            <Info className="h-4 w-4 text-[#33758F]" />
                                            <span>Calculation Transparency & Data Sources</span>
                                        </div>
                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] text-slate-600 pt-1">
                                            <div>
                                                <strong>Idle Formula:</strong> {transparency.idle_formula || "Idle Hours × Idle Rate (L/h)"}
                                            </div>
                                            <div>
                                                <strong>Driving Formula:</strong> {transparency.driving_formula || "Fuel Purchased - Idle Fuel"}
                                            </div>
                                            <div>
                                                <strong>Telematics:</strong> {transparency.telematics_source || "Wialon Native Reports"}
                                            </div>
                                            <div>
                                                <strong>Transactions:</strong> {transparency.transaction_source || "Supabase Fuel Invoices"}
                                            </div>
                                        </div>
                                        <p className="text-[10px] text-slate-400 italic pt-1">
                                            {transparency.disclaimer || "Fuel purchases represent refuelling statement logs. Instantaneous consumption is estimated from stationary ignition duration."}
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* ─── TAB 2: TRANSACTIONS ─────────────────────────────── */}
                            {activeTab === "transactions" && (
                                <div className="space-y-4">
                                    <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
                                        <table className="w-full text-left text-xs">
                                            <thead className="border-b border-slate-200 bg-slate-50 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                                                <tr>
                                                    <th className="px-3.5 py-3">Date & Time</th>
                                                    <th className="px-3.5 py-3">Site / Station</th>
                                                    <th className="px-3.5 py-3">Product</th>
                                                    <th className="px-3.5 py-3 text-right">Quantity (L)</th>
                                                    <th className="px-3.5 py-3 text-right">Unit Price</th>
                                                    <th className="px-3.5 py-3 text-right">Total (AED)</th>
                                                    <th className="px-3.5 py-3 text-right">Odometer</th>
                                                    <th className="px-3.5 py-3">Driver / Invoice</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-slate-100">
                                                {txns.length === 0 ? (
                                                    <tr>
                                                        <td colSpan={8} className="py-8 text-center text-xs text-slate-400">
                                                            No transactions recorded for this vehicle in the selected period.
                                                        </td>
                                                    </tr>
                                                ) : (
                                                    txns.map((t) => (
                                                        <tr key={t.id} className="hover:bg-slate-50/60 transition-colors">
                                                            <td className="px-3.5 py-2.5 font-mono text-[11px] font-bold text-slate-800 whitespace-nowrap">
                                                                {t.transaction_date}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 text-slate-700 whitespace-nowrap">
                                                                {t.station_name || "—"}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 font-bold text-slate-800 whitespace-nowrap">
                                                                {fuelTypeLabel(t.fuel_type)}
                                                                {isRefundAdjustment(t) && (
                                                                    <span className="ml-1.5 rounded bg-amber-100 px-1 py-0.5 text-[9px] font-bold text-amber-800">Refund</span>
                                                                )}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 text-right font-black text-slate-900 whitespace-nowrap">
                                                                {formatNumber(t.litres, 1)}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 text-right font-mono text-slate-600 whitespace-nowrap">
                                                                {t.unit_price != null ? `AED ${fixedOrDash(t.unit_price)}` : "—"}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 text-right font-black text-emerald-800 whitespace-nowrap">
                                                                {t.total_cost != null ? `AED ${formatNumber(t.total_cost, 2)}` : "—"}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 text-right font-mono text-slate-600 whitespace-nowrap">
                                                                {t.odometer_km ? `${formatNumber(t.odometer_km, 0)} km` : (
                                                                    <span className="text-amber-600 text-[10px] font-bold">Missing</span>
                                                                )}
                                                            </td>
                                                            <td className="px-3.5 py-2.5 text-slate-500 whitespace-nowrap text-[11px]">
                                                                {t.driver_name || t.invoice_number || "—"}
                                                            </td>
                                                        </tr>
                                                    ))
                                                )}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            )}

                            {/* ─── TAB 3: ACTIVITY ─────────────────────────────────── */}
                            {activeTab === "activity" && (
                                <div className="space-y-4">
                                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-center">
                                        <div className="rounded-2xl border border-sky-200 bg-sky-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase text-sky-700 block">Driving Time</span>
                                            <span className="text-2xl font-black text-sky-900 block mt-1">
                                                {formatMeasurement(drivingHours, 1, "hrs")}
                                            </span>
                                            <span className="text-[11px] text-sky-700 mt-1 block">
                                                {activity.trip_count ? `${activity.trip_count} trips recorded` : "Moving telemetry"}
                                            </span>
                                        </div>

                                        <div className="rounded-2xl border border-amber-200 bg-amber-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase text-amber-700 block">Idling Time</span>
                                            <span className="text-2xl font-black text-amber-900 block mt-1">
                                                {formatMeasurement(idleHours, 1, "hrs")}
                                            </span>
                                            <span className="text-[11px] text-amber-700 mt-1 block">
                                                {activity.idle_events_count ? `${activity.idle_events_count} idle sessions` : "Stationary ignition"}
                                            </span>
                                        </div>

                                        <div className="rounded-2xl border border-slate-200 bg-slate-50/50 p-4">
                                            <span className="text-[10px] font-bold uppercase text-slate-500 block">Engine Hours</span>
                                            <span className="text-2xl font-black text-slate-800 block mt-1">
                                                {formatMeasurement(engineHours, 1, "hrs")}
                                            </span>
                                            <span className="text-[11px] text-slate-500 mt-1 block">
                                                Total operational runtime
                                            </span>
                                        </div>
                                    </div>

                                    {/* Discrete Idle Events if present */}
                                    {activity.idle_events && activity.idle_events.length > 0 && (
                                        <div className="rounded-2xl border border-slate-200 bg-white p-4">
                                            <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-3">
                                                Discrete Idling Events Log
                                            </h4>
                                            <div className="max-h-56 overflow-y-auto divide-y divide-slate-100 text-xs">
                                                {activity.idle_events.map((ev, idx) => (
                                                    <div key={idx} className="py-2.5 flex items-center justify-between">
                                                        <div>
                                                            <span className="font-bold text-slate-800 block">
                                                                {ev.address || "Stationary Location"}
                                                            </span>
                                                            <span className="text-[10px] text-slate-400">
                                                                {ev.start_time ? new Date(ev.start_time * 1000).toLocaleString("en-US", { timeZone: "Asia/Dubai" }) : "—"}
                                                            </span>
                                                        </div>
                                                        <div className="text-right">
                                                            <span className="font-mono font-bold text-amber-700">
                                                                {isKnownNumber(ev.duration_minutes)
                                                                    ? formatMeasurement(ev.duration_minutes, 0, "mins")
                                                                    : formatMeasurement(isKnownNumber(ev.duration) ? ev.duration / 60 : null, 0, "mins")}
                                                            </span>
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* ─── TAB 4: ANOMALIES ────────────────────────────────── */}
                            {activeTab === "anomalies" && (
                                <div className="space-y-3">
                                    {anomalies.length === 0 ? (
                                        <div className="py-12 text-center text-xs text-slate-400 border border-dashed border-slate-200 rounded-2xl bg-slate-50/50">
                                            <CheckCircle2 className="mx-auto h-7 w-7 text-emerald-500 mb-2" />
                                            <span className="font-bold text-slate-800">No anomalies detected</span> for this vehicle in the selected timeframe.
                                        </div>
                                    ) : (
                                        anomalies.map((a) => (
                                            <div
                                                key={a.id}
                                                className="rounded-2xl border border-amber-200 bg-amber-50/30 p-4 text-xs space-y-1"
                                            >
                                                <div className="flex items-center justify-between">
                                                    <span className="font-black text-slate-900">{a.title}</span>
                                                    <span className="rounded-md bg-amber-100 px-1.5 py-0.5 text-[10px] font-black uppercase text-amber-800">
                                                        {a.severity}
                                                    </span>
                                                </div>
                                                <p className="text-slate-600 leading-relaxed">{a.description}</p>
                                                {a.recorded_value && (
                                                    <div className="text-[11px] text-slate-500 pt-1">
                                                        <strong>Recorded:</strong> {a.recorded_value} • <strong>Baseline:</strong> {a.threshold_or_baseline}
                                                    </div>
                                                )}
                                            </div>
                                        ))
                                    )}
                                </div>
                            )}
                        </>
                    )}
                </div>

                {/* ─── MODAL FOOTER ────────────────────────────────────────── */}
                <div className="flex items-center justify-end px-6 py-3.5 border-t border-slate-100 bg-slate-50/50">
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700 hover:bg-slate-100 transition-colors shadow-2xs cursor-pointer"
                    >
                        Close Details
                    </button>
                </div>
            </div>
        </div>,
        document.body
    )
}
