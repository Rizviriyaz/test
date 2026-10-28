import { ArrowUpRight } from "lucide-react"
import SectionCard from "../Common/SectionCard"
import { ErrorState } from "../Common/StateViews"
import { shareSplit } from "./fuelLabels"

const DRIVE_COLOR = "#33758F"
const IDLE_COLOR = "#D97706"

function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    })
}

const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v))
const pctText = (v) => (v === null ? "—" : `${Math.round(v)}%`)

// One stacked bar. A split the data cannot support (a part unknown or negative) is not drawn: no false 100%.
function SplitBar({ title, tag, split, driveText, idleText, unavailable }) {
    return (
        <div>
            <div className="mb-1.5 flex items-baseline justify-between gap-2 text-xs">
                <span className="font-medium text-slate-700">
                    {title} <span className="font-mono text-[10px] text-slate-400">{tag}</span>
                </span>
                <span className="tabular-nums text-[11px] text-slate-500">{driveText} · {idleText}</span>
            </div>
            {split ? (
                <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
                    <div style={{ width: `${split.first}%`, backgroundColor: DRIVE_COLOR }} className="transition-[width] duration-300 motion-reduce:transition-none" />
                    <div style={{ width: `${split.second}%`, backgroundColor: IDLE_COLOR }} className="transition-[width] duration-300 motion-reduce:transition-none" />
                </div>
            ) : (
                <div className="flex h-2.5 w-full items-center rounded-full bg-slate-100" title={unavailable} />
            )}
            <div className="mt-1.5 flex items-center justify-between text-[11px]">
                {split ? (
                    <>
                        <span className="inline-flex items-center gap-1.5 text-slate-600">
                            <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: DRIVE_COLOR }} aria-hidden="true" />
                            Driving {pctText(split.first)}
                        </span>
                        <span className="inline-flex items-center gap-1.5 text-slate-600">
                            <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: IDLE_COLOR }} aria-hidden="true" />
                            Idling {pctText(split.second)}
                        </span>
                    </>
                ) : (
                    <span className="text-slate-400">{unavailable}</span>
                )}
            </div>
        </div>
    )
}

