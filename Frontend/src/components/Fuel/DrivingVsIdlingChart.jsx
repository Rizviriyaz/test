import SectionCard from "../Common/SectionCard"
import { ErrorState } from "../Common/StateViews"
import { shareSplit } from "./fuelLabels"

const DRIVE_COLOR = "#33758F"
const IDLE_COLOR = "#D97706"
// Idle ranking columns: rank, vehicle, idle hours, estimated fuel, estimated cost
const WASTER_GRID = "grid grid-cols-[1rem_minmax(0,1fr)_auto_auto_auto] items-center gap-x-3 px-4"

function formatCurrency(val) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    })
}

const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v))
const pctText = (v) => (v === null ? "—" : `${Math.round(v)}%`)

// One comparison row: driving and idling side by side (value + share), then the stacked bar. A split the data
// cannot support (a part unknown or negative) is not drawn: no false 100%.
function SplitRow({ label, tag, split, driveValue, idleValue, unavailable }) {
    return (
        <div className="py-2.5 first:pt-0 last:pb-0">
            <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-4 sm:gap-x-6">
                <span className="min-w-0 text-xs font-medium text-slate-700">
                    {label} <span className="font-mono text-[10px] font-normal text-slate-400">{tag}</span>
                </span>
                <span className="w-28 text-right tabular-nums sm:w-32">
                    <span className="text-sm font-semibold text-slate-900">{driveValue}</span>
                    <span className="ml-1 text-[11px] text-slate-500">{split ? pctText(split.first) : ""}</span>
                </span>
                <span className="w-28 text-right tabular-nums sm:w-32">
                    <span className="text-sm font-semibold text-amber-800">{idleValue}</span>
                    <span className="ml-1 text-[11px] text-slate-500">{split ? pctText(split.second) : ""}</span>
                </span>
            </div>
            {split ? (
                <div className="mt-1.5 flex h-2 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
                    <div style={{ width: `${split.first}%`, backgroundColor: DRIVE_COLOR }} className="transition-[width] duration-300 motion-reduce:transition-none" />
                    <div style={{ width: `${split.second}%`, backgroundColor: IDLE_COLOR }} className="transition-[width] duration-300 motion-reduce:transition-none" />
                </div>
            ) : (
                <p className="mt-1 text-[11px] text-slate-400">{unavailable}</p>
            )}
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
        <div className="grid grid-cols-1 gap-3 sm:gap-4 lg:grid-cols-12 items-stretch">
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
                    <div className="space-y-3" role="status" aria-label="Loading driving vs idling">
                        <div className="h-9 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                        <div className="h-9 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                    </div>
                ) : (
                    <div>
                        {hoursUnknown !== null && hoursUnknown > 0 && (
                            <p className="mb-2 text-[11px] text-amber-800" role="status">
                                Hours reported for {hoursKnown ?? 0} of {(hoursKnown ?? 0) + hoursUnknown} vehicles; the other {hoursUnknown} are not included.
                            </p>
                        )}
                        {/* Column legend: the bar colours double as the driving / idling key */}
                        <div className="mb-1 grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 text-[11px] font-medium text-slate-500 sm:gap-x-6">
                            <span />
                            <span className="inline-flex w-28 items-center justify-end gap-1.5 sm:w-32">
                                <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: DRIVE_COLOR }} aria-hidden="true" />
                                Driving
                            </span>
                            <span className="inline-flex w-28 items-center justify-end gap-1.5 sm:w-32">
                                <span className="h-2 w-2 rounded-[2px]" style={{ backgroundColor: IDLE_COLOR }} aria-hidden="true" />
                                Idling
                            </span>
                        </div>
                        <div className="divide-y divide-slate-100">
                            <SplitRow
                                label="Hours"
                                tag="Recorded"
                                split={hoursSplit && { first: hoursDrivingPct, second: hoursIdlePct }}
                                driveValue={drivingHours !== null ? `${drivingHours.toFixed(1)} h` : "—"}
                                idleValue={idleHours !== null ? `${idleHours.toFixed(1)} h` : "—"}
                                unavailable="Split unavailable: hours incomplete"
                            />
                            <SplitRow
                                label="Est. fuel"
                                tag={`@ ${safeIdleRate.toFixed(1)} L/h`}
                                split={fuelSplit}
                                driveValue={drivingFuel !== null ? `${Math.round(drivingFuel).toLocaleString()} L` : "—"}
                                idleValue={idleFuel !== null ? `${Math.round(idleFuel).toLocaleString()} L` : "—"}
                                unavailable={fuelUnavailable}
                            />
                        </div>
                        <p className="mt-2 text-[11px] text-slate-400">Fuel split covers vehicles with Wialon telemetry only.</p>
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
                            <div key={i} className="h-6 animate-pulse rounded bg-slate-50 motion-reduce:animate-none" />
                        ))}
                    </div>
                ) : wastersError ? (
                    <ErrorState title="Ranking unavailable" message={wastersError} onRetry={onRetryWasters} className="border-0 py-8" />
                ) : wasters.length === 0 ? (
                    <p className="px-4 py-10 text-center text-xs text-slate-400">No estimated idle fuel in this period.</p>
                ) : (
                    <>
                        <div className={`${WASTER_GRID} border-b border-slate-100 bg-slate-50 py-1.5 text-[11px] font-medium text-slate-500`} aria-hidden="true">
                            <span className="text-right">#</span>
                            <span>Vehicle</span>
                            <span className="text-right">Idle</span>
                            <span className="text-right">Est. fuel</span>
                            <span className="text-right">Est. cost</span>
                        </div>
                        <ol className="divide-y divide-slate-100">
                            {wasters.map((v, idx) => (
                                <li key={v.vehicle_identifier}>
                                    <button
                                        type="button"
                                        onClick={() => onSelectVehicle?.(v.vehicle_identifier)}
                                        className={`group ${WASTER_GRID} w-full py-2 text-left text-xs transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40`}
                                        title={`Inspect ${v.vehicle_identifier}`}
                                    >
                                        <span className="text-right font-mono text-[11px] text-slate-400">{idx + 1}</span>
                                        <span className="truncate font-mono font-semibold text-slate-900 group-hover:text-brand">{v.vehicle_identifier}</span>
                                        <span className="text-right tabular-nums text-slate-600">
                                            {v.idle_duration_hours != null ? `${Number(v.idle_duration_hours).toFixed(1)} h` : "—"}
                                        </span>
                                        <span className="text-right font-semibold tabular-nums text-amber-800">{Number(v.estimated_idle_fuel_litres).toFixed(1)} L</span>
                                        <span className="text-right tabular-nums text-slate-700">
                                            {v.estimated_idle_cost != null ? `AED ${Math.round(v.estimated_idle_cost).toLocaleString()}` : "—"}
                                        </span>
                                    </button>
                                </li>
                            ))}
                        </ol>
                    </>
                )}
            </SectionCard>
        </div>
    )
}
