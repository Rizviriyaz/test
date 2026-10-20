import { useState } from "react"
import { createPortal } from "react-dom"
import {
    Droplets,
    DollarSign,
    Route,
    Gauge,
    Clock,
    Flame,
    ShieldCheck,
    Activity,
    AlertTriangle,
    X,
    Info,
    Calendar,
    Database,
    Calculator,
    ArrowUpRight,
    HelpCircle,
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

    if (loading) {
        return (
            <div className="space-y-4 mb-6">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                    {[1, 2, 3, 4].map((i) => (
                        <div key={i} className="animate-pulse rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
                            <div className="flex items-center justify-between mb-2.5">
                                <div className="h-3 w-24 bg-slate-200 rounded" />
                                <div className="h-7 w-7 bg-slate-100 rounded-lg" />
                            </div>
                            <div className="h-7 w-32 bg-slate-200 rounded mb-2" />
                            <div className="h-3 w-20 bg-slate-100 rounded" />
                        </div>
                    ))}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                    {[5, 6, 7, 8].map((i) => (
                        <div key={i} className="animate-pulse rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs">
                            <div className="flex items-center justify-between mb-2.5">
                                <div className="h-3 w-24 bg-slate-200 rounded" />
                                <div className="h-7 w-7 bg-slate-100 rounded-lg" />
                            </div>
                            <div className="h-7 w-32 bg-slate-200 rounded mb-2" />
                            <div className="h-3 w-20 bg-slate-100 rounded" />
                        </div>
                    ))}
                </div>
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

    return (
        <div className="space-y-4 mb-6">
            {(excluded || showTelemetryNotice) && (
                <div className="rounded-2xl border border-amber-200 bg-amber-50/70 p-3 text-[11px] text-amber-900 flex items-start gap-2" role="status">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
                    <div className="space-y-0.5">
                        {excluded && (
                            <span className="block font-bold">
                                KPIs cover vehicles matched to exactly one Wialon unit; {excluded.count.toLocaleString()} of {excluded.allCount.toLocaleString()} transactions
                                ({formatNumber(excluded.litres, 1)} L) are excluded and listed separately.
                            </span>
                        )}
                        {showTelemetryNotice && (
                            <span className="block font-bold">Wialon telemetry {telemetryStatus}: values shown as "—" were not reported.</span>
                        )}
                        {showTelemetryNotice && dataWarnings.map((w) => <span key={w} className="block">{w}</span>)}
                    </div>
                </div>
            )}

            {/* ─── SECTION 1: RECORDED FUEL TRANSACTIONS ────────────────────── */}
            <div>
                <div className="flex items-center justify-between mb-2 px-1">
                    <div className="flex items-center gap-2">
                        <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-[10px] font-black tracking-wider uppercase text-emerald-800 border border-emerald-200/60">
                            <ShieldCheck className="h-3 w-3" />
                            Recorded Fuel Transactions
                        </span>
                        <span className="text-[11px] font-medium text-slate-400 hidden sm:inline">
                            Fuel statement invoices (net of refunds)
                        </span>
                    </div>
                    {avgPrice !== null && (
                        <span className="text-[11px] font-bold text-slate-500">
                            Weighted Avg: <span className="font-mono text-slate-900 font-black">AED {avgPrice.toFixed(2)}/L</span>
                        </span>
                    )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                    {/* 1. Fuel Purchased */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("total_litres")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs hover:border-[#33758F]/60 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Fuel Purchased
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-slate-400 group-hover:text-[#33758F] font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-blue-50 p-1.5 text-blue-600 border border-blue-100">
                                    <Droplets className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {formatNumber(totalLitres, 1)}
                            </span>
                            <span className="text-xs font-bold text-slate-400">Litres</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span>Net purchased volume</span>
                            <ClassificationBadge classification={vc} field="total_litres" />
                        </div>
                    </button>

                    {/* 2. Fuel Expenditure */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("total_cost")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs hover:border-[#33758F]/60 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Recorded Expenditure
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-slate-400 group-hover:text-[#33758F] font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-emerald-50 p-1.5 text-emerald-600 border border-emerald-100">
                                    <DollarSign className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-xs font-bold text-slate-500">AED</span>
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {formatCurrency(totalCost)}
                            </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span>Total fuel spend</span>
                            <ClassificationBadge classification={vc} field="total_cost" />
                        </div>
                    </button>

                    {/* 3. Refuel Transactions */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("transaction_count")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs hover:border-[#33758F]/60 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Refuel Transactions
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-slate-400 group-hover:text-[#33758F] font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-amber-50 p-1.5 text-amber-600 border border-amber-100">
                                    <Activity className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {transactionCount !== undefined ? formatCurrency(transactionCount) : "—"}
                            </span>
                            <span className="text-xs font-bold text-slate-400">Events</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span>
                                {totalLitres && transactionCount ? `~${(totalLitres / transactionCount).toFixed(1)} L / refuel` : "Statement records"}
                            </span>
                            <ClassificationBadge classification={vc} field="transaction_count" />
                        </div>
                    </button>

                    {/* 4. Vehicles Refuelled */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("vehicle_count")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-4 shadow-xs hover:border-[#33758F]/60 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">
                                Vehicles Refuelled
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-slate-400 group-hover:text-[#33758F] font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-purple-50 p-1.5 text-purple-600 border border-purple-100">
                                    <Route className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {vehicleCount !== undefined && vehicleCount !== null ? vehicleCount : "—"}
                            </span>
                            <span className="text-xs font-bold text-slate-400">Wialon units</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                            <span className="text-emerald-700 font-bold">
                                {vehiclesWithDistance !== undefined && vehiclesWithDistance !== null ? `${vehiclesWithDistance} with distance` : "—"}
                            </span>
                            <span className="font-semibold text-slate-500">
                                {engineOnlyCount ? `${engineOnlyCount} engine-only` : ""}
                            </span>
                        </div>
                    </button>
                </div>
            </div>

            {/* ─── SECTION 2: ESTIMATED TELEMATICS & CONSUMPTION ─────────── */}
            <div>
                <div className="flex items-center justify-between mb-2 px-1">
                    <div className="flex items-center gap-2">
                        <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 text-[10px] font-black tracking-wider uppercase text-amber-800 border border-amber-200/60">
                            <Flame className="h-3 w-3" />
                            Estimated Telematics Intelligence
                        </span>
                        <span className="text-[11px] font-medium text-slate-400 hidden sm:inline">
                            Stationary engine duration @ {idleRate.toFixed(1)} L/h rate
                        </span>
                    </div>
                    {idleHours !== undefined && idleHours !== null && (
                        <span className="text-[11px] font-bold text-slate-500">
                            Qualifying Idle: <span className="font-mono text-amber-700 font-black">{idleHours.toFixed(1)} hrs</span>
                        </span>
                    )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                    {/* 5. Estimated Idle Fuel */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("estimated_idle_fuel")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-amber-200/80 bg-gradient-to-br from-amber-50/40 to-white p-4 shadow-xs hover:border-amber-400 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-amber-900">
                                Estimated Idling Fuel
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-amber-700/80 group-hover:text-amber-900 font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-amber-100 p-1.5 text-amber-700 border border-amber-200">
                                    <Clock className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-amber-900">
                                {estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) : "—"}
                            </span>
                            <span className="text-xs font-bold text-amber-700">Litres</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-amber-800">
                            <span>
                                {idlePct !== null && idlePct !== undefined ? `${idlePct.toFixed(1)}% of fuel` : "Idle consumption"}
                            </span>
                            <span className="font-mono text-[10px] bg-amber-100 px-1.5 py-0.5 rounded font-bold">
                                {idleRate.toFixed(1)} L/h
                            </span>
                        </div>
                    </button>

                    {/* 6. Estimated Idle Fuel Cost */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("estimated_idle_cost")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-rose-200/80 bg-gradient-to-br from-rose-50/40 to-white p-4 shadow-xs hover:border-rose-400 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-rose-900">
                                Estimated Idle Cost
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-rose-700/80 group-hover:text-rose-900 font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-rose-100 p-1.5 text-rose-700 border border-rose-200">
                                    <Flame className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-xs font-bold text-rose-700">AED</span>
                            <span className="text-2xl font-black tracking-tight text-rose-900">
                                {estimatedIdleCost !== null && estimatedIdleCost !== undefined ? formatCurrency(estimatedIdleCost) : "—"}
                            </span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-rose-800">
                            <span>Direct idling loss</span>
                            <span className="font-semibold text-rose-700">Actionable Waste</span>
                        </div>
                    </button>

                    {/* 7. Estimated Driving Fuel */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("estimated_driving_fuel")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-sky-200/80 bg-gradient-to-br from-sky-50/30 to-white p-4 shadow-xs hover:border-sky-400 hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-sky-900">
                                Estimated Driving Fuel
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-sky-700/80 group-hover:text-sky-900 font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-sky-100 p-1.5 text-sky-700 border border-sky-200">
                                    <Route className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-sky-900">
                                {estimatedDrivingFuel !== null && estimatedDrivingFuel !== undefined ? formatNumber(estimatedDrivingFuel, 1) : "—"}
                            </span>
                            <span className="text-xs font-bold text-sky-700">Litres</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-sky-800">
                            <span>
                                {drivingPct !== null && drivingPct !== undefined ? `${drivingPct.toFixed(1)}% productive` : (drivingHours ? `${drivingHours.toFixed(1)}h in motion` : "Productive motion")}
                            </span>
                            <span className="font-mono text-[10px] bg-sky-100 px-1.5 py-0.5 rounded font-bold" title={trackedLitres !== undefined && trackedLitres !== null ? `${formatNumber(trackedLitres, 1)} L purchased by vehicles with Wialon distance` : undefined}>
                                {formatNumber(totalDistance, 0)} km
                            </span>
                        </div>
                    </button>

                    {/* 8. Fleet Fuel Efficiency */}
                    <button
                        type="button"
                        onClick={() => setSelectedKpi("efficiency")}
                        className="group text-left relative overflow-hidden rounded-2xl border border-[#33758F]/30 bg-gradient-to-br from-[#33758F]/5 to-white p-4 shadow-xs hover:border-[#33758F] hover:shadow-sm transition-all cursor-pointer"
                        title="Click to view exact formula and calculation breakdown"
                    >
                        <div className="flex items-center justify-between mb-1.5">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-[#33758F]">
                                Purchase-based Efficiency
                            </span>
                            <div className="flex items-center gap-1">
                                <span className="text-[10px] text-[#33758F]/80 group-hover:text-[#33758F] font-semibold transition-colors flex items-center">
                                    Formula <ArrowUpRight className="h-3 w-3 ml-0.5" />
                                </span>
                                <div className="rounded-lg bg-[#33758F]/10 p-1.5 text-[#33758F] border border-[#33758F]/20">
                                    <Gauge className="h-3.5 w-3.5" />
                                </div>
                            </div>
                        </div>
                        <div className="flex items-baseline gap-1.5">
                            <span className="text-2xl font-black tracking-tight text-slate-900">
                                {efficiency !== null && efficiency !== undefined ? formatNumber(efficiency, 1) : "—"}
                            </span>
                            <span className="text-xs font-bold text-slate-500">L / 100 km</span>
                        </div>
                        <div className="mt-2 flex items-center justify-between text-[11px] text-slate-600">
                            <span>
                                {costPerKm !== null && costPerKm !== undefined
                                    ? `AED ${formatNumber(costPerKm, 2)} / km`
                                    : suppressedReason === "insufficient_distance"
                                        ? "Below minimum distance"
                                        : suppressedReason === "non_positive_net_fuel"
                                            ? "Refunds exceed purchases"
                                            : "Fuel purchased ÷ Wialon km"}
                            </span>
                            <ClassificationBadge classification={vc} field="fuel_efficiency_l_per_100km" />
                        </div>
                    </button>
                </div>
            </div>

            {/* ─── INTERACTIVE KPI FORMULA & BREAKDOWN MODAL ───────────── */}
            {activeBreakdown && createPortal(
                <div
                    className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/50 backdrop-blur-xs p-4 overflow-y-auto"
                    onClick={() => setSelectedKpi(null)}
                >
                    <div
                        className="w-full max-w-xl rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl my-8 animate-in fade-in zoom-in-95 duration-150"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Header */}
                        <div className="flex items-center justify-between pb-3.5 border-b border-slate-100">
                            <div className="flex items-center gap-2.5">
                                <div className="rounded-xl bg-[#33758F]/10 p-2 text-[#33758F]">
                                    <Calculator className="h-4 w-4" />
                                </div>
                                <div>
                                    <div className="flex items-center gap-2">
                                        <h3 className="text-base font-black text-slate-900">
                                            {activeBreakdown.title}
                                        </h3>
                                        <span
                                            className={`rounded-md px-2 py-0.5 text-[10px] font-black uppercase tracking-wider ${
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
                                className="rounded-xl p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors cursor-pointer"
                            >
                                <X className="h-5 w-5" />
                            </button>
                        </div>

                        {/* Top Result & Metadata Card */}
                        <div className="my-4 rounded-2xl border border-slate-200/90 bg-slate-50/70 p-4">
                            <div className="flex flex-col sm:flex-row sm:items-baseline justify-between gap-2 pb-3 border-b border-slate-200/60">
                                <div>
                                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block">
                                        Current Filter Result
                                    </span>
                                    <span className="text-3xl font-black tracking-tight text-slate-900">
                                        {activeBreakdown.resultText}
                                    </span>
                                </div>
                                <div className="text-left sm:text-right text-[11px] text-slate-500 space-y-0.5">
                                    <div className="flex items-center sm:justify-end gap-1.5 font-medium">
                                        <Calendar className="h-3 w-3 text-[#33758F]" />
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
                                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500 block mb-1">
                                    Exact Calculation Formula
                                </span>
                                <div className="rounded-xl bg-white border border-slate-200/90 p-2.5 font-mono text-xs font-bold text-slate-800 shadow-2xs">
                                    {activeBreakdown.formulaText}
                                </div>
                            </div>
                        </div>

                        {/* Actual Input Values Table */}
                        <div className="my-4">
                            <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-2 px-0.5">
                                Actual Input Values (From API Data)
                            </span>
                            <div className="rounded-xl border border-slate-200 overflow-hidden text-xs">
                                <table className="w-full text-left">
                                    <thead className="bg-slate-100/70 text-[10px] font-bold uppercase tracking-wider text-slate-500 border-b border-slate-200">
                                        <tr>
                                            <th className="px-3 py-2">Variable / Metric</th>
                                            <th className="px-3 py-2 text-right">Actual Value</th>
                                            <th className="px-3 py-2 text-slate-400">Data Source</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 bg-white">
                                        {activeBreakdown.inputs.map((inp, idx) => (
                                            <tr key={idx} className="hover:bg-slate-50/60">
                                                <td className="px-3 py-2 font-semibold text-slate-700">{inp.label}</td>
                                                <td className="px-3 py-2 font-mono font-bold text-slate-900 text-right">{inp.value}</td>
                                                <td className="px-3 py-2 text-[11px] text-slate-400">{inp.source}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        {/* Step-by-Step Calculation */}
                        {activeBreakdown.calculationSteps && activeBreakdown.calculationSteps.length > 0 && (
                            <div className="my-4 rounded-xl border border-slate-100 bg-slate-50/50 p-3 text-xs space-y-1">
                                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400 block mb-1">
                                    Substitution & Calculation Steps
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
                        <div className="rounded-xl border border-blue-100 bg-blue-50/40 p-3 text-xs text-slate-600 flex items-start gap-2">
                            <Info className="h-4 w-4 text-[#33758F] shrink-0 mt-0.5" />
                            <p className="text-[11px] leading-relaxed">
                                {activeBreakdown.natureText}
                            </p>
                        </div>

                        {/* Modal Footer */}
                        <div className="mt-5 flex justify-end">
                            <button
                                type="button"
                                onClick={() => setSelectedKpi(null)}
                                className="rounded-xl bg-slate-100 hover:bg-slate-200 px-4 py-2 text-xs font-bold text-slate-700 transition-colors cursor-pointer"
                            >
                                Close Breakdown
                            </button>
                        </div>
                    </div>
                </div>,
                document.body
            )}
        </div>
    )
}
