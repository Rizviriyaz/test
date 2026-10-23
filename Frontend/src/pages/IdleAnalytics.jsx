import { useEffect, useMemo, useState, useCallback, useRef } from "react"
import {
    Truck,
    MapPin,
    CheckCircle2,
    ArrowUpRight,
    Download,
    Layers,
    X,
    ChevronRight,
    Calendar,
} from "lucide-react"

import { getCachedFleetIdleAnalytics, getFleetIdleAnalytics } from "../api/fleetApi"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import FilterSelect from "../components/Common/FilterSelect"
import Pagination from "../components/Common/Pagination"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import { EmptyState, ErrorBanner, ErrorState, LoadingState } from "../components/Common/StateViews"
import AnalyticsChart from "../components/Analytics/AnalyticsChart"
import IdleMapModal from "../components/Analytics/IdleMapModal"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import { LocationName } from "../utils/reverseGeocode"

import { formatUAEDateTime, formatUAETime } from "../utils/datetime"
import { partialEventsNote } from "../utils/knownTotals"
import { RANGE_PRESETS, resolvePreset, toUaeInputValue, validateRange } from "../utils/dateRange"

// ─── Utilities ───────────────────────────────────────────────

const formatDuration = (seconds) => {
    const s = Math.max(0, Number(seconds || 0))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = Math.floor(s % 60)
    if (h > 0) return `${h}h ${m}m`
    if (m > 0) return `${m}m ${sec}s`
    return `${sec}s`
}

const formatDateTime = (ts) => formatUAEDateTime(ts)