export default function DrivingVsIdlingChart({
    efficiencyData,
    loading,
    idleRate = 2.0,
    // Fleet-wide ranking (efficiency/vehicles sorted by estimated idle fuel), independent of the leaderboard page
    idleWasters = [],
    wastersLoading = false,
    wastersError = null,
    onRetryWasters,
    onSelectVehicle,
}) {
    const safeIdleRate = typeof idleRate === "number" && !isNaN(idleRate) ? idleRate : 2.0
    const drivingFuel = num(efficiencyData?.estimated_driving_fuel_litres)
    const idleFuel = num(efficiencyData?.estimated_idle_fuel_litres)
    const drivingHours = num(efficiencyData?.total_driving_hours)
    const idleHours = num(efficiencyData?.total_idle_hours)
    const idleCost = num(efficiencyData?.estimated_idle_cost)
    // Vehicles whose Wialon hours are missing are left out of the hour totals (never counted as 0 h): say how many
    const hoursKnown = num(efficiencyData?.vehicles_hours_known)
    const hoursUnknown = num(efficiencyData?.vehicles_hours_unknown)

    // Hour shares come from recorded hours only, never from the fuel percentages
    const hoursSplit = shareSplit(drivingHours, idleHours)
    const hoursDrivingPct = hoursSplit ? hoursSplit.first : null
    const hoursIdlePct = hoursSplit ? hoursSplit.second : null
    const fuelSplit = shareSplit(drivingFuel, idleFuel)
    const fuelUnavailable = drivingFuel !== null && drivingFuel < 0
        ? "Split unavailable: idle estimate exceeds fuel purchased"
        : "Split unavailable: fuel estimate incomplete"

    const wasters = idleWasters.filter((v) => num(v.estimated_idle_fuel_litres) > 0).slice(0, 5)

    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12 items-stretch">
            <SectionCard
                title="Driving vs Idling"
                className="lg:col-span-7"
                actions={idleCost !== null && idleCost > 0 ? (
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-900" title="Sum per vehicle of idle hours × idle fuel rate × that vehicle's average price">
                        AED {formatCurrency(idleCost)} est. idle cost
                    </span>
                ) : null}
            >
                {loading ? (
                    <div className="space-y-5" role="status" aria-label="Loading driving vs idling">
                        <div className="h-10 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                        <div className="h-10 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                    </div>
                ) : (
                    <div className="space-y-5">
                        {hoursUnknown !== null && hoursUnknown > 0 && (
                            <p className="text-[11px] text-amber-800" role="status">
                                Hours reported for {hoursKnown ?? 0} of {(hoursKnown ?? 0) + hoursUnknown} vehicles; the other {hoursUnknown} are not included.
                            </p>
                        )}
                        <SplitBar
                            title="Engine time"
                            tag="Recorded"
                            split={hoursSplit && { first: hoursDrivingPct, second: hoursIdlePct }}
                            driveText={drivingHours !== null ? `${drivingHours.toFixed(1)} h driving` : "— driving"}
                            idleText={idleHours !== null ? `${idleHours.toFixed(1)} h idling` : "— idling"}
                            unavailable="Split unavailable: hours incomplete"
                        />
                        <SplitBar
                            title="Fuel"
                            tag={`Estimated @ ${safeIdleRate.toFixed(1)} L/h`}
                            split={fuelSplit}
                            driveText={drivingFuel !== null ? `${Math.round(drivingFuel).toLocaleString()} L driving` : "— driving"}
                            idleText={idleFuel !== null ? `${Math.round(idleFuel).toLocaleString()} L idle` : "— idle"}
                            unavailable={fuelUnavailable}
                        />
                        <p className="text-[11px] text-slate-400">Fuel split covers vehicles with Wialon telemetry only.</p>
                    </div>
                )}
            </SectionCard>

            <SectionCard
                title="Highest Idle Fuel Wasters"
                className="lg:col-span-5"
                flush
                actions={<span className="font-mono text-[11px] text-slate-400">Est. @ {safeIdleRate.toFixed(1)} L/h</span>}
            >
                {wastersLoading ? (
                    <div className="space-y-2 p-4" role="status" aria-label="Loading idle wasters">
                        {[0, 1, 2, 3, 4].map((i) => (
                            <div key={i} className="h-7 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                        ))}
                    </div>
                ) : wastersError ? (
                    <ErrorState title="Ranking unavailable" message={wastersError} onRetry={onRetryWasters} className="border-0 py-8" />
                ) : wasters.length === 0 ? (
                    <p className="px-4 py-10 text-center text-xs text-slate-400">No estimated idle fuel in this period.</p>
                ) : (
                    <ol className="divide-y divide-slate-100">
                        {wasters.map((v, idx) => (
                            <li key={v.vehicle_identifier}>
                                <button
                                    type="button"
                                    onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                    className="group flex w-full items-center justify-between gap-3 px-4 py-2 text-left text-xs transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
                                    title={`Inspect ${v.vehicle_identifier}`}
                                >
                                    <span className="flex min-w-0 items-center gap-2.5">
                                        <span className="w-4 shrink-0 text-right font-mono text-[11px] text-slate-400">{idx + 1}</span>
                                        <span className="min-w-0">
                                            <span className="block truncate font-mono font-semibold text-slate-900 group-hover:text-brand">{v.vehicle_identifier}</span>
                                            <span className="block truncate text-[11px] text-slate-400">
                                                {v.idle_duration_hours != null ? `${Number(v.idle_duration_hours).toFixed(1)} h idle` : "Idle hours unavailable"}
                                            </span>
                                        </span>
                                    </span>
                                    <span className="flex shrink-0 items-center gap-2 text-right">
                                        <span>
                                            <span className="block font-semibold tabular-nums text-amber-800">{Number(v.estimated_idle_fuel_litres).toFixed(1)} L</span>
                                            <span className="block text-[10px] tabular-nums text-slate-500">
                                                {v.estimated_idle_cost != null ? `AED ${Math.round(v.estimated_idle_cost).toLocaleString()} est.` : "Cost —"}
                                            </span>
                                        </span>
                                        <ArrowUpRight className="h-3.5 w-3.5 text-slate-300 group-hover:text-brand" aria-hidden="true" />
                                    </span>
                                </button>
                            </li>
                        ))}
                    </ol>
                )}
            </SectionCard>
        </div>
    )
}
