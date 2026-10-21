import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import {
    AlertTriangle,
    X,
    Info,
    Calendar,
    Database,
    Calculator,
} from "lucide-react"
import ClassificationBadge from "./ClassificationBadge"
import { excludedSummary } from "./fuelLabels"

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

export default function KpiCards({ efficiencyData, loading, idleRate = 2.0 }) {
    const [selectedKpi, setSelectedKpi] = useState(null)

    useEffect(() => {
        if (!selectedKpi) return undefined
        const onKey = (e) => { if (e.key === "Escape") setSelectedKpi(null) }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [selectedKpi])

    if (loading) {
        return (
            <div className="mb-5 space-y-4" role="status" aria-label="Loading fuel KPIs">
                {[0, 1].map((row) => (
                    <div key={row}>
                        <div className="mb-2 h-3 w-40 animate-pulse rounded bg-slate-200 motion-reduce:animate-none" />
                        <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-2 lg:grid-cols-4">
                            {[0, 1, 2, 3].map((i) => (
                                <div key={i} className="bg-white px-4 py-3">
                                    <div className="h-3 w-24 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                                    <div className="mt-2 h-6 w-28 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                                    <div className="mt-2 h-3 w-20 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        )
    }

    const totalLitres = efficiencyData?.total_litres
    const totalCost = efficiencyData?.total_cost
    const transactionCount = efficiencyData?.transaction_count
    const vehicleCount = efficiencyData?.vehicle_count
    const trackedLitres = efficiencyData?.tracked_litres
    const trackedCost = efficiencyData?.tracked_cost
    const vehiclesWithDistance = efficiencyData?.vehicles_with_distance
    const engineOnlyCount = efficiencyData?.vehicles_engine_hours_only
    const vc = efficiencyData?.value_classification
    const excluded = excludedSummary(efficiencyData?.excluded_scope)
    const telemetryStatus = efficiencyData?.telematics_status
    const dataWarnings = Array.isArray(efficiencyData?.data_warnings) ? efficiencyData.data_warnings : []
    const suppressedReason = efficiencyData?.efficiency_suppressed_reason
    const showTelemetryNotice = Boolean(telemetryStatus && telemetryStatus !== "complete")

    const totalDistance = efficiencyData?.total_distance_km
    const drivingHours = efficiencyData?.total_driving_hours
    const idleHours = efficiencyData?.total_idle_hours
    const estimatedIdleFuel = efficiencyData?.estimated_idle_fuel_litres
    const estimatedIdleCost = efficiencyData?.estimated_idle_cost
    const estimatedDrivingFuel = efficiencyData?.estimated_driving_fuel_litres
    const idlePct = efficiencyData?.estimated_idle_percentage
    const drivingPct = efficiencyData?.estimated_driving_percentage
    const efficiency = efficiencyData?.fuel_efficiency_l_per_100km
    const costPerKm = efficiencyData?.fuel_cost_per_km

    const avgPrice = totalLitres && totalCost ? (totalCost / totalLitres) : null

    // Period Range strings for breakdown modal
    const periodFrom = efficiencyData?.excluded_scope?.period?.date_from || "Period Start"
    const periodTo = efficiencyData?.excluded_scope?.period?.date_to || "Period End"
    const dateRangeStr = `${periodFrom} to ${periodTo}`

    // Breakdown details generator for active KPI
    const getBreakdownDetails = (kpiKey) => {
        switch (kpiKey) {
            case "total_litres":
                return {
                    title: "Fuel Purchased",
                    category: "Recorded Statement Invoices",
                    classificationBadge: "recorded",
                    resultText: `${formatNumber(totalLitres, 1)} Litres`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel statement invoices (import statement logs, net of refunds)",
                    formulaText: "Total Invoiced Litres (Purchases) - Refund Adjustments",
                    natureText: "Directly aggregated from uploaded fuel statement invoices. Refunds and negative billing adjustments are deducted.",
                    inputs: [
                        { label: "Net Purchased Volume", value: `${formatNumber(totalLitres, 1)} L`, source: "Invoice Statement Aggregation" },
                        { label: "Refuel Transactions", value: `${transactionCount != null ? formatCurrency(transactionCount) : "—"} refuels`, source: "Invoice Row Count" },
                        { label: "Total Invoiced Spend", value: `AED ${formatCurrency(totalCost)}`, source: "Invoice Total Costs" },
                        { label: "Weighted Average Price", value: avgPrice !== null ? `AED ${avgPrice.toFixed(2)} / L` : "—", source: "Total Cost ÷ Total Litres" },
                    ],
                    calculationSteps: [
                        "Net Litres = Sum(Purchase Transactions) - Sum(Refund Adjustments)",
                        `Result = ${formatNumber(totalLitres, 1)} L`,
                    ],
                }
            case "total_cost":
                return {
                    title: "Recorded Expenditure",
                    category: "Recorded Statement Invoices",
                    classificationBadge: "recorded",
                    resultText: `AED ${formatCurrency(totalCost)}`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel statement invoices (Invoiced amounts in AED)",
                    formulaText: "Sum of All Invoiced Refuel Costs (AED)",
                    natureText: "Recorded financial expenditure directly from supplier statement invoices, reflecting net billing.",
                    inputs: [
                        { label: "Total Invoiced Spend", value: `AED ${formatCurrency(totalCost)}`, source: "Invoice Cost Sum" },
                        { label: "Purchased Volume", value: `${formatNumber(totalLitres, 1)} L`, source: "Invoice Litres Sum" },
                        { label: "Effective Fuel Price", value: avgPrice !== null ? `AED ${avgPrice.toFixed(2)} / L` : "—", source: "Total Cost ÷ Total Litres" },
                        { label: "Refuel Events", value: `${transactionCount != null ? formatCurrency(transactionCount) : "—"} events`, source: "Invoice Rows" },
                    ],
                    calculationSteps: [
                        "Total Expenditure = Sum(Line Item Amounts in AED)",
                        `Result = AED ${formatCurrency(totalCost)}`,
                    ],
                }
            case "transaction_count":
                return {
                    title: "Refuel Transactions",
                    category: "Recorded Transaction Logs",
                    classificationBadge: "recorded",
                    resultText: `${transactionCount != null ? formatCurrency(transactionCount) : "—"} Refuels`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel statement invoice line items",
                    formulaText: "Count of Refuel Events in Selected Period",
                    natureText: "Exact count of valid fuel transaction events processed from supplier fuel statements.",
                    inputs: [
                        { label: "Total Invoiced Refuels", value: `${transactionCount != null ? formatCurrency(transactionCount) : "—"}`, source: "Fuel Invoices" },
                        { label: "Total Fuel Volume", value: `${formatNumber(totalLitres, 1)} L`, source: "Invoice Litres Sum" },
                        { label: "Avg Litres per Refuel", value: totalLitres && transactionCount ? `${(totalLitres / transactionCount).toFixed(1)} L / refuel` : "—", source: "Litres ÷ Refuel Count" },
                    ],
                    calculationSteps: [
                        "Count = Total individual refuelling transactions in period",
                        `Result = ${transactionCount != null ? formatCurrency(transactionCount) : "—"} events`,
                    ],
                }
            case "vehicle_count":
                return {
                    title: "Vehicles Refuelled",
                    category: "Recorded & Matched Scope",
                    classificationBadge: "recorded",
                    resultText: `${vehicleCount ?? "—"} Units`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Unit Registry & Fuel Statement Plate Resolution",
                    formulaText: "Count of Unique Wialon Units with Refuel Transactions in Period",
                    natureText: "Fleet units matched to exactly one active Wialon telematics unit that recorded fuel transactions.",
                    inputs: [
                        { label: "Matched Wialon Units", value: `${vehicleCount ?? "—"}`, source: "Wialon Unit Registry" },
                        { label: "Units with GPS Distance", value: `${vehiclesWithDistance ?? "—"}`, source: "Wialon Odometer Reports" },
                        { label: "Units with Engine-Hours Only", value: `${engineOnlyCount ?? 0}`, source: "Wialon Engine Reports" },
                        { label: "Fuel Volume (Tracked Units)", value: `${formatNumber(trackedLitres, 1)} L`, source: "Matched Vehicle Invoices" },
                    ],
                    calculationSteps: [
                        "Count = Distinct Wialon Units attributed to valid fuel transactions",
                        `Result = ${vehicleCount ?? "—"} units (${vehiclesWithDistance ?? "—"} reporting GPS distance)`,
                    ],
                }
            case "estimated_idle_fuel":
                return {
                    title: "Estimated Idling Fuel",
                    category: "Estimated Telematics",
                    classificationBadge: "estimated",
                    resultText: `${estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) + " L" : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Stationary Engine Telematics & Idle Coefficient",
                    formulaText: "Total Qualifying Idle Hours (hrs) × Configured Idle Rate (L/h)",
                    natureText: "Estimated telematics value. Wialon reports duration where ignition was on while speed was 0 km/h. Fuel consumed is estimated using the configurable burn coefficient.",
                    inputs: [
                        { label: "Qualifying Idle Duration", value: idleHours !== null && idleHours !== undefined ? `${idleHours.toFixed(1)} hrs` : "—", source: "Wialon Telematics (Stationary Engine)" },
                        { label: "Configured Idle Burn Rate", value: `${idleRate.toFixed(1)} L/h`, source: "Dashboard Coefficient Parameter" },
                        { label: "Total Fleet Fuel", value: `${formatNumber(totalLitres, 1)} L`, source: "Invoice Total Litres" },
                        { label: "Idle Consumption Share", value: idlePct !== null && idlePct !== undefined ? `${idlePct.toFixed(1)}%` : "—", source: "Idle Litres ÷ Total Litres" },
                    ],
                    calculationSteps: [
                        `Idle Fuel = ${idleHours !== null && idleHours !== undefined ? idleHours.toFixed(1) : "—"} hrs × ${idleRate.toFixed(1)} L/h`,
                        `Result = ${estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) + " L" : "—"} (${idlePct !== null && idlePct !== undefined ? idlePct.toFixed(1) + "% of fuel volume" : "—"})`,
                    ],
                }
            case "estimated_idle_cost":
                return {
                    title: "Estimated Idle Cost",
                    category: "Estimated Telematics",
                    classificationBadge: "estimated",
                    resultText: `AED ${estimatedIdleCost !== null && estimatedIdleCost !== undefined ? formatCurrency(estimatedIdleCost) : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Telematics & Fuel Statement Unit Prices",
                    formulaText: "Estimated Idle Fuel (L) × Weighted Average Fuel Cost (AED/L)",
                    natureText: "Estimated financial expenditure consumed while engines were idling stationarily without moving.",
                    inputs: [
                        { label: "Estimated Idle Fuel", value: estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? `${formatNumber(estimatedIdleFuel, 1)} L` : "—", source: "Idle Hours × Idle Rate" },
                        { label: "Weighted Average Price", value: avgPrice !== null ? `AED ${avgPrice.toFixed(2)} / L` : "—", source: "Total Cost ÷ Total Litres" },
                        { label: "Total Fuel Spend", value: `AED ${formatCurrency(totalCost)}`, source: "Invoiced Total Costs" },
                    ],
                    calculationSteps: [
                        `Idle Cost = ${estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) + " L" : "—"} × AED ${avgPrice !== null ? avgPrice.toFixed(2) : "—"}/L`,
                        `Result = AED ${estimatedIdleCost !== null && estimatedIdleCost !== undefined ? formatCurrency(estimatedIdleCost) : "—"}`,
                    ],
                }
            case "estimated_driving_fuel":
                return {
                    title: "Estimated Driving Fuel",
                    category: "Estimated Telematics",
                    classificationBadge: "estimated",
                    resultText: `${estimatedDrivingFuel !== null && estimatedDrivingFuel !== undefined ? formatNumber(estimatedDrivingFuel, 1) + " L" : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel Statements & Wialon Motion Duration Telematics",
                    formulaText: "Tracked Purchased Fuel (L) - Estimated Idle Fuel (L)",
                    natureText: "Estimated fuel consumed for productive driving motion after subtracting the estimated stationary idle consumption.",
                    inputs: [
                        { label: "Tracked Fuel Purchased", value: `${formatNumber(trackedLitres ?? totalLitres, 1)} L`, source: "Invoiced Litres for Tracked Vehicles" },
                        { label: "Estimated Idle Fuel", value: estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? `${formatNumber(estimatedIdleFuel, 1)} L` : "—", source: "Idle Hours × Idle Rate" },
                        { label: "Productive Driving Hours", value: drivingHours !== null && drivingHours !== undefined ? `${drivingHours.toFixed(1)} hrs` : "—", source: "Wialon Moving Duration" },
                        { label: "Tracked GPS Distance", value: totalDistance !== null && totalDistance !== undefined ? `${formatNumber(totalDistance, 0)} km` : "—", source: "Wialon Mileage" },
                        { label: "Productive Fuel Share", value: drivingPct !== null && drivingPct !== undefined ? `${drivingPct.toFixed(1)}%` : "—", source: "Driving Litres ÷ Total Litres" },
                    ],
                    calculationSteps: [
                        `Driving Fuel = ${formatNumber(trackedLitres ?? totalLitres, 1)} L - ${estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) + " L" : "0 L"}`,
                        `Result = ${estimatedDrivingFuel !== null && estimatedDrivingFuel !== undefined ? formatNumber(estimatedDrivingFuel, 1) + " L" : "—"} (${drivingPct !== null && drivingPct !== undefined ? drivingPct.toFixed(1) + "% productive motion" : "—"})`,
                    ],
                }
            case "efficiency":
                return {
                    title: "Purchase-based Fleet Efficiency",
                    category: "Derived Ratio",
                    classificationBadge: "derived",
                    resultText: `${efficiency !== null && efficiency !== undefined ? formatNumber(efficiency, 1) + " L / 100 km" : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel Invoices (Litres & Cost) + Wialon GPS Telematics (Mileage)",
                    formulaText: "(Tracked Fuel Purchased (L) ÷ Total Wialon Distance (km)) × 100",
                    natureText: "Purchase-based derived efficiency calculated from recorded refuelling volume divided by verified GPS distance. Withheld if distance is below the minimum threshold or if net litres are non-positive.",
                    inputs: [
                        { label: "Tracked Fuel Purchased", value: `${formatNumber(trackedLitres, 1)} L`, source: "Invoiced Litres of Vehicles with Distance" },
                        { label: "Total Wialon Distance", value: totalDistance !== null && totalDistance !== undefined ? `${formatNumber(totalDistance, 1)} km` : "—", source: "Wialon GPS Odometer Reports" },
                        { label: "Tracked Fuel Cost", value: trackedCost !== null && trackedCost !== undefined ? `AED ${formatCurrency(trackedCost)}` : "—", source: "Invoiced AED for Tracked Units" },
                        { label: "Fuel Cost per km", value: costPerKm !== null && costPerKm !== undefined ? `AED ${formatNumber(costPerKm, 2)} / km` : "—", source: "Tracked Cost ÷ Total Distance" },
                        { label: "Minimum Distance Rule", value: `${efficiencyData?.min_efficiency_distance_km ?? 50} km`, source: "System Quality Safeguard" },
                        { label: "Suppression Status", value: suppressedReason || "Active (Distance Threshold Satisfied)", source: "Calculation Guard" },
                    ],
                    calculationSteps: [
                        `Efficiency = (${formatNumber(trackedLitres, 1)} L ÷ ${totalDistance !== null && totalDistance !== undefined ? formatNumber(totalDistance, 1) : "—"} km) × 100`,
                        `Result = ${efficiency !== null && efficiency !== undefined ? formatNumber(efficiency, 1) + " L/100 km" : "—"} (Cost: ${costPerKm !== null && costPerKm !== undefined ? "AED " + formatNumber(costPerKm, 2) + "/km" : "—"})`,
                    ],
                }
            default:
                return null
        }
    }

    const activeBreakdown = selectedKpi ? getBreakdownDetails(selectedKpi) : null

    const has = (v) => v !== null && v !== undefined

    const recordedCells = [
        {
            key: "total_litres",
            label: "Fuel purchased",
            value: formatNumber(totalLitres, 1),
            unit: "L",
            note: "Net purchased volume",
            badge: <ClassificationBadge classification={vc} field="total_litres" />,
        },
        {
            key: "total_cost",
            label: "Recorded expenditure",
            prefix: "AED",
            value: formatCurrency(totalCost),
            note: "Total fuel spend",
            badge: <ClassificationBadge classification={vc} field="total_cost" />,
        },
        {
            key: "transaction_count",
            label: "Refuel transactions",
            value: transactionCount !== undefined ? formatCurrency(transactionCount) : "—",
            unit: "events",
            note: totalLitres && transactionCount ? `~${(totalLitres / transactionCount).toFixed(1)} L per refuel` : "Statement records",
            badge: <ClassificationBadge classification={vc} field="transaction_count" />,
        },
        {
            key: "vehicle_count",
            label: "Vehicles refuelled",
            value: has(vehicleCount) ? vehicleCount : "—",
            unit: "Wialon units",
            note: [
                has(vehiclesWithDistance) ? `${vehiclesWithDistance} with distance` : "—",
                engineOnlyCount ? `${engineOnlyCount} engine-only` : null,
            ].filter(Boolean).join(" · "),
        },
    ]

    const estimatedCells = [
        {
            key: "estimated_idle_fuel",
            label: "Estimated idling fuel",
            value: has(estimatedIdleFuel) ? formatNumber(estimatedIdleFuel, 1) : "—",
            unit: "L",
            note: has(idlePct) ? `${idlePct.toFixed(1)}% of fuel` : "Idle consumption",
            tag: `${idleRate.toFixed(1)} L/h`,
        },
        {
            key: "estimated_idle_cost",
            label: "Estimated idle cost",
            prefix: "AED",
            value: has(estimatedIdleCost) ? formatCurrency(estimatedIdleCost) : "—",
            valueClass: has(estimatedIdleCost) && estimatedIdleCost > 0 ? "text-amber-800" : "text-slate-900",
            note: "Direct idling loss",
        },
        {
            key: "estimated_driving_fuel",
            label: "Estimated driving fuel",
            value: has(estimatedDrivingFuel) ? formatNumber(estimatedDrivingFuel, 1) : "—",
            unit: "L",
            note: has(drivingPct) ? `${drivingPct.toFixed(1)}% productive` : (drivingHours ? `${drivingHours.toFixed(1)}h in motion` : "Productive motion"),
            tag: `${formatNumber(totalDistance, 0)} km`,
            tagTitle: has(trackedLitres) ? `${formatNumber(trackedLitres, 1)} L purchased by vehicles with Wialon distance` : undefined,
        },
        {
            key: "efficiency",
            label: "Purchase-based efficiency",
            value: has(efficiency) ? formatNumber(efficiency, 1) : "—",
            unit: "L/100 km",
            note: has(costPerKm)
                ? `AED ${formatNumber(costPerKm, 2)} per km`
                : suppressedReason === "insufficient_distance"
                    ? "Below minimum distance"
                    : suppressedReason === "non_positive_net_fuel"
                        ? "Refunds exceed purchases"
                        : "Fuel purchased ÷ Wialon km",
            badge: <ClassificationBadge classification={vc} field="fuel_efficiency_l_per_100km" />,
        },
    ]

    const renderCell = (cell) => (
        <button
            key={cell.key}
            type="button"
            onClick={() => setSelectedKpi(cell.key)}
            className="group flex min-w-0 flex-col bg-white px-4 py-3 text-left transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
            title="View formula and calculation breakdown"
        >
            <span className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium text-slate-500">{cell.label}</span>
                <span className="flex shrink-0 items-center gap-1.5">
                    {cell.badge}
                    <Calculator className="h-3.5 w-3.5 text-slate-300 transition-colors group-hover:text-brand" aria-hidden="true" />
                </span>
            </span>
            <span className="mt-1 flex items-baseline gap-1">
                {cell.prefix && <span className="text-xs text-slate-500">{cell.prefix}</span>}
                <span className={`text-[22px] font-semibold leading-7 tracking-tight tabular-nums ${cell.valueClass || "text-slate-900"}`}>{cell.value}</span>
                {cell.unit && <span className="text-xs text-slate-500">{cell.unit}</span>}
            </span>
            <span className="mt-0.5 flex items-center justify-between gap-2 text-xs text-slate-500">
                <span className="truncate">{cell.note}</span>
                {cell.tag && (
                    <span className="shrink-0 font-mono text-[11px] text-slate-600" title={cell.tagTitle}>{cell.tag}</span>
                )}
            </span>
        </button>
    )

    const strip = "grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 sm:grid-cols-2 lg:grid-cols-4"

    return (
        <div className="mb-5 space-y-4">
            {(excluded || showTelemetryNotice) && (
                <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" role="status">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                    <div className="space-y-0.5">
                        {excluded && (
                            <span className="block">
                                KPIs cover vehicles matched to exactly one Wialon unit; {excluded.count.toLocaleString()} of {excluded.allCount.toLocaleString()} transactions
                                ({formatNumber(excluded.litres, 1)} L) are excluded and listed separately.
                            </span>
                        )}
                        {showTelemetryNotice && (
                            <span className="block font-medium">Wialon telemetry {telemetryStatus}: values shown as "—" were not reported.</span>
                        )}
                        {showTelemetryNotice && dataWarnings.map((w) => <span key={w} className="block">{w}</span>)}
                    </div>
                </div>
            )}

            <section aria-labelledby="fuel-recorded-heading">
                <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <h2 id="fuel-recorded-heading" className="type-section-title text-slate-900">
                        Recorded <span className="font-normal text-slate-500">· fuel statement invoices, net of refunds</span>
                    </h2>
                    {avgPrice !== null && (
                        <span className="text-xs text-slate-500">
                            Weighted avg <span className="font-mono font-medium text-slate-900">AED {avgPrice.toFixed(2)}/L</span>
                        </span>
                    )}
                </div>
                <div className={strip}>{recordedCells.map(renderCell)}</div>
            </section>

            <section aria-labelledby="fuel-estimated-heading">
                <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <h2 id="fuel-estimated-heading" className="type-section-title text-slate-900">
                        Estimated <span className="font-normal text-slate-500">· stationary engine time at {idleRate.toFixed(1)} L/h</span>
                    </h2>
                    {has(idleHours) && (
                        <span className="text-xs text-slate-500">
                            Qualifying idle <span className="font-mono font-medium text-slate-900">{idleHours.toFixed(1)} h</span>
                        </span>
                    )}
                </div>
                <div className={strip}>{estimatedCells.map(renderCell)}</div>
            </section>

            {/* ─── INTERACTIVE KPI FORMULA & BREAKDOWN MODAL ───────────── */}
            {activeBreakdown && createPortal(
                <div
                    className="fixed inset-0 z-[9999] flex items-center justify-center overflow-y-auto bg-slate-900/40 p-4"
                    onClick={() => setSelectedKpi(null)}
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-label={`${activeBreakdown.title} calculation breakdown`}
                        className="my-8 w-full max-w-xl rounded-xl border border-slate-200 bg-white p-5 shadow-dialog"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between pb-3.5 border-b border-slate-100">
                            <div className="flex items-center gap-2.5">
                                <div>
                                    <div className="flex items-center gap-2">
                                        <h3 className="text-[15px] font-semibold text-slate-900">
                                            {activeBreakdown.title}
                                        </h3>
                                        <span
                                            className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                                                activeBreakdown.classificationBadge === "recorded"
                                                    ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                                                    : activeBreakdown.classificationBadge === "estimated"
                                                        ? "bg-amber-50 text-amber-800 border border-amber-200"
                                                        : "bg-sky-50 text-sky-800 border border-sky-200"
                                            }`}
                                        >
                                            {activeBreakdown.category}
                                        </span>
                                    </div>
                                    <p className="text-xs text-slate-500 mt-0.5">
                                        Calculation breakdown, actual inputs, and audit trail
                                    </p>
                                </div>
                            </div>

                            <button
                                type="button"
                                onClick={() => setSelectedKpi(null)}
                                className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                                aria-label="Close"
                            >
                                <X className="h-5 w-5" />
                            </button>
                        </div>

                        {/* Top Result & Metadata Card */}
                        <div className="my-4 rounded-lg border border-slate-200 p-4">
                            <div className="flex flex-col sm:flex-row sm:items-baseline justify-between gap-2 pb-3 border-b border-slate-200/60">
                                <div>
                                    <span className="block text-xs text-slate-500">
                                        Result for current filters
                                    </span>
                                    <span className="text-[28px] font-semibold tracking-tight tabular-nums text-slate-900">
                                        {activeBreakdown.resultText}
                                    </span>
                                </div>
                                <div className="text-left sm:text-right text-[11px] text-slate-500 space-y-0.5">
                                    <div className="flex items-center sm:justify-end gap-1.5 font-medium">
                                        <Calendar className="h-3 w-3 text-brand" />
                                        <span>{activeBreakdown.dateRange}</span>
                                    </div>
                                    <div className="flex items-center sm:justify-end gap-1.5 text-slate-400">
                                        <Database className="h-3 w-3 text-slate-400" />
                                        <span className="truncate max-w-xs">{activeBreakdown.dataSource}</span>
                                    </div>
                                </div>
                            </div>

                            {/* Exact Mathematical Formula */}
                            <div className="pt-3">
                                <span className="mb-1 block text-xs text-slate-500">
                                    Formula
                                </span>
                                <div className="rounded-md bg-slate-50 px-2.5 py-2 font-mono text-xs text-slate-800">
                                    {activeBreakdown.formulaText}
                                </div>
                            </div>
                        </div>

                        {/* Actual Input Values Table */}
                        <div className="my-4">
                            <span className="mb-1.5 block text-xs text-slate-500">
                                Input values (from API data)
                            </span>
                            <div className="rounded-lg border border-slate-200 overflow-hidden text-xs">
                                <table className="w-full text-left">
                                    <thead className="border-b border-slate-200 bg-slate-50 text-[11px] text-slate-500">
                                        <tr>
                                            <th scope="col" className="px-3 py-2 font-medium">Variable</th>
                                            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
                                            <th scope="col" className="px-3 py-2 font-medium">Source</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 bg-white">
                                        {activeBreakdown.inputs.map((inp, idx) => (
                                            <tr key={idx} className="hover:bg-slate-50/60">
                                                <td className="px-3 py-2 text-slate-700">{inp.label}</td>
                                                <td className="px-3 py-2 text-right font-mono font-medium text-slate-900">{inp.value}</td>
                                                <td className="px-3 py-2 text-[11px] text-slate-500">{inp.source}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        {/* Step-by-Step Calculation */}
                        {activeBreakdown.calculationSteps && activeBreakdown.calculationSteps.length > 0 && (
                            <div className="my-4 space-y-1 rounded-md bg-slate-50 px-3 py-2.5 text-xs">
                                <span className="mb-1 block text-xs text-slate-500">
                                    Calculation
                                </span>
                                {activeBreakdown.calculationSteps.map((step, i) => (
                                    <div key={i} className="font-mono text-[11px] text-slate-700 flex items-center gap-2">
                                        <span className="text-slate-400">•</span>
                                        <span>{step}</span>
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Nature & Data Quality Guarantee */}
                        <div className="flex items-start gap-2 border-t border-slate-200 pt-3 text-xs text-slate-600">
                            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                            <p className="text-[11px] leading-relaxed">
                                {activeBreakdown.natureText}
                            </p>
                        </div>

                        {/* Modal Footer */}
                        <div className="mt-5 flex justify-end">
                            <button
                                type="button"
                                onClick={() => setSelectedKpi(null)}
                                className="h-8 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 cursor-pointer"
                            >
                                Close
                            </button>
                        </div>
                    </div>
                </div>,
                document.body
            )}
        </div>
    )
}
