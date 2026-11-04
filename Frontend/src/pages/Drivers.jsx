import { useCallback, useEffect, useMemo, useState } from "react"
import { Layers, Users } from "lucide-react"

import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import FreshnessBadge from "../components/Common/FreshnessBadge"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import FilterSelect from "../components/Common/FilterSelect"
import Pagination from "../components/Common/Pagination"
import { EmptyState, ErrorBanner, ErrorState, LoadingState } from "../components/Common/StateViews"
import DriverDetailModal from "../components/Drivers/DriverDetailModal"
import {
    DEFAULT_FILTERS, NOT_AVAILABLE, STATUS_FILTERS, filterDrivers, groupLabel, kpiCounts, vehicleLabel,
} from "../components/Drivers/driverFilters"
import { buildDriverIdleIndex, driverIdleSummary } from "../components/Drivers/driverIdle"
import { getDrivers, getIdleEvents, isAbortError, peekDrivers, peekIdleEvents } from "../api/fleetApi"

// While the server re-reads Wialon (`refreshing` response) the page asks again this often, at most this many times
const RECHECK_MS = 5000
const MAX_RECHECKS = 12
import { formatElapsedDuration, formatUAETime } from "../utils/datetime"

const PAGE_SIZE = 25

const pill = (active) => `shrink-0 rounded-md px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
    active ? "bg-white text-slate-900 shadow-xs" : "text-slate-600 hover:text-slate-900"
}`

function Missing() {
    return <span className="text-slate-400">{NOT_AVAILABLE}</span>
}

// Assigned vehicle cell: plate (or unit name), "Unassigned", or Not available when the bound vehicle has no label
function AssignedVehicle({ assignment: a = {} }) {
    if (a.status === "unassigned") return <span className="text-slate-400">Unassigned</span>
    const vehicle = vehicleLabel(a)
    if (!vehicle) return <Missing />
    return (
        <>
            <p className="truncate font-mono font-semibold text-slate-900">{vehicle}</p>
            {a.plate && a.vehicle_name && a.vehicle_name !== a.plate && (
                <p className="truncate text-[11px] text-slate-500">{a.vehicle_name}</p>
            )}
        </>
    )
}

// Today's Idle Time cell: the attributed total, "+" when some vehicles' idle reports were unread, "—" when unknown
function IdleTimeCell({ summary, loading, error }) {
    if (!summary) {
        return loading
            ? <span className="inline-block h-3 w-12 animate-pulse rounded bg-slate-100 align-middle motion-reduce:animate-none" aria-label="Loading idle time" />
            : <span className="text-slate-400" title={error ? "Idle events could not be loaded" : undefined}>—</span>
    }
    if (summary.status !== "ok") {
        return <span className="text-slate-400" title="Idle events cannot be attributed to this driver by name">—</span>
    }
    return (
        <span className={`tabular-nums ${summary.totalSeconds > 0 ? "font-semibold text-amber-800" : "text-slate-500"}`}
            title={summary.partial ? "At least this long: some Wialon idle events could not be read" : undefined}>
            {formatElapsedDuration(summary.totalSeconds)}{summary.partial ? "+" : ""}
        </span>
    )
}

