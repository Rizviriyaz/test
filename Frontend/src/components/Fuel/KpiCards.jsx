import { useEffect, useState } from "react"
import { createPortal } from "react-dom"
import {
    X,
    Info,
    Calendar,
    Database,
    Calculator,
} from "lucide-react"
import { fuelShareNote, statementSplit, transactionCountLabel, TREND_STATUS_LABELS } from "./fuelLabels"

function formatNumber(val, decimals = 1) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    })
}

const CARD_SHADOW = "shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]"

function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    })
}

// Recorded statement amounts (statement, matched and the rest) in fils, so the three displayed amounts add up
function formatMoney(val) {
    return formatNumber(val, 2)
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
        const skeleton = (count, cols) => (
            <div className={`grid ${cols} gap-px overflow-hidden rounded-xl border border-slate-200/70 bg-slate-200/60 ${CARD_SHADOW}`}>
                {Array.from({ length: count }, (_, i) => (
                    <div key={i} className="bg-white px-3 py-2.5">
                        <div className="h-3 w-20 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                        <div className="mt-1.5 h-6 w-24 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                        <div className="mt-1.5 h-3 w-16 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                    </div>
                ))}
            </div>
        )
        return (
            <div role="status" aria-label="Loading fuel KPIs" className="space-y-3 sm:space-y-4">
                {skeleton(2, "grid-cols-2")}
                {skeleton(8, "grid-cols-2 sm:grid-cols-4")}
            </div>
        )
    }

    // Excel statement: every eligible statement record (matched, unmatched and ambiguous). Suspected duplicates,
    // partial-month summaries and dated rows outside monthly-summary reporting are left out by the backend.
    // Statement, matched part and the rest at the displayed precision (litres 0.1 L, cost 0.01 AED): the rest (records
    // with no single Wialon unit) is statement − matched, so the displayed figures always add up (fuelLabels)
    const litresSplit = statementSplit(efficiencyData?.total_litres, efficiencyData?.matched_litres, 1)
    const costSplit = statementSplit(efficiencyData?.total_cost, efficiencyData?.matched_cost, 2)
    const statementLitres = litresSplit.total
    const statementCost = costSplit.total
    const statementRowsLabel = transactionCountLabel(efficiencyData?.transaction_count, efficiencyData?.monthly_summary_rows)
    // Statement records with no single Wialon unit: in the statement totals only, never in the matched fleet
    const unmappedLitres = litresSplit.rest
    const unmappedCost = costSplit.rest
    const unmatchedCount = efficiencyData?.unmatched_vehicle_count
    const statementPrice = statementLitres && statementCost ? statementCost / statementLitres : null

    // Matched fleet: vehicles matched to exactly one Wialon unit, the population of every card below
    const totalLitres = litresSplit.matched
    const totalCost = costSplit.matched
    const vehicleCount = efficiencyData?.vehicle_count
    // Monthly-summary rows are statement totals, not refuels: counts are labelled as rows
    const rowsLabel = transactionCountLabel(efficiencyData?.matched_row_count, efficiencyData?.excluded_scope?.monthly_summary_rows)
    const trackedLitres = efficiencyData?.tracked_litres
    // Basis of the idle/driving fuel shares: litres of vehicles with distance or engine hours
    // Litres of the vehicles whose idle fuel is estimated: the basis of the idle/driving fuel shares
    const idleBasisLitres = efficiencyData?.idle_basis_litres
    const vehiclesWithDistance = efficiencyData?.vehicles_with_distance
    const engineOnlyCount = efficiencyData?.vehicles_engine_hours_only
    const suppressedReason = efficiencyData?.efficiency_suppressed_reason

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
    const kmPerLitre = efficiency && efficiency > 0 ? (100 / efficiency) : null

    // Period Range strings for breakdown modal
    const periodFrom = efficiencyData?.excluded_scope?.period?.date_from || "Period Start"
    const periodTo = efficiencyData?.excluded_scope?.period?.date_to || "Period End"
    const dateRangeStr = `${periodFrom} to ${periodTo}`

    // Breakdown details generator for active KPI
    const getBreakdownDetails = (kpiKey) => {
        switch (kpiKey) {
            case "statement_litres":
                return {
                    title: "Total Fuel Purchased (Excel statement)",
                    category: "Recorded Statement Invoices",
                    classificationBadge: "recorded",
                    resultText: `${formatNumber(statementLitres, 1)} Litres`,
                    dateRange: dateRangeStr,
                    dataSource: "Monthly fuel statement summaries (net of refunds)",
                    formulaText: "Matched vehicles + records without a single Wialon vehicle",
                    natureText: "Every eligible statement record, matched to Wialon or not. Suspected duplicates, partial-month summaries and dated rows outside monthly-summary reporting are excluded.",
                    inputs: [
                        { label: "Statement volume", value: `${formatNumber(statementLitres, 1)} L`, source: "All eligible statement records" },
                        { label: "Matched to one Wialon vehicle", value: `${formatNumber(totalLitres, 1)} L`, source: "Matched Fleet Performance" },
                        { label: "No single Wialon vehicle", value: `${formatNumber(unmappedLitres, 1)} L`, source: `${unmatchedCount ?? "—"} identifier(s), listed in Data Coverage` },
                        { label: "Statement rows", value: statementRowsLabel, source: "Monthly Summary Rows" },
                        { label: "Weighted Average Price", value: statementPrice !== null ? `AED ${statementPrice.toFixed(2)} / L` : "—", source: "Statement Cost ÷ Statement Litres" },
                    ],
                    calculationSteps: [
                        `Statement Litres = ${formatNumber(totalLitres, 1)} L matched + ${formatNumber(unmappedLitres, 1)} L without a Wialon vehicle`,
                        `Result = ${formatNumber(statementLitres, 1)} L`,
                    ],
                }
            case "statement_cost":
                return {
                    title: "Total Fuel Cost (Excel statement)",
                    category: "Recorded Statement Invoices",
                    classificationBadge: "recorded",
                    resultText: `AED ${formatMoney(statementCost)}`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel statement invoices (Invoiced amounts in AED)",
                    formulaText: "Matched vehicles + records without a single Wialon vehicle",
                    natureText: "Net invoiced amount of every eligible statement record, matched to Wialon or not.",
                    inputs: [
                        { label: "Statement spend", value: `AED ${formatMoney(statementCost)}`, source: "All eligible statement records" },
                        { label: "Matched to one Wialon vehicle", value: `AED ${formatMoney(totalCost)}`, source: "Matched Fleet Performance" },
                        { label: "No single Wialon vehicle", value: `AED ${formatMoney(unmappedCost)}`, source: "Listed in Data Coverage" },
                        { label: "Statement rows", value: statementRowsLabel, source: "Invoice Rows" },
                    ],
                    calculationSteps: [
                        `Statement Cost = AED ${formatMoney(totalCost)} matched + AED ${formatMoney(unmappedCost)} without a Wialon vehicle`,
                        `Result = AED ${formatMoney(statementCost)}`,
                    ],
                }
            case "total_litres":
                return {
                    title: "Fuel Purchased (matched fleet)",
                    category: "Recorded Statement Invoices",
                    classificationBadge: "recorded",
                    resultText: `${formatNumber(totalLitres, 1)} Litres`,
                    dateRange: dateRangeStr,
                    dataSource: "Monthly fuel statement summaries (net of refunds)",
                    formulaText: "Sum of net litres of vehicles matched to exactly one Wialon unit",
                    natureText: "Only vehicles matched to one Wialon unit, the same vehicles as every other matched-fleet value. Records without a single Wialon vehicle are in the Excel statement totals only.",
                    inputs: [
                        { label: "Matched volume", value: `${formatNumber(totalLitres, 1)} L`, source: "Matched vehicles' monthly summaries" },
                        { label: "Matched vehicles", value: `${vehicleCount ?? "—"}`, source: "Wialon Unit Registry" },
                        { label: "Statement rows", value: rowsLabel, source: "Monthly Summary Rows" },
                        { label: "Weighted Average Price", value: avgPrice !== null ? `AED ${avgPrice.toFixed(2)} / L` : "—", source: "Matched Cost ÷ Matched Litres" },
                    ],
                    calculationSteps: [
                        "Matched Litres = Sum(net litres of matched vehicles)",
                        `Result = ${formatNumber(totalLitres, 1)} L`,
                    ],
                }
            case "total_cost":
                return {
                    title: "Fuel Cost (matched fleet)",
                    category: "Recorded Statement Invoices",
                    classificationBadge: "recorded",
                    resultText: `AED ${formatMoney(totalCost)}`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel statement invoices (Invoiced amounts in AED)",
                    formulaText: "Sum of net invoiced cost of vehicles matched to exactly one Wialon unit",
                    natureText: "Net invoiced amount of the matched vehicles only.",
                    inputs: [
                        { label: "Matched spend", value: `AED ${formatMoney(totalCost)}`, source: "Matched vehicles' monthly summaries" },
                        { label: "Matched volume", value: `${formatNumber(totalLitres, 1)} L`, source: "Matched vehicles' monthly summaries" },
                        { label: "Effective Fuel Price", value: avgPrice !== null ? `AED ${avgPrice.toFixed(2)} / L` : "—", source: "Matched Cost ÷ Matched Litres" },
                        { label: "Statement rows", value: rowsLabel, source: "Invoice Rows" },
                    ],
                    calculationSteps: [
                        "Matched Cost = Sum(net cost of matched vehicles)",
                        `Result = AED ${formatMoney(totalCost)}`,
                    ],
                }
            case "total_distance":
                return {
                    title: "Total Distance Travelled",
                    category: "Recorded Telematics",
                    classificationBadge: "recorded",
                    resultText: `${totalDistance !== null && totalDistance !== undefined ? formatNumber(totalDistance, 1) + " km" : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Trips Report – All Units",
                    formulaText: "Sum of verified GPS trip distance across matched units",
                    natureText: "Authoritative distance travelled recorded by Wialon telematics for units matched to fuel transactions.",
                    inputs: [
                        { label: "Total GPS Distance", value: totalDistance !== null && totalDistance !== undefined ? `${formatNumber(totalDistance, 1)} km` : "—", source: "Wialon GPS Reports" },
                        { label: "Units with Distance", value: `${vehiclesWithDistance ?? "—"} units`, source: "Wialon Trips Registry" },
                        { label: "Total Units Refuelled", value: `${vehicleCount ?? "—"} units`, source: "Matched Vehicle Scope" },
                    ],
                    calculationSteps: [
                        "Total Distance = Sum(Trip Distance in km for matched units)",
                        `Result = ${totalDistance !== null && totalDistance !== undefined ? formatNumber(totalDistance, 1) + " km" : "—"}`,
                    ],
                }
            case "efficiency":
                return {
                    title: "Fleet Fuel Efficiency",
                    category: "Derived Ratio",
                    classificationBadge: "derived",
                    resultText: `${kmPerLitre !== null ? formatNumber(kmPerLitre, 2) + " km/L" : "—"} (${efficiency !== null && efficiency !== undefined ? formatNumber(efficiency, 1) + " L/100km" : "—"})`,
                    dateRange: dateRangeStr,
                    dataSource: "Fuel Invoices (Litres & Cost) + Wialon GPS Telematics (Mileage)",
                    formulaText: "km/L = Total Distance (km) ÷ Tracked Fuel (L) | L/100km = (Tracked Fuel ÷ Distance) × 100",
                    natureText: "Derived efficiency calculated from recorded refuelling volume divided by verified GPS distance. Withheld if distance is below minimum threshold or if net litres are non-positive.",
                    inputs: [
                        { label: "Fuel Efficiency (km/L)", value: kmPerLitre !== null ? `${formatNumber(kmPerLitre, 2)} km/L` : "—", source: "Total Distance ÷ Tracked Fuel" },
                        { label: "Fuel Efficiency (L/100km)", value: efficiency !== null && efficiency !== undefined ? `${formatNumber(efficiency, 1)} L/100 km` : "—", source: "(Tracked Fuel ÷ Distance) × 100" },
                        { label: "Tracked Fuel Purchased", value: `${formatNumber(trackedLitres, 1)} L`, source: "Invoiced Litres of Vehicles with Distance" },
                        { label: "Total Wialon Distance", value: totalDistance !== null && totalDistance !== undefined ? `${formatNumber(totalDistance, 1)} km` : "—", source: "Wialon GPS Odometer Reports" },
                        { label: "Fuel Cost per km", value: costPerKm !== null && costPerKm !== undefined ? `AED ${formatNumber(costPerKm, 2)} / km` : "—", source: "Tracked Cost ÷ Total Distance" },
                        { label: "Suppression Status", value: suppressedReason || "Active (Distance Threshold Satisfied)", source: "Calculation Guard" },
                    ],
                    calculationSteps: [
                        `km/L = ${totalDistance !== null && totalDistance !== undefined ? formatNumber(totalDistance, 1) : "—"} km ÷ ${formatNumber(trackedLitres, 1)} L`,
                        `Result = ${kmPerLitre !== null ? formatNumber(kmPerLitre, 2) + " km/L" : "—"} (${efficiency !== null && efficiency !== undefined ? formatNumber(efficiency, 1) + " L/100 km" : "—"})`,
                    ],
                }
            case "driving_hours":
                return {
                    title: "Total Driving Hours",
                    category: "Recorded Telematics",
                    classificationBadge: "recorded",
                    resultText: `${drivingHours !== null && drivingHours !== undefined ? formatNumber(drivingHours, 1) + " h" : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Motion Sensor Telematics",
                    formulaText: "Sum of duration with ignition on and vehicle speed > 0 km/h",
                    natureText: "Operational duration spent in active motion performing transportation tasks.",
                    inputs: [
                        { label: "Productive Driving Time", value: drivingHours !== null && drivingHours !== undefined ? `${drivingHours.toFixed(1)} hrs` : "—", source: "Wialon Motion Telematics" },
                        { label: "Estimated Driving Fuel", value: estimatedDrivingFuel !== null && estimatedDrivingFuel !== undefined ? `${formatNumber(estimatedDrivingFuel, 1)} L` : "—", source: "Tracked Litres - Estimated Idle Fuel" },
                        { label: "Estimated driving share of fuel", value: drivingPct !== null && drivingPct !== undefined ? `${drivingPct.toFixed(1)}%` : "—", source: "Estimated driving litres ÷ litres of vehicles with known idle hours" },
                    ],
                    calculationSteps: [
                        "Driving Hours = Sum of motion durations across matched units",
                        `Result = ${drivingHours !== null && drivingHours !== undefined ? formatNumber(drivingHours, 1) + " h" : "—"}`,
                    ],
                }
            case "idle_hours":
                return {
                    title: "Total Idling Hours",
                    category: "Recorded Telematics",
                    classificationBadge: "recorded",
                    resultText: `${idleHours !== null && idleHours !== undefined ? formatNumber(idleHours, 1) + " h" : "—"}`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Stationary Engine Telematics",
                    formulaText: "Sum of duration with ignition on and vehicle speed = 0 km/h",
                    natureText: "Duration where vehicle engine was running stationarily without productive movement.",
                    inputs: [
                        { label: "Stationary Idle Time", value: idleHours !== null && idleHours !== undefined ? `${idleHours.toFixed(1)} hrs` : "—", source: "Wialon Stationary Engine Telematics" },
                        { label: "Configured Idle Burn Rate", value: `${idleRate.toFixed(1)} L/h`, source: "Configurable Idle Parameter" },
                        { label: "Estimated idle share of fuel", value: idlePct !== null && idlePct !== undefined ? `${idlePct.toFixed(1)}%` : "—", source: "Estimated idle litres ÷ litres of vehicles with known idle hours" },
                    ],
                    calculationSteps: [
                        "Idling Hours = Sum of stationary engine durations",
                        `Result = ${idleHours !== null && idleHours !== undefined ? formatNumber(idleHours, 1) + " h" : "—"}`,
                    ],
                }
            case "estimated_idle_fuel":
                return {
                    title: "Estimated Idling Fuel Waste",
                    category: "Estimated Telematics",
                    classificationBadge: "estimated",
                    resultText: `${estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) + " L" : "—"} (AED ${estimatedIdleCost !== null && estimatedIdleCost !== undefined ? formatCurrency(estimatedIdleCost) : "—"})`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Stationary Engine Telematics & Configured Idle Rate",
                    formulaText: "Qualifying Idle Hours (hrs) × Configured Idle Rate (L/h)",
                    natureText: "Estimated telematics value. Wialon reports duration where ignition was on while speed was 0 km/h. Fuel consumed is estimated using the configurable burn coefficient.",
                    inputs: [
                        { label: "Qualifying Idle Duration", value: idleHours !== null && idleHours !== undefined ? `${idleHours.toFixed(1)} hrs` : "—", source: "Wialon Telematics (Stationary Engine)" },
                        { label: "Configured Idle Burn Rate", value: `${idleRate.toFixed(1)} L/h`, source: "Dashboard Coefficient Parameter" },
                        { label: "Fuel of vehicles with known idle hours", value: idleBasisLitres !== null && idleBasisLitres !== undefined ? `${formatNumber(idleBasisLitres, 1)} L` : "—", source: "Net purchased litres of vehicles with known Wialon idle hours, recorded 0 h included (mixed-fuel vehicles excluded under a fuel-type filter)" },
                        { label: "Estimated idle share of fuel", value: idlePct !== null && idlePct !== undefined ? `${idlePct.toFixed(1)}%` : "—", source: "Estimated idle litres ÷ litres of vehicles with known idle hours" },
                        { label: "Estimated Idle Spend", value: `AED ${estimatedIdleCost !== null && estimatedIdleCost !== undefined ? formatCurrency(estimatedIdleCost) : "—"}`, source: "Sum of each vehicle's idle litres × its own average price" },
                    ],
                    calculationSteps: [
                        `Idle Fuel = Σ per vehicle (idle hours × ${idleRate.toFixed(1)} L/h), over ${idleHours !== null && idleHours !== undefined ? idleHours.toFixed(1) : "—"} idle hrs`,
                        `Result = ${estimatedIdleFuel !== null && estimatedIdleFuel !== undefined ? formatNumber(estimatedIdleFuel, 1) + " L" : "—"} (${idlePct !== null && idlePct !== undefined ? idlePct.toFixed(1) + "% of those vehicles' fuel" : "—"})`,
                    ],
                }
            case "vehicle_count":
                return {
                    title: "Vehicles Refuelled & Tracked",
                    category: "Recorded Scope",
                    classificationBadge: "recorded",
                    resultText: `${vehicleCount ?? "—"} Units`,
                    dateRange: dateRangeStr,
                    dataSource: "Wialon Unit Registry & Fuel Statement Plate Resolution",
                    formulaText: "Count of Unique Wialon Units with Refuel Transactions in Period",
                    natureText: "Fleet units matched to an active Wialon telematics unit that recorded fuel transactions. This is not the total fleet size.",
                    inputs: [
                        { label: "Matched Wialon Units", value: `${vehicleCount ?? "—"}`, source: "Wialon Unit Registry" },
                        { label: "Units with GPS Distance", value: `${vehiclesWithDistance ?? "—"}`, source: "Wialon Odometer Reports" },
                        { label: "Units with Engine-Hours Only", value: `${engineOnlyCount ?? "—"}`, source: "Wialon Engine Reports" },
                        { label: "Fuel Volume (Tracked Units)", value: `${formatNumber(trackedLitres, 1)} L`, source: "Matched Vehicle Invoices" },
                    ],
                    calculationSteps: [
                        "Count = Distinct Wialon Units attributed to valid fuel transactions",
                        `Result = ${vehicleCount ?? "—"} units (${vehiclesWithDistance ?? "—"} reporting GPS distance)`,
                    ],
                }
            default:
                return null
        }
    }

    const activeBreakdown = selectedKpi ? getBreakdownDetails(selectedKpi) : null
    const has = (v) => v !== null && v !== undefined

    // Primary & Secondary KPI configurations. Unknown values render as "—" with an "Unavailable" note, never as 0.
    const efficiencyWithheld = !has(efficiency) && suppressedReason ? (TREND_STATUS_LABELS[suppressedReason] || "Withheld") : null
    const statementMetrics = [
        {
            key: "statement_litres",
            label: "Total Fuel Purchased",
            value: formatNumber(statementLitres, 1),
            unit: "L",
            note: unmappedLitres ? `incl. ${formatNumber(unmappedLitres, 1)} L without a Wialon vehicle` : null,
            provenance: "Recorded",
            title: "All eligible Excel statement records, matched to Wialon or not",
        },
        {
            key: "statement_cost",
            label: "Total Fuel Cost",
            prefix: "AED",
            value: formatMoney(statementCost),
            note: statementRowsLabel,
            provenance: "Recorded",
            title: "All eligible Excel statement records, matched to Wialon or not",
        },
    ]

    const primaryMetrics = [
        {
            key: "total_litres",
            label: "Fuel Purchased",
            value: formatNumber(totalLitres, 1),
            unit: "L",
            note: avgPrice ? `AED ${avgPrice.toFixed(2)} / L avg` : null,
            provenance: "Recorded",
            title: "Vehicles matched to one Wialon unit",
        },
        {
            key: "total_cost",
            label: "Fuel Cost",
            prefix: "AED",
            value: formatMoney(totalCost),
            note: rowsLabel,
            provenance: "Recorded",
            title: "Vehicles matched to one Wialon unit",
        },
        {
            key: "total_distance",
            label: "Distance",
            value: has(totalDistance) ? formatNumber(totalDistance, 0) : "—",
            unit: "km",
            note: !has(totalDistance)
                ? "Unavailable"
                : has(vehiclesWithDistance) && has(vehicleCount) ? `${vehiclesWithDistance} of ${vehicleCount} vehicles` : null,
            provenance: "Recorded",
        },
        {
            key: "efficiency",
            label: "Fuel Efficiency",
            value: has(kmPerLitre) ? formatNumber(kmPerLitre, 2) : (has(efficiency) ? formatNumber(efficiency, 1) : "—"),
            unit: has(kmPerLitre) ? "km/L" : (has(efficiency) ? "L/100km" : ""),
            note: efficiencyWithheld
                ?? (has(kmPerLitre) && has(efficiency)
                    ? `${efficiency.toFixed(1)} L/100km · AED ${fixedOrDash(costPerKm)}/km`
                    : (has(costPerKm) ? `AED ${fixedOrDash(costPerKm)} / km` : "Unavailable")),
            provenance: "Derived",
            title: "Fuel purchased ÷ Wialon distance (purchase-based, not measured consumption)",
        },
    ]

    const secondaryMetrics = [
        {
            key: "driving_hours",
            label: "Driving Hours",
            value: has(drivingHours) ? formatNumber(drivingHours, 1) : "—",
            unit: "h",
            note: has(drivingHours) ? fuelShareNote(drivingPct) : "Unavailable",
            provenance: "Recorded",
        },
        {
            key: "idle_hours",
            label: "Idling Hours",
            value: has(idleHours) ? formatNumber(idleHours, 1) : "—",
            unit: "h",
            note: has(idleHours) ? fuelShareNote(idlePct) : "Unavailable",
            provenance: "Recorded",
        },
        {
            key: "estimated_idle_fuel",
            label: "Est. Idle Fuel",
            value: has(estimatedIdleFuel) ? formatNumber(estimatedIdleFuel, 1) : "—",
            unit: "L",
            note: has(estimatedIdleCost) ? `AED ${formatCurrency(estimatedIdleCost)} est. cost` : (has(estimatedIdleFuel) ? "Cost unavailable" : "Unavailable"),
            valueClass: has(estimatedIdleCost) && estimatedIdleCost > 0 ? "text-amber-800" : "text-slate-900",
            tag: `${idleRate.toFixed(1)} L/h`,
            provenance: "Estimated",
            title: "Sum per vehicle of idle hours × idle fuel rate (estimate, not measured consumption)",
        },
        {
            key: "vehicle_count",
            label: "Vehicles",
            value: has(vehicleCount) ? vehicleCount : "—",
            unit: "matched",
            note: has(vehiclesWithDistance) ? `${vehiclesWithDistance} with distance` : null,
            provenance: "Recorded",
            title: "Refuelled vehicles matched to one Wialon unit (not total fleet size)",
        },
    ]

    function fixedOrDash(val) {
        if (val === null || val === undefined) return "—"
        return Number(val).toFixed(2)
    }

    // Label, value and (only when it adds context) one short note; provenance stays in the tooltip and the breakdown
    const renderCard = (cell) => (
        <button
            key={cell.key}
            type="button"
            onClick={() => setSelectedKpi(cell.key)}
            className="group flex min-w-0 flex-col bg-white px-3 py-2.5 text-left transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
            title={`${cell.title || cell.label} (${cell.provenance}). Click for the calculation.`}
        >
            <span className="flex items-center justify-between gap-1.5">
                <span className="truncate text-[11px] font-medium text-slate-500">
                    {cell.label}
                </span>
                {cell.tag ? (
                    <span className="shrink-0 font-mono text-[10px] font-medium text-slate-600 bg-slate-100 rounded px-1">
                        {cell.tag}
                    </span>
                ) : (
                    <Calculator className="h-3 w-3 shrink-0 text-slate-300 transition-colors group-hover:text-brand" aria-hidden="true" />
                )}
            </span>
            <span className="mt-0.5 flex items-baseline gap-1">
                {cell.prefix && <span className="text-xs font-medium text-slate-500">{cell.prefix}</span>}
                <span className={`text-[22px] sm:text-2xl font-semibold leading-tight tracking-tight tabular-nums ${cell.valueClass || "text-slate-900"}`}>
                    {cell.value}
                </span>
                {cell.unit && <span className="text-xs text-slate-500 font-normal">{cell.unit}</span>}
            </span>
            {cell.note && (
                <span className="mt-0.5 truncate text-[11px] text-slate-500">{cell.note}</span>
            )}
        </button>
    )

    const groupHeading = (id, text) => (
        <h2 id={id} className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">{text}</h2>
    )

    return (
        <div className="space-y-3 sm:space-y-4">
            {/* Excel statement totals: every eligible record, matched to Wialon or not */}
            <section aria-labelledby="fuel-statement-heading">
                {groupHeading("fuel-statement-heading", "Excel Statement")}
                <div className={`grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200/70 bg-slate-200/60 ${CARD_SHADOW}`}>
                    {statementMetrics.map(renderCard)}
                </div>
            </section>

            {/* Matched fleet: vehicles matched to exactly one Wialon unit only */}
            <section aria-labelledby="fuel-matched-heading">
                {groupHeading("fuel-matched-heading", "Matched Fleet Performance")}
                <div className={`grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200/70 bg-slate-200/60 sm:grid-cols-4 ${CARD_SHADOW}`}>
                    {primaryMetrics.map(renderCard)}
                    {secondaryMetrics.map(renderCard)}
                </div>
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
                                    Calculation formula, inputs, data source, and transparent audit trail
                                </p>
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

                        {/* Result & Metadata Card */}
                        <div className="my-4 rounded-lg border border-slate-200 p-4">
                            <div className="flex flex-col sm:flex-row sm:items-baseline justify-between gap-2 pb-3 border-b border-slate-200/60">
                                <div>
                                    <span className="block text-xs text-slate-500">
                                        Result for current filters
                                    </span>
                                    <span className="text-[26px] font-semibold tracking-tight tabular-nums text-slate-900">
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
                                <span className="mb-1 block text-xs text-slate-500 font-medium">
                                    Formula
                                </span>
                                <div className="rounded-md bg-slate-50 px-2.5 py-2 font-mono text-xs text-slate-800">
                                    {activeBreakdown.formulaText}
                                </div>
                            </div>
                        </div>

                        {/* Actual Input Values Table */}
                        <div className="my-4">
                            <span className="mb-1.5 block text-xs text-slate-500 font-medium">
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
                                <span className="mb-1 block text-xs text-slate-500 font-medium">
                                    Calculation Steps
                                </span>
                                {activeBreakdown.calculationSteps.map((step, i) => (
                                    <div key={i} className="font-mono text-[11px] text-slate-700 flex items-center gap-2">
                                        <span className="text-slate-400">•</span>
                                        <span>{step}</span>
                                    </div>
                                ))}
                            </div>
                        )}

                        {/* Nature & Data Quality */}
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