const exportToCSV = (events) => {
    if (!events || events.length === 0) return
    const headers = [
        // Start/end bound the Wialon engine-on period; the duration is the idling within it
        "Vehicle Name", "Vehicle ID", "Group", "Engine-On Period Start", "Engine-On Period End",
        "Idle Duration (min)", "Idle Duration (HH:MM:SS)", "Latitude", "Longitude",
    ]
    const rows = events.map((ev) => {
        const dur = Number(ev.duration || 0)
        const h = Math.floor(dur / 3600).toString().padStart(2, "0")
        const m = Math.floor((dur % 3600) / 60).toString().padStart(2, "0")
        const s = Math.floor(dur % 60).toString().padStart(2, "0")
        return [
            ev.vehicle_name || "",
            ev.vehicle_id || "",
            ev.group_name || "",
            formatDateTime(ev.start_time),
            formatDateTime(ev.end_time),
            ev.duration_minutes || "",
            `${h}:${m}:${s}`,
            ev.location?.latitude || "",
            ev.location?.longitude || "",
        ]
    })
    const csv = [headers, ...rows].map((r) => r.map((v) => `"${v}"`).join(",")).join("\n")
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `idle-analytics-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
}

// ─── Filter Bar ───────────────────────────────────────────────

// Presets the backend resolves itself with identical bounds (and caches by name).
const SERVER_TIMEFRAME_PRESETS = new Set(["today", "yesterday"])

const presetInputs = (id) => {
    const r = resolvePreset(id)
    return { from: toUaeInputValue(r.from), to: toUaeInputValue(r.to) }
}

// ─── Main Page ────────────────────────────────────────────────

export default function IdleAnalytics() {
    // ── Time filters ──
    // `rangePreset` is a RANGE_PRESETS id, or "custom" once either field is edited.
    const [rangePreset, setRangePreset] = useState("today")
    const [rangeInputs, setRangeInputs] = useState(() => presetInputs("today"))
    const [refreshNonce, setRefreshNonce] = useState(0)
    const manualRefreshRef = useRef(false)

    const applyPreset = useCallback((id) => {
        setRangePreset(id)
        setRangeInputs(presetInputs(id))
    }, [])

    const editRangeInput = (field, value) => {
        setRangePreset("custom")
        setRangeInputs((prev) => ({ ...prev, [field]: value }))
    }

    // Presets ending "now" are re-resolved on refresh so the fields and the request stay in step.
    const refresh = useCallback(() => {
        manualRefreshRef.current = true
        if (rangePreset !== "custom") applyPreset(rangePreset)
        setRefreshNonce((n) => n + 1)
    }, [rangePreset, applyPreset])

    // ── Other filters ──
    const [filterGroupId, setFilterGroupId] = useState("")
    const [filterVehicleId, setFilterVehicleId] = useState("")
    const [searchText, setSearchText] = useState("")

    // ── Data ──
    const [analytics, setAnalytics] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")
    const [lastUpdated, setLastUpdated] = useState(null)
    // `analytics` may be the events-free summary while the full response (discrete idle events) is loading
    const [showingSummary, setShowingSummary] = useState(false)
    const [eventsLoading, setEventsLoading] = useState(false)
    const [eventsError, setEventsError] = useState("")

    // ── UI state ──
    const [selectedGroupId, setSelectedGroupId] = useState(null)
    const [drillVehicleId, setDrillVehicleId] = useState(null)
    const [mapEvent, setMapEvent] = useState(null)
    const [drawerVehicle, setDrawerVehicle] = useState(null)
    const [sortBy, setSortBy] = useState("start_time")
    const [sortDir, setSortDir] = useState("desc")
    const [page, setPage] = useState(1)
    const [pageSize, setPageSize] = useState(10)

    // Request sequence tracking to guard against out-of-order race conditions
    const reqSeqRef = useRef(0)

    // ── Load analytics ──
    const rangeError = rangePreset === "custom" ? validateRange(rangeInputs.from, rangeInputs.to).error || "" : ""

    const load = useCallback(async () => {
        const isManualRefresh = manualRefreshRef.current
        manualRefreshRef.current = false
        const currentReq = ++reqSeqRef.current
        setLoading(true)
        setError("")
        const opts = {}
        if (SERVER_TIMEFRAME_PRESETS.has(rangePreset)) {
            opts.timeframe = rangePreset
        } else {
            const range = validateRange(rangeInputs.from, rangeInputs.to)
            if (range.error) {
                setError(range.error)
                setLoading(false)
                return
            }
            opts.from_time = range.from
            opts.to_time = range.to
        }
        if (isManualRefresh) {
            opts.skip_cache = true
            opts.refresh = true
        }
        // Pass server-side group/vehicle filter only when drilling
        if (filterGroupId) opts.group_id = Number(filterGroupId)
        if (filterVehicleId) opts.vehicle_id = Number(filterVehicleId)
        setEventsError("")

        const cachedFull = getCachedFleetIdleAnalytics(opts)
        if (cachedFull) {
            setAnalytics(cachedFull)
            setShowingSummary(false)
            setEventsLoading(false)
            setLastUpdated(new Date())
            setLoading(false)
            return
        }

        // KPIs and per-vehicle/group totals come from a summary request that skips idle-event paging, so they show
        // while the full request (discrete events: trend, hotspots, event list) is still paging Wialon sub-rows.
        setEventsLoading(true)
        let fullApplied = false
        const isCurrent = () => currentReq === reqSeqRef.current
        const summaryReq = getFleetIdleAnalytics({ ...opts, include_events: false }).then((data) => {
            if (!isCurrent() || fullApplied) return
            setAnalytics(data)
            setShowingSummary(true)
            setLastUpdated(new Date())
            setLoading(false)
        })
        const fullReq = getFleetIdleAnalytics(opts).then((data) => {
            if (!isCurrent()) return
            fullApplied = true
            setAnalytics(data)
            setShowingSummary(false)
            setLastUpdated(new Date())
        })
        const [summaryRes, fullRes] = await Promise.allSettled([summaryReq, fullReq])
        if (!isCurrent()) return
        setLoading(false)
        setEventsLoading(false)
        if (fullRes.status === "rejected") {
            console.error(fullRes.reason)
            const message = fullRes.reason?.message || "Failed to load idle analytics. Please try again."
            // With the summary on screen, only the event-based sections are missing: say so instead of hiding the KPIs
            if (summaryRes.status === "rejected") setError(message)
            else setEventsError(`Idle events could not be loaded: ${message}`)
        }
    }, [rangePreset, rangeInputs, filterGroupId, filterVehicleId])

    useEffect(() => {
        // Debounce typed custom ranges so partially edited values don't each trigger a Wialon report.
        const timer = setTimeout(() => { void load() }, rangePreset === "custom" ? 600 : 0)
        return () => clearTimeout(timer)
        // refreshNonce forces a reload when a refresh leaves the range unchanged.
    }, [load, rangePreset, refreshNonce])

    // ── Derived / filtered data ──
    const kpis = analytics?.kpis || {}
    const groups = analytics?.groups || []
    const allEvents = useMemo(() => analytics?.events || [], [analytics])
    const byVehicle = useMemo(() => analytics?.by_vehicle || [], [analytics])
    const byGroup = useMemo(() => analytics?.by_group || [], [analytics])
    const byLocation = useMemo(() => analytics?.by_location || [], [analytics])
    const trend = useMemo(() => analytics?.trend || [], [analytics])
    // Trend, hotspots and the event list come from discrete events; null when they are complete
    const eventsPending = showingSummary && eventsLoading
    const eventsNote = eventsPending ? "Loading idle events…" : partialEventsNote(analytics)

    // An idle event row has no live state: take status / idle start from the vehicle's by_vehicle row,
    // memoized because the drawer resyncs whenever the vehicle object changes
    const drawerVehicleRow = drawerVehicle
        ? byVehicle.find((v) => String(v.vehicle_id) === String(drawerVehicle.vehicle_id))
        : null
    const drawerVehicleWithLive = useMemo(() => {
        if (!drawerVehicle || !drawerVehicleRow) return drawerVehicle
        const { status, idle_start, idle_duration } = drawerVehicleRow
        return { ...drawerVehicle, status, idle_start, idle_duration }
    }, [drawerVehicle, drawerVehicleRow])
    // by_vehicle idle_events null = that vehicle's events were not read completely
    const drawerEventsComplete = drawerVehicleRow ? drawerVehicleRow.idle_events !== null : analytics?.events_complete !== false

    // Filtered vehicle options for the vehicle dropdown
    const vehicleOptions = useMemo(() => {
        if (!byVehicle || byVehicle.length === 0) return []
        return byVehicle.filter((v) => !filterGroupId || String(v.group_id) === String(filterGroupId))
    }, [byVehicle, filterGroupId])

    // Reset page to 1 whenever any filter or sort changes (adjusted during render)
    const pageResetKey = JSON.stringify([drillVehicleId, filterVehicleId, filterGroupId, searchText, sortBy, sortDir, rangePreset, rangeInputs])
    const [lastPageResetKey, setLastPageResetKey] = useState(pageResetKey)
    if (pageResetKey !== lastPageResetKey) {
        setLastPageResetKey(pageResetKey)
        setPage(1)
    }

    // Client-side filters applied to event list
    const filteredEvents = useMemo(() => {
        let evs = allEvents
        if (drillVehicleId) {
            evs = evs.filter((e) => Number(e.vehicle_id) === Number(drillVehicleId))
        }
        if (filterVehicleId) {
            evs = evs.filter((e) => Number(e.vehicle_id) === Number(filterVehicleId))
        }
        if (searchText.trim()) {
            const q = searchText.trim().toLowerCase()
            evs = evs.filter((e) => {
                const vPlate = (e.registration_plate || e.plate || "").toLowerCase()
                const vName = (e.vehicle_name || "").toLowerCase()
                const vId = String(e.vehicle_id || "")
                const gName = (e.group_name || "").toLowerCase()
                return vPlate.includes(q) || vName.includes(q) || vId.includes(q) || gName.includes(q)
            })
        }
        // Deterministic Sort
        evs = [...evs].sort((a, b) => {
            let aVal = a[sortBy] ?? 0
            let bVal = b[sortBy] ?? 0
            if (aVal !== bVal) {
                if (sortDir === "asc") return aVal > bVal ? 1 : -1
                return aVal < bVal ? 1 : -1
            }
            // Deterministic secondary tie-breaker
            return (b.start_time || 0) - (a.start_time || 0) || String(b.id || "").localeCompare(String(a.id || ""))
        })
        return evs
    }, [allEvents, drillVehicleId, filterVehicleId, searchText, sortBy, sortDir])

    // Pagination calculations
    const totalEvents = filteredEvents.length
    const totalPages = Math.max(1, Math.ceil(totalEvents / pageSize))
    const safePage = Math.min(page, totalPages)
    const startIndex = (safePage - 1) * pageSize
    const paginatedEvents = useMemo(() => {
        return filteredEvents.slice(startIndex, startIndex + pageSize)
    }, [filteredEvents, startIndex, pageSize])
    const visibleStart = totalEvents === 0 ? 0 : startIndex + 1
    const visibleEnd = Math.min(startIndex + pageSize, totalEvents)

    // Highest idle individual qualifying event resolution for interactive KPI card
    const highestIdleEvent = useMemo(() => {
        return analytics?.kpis?.longest_idle_event || analytics?.kpis?.highest_idle_event || null
    }, [analytics])

    // Stats for active vehicle search / filter
    const activeSearchStats = useMemo(() => {
        if (!searchText.trim() && !drillVehicleId && !filterVehicleId) return null
        const totalSec = filteredEvents.reduce((acc, ev) => acc + Number(ev.duration || 0), 0)
        const longestSec = filteredEvents.reduce((acc, ev) => Math.max(acc, Number(ev.duration || 0)), 0)
        const avgSec = filteredEvents.length > 0 ? Math.round(totalSec / filteredEvents.length) : 0
        const uniqueVehicles = Array.from(new Set(filteredEvents.map((ev) => ev.vehicle_name).filter(Boolean)))
        return {
            eventCount: filteredEvents.length,
            totalSeconds: totalSec,
            longestSeconds: longestSec,
            averageSeconds: avgSec,
            uniqueVehicles,
        }
    }, [filteredEvents, searchText, drillVehicleId, filterVehicleId])

    // Chart data shapes
    const vehicleChartData = useMemo(
        () =>
            byVehicle.slice(0, 15).map((v) => ({
                label: v.vehicle_name?.split(" ").slice(-1)[0] || v.vehicle_name,
                fullLabel: v.vehicle_name,
                value: v.idle_minutes == null ? null : Math.round(v.idle_minutes),
                events: v.idle_events,
                status: v.status,
                vehicle_id: v.vehicle_id,
            })),
        [byVehicle]
    )

    const groupChartData = useMemo(
        () =>
            byGroup.map((g) => ({
                label: g.group_name,
                value: g.idle_minutes == null ? null : Math.round(g.idle_minutes),
                events: g.idle_events,
                group_id: g.group_id,
            })),
        [byGroup]
    )

    const locationChartData = useMemo(
        () =>
            byLocation.slice(0, 10).map((l, i) => ({
                label: `Hotspot ${i + 1}`,
                fullLabel: l.label,
                value: Math.round(l.idle_minutes),
                events: l.event_count,
                lat: l.latitude,
                lng: l.longitude,
            })),
        [byLocation]
    )

    const trendChartData = useMemo(
        () =>
            trend.map((p) => ({
                label: p.label,
                value: Math.round(p.idle_minutes),
                events: p.event_count,
            })),
        [trend]
    )

    // Selected group card data
    const selectedGroupData = useMemo(() => {
        if (!selectedGroupId) return null
        const gData = byGroup.find((g) => g.group_id === selectedGroupId)
        const gVehicles = byVehicle.filter((v) => v.group_id === selectedGroupId)
        return { ...gData, vehicles: gVehicles }
    }, [selectedGroupId, byGroup, byVehicle])

    const handleSort = (col) => {
        if (sortBy === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"))
        else { setSortBy(col); setSortDir("desc") }
    }

    const SortIcon = ({ col }) => {
        if (sortBy !== col) return <span className="ml-1 text-slate-300" aria-hidden="true">↕</span>
        return <span className="ml-1 text-brand" aria-hidden="true">{sortDir === "asc" ? "↑" : "↓"}</span>
    }

    const resetFilters = () => {
        applyPreset("today")
        setFilterGroupId("")
        setFilterVehicleId("")
        setSearchText("")
        setSelectedGroupId(null)
        setDrillVehicleId(null)
    }

    const activeFilterCount = [
        filterGroupId !== "",
        filterVehicleId !== "",
        searchText.trim() !== "",
    ].filter(Boolean).length

    const segBtn = (active) =>
        `h-full rounded px-2.5 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
            active ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]" : "text-slate-600 hover:text-slate-900"
        }`

    const locationButton = (lat, lng, onClick, maxW) => (
        <div className="flex min-w-0 items-center gap-1">
            <button
                type="button"
                onClick={onClick}
                className={`group inline-flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 -mx-1 text-left text-xs text-slate-700 transition-colors hover:bg-slate-100 hover:text-brand cursor-pointer ${maxW}`}
                title="View on map"
            >
                <MapPin className="h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-brand" aria-hidden="true" />
                <span className="truncate">
                    <LocationName lat={lat} lng={lng} />
                </span>
            </button>
            <a
                href={`https://www.google.com/maps?q=${lat},${lng}`}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 rounded p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700"
                title="Open in Google Maps"
                aria-label="Open in Google Maps"
            >
                <ArrowUpRight className="h-3.5 w-3.5" />
            </a>
        </div>
    )

    return (
        <AppShell
            overlay={
                <>
                    {mapEvent && <IdleMapModal event={mapEvent} onClose={() => setMapEvent(null)} />}
                    {drawerVehicle && (
                        <VehicleDetailDrawer
                            vehicle={drawerVehicleWithLive}
                            // The drawer lists today's events: reuse the page's only when it shows today, else it fetches today
                            idleEvents={rangePreset === "today" && !showingSummary ? analytics?.events : null}
                            idleEventsComplete={drawerEventsComplete}
                            onClose={() => setDrawerVehicle(null)}
                        />
                    )}
                </>
            }
        >
            <PageHeader
                title="Idle Analytics"
                subtitle="Idling by vehicle, group and location"
                onRefresh={refresh}
                loading={loading || eventsLoading}
                lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
            />

            <div className="space-y-4">
                {/* ── Filter toolbar ── */}
                <section aria-label="Filters" className="flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 bg-white p-2">
                    <fieldset className="flex min-w-0 max-w-full flex-col gap-1.5">
                        <legend className="sr-only">Date range</legend>
                        <div role="group" aria-label="Date range presets" className="flex h-8 max-w-full items-center self-start overflow-x-auto rounded-md bg-slate-100 p-0.5">
                            {RANGE_PRESETS.map((p) => (
                                <button
                                    key={p.id}
                                    type="button"
                                    onClick={() => applyPreset(p.id)}
                                    aria-pressed={rangePreset === p.id}
                                    className={`${segBtn(rangePreset === p.id)} whitespace-nowrap`}
                                >
                                    {p.label}
                                </button>
                            ))}
                            {rangePreset === "custom" && (
                                <span className="inline-flex h-full items-center whitespace-nowrap rounded bg-white px-2.5 text-xs font-medium text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]">
                                    Custom
                                </span>
                            )}
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5">
                            {[["from", "From"], ["to", "To"]].map(([field, label]) => (
                                <label
                                    key={field}
                                    className={`flex h-8 items-center gap-1.5 rounded-md border bg-white px-2.5 focus-within:ring-2 ${
                                        rangeError
                                            ? "border-red-300 focus-within:border-red-400 focus-within:ring-red-100"
                                            : "border-slate-200 focus-within:border-brand focus-within:ring-brand/15"
                                    }`}
                                >
                                    <Calendar className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
                                    <span className="w-8 shrink-0 text-xs text-slate-500">{label}</span>
                                    <input
                                        type="datetime-local"
                                        step="1"
                                        value={rangeInputs[field]}
                                        onChange={(e) => editRangeInput(field, e.target.value)}
                                        aria-invalid={Boolean(rangeError)}
                                        title={rangeError || undefined}
                                        className="min-w-0 bg-transparent text-xs font-medium tabular-nums text-slate-800 outline-none cursor-pointer"
                                    />
                                </label>
                            ))}
                        </div>
                    </fieldset>

                    <span className="mx-0.5 mb-1.5 hidden h-5 w-px bg-slate-200 sm:block" aria-hidden="true" />

                    <FilterSelect
                        icon={Layers}
                        value={filterGroupId}
                        onChange={(val) => {
                            setFilterGroupId(val)
                            setFilterVehicleId("")
                        }}
                        ariaLabel="Filter customer group"
                        active={filterGroupId !== ""}
                        selectClassName="max-w-[150px]"
                    >
                        <option value="">All Groups</option>
                        {groups.map((g) => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                        ))}
                    </FilterSelect>

                    <FilterSelect
                        icon={Truck}
                        value={filterVehicleId}
                        onChange={setFilterVehicleId}
                        ariaLabel="Filter vehicle"
                        active={filterVehicleId !== ""}
                        selectClassName="max-w-[150px]"
                    >
                        <option value="">All Vehicles</option>
                        {vehicleOptions.map((v) => (
                            <option key={v.vehicle_id} value={v.vehicle_id}>
                                {v.registration_plate ? `${v.registration_plate} (${v.vehicle_name})` : v.vehicle_name}
                            </option>
                        ))}
                    </FilterSelect>

                    <SearchInput
                        value={searchText}
                        onChange={setSearchText}
                        placeholder="Search vehicle or group…"
                        className="w-full sm:w-52"
                    />

                    {activeFilterCount > 0 && (
                        <button
                            type="button"
                            onClick={() => {
                                setFilterGroupId("")
                                setFilterVehicleId("")
                                setSearchText("")
                            }}
                            className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900 cursor-pointer"
                            title="Clear active filters"
                        >
                            <X className="h-3.5 w-3.5" />
                            Clear {activeFilterCount}
                        </button>
                    )}

                    <button
                        type="button"
                        onClick={() => exportToCSV(filteredEvents)}
                        disabled={filteredEvents.length === 0}
                        className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                        title="Export filtered idle events to CSV"
                        aria-label="Export filtered idle events to CSV"
                    >
                        <Download className="h-3.5 w-3.5 text-slate-500" />
                        Export CSV
                    </button>
                </section>

                {error && analytics && <ErrorBanner message={error} onRetry={refresh} />}
                {!error && eventsError && analytics && <ErrorBanner message={eventsError} onRetry={refresh} />}

                {loading && !analytics ? (
                    <LoadingState label="Analyzing fleet idle history…" />
                ) : !analytics && error ? (
                    <ErrorState message={error} onRetry={refresh} />
                ) : analytics ? (
                    <>
                        {/* ── Summary ── */}
                        <KpiStrip label="Idle summary" className="grid-cols-2 sm:grid-cols-3 xl:grid-cols-6">
                            <KpiCard label="Total vehicles" value={kpis.total_vehicles ?? 0} />
                            <KpiCard
                                label="Currently idling"
                                dot="bg-status-idling"
                                value={kpis.currently_idling_vehicles ?? 0}
                                sub={kpis.idling_fleet_ratio != null ? `${kpis.idling_fleet_ratio}% of fleet` : undefined}
                            />
                            <KpiCard label="Idle events" value={eventsPending ? "…" : kpis.idle_event_count ?? "—"} sub={eventsPending ? "Loading idle events…" : undefined} />
                            <KpiCard
                                label="Total idle time"
                                value={formatDuration(kpis.total_idle_seconds)}
                                sub={kpis.total_idle_partial ? "Partial: some vehicles unreadable" : undefined}
                            />
                            <KpiCard
                                label="Avg idle duration"
                                value={eventsPending ? "…" : kpis.average_idle_seconds == null ? "—" : formatDuration(kpis.average_idle_seconds)}
                            />
                            <KpiCard
                                label="Longest idle"
                                value={eventsPending ? "…" : kpis.longest_idle_seconds == null ? "—" : formatDuration(kpis.longest_idle_seconds)}
                                valueColor={highestIdleEvent ? "text-amber-800" : "text-slate-900"}
                                sub={
                                    highestIdleEvent
                                        ? `${highestIdleEvent.vehicle_name} · view on map`
                                        : eventsPending
                                            ? "Loading idle events…"
                                            : kpis.longest_idle_seconds == null
                                            ? "Idle events incomplete"
                                            : "No idling in period"
                                }
                                onClick={highestIdleEvent ? () => setMapEvent(highestIdleEvent) : undefined}
                                title={
                                    highestIdleEvent
                                        ? `Inspect highest idle event for vehicle: ${highestIdleEvent.vehicle_name}`
                                        : "Highest idle event"
                                }
                            />
                        </KpiStrip>

                        {/* ── Customer groups (select a row to drill down) ── */}
                        {byGroup.length > 0 && (
                            <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
                                <div className="flex items-baseline justify-between gap-2 border-b border-slate-200 px-4 py-3">
                                    <h2 className="type-section-title text-slate-900">Customer groups</h2>
                                    <p className="text-xs text-slate-500">Select a group to list its vehicles</p>
                                </div>
                                <div className="max-h-[320px] overflow-auto">
                                    <table className="w-full min-w-[560px] text-xs">
                                        <thead className="sticky top-0 bg-slate-50">
                                            <tr className="border-b border-slate-200 text-left text-[11px] text-slate-500">
                                                <th scope="col" className="px-4 py-2 font-medium">Group</th>
                                                <th scope="col" className="px-3 py-2 text-right font-medium">Vehicles</th>
                                                <th scope="col" className="px-3 py-2 text-right font-medium">Idling now</th>
                                                <th scope="col" className="px-3 py-2 text-right font-medium">Total idle</th>
                                                <th scope="col" className="px-3 py-2 text-right font-medium">Events</th>
                                                <th scope="col" className="px-4 py-2 text-right font-medium">Avg / event</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {byGroup.map((g) => {
                                                const isSelected = selectedGroupId === g.group_id
                                                const toggle = () => {
                                                    setSelectedGroupId(isSelected ? null : g.group_id)
                                                    setDrillVehicleId(null)
                                                }
                                                return (
                                                    <tr
                                                        key={g.group_id}
                                                        onClick={toggle}
                                                        className={`cursor-pointer transition-colors ${isSelected ? "bg-brand-light" : "hover:bg-slate-50"}`}
                                                    >
                                                        <td className="px-4 py-2">
                                                            <button
                                                                type="button"
                                                                onClick={(e) => { e.stopPropagation(); toggle() }}
                                                                aria-pressed={isSelected}
                                                                className="flex max-w-[260px] items-center gap-1.5 rounded text-left font-medium text-slate-900 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                                            >
                                                                <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform motion-reduce:transition-none ${isSelected ? "rotate-90 text-brand" : ""}`} aria-hidden="true" />
                                                                <span className="truncate">{g.group_name}</span>
                                                            </button>
                                                        </td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{g.vehicle_count}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums">
                                                            {g.currently_idling > 0 ? (
                                                                <span className="inline-flex items-center gap-1.5 font-medium text-amber-800">
                                                                    <span className="h-1.5 w-1.5 rounded-full bg-status-idling" aria-hidden="true" />
                                                                    {g.currently_idling}
                                                                </span>
                                                            ) : <span className="text-slate-400">0</span>}
                                                        </td>
                                                        <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-900">{g.total_idle_seconds == null ? "—" : formatDuration(g.total_idle_seconds)}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{g.idle_events ?? "—"}</td>
                                                        <td className="px-4 py-2 text-right tabular-nums text-slate-700">{g.average_idle_minutes == null ? "—" : `${g.average_idle_minutes}m`}</td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>

                                {/* ── Group drill-down ── */}
                                {selectedGroupData && (
                                    <div className="border-t border-slate-200 bg-slate-50/70">
                                        <div className="flex items-center justify-between gap-2 px-4 py-2">
                                            <h3 className="min-w-0 truncate text-xs font-medium text-slate-700">
                                                {selectedGroupData.group_name} · {selectedGroupData.vehicles.length} vehicles
                                                <span className="font-normal text-slate-500"> · select one to filter events</span>
                                            </h3>
                                            <button
                                                type="button"
                                                onClick={() => setSelectedGroupId(null)}
                                                className="rounded p-1 text-slate-500 transition-colors hover:bg-white hover:text-slate-900 cursor-pointer"
                                                aria-label="Close group drill-down"
                                            >
                                                <X className="h-4 w-4" />
                                            </button>
                                        </div>
                                        <ul className="max-h-[320px] divide-y divide-slate-200/70 overflow-y-auto border-t border-slate-200/70">
                                            {selectedGroupData.vehicles.map((v) => (
                                                <li key={v.vehicle_id}>
                                                    <button
                                                        type="button"
                                                        aria-pressed={drillVehicleId === v.vehicle_id}
                                                        onClick={() => setDrillVehicleId(drillVehicleId === v.vehicle_id ? null : v.vehicle_id)}
                                                        className={`flex w-full flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-2 text-left transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40 ${drillVehicleId === v.vehicle_id ? "bg-white shadow-[inset_2px_0_0_var(--color-brand)]" : "hover:bg-white"}`}
                                                    >
                                                        <span className="min-w-0">
                                                            <span className="block truncate font-mono text-xs font-semibold text-slate-900">{v.registration_plate || v.plate || v.vehicle_name}</span>
                                                            <span className="block truncate text-[11px] text-slate-500">{v.vehicle_name ? `${v.vehicle_name} · ` : ""}ID {v.vehicle_id}</span>
                                                        </span>
                                                        <span className="flex items-center gap-6 text-xs tabular-nums">
                                                            <span className="text-slate-500">Idle <span className="font-semibold text-slate-900">{v.total_idle_seconds == null ? "—" : formatDuration(v.total_idle_seconds)}</span></span>
                                                            <span className="text-slate-500">Events <span className="font-semibold text-slate-900">{v.idle_events ?? "—"}</span></span>
                                                            <FleetStatusBadge status={v.status} size="xs" className="w-16" />
                                                        </span>
                                                    </button>
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </section>
                        )}

                        {/* ── Charts ── */}
                        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                            <AnalyticsChart
                                title="Idle duration trend"
                                subtitle={eventsNote || undefined}
                                data={trendChartData}
                                dataKey="value"
                                nameKey="label"
                                availableTypes={["line", "area", "bar"]}
                                defaultType="area"
                                unit="min"
                                height={300}
                            />
                            <AnalyticsChart
                                title="Idle by customer group"
                                data={groupChartData}
                                dataKey="value"
                                nameKey="label"
                                availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                defaultType="horizontal-bar"
                                unit="min"
                                height={300}
                                onItemClick={(d) => {
                                    if (d?.activePayload?.[0]?.payload?.group_id) {
                                        setSelectedGroupId(d.activePayload[0].payload.group_id)
                                    }
                                }}
                            />
                            <AnalyticsChart
                                title="Top idle vehicles"
                                subtitle="Select a bar to filter events"
                                data={vehicleChartData}
                                dataKey="value"
                                nameKey="label"
                                availableTypes={["horizontal-bar", "bar", "pie", "donut"]}
                                defaultType="horizontal-bar"
                                unit="min"
                                height={300}
                                onItemClick={(d) => {
                                    const payload = d?.activePayload?.[0]?.payload
                                    if (payload?.fullLabel || payload?.label) {
                                        setSearchText(payload.fullLabel || payload.label)
                                    }
                                }}
                            />
                            {locationChartData.length > 0 && (
                                <AnalyticsChart
                                    title="Idle location hotspots"
                                    subtitle={eventsNote ? `Top 10 locations · ${eventsNote}` : "Top 10 locations"}
                                    data={locationChartData}
                                    dataKey="value"
                                    nameKey="label"
                                    availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                    defaultType="bar"
                                    unit="min"
                                    height={300}
                                />
                            )}
                        </section>

                        {/* ── Idle events table ── */}
                        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
                            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-200 px-4 py-3">
                                <h2 className="type-section-title text-slate-900">Idle events</h2>
                                <p className="text-xs tabular-nums text-slate-500">
                                    <span className="font-medium text-slate-800">{filteredEvents.length}</span> events
                                    {eventsNote && <span className="ml-2 text-amber-700">{eventsNote}</span>}
                                </p>
                            </div>
                            <p className="border-b border-slate-200 px-4 py-2 text-[11px] text-slate-500">
                                Each row is one Wialon engine-on period. Idle time is the idling within that period, so it can be shorter than the time between engine on and engine off.
                            </p>

                            {activeSearchStats && (
                                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-brand-light/60 px-4 py-2 text-xs text-slate-600">
                                    <p className="min-w-0">
                                        <span className="font-medium text-slate-900">
                                            {searchText ? `"${searchText}"` : "Filtered vehicle"}
                                        </span>
                                        {" "}· {activeSearchStats.eventCount} event{activeSearchStats.eventCount !== 1 ? "s" : ""} · total <span className="font-semibold tabular-nums text-slate-900">{formatDuration(activeSearchStats.totalSeconds)}</span> · longest <span className="font-semibold tabular-nums text-slate-900">{formatDuration(activeSearchStats.longestSeconds)}</span>
                                    </p>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setSearchText("")
                                            setDrillVehicleId(null)
                                            setFilterVehicleId("")
                                        }}
                                        className="inline-flex items-center gap-1 font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                    >
                                        <X className="h-3.5 w-3.5" />
                                        Clear
                                    </button>
                                </div>
                            )}

                            {eventsPending ? (
                                <LoadingState label="Loading idle events…" />
                            ) : filteredEvents.length === 0 ? (
                                <EmptyState
                                    icon={CheckCircle2}
                                    title={allEvents.length === 0 ? "No idle events in this period" : "No idle events match your filters"}
                                    className="border-none"
                                    action={allEvents.length > 0 && (
                                        <button type="button" onClick={resetFilters} className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer">
                                            Reset filters
                                        </button>
                                    )}
                                />
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full min-w-[900px] text-xs">
                                        <thead>
                                            <tr className="border-b border-slate-200 bg-slate-50">
                                                {[
                                                    ["vehicle_name", "Vehicle", "text-left"],
                                                    ["group_name", "Group", "text-left"],
                                                    ["start_time", "Engine on", "text-left"],
                                                    ["end_time", "Engine off", "text-left"],
                                                    ["duration", "Idle time", "text-right"],
                                                    [null, "Location", "text-left"],
                                                ].map(([col, label, align]) => (
                                                    <th
                                                        key={label}
                                                        scope="col"
                                                        aria-sort={col && sortBy === col ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
                                                        className={`px-4 py-2 text-[11px] font-medium text-slate-500 ${align}`}
                                                    >
                                                        {col ? (
                                                            <button
                                                                type="button"
                                                                onClick={() => handleSort(col)}
                                                                className={`inline-flex items-center hover:text-slate-900 cursor-pointer ${sortBy === col ? "text-slate-900" : ""}`}
                                                            >
                                                                {label}
                                                                <SortIcon col={col} />
                                                            </button>
                                                        ) : label}
                                                    </th>
                                                ))}
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {paginatedEvents.map((ev, idx) => {
                                                const hasLoc = ev.location?.latitude != null && ev.location?.longitude != null
                                                const displayPlate = ev.registration_plate || ev.plate || null
                                                return (
                                                    <tr key={ev.id || idx} className="transition-colors hover:bg-slate-50">
                                                        <td className="px-4 py-2">
                                                            <button
                                                                type="button"
                                                                onClick={() => setDrawerVehicle(ev)}
                                                                className="group block max-w-[220px] rounded text-left cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                                                title="Inspect vehicle details & idle history"
                                                            >
                                                                <span className="block truncate font-mono text-xs font-semibold text-slate-900 group-hover:text-brand">
                                                                    {displayPlate || ev.vehicle_name}
                                                                </span>
                                                                <span className="block truncate text-[11px] text-slate-500">
                                                                    {displayPlate && ev.vehicle_name ? `${ev.vehicle_name} · ` : ""}ID {ev.vehicle_id}
                                                                </span>
                                                            </button>
                                                        </td>
                                                        <td className="max-w-[160px] truncate px-4 py-2 text-slate-600">{ev.group_name || "—"}</td>
                                                        <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-700">{formatDateTime(ev.start_time)}</td>
                                                        <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-700">{formatDateTime(ev.end_time)}</td>
                                                        <td className="whitespace-nowrap px-4 py-2 text-right font-semibold tabular-nums text-amber-800">
                                                            {formatDuration(ev.duration)}
                                                        </td>
                                                        <td className="px-4 py-2">
                                                            {hasLoc
                                                                ? locationButton(ev.location.latitude, ev.location.longitude, () => setMapEvent(ev), "max-w-[240px]")
                                                                : <span className="text-slate-400">No GPS</span>}
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                            {filteredEvents.length > 0 && (
                                <Pagination
                                    page={safePage}
                                    totalPages={totalPages}
                                    onPageChange={setPage}
                                    start={visibleStart}
                                    end={visibleEnd}
                                    total={totalEvents}
                                    noun="idle events"
                                >
                                    <label className="flex items-center gap-1.5 border-l border-slate-200 pl-3">
                                        <span className="text-slate-500">Rows</span>
                                        <select
                                            value={pageSize}
                                            onChange={(e) => {
                                                setPageSize(Number(e.target.value))
                                                setPage(1)
                                            }}
                                            className="h-7 rounded-md border border-slate-200 bg-white px-1.5 text-xs font-medium text-slate-700 outline-none cursor-pointer focus:border-brand"
                                            aria-label="Rows per page"
                                        >
                                            <option value={10}>10</option>
                                            <option value={25}>25</option>
                                            <option value={50}>50</option>
                                            <option value={100}>100</option>
                                        </select>
                                    </label>
                                </Pagination>
                            )}
                        </section>

                        {/* ── Location hotspots ── */}
                        {byLocation.length > 0 && (
                            <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
                                <div className="border-b border-slate-200 px-4 py-3">
                                    <h2 className="type-section-title text-slate-900">Location hotspots</h2>
                                    {eventsNote && <p className="mt-0.5 text-xs text-amber-700">{eventsNote}</p>}
                                </div>
                                <ol className="divide-y divide-slate-100">
                                    {byLocation.map((loc, idx) => (
                                        <li key={loc.location_key} className="flex items-center justify-between gap-4 px-4 py-2 transition-colors hover:bg-slate-50">
                                            <div className="flex min-w-0 items-center gap-3">
                                                <span className="w-5 shrink-0 text-right text-xs font-medium tabular-nums text-slate-400">{idx + 1}</span>
                                                <div className="min-w-0">
                                                    {locationButton(loc.latitude, loc.longitude, () => setMapEvent(loc), "max-w-[320px]")}
                                                    <p className="text-[11px] text-slate-500">
                                                        {loc.vehicle_count} vehicle{loc.vehicle_count !== 1 ? "s" : ""} · {loc.event_count} event{loc.event_count !== 1 ? "s" : ""}
                                                    </p>
                                                </div>
                                            </div>
                                            <p className="shrink-0 text-right text-[13px] font-semibold tabular-nums text-slate-900">
                                                {formatDuration(loc.total_idle_seconds)}
                                                <span className="block text-[11px] font-normal text-slate-500">total idle</span>
                                            </p>
                                        </li>
                                    ))}
                                </ol>
                            </section>
                        )}
                    </>
                ) : null}
            </div>
        </AppShell>
    )
}
