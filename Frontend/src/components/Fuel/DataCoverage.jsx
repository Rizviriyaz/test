import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AlertTriangle, Info, Loader2 } from "lucide-react"
import SectionCard from "../Common/SectionCard"
import SearchInput from "../Common/SearchInput"
import { ErrorState } from "../Common/StateViews"
import { clearClientCache, getFuelExcluded, getVehicleEfficiencyList, getVehicles } from "../../api/fleetApi"
import { fuelVehicleListParams } from "../../utils/fleetRules"
import { buildCoverage, distanceReadiness, filterCoverageRows, overlapUnitIds, partialSummaryRowCount } from "./coverageRows"

const PAGE_SIZE = 500

const fmt = (v, digits) => (v === null ? "—" : v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }))

// Every page of a paginated fuel list (the backend caps page_size at 500)
async function fetchAllPages(fetchPage) {
    const rows = []
    let first = null
    for (let page = 1; ; page += 1) {
        const res = await fetchPage(page)
        if (!first) first = res
        const data = Array.isArray(res?.data) ? res.data : []
        rows.push(...data)
        if (data.length === 0 || rows.length >= (Number(res?.total) || 0)) return { rows, first }
    }
}

function GroupsCell({ groups }) {
    if (!groups.length) return <span className="text-slate-400">—</span>
    return <span className="block truncate" title={groups.join(", ")}>{groups.join(", ")}</span>
}

function VehicleCell({ row }) {
    return (
        <span className="block min-w-0">
            <span className="block truncate font-mono font-semibold text-slate-900" title={row.name}>{row.name}</span>
            {row.plate && row.plate !== row.name && (
                <span className="block truncate text-[11px] text-slate-400">{row.plate}</span>
            )}
        </span>
    )
}