export default function Drivers() {
    // The last directory and today's idle events (e.g. on returning to the page) are shown at once, with their own
    // read times, while the new responses load
    const [data, setData] = useState(() => peekDrivers()?.data ?? null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [reloadKey, setReloadKey] = useState(0)
    const [filters, setFilters] = useState(DEFAULT_FILTERS)
    const [page, setPage] = useState(1)
    const [selected, setSelected] = useState(null)
    // Today's (UAE) fleet idle events, loaded alongside the directory without blocking it
    const [idleData, setIdleData] = useState(() => peekIdleEvents({ timeframe: "today" })?.data ?? null)
    const [idleLoading, setIdleLoading] = useState(true)
    const [idleError, setIdleError] = useState(null)
    // Bumped (a bounded number of times) while the server answers from its previous result and re-reads Wialon
    const [recheck, setRecheck] = useState({ idle: 0, drivers: 0 })
    const scheduleRecheck = useCallback((name, res) => {
        if (!res?.refreshing) return null
        return setTimeout(() => setRecheck((r) => (r[name] < MAX_RECHECKS ? { ...r, [name]: r[name] + 1 } : r)), RECHECK_MS)
    }, [])

    useEffect(() => {
        const controller = new AbortController()
        let timer = null
        // The same request the Idle Dashboard and vehicle drawer make for today; refresh re-reads the backend's cached
        // fleet report instead of forcing a new one (about one Wialon report per vehicle)
        getIdleEvents({ timeframe: "today", signal: controller.signal })
            .then((res) => { setIdleData(res); setIdleError(null); timer = scheduleRecheck("idle", res) })
            .catch((e) => { if (!isAbortError(e)) setIdleError(e.message) })
            .finally(() => { if (!controller.signal.aborted) setIdleLoading(false) })
        return () => { controller.abort(); clearTimeout(timer) }
    }, [reloadKey, recheck.idle, scheduleRecheck])

    useEffect(() => {
        const controller = new AbortController()
        let timer = null
        getDrivers({ refresh: reloadKey > 0 && recheck.drivers === 0, signal: controller.signal })
            .then((res) => { setData(res); setError(null); timer = scheduleRecheck("drivers", res) })
            .catch((e) => { if (!isAbortError(e)) setError(e.message) })
            .finally(() => { if (!controller.signal.aborted) setLoading(false) })
        return () => { controller.abort(); clearTimeout(timer) }
    }, [reloadKey, recheck.drivers, scheduleRecheck])

    const refresh = useCallback(() => {
        setLoading(true)
        setError(null)
        setIdleLoading(true)
        setRecheck({ idle: 0, drivers: 0 })
        setReloadKey((k) => k + 1)
    }, [])

    const updateFilters = useCallback((patch) => {
        setFilters((prev) => ({ ...prev, ...patch }))
        setPage(1)
    }, [])

    const closeDetails = useCallback(() => setSelected(null), [])

    const drivers = useMemo(() => (Array.isArray(data?.drivers) ? data.drivers : []), [data])
    const groups = useMemo(() => (Array.isArray(data?.groups) ? data.groups : []), [data])
    const counts = useMemo(() => kpiCounts(data), [data])
    const idleIndex = useMemo(() => (idleData ? buildDriverIdleIndex(drivers, idleData) : null), [drivers, idleData])
    const idleOf = useCallback((d) => (idleIndex && !idleError ? driverIdleSummary(d, idleIndex) : null), [idleIndex, idleError])
    const filtered = useMemo(() => {
        const rows = filterDrivers(drivers, filters)
        if (filters.status !== "assigned") return rows
        // Assigned: highest idle time today first; drivers without a known idle time keep their order at the end
        const seconds = new Map(rows.map((d) => {
            const s = idleOf(d)
            return [d, s?.status === "ok" ? s.totalSeconds : -1]
        }))
        return [...rows].sort((a, b) => seconds.get(b) - seconds.get(a))
    }, [drivers, filters, idleOf])

    const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
    const currentPage = Math.min(page, totalPages)
    const start = (currentPage - 1) * PAGE_SIZE
    const pageRows = filtered.slice(start, start + PAGE_SIZE)
    const filtersActive = filters.status !== "all" || Boolean(filters.search) || Boolean(filters.group)
    const kpiLoading = loading && !data

    return (
        <AppShell>
            <PageHeader
                title="Drivers"
                onRefresh={refresh}
                loading={loading}
                lastUpdated={data?.retrieved_at ? formatUAETime(data.retrieved_at) : null}
                lastUpdatedLabel="Data as of"
            >
                <FreshnessBadge freshness={data?.freshness} loading={loading} error={error} hasData={Boolean(data)} />
            </PageHeader>

            <div className="space-y-4">
                {error && data && <ErrorBanner message={`Refresh failed: ${error}`} onRetry={refresh} />}
                {!error && data?.stale && (
                    <ErrorBanner message="Wialon could not be reached; showing the last driver directory read." onRetry={refresh} />
                )}

                <KpiStrip label="Driver summary" className="grid-cols-3">
                    <KpiCard
                        label="Total drivers"
                        loading={kpiLoading}
                        value={data ? counts.total : "—"}
                        onClick={() => updateFilters({ status: "all" })}
                        selected={filters.status === "all"}
                        title="Show all drivers"
                    />
                    <KpiCard
                        label="Assigned drivers"
                        dot="bg-status-moving"
                        loading={kpiLoading}
                        value={data ? counts.assigned : "—"}
                        onClick={() => updateFilters({ status: "assigned" })}
                        selected={filters.status === "assigned"}
                        title="Show drivers bound to a vehicle"
                    />
                    <KpiCard
                        label="Unassigned drivers"
                        dot="bg-slate-400"
                        loading={kpiLoading}
                        value={data ? counts.unassigned : "—"}
                        onClick={() => updateFilters({ status: "unassigned" })}
                        selected={filters.status === "unassigned"}
                        title="Show drivers not bound to any vehicle"
                    />
                </KpiStrip>

                <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                    <div className="flex flex-col gap-3 border-b border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between">
                        <SearchInput
                            id="drivers-search"
                            placeholder="Search driver, Employee ID or vehicle…"
                            value={filters.search}
                            onChange={(search) => updateFilters({ search })}
                            className="w-full sm:max-w-xs"
                        />
                        <div className="flex flex-wrap items-center gap-2">
                            <div role="group" aria-label="Filter by assignment" className="flex flex-wrap items-center gap-1 rounded-lg bg-slate-100 p-1">
                                {STATUS_FILTERS.map((f) => (
                                    <button
                                        key={f.id}
                                        type="button"
                                        onClick={() => updateFilters({ status: f.id })}
                                        aria-pressed={filters.status === f.id}
                                        className={pill(filters.status === f.id)}
                                    >
                                        {f.label}
                                    </button>
                                ))}
                            </div>
                            <FilterSelect
                                icon={Layers}
                                ariaLabel="Filter by vehicle group"
                                value={filters.group}
                                onChange={(group) => updateFilters({ group })}
                                active={Boolean(filters.group)}
                            >
                                <option value="">All groups</option>
                                {groups.map((g) => <option key={g} value={g}>{g}</option>)}
                            </FilterSelect>
                        </div>
                    </div>

                    {loading && !data ? (
                        <LoadingState label="Loading drivers from Wialon…" className="rounded-none border-0" rows={10} columns={5} />
                    ) : error && !data ? (
                        <ErrorState message={error} onRetry={refresh} className="rounded-none border-0" />
                    ) : filtered.length === 0 ? (
                        <EmptyState
                            icon={Users}
                            className="rounded-none border-0"
                            title={drivers.length === 0 ? "No drivers found in Wialon" : "No drivers match your search and filter"}
                            action={filtersActive && (
                                <button
                                    type="button"
                                    onClick={() => updateFilters(DEFAULT_FILTERS)}
                                    className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                >
                                    Clear filter and search
                                </button>
                            )}
                        />
                    ) : (
                        <>
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[720px] text-xs">
                                    <thead>
                                        <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] font-semibold text-slate-700">
                                            <th scope="col" className="px-4 py-2.5">Driver Name</th>
                                            <th scope="col" className="px-3 py-2.5">Employee ID</th>
                                            <th scope="col" className="px-3 py-2.5">Assigned Vehicle</th>
                                            <th scope="col" className="px-3 py-2.5">Group</th>
                                            <th scope="col" className="whitespace-nowrap px-4 py-2.5 text-right">Today's Idle Time</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {pageRows.map((d, i) => {
                                            const open = () => setSelected(d)
                                            return (
                                                <tr
                                                    key={`${start + i}-${d.driver_id ?? ""}-${d.name ?? ""}`}
                                                    onClick={open}
                                                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open() } }}
                                                    tabIndex={0}
                                                    aria-label={`View details for ${d.name || "driver"}`}
                                                    className={`group cursor-pointer transition-colors hover:bg-slate-50/90 focus-visible:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40 ${
                                                        selected === d ? "bg-brand-light/70" : ""
                                                    }`}
                                                >
                                                    <td className="max-w-[280px] px-4 py-2.5">
                                                        <p className="truncate font-semibold text-slate-900 transition-colors group-hover:text-brand">
                                                            {d.name || <Missing />}
                                                        </p>
                                                    </td>
                                                    <td className="whitespace-nowrap px-3 py-2.5 font-mono tabular-nums text-slate-800">
                                                        {d.driver_id || <Missing />}
                                                    </td>
                                                    <td className="max-w-[260px] px-3 py-2.5">
                                                        <AssignedVehicle assignment={d.assignment} />
                                                    </td>
                                                    <td className="max-w-[200px] truncate px-3 py-2.5 text-slate-700">
                                                        {groupLabel(d.assignment) || <span className="text-slate-400">—</span>}
                                                    </td>
                                                    <td className="whitespace-nowrap px-4 py-2.5 text-right">
                                                        <IdleTimeCell summary={idleOf(d)} loading={idleLoading} error={idleError} />
                                                    </td>
                                                </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            </div>
                            <Pagination
                                page={currentPage}
                                totalPages={totalPages}
                                onPageChange={setPage}
                                start={start + 1}
                                end={start + pageRows.length}
                                total={filtered.length}
                                noun={filtered.length === 1 ? "driver" : "drivers"}
                            >
                                <span className="hidden text-[11px] text-slate-400 sm:inline">Click a driver to view full details</span>
                            </Pagination>
                        </>
                    )}
                </section>
            </div>

            {selected && (
                <DriverDetailModal
                    driver={selected}
                    idle={{ loading: idleLoading, error: idleError, summary: idleOf(selected) }}
                    onClose={closeDetails}
                />
            )}
        </AppShell>
    )
}