function StatusLabel({ tone, children, title }) {
    const dot = tone === "amber" ? "bg-status-idling" : tone === "rose" ? "bg-status-offline" : "bg-slate-300"
    const text = tone === "amber" ? "text-amber-800" : tone === "rose" ? "text-rose-700" : "text-slate-600"
    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium ${text}`} title={title}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
            {children}
        </span>
    )
}

const TH = "px-3 py-2 font-medium"
const TD = "px-3 py-2"

/**
 * Data Coverage: dashboard (Wialon) fleet vehicles without fuel records in the period, and fleet vehicles with fuel
 * records but no Wialon distance. Period and vehicle filter come from the fuel filters; `refreshKey` reloads after an
 * import or a telematics retry. The distance list is shown only from final Wialon results.
 */
export default function DataCoverage({ queryParams, vehicleFilter, onClearVehicle, refreshKey }) {
    const [state, setState] = useState({ loading: true, error: null, fleet: [], fuel: [], ambiguousNames: [], ambiguousError: false, readiness: "ready", excludedScope: null })
    const [tab, setTab] = useState("not_matched")
    const [search, setSearch] = useState("")
    const requestIdRef = useRef(0)

    const load = useCallback(async () => {
        const requestId = ++requestIdRef.current
        setState((s) => ({ ...s, loading: true, error: null }))
        try {
            const listParams = fuelVehicleListParams(queryParams)
            const [fleetRes, fuelRes, ambiguousRes] = await Promise.allSettled([
                getVehicles(),
                fetchAllPages((page) => getVehicleEfficiencyList({ ...listParams, page, page_size: PAGE_SIZE, sort_by: "vehicle_identifier", sort_order: "asc" })),
                fetchAllPages((page) => getFuelExcluded({ ...queryParams, reason: "ambiguous", page, page_size: PAGE_SIZE })),
            ])
            if (requestId !== requestIdRef.current) return
            if (fleetRes.status === "rejected") throw fleetRes.reason
            if (fuelRes.status === "rejected") throw fuelRes.reason
            setState({
                loading: false,
                error: null,
                fleet: Array.isArray(fleetRes.value?.vehicles) ? fleetRes.value.vehicles : [],
                fuel: fuelRes.value.rows,
                readiness: distanceReadiness(fuelRes.value.first),
                excludedScope: fuelRes.value.first?.excluded_scope || null,
                // Without the ambiguity list, every fleet unit without fuel is still listed; only the flag is missing
                ambiguousNames: ambiguousRes.status === "fulfilled" ? ambiguousRes.value.rows.flatMap((g) => g.candidate_unit_names || []) : [],
                ambiguousError: ambiguousRes.status === "rejected",
            })
        } catch (err) {
            if (requestId !== requestIdRef.current) return
            setState((s) => ({ ...s, loading: false, error: err?.message || "Failed to load data coverage." }))
        }
    }, [queryParams])

    // The page re-renders with a new refreshKey on every telematics retry and after an import; it clears the fuel
    // client cache first, so this reload asks the backend again
    useEffect(() => {
        load()
    }, [load, refreshKey])

    // Manual retry while Wialon reports are still running: the cached placeholder response must not be reused
    const retryDistance = () => {
        clearClientCache("/api/fuel/analytics/efficiency/vehicles")
        load()
    }

    const { notMatched, excludedFromKpi, withoutDistance } = useMemo(
        () => buildCoverage(state.fleet, state.fuel, {
            ambiguousUnitNames: state.ambiguousNames,
            excludedUnitIds: overlapUnitIds(state.excludedScope),
        }),
        [state.fleet, state.fuel, state.ambiguousNames, state.excludedScope]
    )
    const partialRows = partialSummaryRowCount(state.excludedScope)

    // A vehicle filter narrows the fuel side to one vehicle; the fleet comparison is then not made at all, rather than
    // listing every other fleet unit as unmatched
    const notMatchedApplies = !vehicleFilter
    // Distance is classified only from final Wialon results: never while reports run, never when they failed
    const distanceFinal = state.readiness === "ready" || state.readiness === "partial"
    const ready = !state.loading && !state.error
    const rows = tab === "not_matched"
        ? (notMatchedApplies ? notMatched : [])
        : (distanceFinal ? withoutDistance : [])
    const shown = filterCoverageRows(rows, search)

    const tabs = [
        { key: "not_matched", label: "Not matched to Excel", count: ready && notMatchedApplies ? notMatched.length : null },
        { key: "no_distance", label: "Matched, no distance", count: ready && distanceFinal ? withoutDistance.length : null, pending: ready && state.readiness === "pending" },
    ]

    const notice = (tone, children, title) => (
        <p
            className={`flex items-start gap-2 border-b border-slate-100 px-4 py-2 text-[11px] ${tone === "warn" ? "bg-amber-50/60 text-amber-900" : "bg-slate-50/60 text-slate-600"}`}
            role="status"
            title={title}
        >
            {tone === "warn"
                ? <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
                : <Info className="mt-px h-3.5 w-3.5 shrink-0 text-slate-400" />}
            <span className="min-w-0">{children}</span>
        </p>
    )

    return (
        <SectionCard
            title="Data Coverage"
            flush
            actions={ready && (
                <span className="text-[11px] tabular-nums text-slate-500">{state.fleet.length} fleet vehicles</span>
            )}
        >
            <div className="flex flex-col gap-2 border-b border-slate-100 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                <div role="tablist" aria-label="Data coverage lists" className="inline-flex h-8 self-start rounded-md bg-slate-100 p-0.5 text-xs font-medium">
                    {tabs.map((t) => (
                        <button
                            key={t.key}
                            type="button"
                            role="tab"
                            aria-selected={tab === t.key}
                            onClick={() => setTab(t.key)}
                            className={`inline-flex items-center gap-1.5 rounded px-2.5 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                tab === t.key ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]" : "text-slate-600 hover:text-slate-900"
                            }`}
                        >
                            {t.label}
                            {t.count !== null && (
                                <span className={`rounded-full px-1.5 text-[10px] tabular-nums ${tab === t.key ? "bg-slate-100 text-slate-700" : "bg-white/70 text-slate-500"}`}>
                                    {t.count}
                                </span>
                            )}
                            {t.pending && <Loader2 className="h-3 w-3 animate-spin text-slate-400 motion-reduce:animate-none" aria-label="Waiting for Wialon" />}
                        </button>
                    ))}
                </div>
                <SearchInput value={search} onChange={setSearch} placeholder="Search vehicle or group…" className="w-full sm:w-56" />
            </div>

            {state.loading ? (
                <div role="status" className="py-10 text-center text-xs text-slate-500">
                    <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                    Comparing the fleet with fuel records…
                </div>
            ) : state.error ? (
                <ErrorState title="Data coverage unavailable" message={state.error} onRetry={load} className="border-0 py-8" />
            ) : tab === "not_matched" && !notMatchedApplies ? (
                <div className="px-4 py-8 text-center text-xs text-slate-500">
                    <Info className="mx-auto mb-2 h-4 w-4 text-slate-400" />
                    <p>
                        Filtered to <span className="font-mono font-semibold text-slate-800">{vehicleFilter}</span>. This list compares the
                        whole fleet with the month&apos;s fuel records, so it is not shown for a single vehicle.
                    </p>
                    {onClearVehicle && (
                        <button
                            type="button"
                            onClick={onClearVehicle}
                            className="mt-3 inline-flex h-8 items-center rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                        >
                            Show all vehicles
                        </button>
                    )}
                </div>
            ) : tab === "no_distance" && state.readiness === "pending" ? (
                <div role="status" className="px-4 py-10 text-center text-xs text-slate-500">
                    <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-slate-400 motion-reduce:animate-none" />
                    <p>Wialon distance reports are still running. Vehicles are listed once they finish.</p>
                    <button type="button" onClick={retryDistance} className="mt-2 font-medium text-brand underline-offset-2 hover:underline cursor-pointer">
                        Check again
                    </button>
                </div>
            ) : tab === "no_distance" && state.readiness === "unavailable" ? (
                <ErrorState
                    title="Wialon distance unavailable"
                    message="Wialon reports failed or timed out, so vehicles without distance cannot be identified for this period."
                    onRetry={retryDistance}
                    className="border-0 py-8"
                />
            ) : (
                <>
                    {tab === "not_matched" && state.ambiguousError && notice("warn",
                        "Ambiguous statement identifiers could not be checked; some vehicles listed here may have unattributed fuel records."
                    )}
                    {tab === "not_matched" && partialRows > 0 && notice("warn",
                        `${partialRows} monthly-summary row(s) are excluded because the period covers only part of their month. Their vehicles cannot be identified, so some may be listed here.`
                    )}
                    {tab === "not_matched" && excludedFromKpi.length > 0 && notice("info",
                        `${excludedFromKpi.length} fleet vehicle(s) have Excel records that are excluded from the KPIs (monthly summary and daily rows overlap); they are not listed as unmatched.`,
                        excludedFromKpi.map((r) => r.name).join("\n")
                    )}
                    {tab === "no_distance" && state.readiness === "partial" && notice("warn",
                        "Some Wialon values were unreadable for this period; vehicles listed here have no readable distance."
                    )}

                    {shown.length === 0 ? (
                        <p className="px-4 py-10 text-center text-xs text-slate-500">
                            {search.trim()
                                ? <>No vehicles match &ldquo;{search.trim()}&rdquo;.</>
                                : tab === "not_matched"
                                    ? "Every fleet vehicle has a matching fuel record in this period."
                                    : "Every matched vehicle has Wialon distance for this period."}
                        </p>
                    ) : (
                        <div className="max-h-[26rem] overflow-auto">
                            <table className="w-full min-w-[30rem] table-fixed text-left text-xs">
                                <thead className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 text-[11px] text-slate-500">
                                    {tab === "not_matched" ? (
                                        <tr>
                                            <th scope="col" className={`${TH} w-[40%]`}>Vehicle</th>
                                            <th scope="col" className={`${TH} w-[32%]`}>Group</th>
                                            <th scope="col" className={TH}>Status</th>
                                        </tr>
                                    ) : (
                                        <tr>
                                            <th scope="col" className={`${TH} w-[28%]`}>Vehicle</th>
                                            <th scope="col" className={`${TH} w-[20%]`}>Group</th>
                                            <th scope="col" className={`${TH} text-right`}>Fuel</th>
                                            <th scope="col" className={`${TH} text-right`}>Cost</th>
                                            <th scope="col" className={`${TH} text-right`}>Distance</th>
                                            <th scope="col" className={`${TH} w-[22%]`}>Status</th>
                                        </tr>
                                    )}
                                </thead>
                                <tbody className="divide-y divide-slate-100">
                                    {tab === "not_matched"
                                        ? shown.map((r) => (
                                            <tr key={r.unitId} className="hover:bg-slate-50/70">
                                                <td className={TD}><VehicleCell row={r} /></td>
                                                <td className={`${TD} text-slate-600`}><GroupsCell groups={r.groups} /></td>
                                                <td className={TD}>
                                                    {r.ambiguous ? (
                                                        <StatusLabel tone="rose" title="A statement identifier names this unit among several candidates, so its fuel is not attributed">
                                                            Ambiguous Excel match
                                                        </StatusLabel>
                                                    ) : (
                                                        <StatusLabel tone="slate">No matching Excel record</StatusLabel>
                                                    )}
                                                </td>
                                            </tr>
                                        ))
                                        : shown.map((r) => (
                                            <tr key={r.unitId} className="hover:bg-slate-50/70">
                                                <td className={TD}>
                                                    <VehicleCell row={r} />
                                                    {r.fuelIdentifier && r.fuelIdentifier !== r.name && r.fuelIdentifier !== r.plate && (
                                                        <span className="block truncate text-[11px] text-slate-400" title="Statement identifier">Excel: {r.fuelIdentifier}</span>
                                                    )}
                                                </td>
                                                <td className={`${TD} text-slate-600`}><GroupsCell groups={r.groups} /></td>
                                                <td className={`${TD} text-right tabular-nums text-slate-900`}>{r.litres === null ? "—" : `${fmt(r.litres, 1)} L`}</td>
                                                <td className={`${TD} text-right tabular-nums text-slate-900`}>{r.cost === null ? "—" : `AED ${fmt(r.cost, 0)}`}</td>
                                                <td className={`${TD} text-right text-slate-400`}>Unavailable</td>
                                                <td className={TD}>
                                                    <StatusLabel tone="amber" title={r.note || undefined}>Distance unavailable</StatusLabel>
                                                </td>
                                            </tr>
                                        ))}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {shown.length > 0 && (
                        <p className="border-t border-slate-100 px-4 py-2 text-[11px] tabular-nums text-slate-500">
                            {search.trim() ? `${shown.length} of ${rows.length}` : rows.length} {rows.length === 1 ? "vehicle" : "vehicles"}
                        </p>
                    )}
                </>
            )}
        </SectionCard>
    )
}
