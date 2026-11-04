import { Suspense, useEffect, useMemo, useState, useCallback, useRef } from "react"
import { AnimatePresence, motion } from "framer-motion"
import {
    Truck,
    MapPin,
    ArrowUpRight,
    Download,
    Layers,
    X,
    ChevronRight,
    RefreshCw,
    SlidersHorizontal,
    ChevronDown,
} from "lucide-react"

import { getCachedIdleEvents, getIdleEvents, peekIdleEvents } from "../api/fleetApi"
import { SkeletonCards, SkeletonChart, SkeletonTableRows } from "../components/Common/Skeleton"
import AppShell from "../components/Common/AppShell"
import FreshnessBadge from "../components/Common/FreshnessBadge"
import { MobileNavButton } from "../components/Layout/Sidebar"
import SectionCard from "../components/Common/SectionCard"
import SearchInput from "../components/Common/SearchInput"
import FilterSelect from "../components/Common/FilterSelect"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import { ErrorBanner, ErrorState } from "../components/Common/StateViews"
import AnalyticsChart from "../components/Analytics/AnalyticsChart"
import IdleMapModal from "../components/Analytics/IdleMapModal"
import VehicleIdleEventsModal from "../components/Analytics/VehicleIdleEventsModal"
import { LazyVehicleDetailDrawer as VehicleDetailDrawer } from "../components/Vehicles/lazyVehicleDetailDrawer"
import { LocationName } from "../utils/reverseGeocode"

import { formatUAEDateTime, formatUAETime } from "../utils/datetime"
import { partialEventsNote } from "../utils/knownTotals"
import { groupIdsOf } from "../utils/fleetRules"
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

// Presets the backend resolves itself with identical bounds (and caches by name).
const SERVER_TIMEFRAME_PRESETS = new Set(["today", "yesterday"])

// Idle Analytics offers the short-range presets only (no Last Month, no custom From/To)
const IDLE_RANGE_PRESETS = RANGE_PRESETS.filter((p) => p.id !== "last_month")

const newestFirst = (a, b) =>
    (b.start_time || 0) - (a.start_time || 0) || String(b.id || "").localeCompare(String(a.id || ""))

// Key matching a vehicle row to its idle events: the Wialon unit id, or the vehicle name when the id is missing
const vehicleEventsKey = (item) => {
    if (item?.vehicle_id != null && item.vehicle_id !== "") return `id:${item.vehicle_id}`
    return item?.vehicle_name ? `name:${item.vehicle_name}` : null
}

const presetInputs = (id) => {
    const r = resolvePreset(id)
    return { from: toUaeInputValue(r.from), to: toUaeInputValue(r.to) }
}

// ─── Main Page ────────────────────────────────────────────────

// While the server re-reads Wialon (`refreshing` response), the page asks again this often, at most this many times
const REVALIDATE_CHECK_MS = 5000
const MAX_REVALIDATE_CHECKS = 12

// First load: the page's KPI strip, vehicle table and charts as placeholders of their final size
function IdleAnalyticsSkeleton() {
    return (
        <div className="space-y-4">
            <SkeletonCards count={6} className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6" label="Loading idle summary" />
            <div className="rounded-lg border border-slate-200 bg-white">
                <div className="border-b border-slate-100 px-4 py-3"><span className="block h-4 w-40 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" /></div>
                <SkeletonTableRows rows={8} columns={6} label="Loading idle events by vehicle" />
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {[0, 1].map((i) => (
                    <div key={i} className="rounded-lg border border-slate-200 bg-white p-4">
                        <span className="mb-3 block h-4 w-32 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                        <SkeletonChart height={220} label="Loading idle chart" />
                    </div>
                ))}
            </div>
        </div>
    )
}

export default function IdleAnalytics() {
    // ── Time filters ──
    const [rangePreset, setRangePreset] = useState("today")
    const [rangeInputs, setRangeInputs] = useState(() => presetInputs("today"))
    const [refreshNonce, setRefreshNonce] = useState(0)
    const manualRefreshRef = useRef(false)

    const applyPreset = useCallback((id) => {
        setRangePreset(id)
        setRangeInputs(presetInputs(id))
    }, [])

    const refresh = useCallback(() => {
        manualRefreshRef.current = true
        if (rangePreset !== "custom") applyPreset(rangePreset)
        setRefreshNonce((n) => n + 1)
    }, [rangePreset, applyPreset])

    // ── Other filters ──
    const [filterGroupId, setFilterGroupId] = useState("")
    const [filterVehicleId, setFilterVehicleId] = useState("")
    const [searchText, setSearchText] = useState("")
    // The filter dropdown is hidden by default; it closes on a second button click, an outside click or Escape
    const [filtersOpen, setFiltersOpen] = useState(false)
    const filterMenuRef = useRef(null)

    useEffect(() => {
        if (!filtersOpen) return
        const handleClickOutside = (event) => {
            if (filterMenuRef.current && !filterMenuRef.current.contains(event.target)) setFiltersOpen(false)
        }
        const handleKeyDown = (event) => {
            if (event.key === "Escape") setFiltersOpen(false)
        }
        document.addEventListener("mousedown", handleClickOutside)
        document.addEventListener("touchstart", handleClickOutside)
        document.addEventListener("keydown", handleKeyDown)
        return () => {
            document.removeEventListener("mousedown", handleClickOutside)
            document.removeEventListener("touchstart", handleClickOutside)
            document.removeEventListener("keydown", handleKeyDown)
        }
    }, [filtersOpen])

    // ── Data ──
    const [analytics, setAnalytics] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")
    const [lastUpdated, setLastUpdated] = useState(null)
    const [showingSummary, setShowingSummary] = useState(false)
    const [eventsLoading, setEventsLoading] = useState(false)
    const [eventsError, setEventsError] = useState("")

    // ── UI state ──
    const [selectedGroupId, setSelectedGroupId] = useState(null)
    const [eventsVehicle, setEventsVehicle] = useState(null)
    const [mapEvent, setMapEvent] = useState(null)
    const [drawerVehicle, setDrawerVehicle] = useState(null)

    const reqSeqRef = useRef(0)
    const revalidateTimerRef = useRef(null)
    const revalidateCountRef = useRef(0)
    const [revalidateNonce, setRevalidateNonce] = useState(0)
    useEffect(() => () => clearTimeout(revalidateTimerRef.current), [])

    // ── Load analytics ──
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
        if (filterGroupId) opts.group_id = Number(filterGroupId)
        if (filterVehicleId) opts.vehicle_id = Number(filterVehicleId)
        setEventsError("")

        clearTimeout(revalidateTimerRef.current)
        const cachedFull = getCachedIdleEvents(opts)
        if (cachedFull && !cachedFull.refreshing) {
            setAnalytics(cachedFull)
            setShowingSummary(false)
            setEventsLoading(false)
            setLastUpdated(new Date())
            setLoading(false)
            return
        }
        // The last response for these filters (e.g. on returning to the page) is shown at once with its own read
        // time while the new one loads, instead of a loading state
        const last = isManualRefresh ? null : peekIdleEvents(opts)
        if (last) {
            setAnalytics(last.data)
            setShowingSummary(false)
            setLastUpdated(new Date(last.fetchedAt))
        }

        // One request: every idle value (events, totals, currently idling) comes from the Wialon Idle Report-Unit
        // template, which has no lighter summary form
        setEventsLoading(!last)
        const isCurrent = () => currentReq === reqSeqRef.current
        try {
            const data = await getIdleEvents(opts)
            if (!isCurrent()) return
            setAnalytics(data)
            setShowingSummary(false)
            setLastUpdated(new Date())
            // The server answered from its previous result while it re-reads Wialon: ask again shortly (bounded),
            // keeping this result on screen
            if (data?.refreshing && revalidateCountRef.current < MAX_REVALIDATE_CHECKS) {
                revalidateCountRef.current += 1
                revalidateTimerRef.current = setTimeout(() => setRevalidateNonce((n) => n + 1), REVALIDATE_CHECK_MS)
            } else {
                revalidateCountRef.current = 0
            }
        } catch (err) {
            if (!isCurrent()) return
            console.error(err)
            setError(err?.message || "Failed to load idle analytics. Please try again.")
        } finally {
            if (isCurrent()) {
                setLoading(false)
                setEventsLoading(false)
            }
        }
    }, [rangePreset, rangeInputs, filterGroupId, filterVehicleId])

    useEffect(() => {
        const timer = setTimeout(() => { void load() }, rangePreset === "custom" ? 600 : 0)
        return () => clearTimeout(timer)
    }, [load, rangePreset, refreshNonce, revalidateNonce])

    // ── Derived / filtered data ──
    const kpis = analytics?.kpis || {}
    const groups = analytics?.groups || []
    const allEvents = useMemo(() => analytics?.events || [], [analytics])
    const byVehicle = useMemo(() => analytics?.by_vehicle || [], [analytics])
    const byGroup = useMemo(() => analytics?.by_group || [], [analytics])
    const byLocation = useMemo(() => analytics?.by_location || [], [analytics])
    const trend = useMemo(() => analytics?.trend || [], [analytics])
    const eventsPending = showingSummary && eventsLoading
    const eventsNote = eventsPending ? "Loading idle events…" : partialEventsNote(analytics)

    const drawerVehicleRow = drawerVehicle
        ? byVehicle.find((v) => String(v.vehicle_id) === String(drawerVehicle.vehicle_id))
        : null
    const drawerVehicleWithLive = useMemo(() => {
        if (!drawerVehicle || !drawerVehicleRow) return drawerVehicle
        const { status, idle_start, idle_duration, idle_duration_status } = drawerVehicleRow
        return { ...drawerVehicle, status, idle_start, idle_duration, idle_duration_status }
    }, [drawerVehicle, drawerVehicleRow])
    const drawerEventsComplete = drawerVehicleRow ? drawerVehicleRow.idle_events !== null : analytics?.events_complete !== false

    const vehicleOptions = useMemo(() => {
        if (!byVehicle || byVehicle.length === 0) return []
        // Every group the vehicle belongs to, not only its primary group (as the backend group filter does)
        return byVehicle.filter((v) => !filterGroupId || groupIdsOf(v).includes(String(filterGroupId)))
    }, [byVehicle, filterGroupId])

    // Events filtered for the CSV export (vehicle filter and search), newest first
    const filteredEvents = useMemo(() => {
        let evs = allEvents
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
        return [...evs].sort(newestFirst)
    }, [allEvents, filterVehicleId, searchText])

    // Per-vehicle idle events from the loaded report, indexed once per load so opening a vehicle is a lookup,
    // not a new request. Keyed by vehicle id; events without one are keyed by vehicle name.
    const eventsByVehicle = useMemo(() => {
        const index = new Map()
        for (const ev of allEvents) {
            const key = vehicleEventsKey(ev)
            if (key == null) continue
            if (!index.has(key)) index.set(key, [])
            index.get(key).push(ev)
        }
        for (const list of index.values()) list.sort(newestFirst)
        return index
    }, [allEvents])

    const eventsVehicleKey = eventsVehicle ? vehicleEventsKey(eventsVehicle) : null
    const eventsVehicleEvents = eventsVehicleKey == null ? [] : eventsByVehicle.get(eventsVehicleKey) || []
    // The vehicle's row in the current load (a refresh replaces it); a null idle_events count means Wialon could not
    // report this vehicle's events
    const eventsVehicleRow = eventsVehicle
        ? byVehicle.find((v) => vehicleEventsKey(v) === eventsVehicleKey) || eventsVehicle
        : null
    const eventsVehicleUnavailable = Boolean(eventsVehicleRow) && eventsVehicleRow.idle_events === null

    const highestIdleEvent = useMemo(() => {
        return analytics?.kpis?.longest_idle_event || analytics?.kpis?.highest_idle_event || null
    }, [analytics])

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

    const selectedGroupData = useMemo(() => {
        if (selectedGroupId == null) return null
        const gData = byGroup.find((g) => g.group_id === selectedGroupId)
        // The group's members (group totals are group-membership totals, so a multi-group vehicle appears in each)
        const gVehicles = byVehicle.filter((v) => groupIdsOf(v).includes(String(selectedGroupId)))
        return { ...gData, vehicles: gVehicles }
    }, [selectedGroupId, byGroup, byVehicle])

    const activeFilterCount = [
        filterGroupId !== "",
        filterVehicleId !== "",
        searchText.trim() !== "",
    ].filter(Boolean).length
    const activeRangeLabel = IDLE_RANGE_PRESETS.find((p) => p.id === rangePreset)?.label || ""

    const locationButton = (lat, lng, onClick, maxW) => (
        <div className="flex min-w-0 items-center gap-1.5">
            <button
                type="button"
                onClick={onClick}
                className={`group inline-flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 -mx-1 text-left text-xs text-slate-700 transition-colors hover:bg-slate-100 hover:text-brand cursor-pointer ${maxW}`}
                title="View on map"
            >
                <MapPin className="h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-brand transition-colors" aria-hidden="true" />
                <span className="truncate font-medium">
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
            contentClassName="pb-24 sm:pb-28"
            overlay={
                <>
                    {eventsVehicle && (
                        <VehicleIdleEventsModal
                            vehicle={eventsVehicleRow}
                            groupName={selectedGroupData?.group_name || eventsVehicleRow?.group_name}
                            events={eventsVehicleEvents}
                            loading={loading || eventsLoading}
                            error={error}
                            unavailable={eventsVehicleUnavailable}
                            note={eventsNote}
                            onRetry={refresh}
                            onClose={() => setEventsVehicle(null)}
                            renderLocation={(ev) => locationButton(ev.location.latitude, ev.location.longitude, () => setMapEvent(ev), "max-w-[320px]")}
                            escapeDisabled={Boolean(mapEvent)}
                        />
                    )}
                    {mapEvent && <IdleMapModal event={mapEvent} onClose={() => setMapEvent(null)} />}
                    {drawerVehicle && (
                        <Suspense fallback={null}>
                            <VehicleDetailDrawer
                                vehicle={drawerVehicleWithLive}
                                idleEvents={rangePreset === "today" && !showingSummary ? analytics?.events : null}
                                idleEventsComplete={drawerEventsComplete}
                                onClose={() => setDrawerVehicle(null)}
                            />
                        </Suspense>
                    )}
                </>
            }
        >
            <div className="space-y-4 pb-6">
                {/* ─── 01 HEADER STRIP & FILTER TOOLBAR ──────────────────── */}
                {/* Compact title-less header: page status and actions right; every filter and the CSV export live in the Filters dropdown */}
                <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200/80 pb-2 sm:pb-2.5">
                    <div className="flex min-w-0 items-center gap-2">
                        <MobileNavButton />
                    </div>

                    <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                        <FreshnessBadge freshness={analytics?.freshness} loading={loading} error={error || null} hasData={Boolean(analytics)} />
                        {lastUpdated && (
                            <span className="hidden h-8 items-center gap-1.5 px-1 text-xs tabular-nums text-slate-500 sm:inline-flex" title="Updated (UAE time)">
                                <span className="h-1.5 w-1.5 rounded-full bg-status-moving" aria-hidden="true" />
                                Updated {formatUAETime(lastUpdated)}
                            </span>
                        )}

                        <button
                            type="button"
                            onClick={refresh}
                            disabled={loading || eventsLoading}
                            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200/80 bg-white px-2.5 text-xs font-medium text-slate-700 shadow-xs transition-colors hover:border-slate-300 hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30 disabled:cursor-default disabled:opacity-60"
                            title="Refresh idle analytics data"
                        >
                            <RefreshCw className={`h-3.5 w-3.5 text-slate-500 ${loading || eventsLoading ? "animate-spin motion-reduce:animate-none" : ""}`} />
                            <span>{loading || eventsLoading ? "Updating…" : "Refresh"}</span>
                        </button>

                        <div ref={filterMenuRef} className="relative">
                            <button
                                type="button"
                                onClick={() => setFiltersOpen((o) => !o)}
                                aria-expanded={filtersOpen}
                                aria-controls="idle-filter-panel"
                                aria-haspopup="true"
                                className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-semibold transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30 ${
                                    filtersOpen || activeFilterCount > 0
                                        ? "border-brand/40 bg-brand-light text-brand"
                                        : "border-slate-200/80 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                                }`}
                            >
                                <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                                <span>Filters</span>
                                {activeRangeLabel && (
                                    <span className="hidden font-medium text-slate-500 sm:inline">· {activeRangeLabel}</span>
                                )}
                                {activeFilterCount > 0 && (
                                    <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-brand px-1 text-[10px] font-bold tabular-nums text-white">
                                        {activeFilterCount}
                                    </span>
                                )}
                                <ChevronDown
                                    className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${filtersOpen ? "rotate-180" : ""}`}
                                    aria-hidden="true"
                                />
                            </button>

                            {/* Dropdown filter panel: date range, entity filters and CSV export */}
                            <AnimatePresence>
                                {filtersOpen && (
                                    <motion.section
                                        id="idle-filter-panel"
                                        aria-label="Filter Controls"
                                        initial={{ opacity: 0, y: -6, scale: 0.98 }}
                                        animate={{ opacity: 1, y: 0, scale: 1 }}
                                        exit={{ opacity: 0, y: -6, scale: 0.98 }}
                                        transition={{ duration: 0.16, ease: "easeOut" }}
                                        style={{ transformOrigin: "top right" }}
                                        className="absolute right-0 top-full z-30 mt-2 flex w-[min(22rem,calc(100vw-2rem))] flex-col gap-3 rounded-xl border border-slate-200/70 bg-white p-3 shadow-[0_12px_32px_-8px_rgba(15,23,42,0.18),0_2px_8px_-2px_rgba(15,23,42,0.06)]"
                                    >
                                        <fieldset className="min-w-0">
                                            <legend className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Date range</legend>
                                            <div
                                                role="group"
                                                aria-label="Date range presets"
                                                className="flex h-8 w-full items-center rounded-lg border border-slate-200/50 bg-slate-100 p-0.5"
                                            >
                                                {IDLE_RANGE_PRESETS.map((p) => {
                                                    const active = rangePreset === p.id
                                                    return (
                                                        <button
                                                            key={p.id}
                                                            type="button"
                                                            onClick={() => applyPreset(p.id)}
                                                            aria-pressed={active}
                                                            className={`h-full flex-1 rounded-md px-2.5 text-xs font-medium transition-all duration-150 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 whitespace-nowrap ${
                                                                active
                                                                    ? "bg-white text-brand font-semibold shadow-xs"
                                                                    : "text-slate-600 hover:text-slate-900 hover:bg-white/50"
                                                            }`}
                                                        >
                                                            {p.label}
                                                        </button>
                                                    )
                                                })}
                                            </div>
                                        </fieldset>

                                        <div className="flex flex-col gap-2">
                                            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Filters</span>
                                            <FilterSelect
                                                icon={Layers}
                                                label="Group"
                                                value={filterGroupId}
                                                onChange={(val) => {
                                                    setFilterGroupId(val)
                                                    setFilterVehicleId("")
                                                }}
                                                ariaLabel="Filter customer group"
                                                active={filterGroupId !== ""}
                                                className="w-full"
                                                selectClassName="flex-1"
                                            >
                                                <option value="">All Groups</option>
                                                {groups.map((g) => (
                                                    <option key={g.id} value={g.id}>{g.name}</option>
                                                ))}
                                            </FilterSelect>

                                            {/* Native select: the browser shows the long option list as a bounded, scrollable popup */}
                                            <FilterSelect
                                                icon={Truck}
                                                label="Vehicle"
                                                value={filterVehicleId}
                                                onChange={setFilterVehicleId}
                                                ariaLabel="Filter vehicle"
                                                active={filterVehicleId !== ""}
                                                className="w-full"
                                                selectClassName="flex-1"
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
                                                className="w-full"
                                            />
                                        </div>

                                        <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-3">
                                            {activeFilterCount > 0 ? (
                                                <button
                                                    type="button"
                                                    onClick={() => {
                                                        setFilterGroupId("")
                                                        setFilterVehicleId("")
                                                        setSearchText("")
                                                    }}
                                                    className="inline-flex h-8 items-center justify-center gap-1 rounded-lg px-2 text-xs font-medium text-slate-500 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
                                                    title="Clear active filters"
                                                >
                                                    <X className="h-3.5 w-3.5" />
                                                    <span>Clear ({activeFilterCount})</span>
                                                </button>
                                            ) : <span />}

                                            <button
                                                type="button"
                                                onClick={() => exportToCSV(filteredEvents)}
                                                disabled={filteredEvents.length === 0}
                                                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200/80 bg-white px-3 text-xs font-medium text-slate-700 shadow-xs hover:bg-slate-50 hover:border-slate-300 hover:text-brand transition-all cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                                                title="Export filtered idle events to CSV"
                                                aria-label="Export filtered idle events to CSV"
                                            >
                                                <Download className="h-3.5 w-3.5 text-slate-500" />
                                                <span>Export CSV</span>
                                            </button>
                                        </div>
                                    </motion.section>
                                )}
                            </AnimatePresence>
                        </div>
                    </div>
                </header>

                {error && analytics && <ErrorBanner message={error} onRetry={refresh} />}
                {!error && eventsError && analytics && <ErrorBanner message={eventsError} onRetry={refresh} />}
                {analytics?.stale && (
                    <p role="status" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                        Wialon is temporarily unavailable: showing the data read at {analytics.retrieved_at ? formatUAETime(analytics.retrieved_at) : "an earlier time"}. It refreshes automatically.
                    </p>
                )}

                {loading && !analytics ? (
                    <IdleAnalyticsSkeleton />
                ) : !analytics && error ? (
                    <ErrorState message={error} onRetry={refresh} />
                ) : analytics ? (
                    <>
                        {/* ─── 02 KPI METRIC STRIP ──────────────────────────────── */}
                        <KpiStrip label="Idle analytics summary" className="grid-cols-2 sm:grid-cols-3 xl:grid-cols-6">
                            <KpiCard
                                label="Total Vehicles"
                                value={kpis.total_vehicles ?? 0}
                            />
                            <KpiCard
                                label="Currently Idling"
                                dot="bg-status-idling"
                                value={kpis.currently_idling_vehicles ?? 0}
                                valueColor="text-amber-800"
                            />
                            <KpiCard
                                label="Idle Events"
                                value={eventsPending ? "…" : (kpis.idle_event_count ?? "—")}
                                sub={eventsPending ? "Loading events…" : eventsNote}
                                title={eventsNote || "Idle Events"}
                            />
                            <KpiCard
                                label="Total Idle Time"
                                value={formatDuration(kpis.total_idle_seconds)}
                                sub={kpis.total_idle_partial ? <span className="font-medium text-amber-700">Partial: some vehicles unknown</span> : null}
                                title={kpis.total_idle_partial ? "Left out of the total: vehicles whose idle time could not be read, that are outside the report group, or that share a name with another vehicle" : "Total Idle Time"}
                            />
                            <KpiCard
                                label="Avg Idle Duration"
                                value={eventsPending ? "…" : kpis.average_idle_seconds == null ? "—" : formatDuration(kpis.average_idle_seconds)}
                            />
                            <KpiCard
                                label="Most Idle in One Period"
                                value={eventsPending ? "…" : kpis.longest_idle_seconds == null ? "—" : formatDuration(kpis.longest_idle_seconds)}
                                valueColor={highestIdleEvent ? "text-amber-800" : "text-slate-900"}
                                onClick={highestIdleEvent ? () => setMapEvent(highestIdleEvent) : undefined}
                                // Wialon's Engine Hours report gives idle time per engine-on period (it may hold several idle
                                // stops and driving), not continuous idle stops
                                title={`Most idle time Wialon reports within one engine-on period; it may add up several idle stops${highestIdleEvent ? ` · Inspect: ${highestIdleEvent.vehicle_name}` : ""}`}
                                sub={highestIdleEvent
                                    ? `${highestIdleEvent.vehicle_name} · View map`
                                    : eventsPending
                                        ? "Loading events…"
                                        : kpis.longest_idle_seconds == null
                                            ? "Incomplete data"
                                            : "No idling in period"}
                            />
                        </KpiStrip>

                        {/* ─── 03 CUSTOMER GROUPS DRILL-DOWN ─────────────────────── */}
                        {byGroup.length > 0 && (
                            <SectionCard
                                title="Customer Groups"
                                flush
                                actions={
                                    selectedGroupId != null ? (
                                        <button
                                            type="button"
                                            onClick={() => setSelectedGroupId(null)}
                                            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800 transition-colors cursor-pointer"
                                        >
                                            <X className="h-3.5 w-3.5" />
                                            Reset selection
                                        </button>
                                    ) : (
                                        <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold tabular-nums text-slate-700">
                                            {byGroup.length} groups
                                        </span>
                                    )
                                }
                            >
                                <div className="max-h-[300px] overflow-auto">
                                    <table className="w-full min-w-[560px] text-xs text-left border-collapse">
                                        <thead className="sticky top-0 z-10 border-b border-slate-100 bg-slate-50/90 backdrop-blur-xs text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                            <tr>
                                                <th scope="col" className="px-4 py-2.5">Group</th>
                                                <th scope="col" className="px-3 py-2.5 text-right">Vehicles</th>
                                                <th scope="col" className="px-3 py-2.5 text-right">Idling now</th>
                                                <th scope="col" className="px-3 py-2.5 text-right">Total idle</th>
                                                <th scope="col" className="px-3 py-2.5 text-right">Events</th>
                                                <th scope="col" className="px-4 py-2.5 text-right">Avg / event</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {byGroup.map((g) => {
                                                const isSelected = selectedGroupId === g.group_id
                                                const toggle = () => setSelectedGroupId(isSelected ? null : g.group_id)
                                                return (
                                                    <tr
                                                        key={g.group_id}
                                                        onClick={toggle}
                                                        className={`cursor-pointer transition-colors ${
                                                            isSelected ? "bg-[#33758F]/8 font-medium" : "hover:bg-slate-50/70"
                                                        }`}
                                                    >
                                                        <td className="px-4 py-2">
                                                            <button
                                                                type="button"
                                                                onClick={(e) => { e.stopPropagation(); toggle() }}
                                                                aria-pressed={isSelected}
                                                                className="flex max-w-[260px] items-center gap-1.5 rounded text-left font-semibold text-slate-900 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                                            >
                                                                <ChevronRight
                                                                    className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform motion-reduce:transition-none ${
                                                                        isSelected ? "rotate-90 text-brand" : ""
                                                                    }`}
                                                                    aria-hidden="true"
                                                                />
                                                                <span className="truncate">{g.group_name}</span>
                                                            </button>
                                                        </td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{g.vehicle_count}</td>
                                                        <td className="px-3 py-2 text-right tabular-nums">
                                                            {g.currently_idling > 0 ? (
                                                                <span className="inline-flex items-center gap-1.5 font-semibold text-amber-800">
                                                                    <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" aria-hidden="true" />
                                                                    {g.currently_idling}
                                                                </span>
                                                            ) : <span className="text-slate-400">0</span>}
                                                        </td>
                                                        <td className="px-3 py-2 text-right font-bold tabular-nums text-slate-900">
                                                            {g.total_idle_seconds == null ? "—" : formatDuration(g.total_idle_seconds)}
                                                        </td>
                                                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{g.idle_events ?? "—"}</td>
                                                        <td className="px-4 py-2 text-right tabular-nums text-slate-700">
                                                            {g.average_idle_minutes == null ? "—" : `${g.average_idle_minutes}m`}
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>

                                {/* Group vehicle drill-down sub-panel */}
                                {selectedGroupData && (
                                    <div className="border-t border-slate-200/80 bg-slate-50/50 p-3 sm:p-4">
                                        <div className="flex items-center justify-between gap-2 pb-2.5">
                                            <div className="flex items-center gap-2">
                                                <span className="h-1.5 w-1.5 rounded-full bg-brand" />
                                                <h3 className="text-xs font-semibold text-slate-800">
                                                    {selectedGroupData.group_name}
                                                    <span className="font-normal text-slate-500"> · {selectedGroupData.vehicles.length} vehicles (select a vehicle to view its idle events)</span>
                                                </h3>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => setSelectedGroupId(null)}
                                                className="rounded p-1 text-slate-400 hover:bg-white hover:text-slate-700 transition-colors cursor-pointer"
                                                aria-label="Close group drill-down"
                                            >
                                                <X className="h-3.5 w-3.5" />
                                            </button>
                                        </div>
                                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-[260px] overflow-y-auto pr-1">
                                            {selectedGroupData.vehicles.map((v, idx) => {
                                                const displayPlate = v.registration_plate || v.plate || v.vehicle_name || "Unknown vehicle"
                                                return (
                                                    <button
                                                        key={v.vehicle_id ?? `${v.vehicle_name}-${idx}`}
                                                        type="button"
                                                        aria-haspopup="dialog"
                                                        onClick={() => setEventsVehicle(v)}
                                                        title={`View idle events for ${displayPlate}`}
                                                        className="group flex items-center justify-between gap-3 p-2.5 rounded-lg border border-slate-200/70 bg-white text-left transition-all cursor-pointer hover:border-brand/40 hover:bg-brand-light/40 hover:shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                                    >
                                                        <div className="min-w-0 flex-1">
                                                            <span className="block truncate font-mono text-xs font-semibold text-slate-900 transition-colors group-hover:text-brand">
                                                                {displayPlate}
                                                            </span>
                                                            {v.vehicle_name && v.vehicle_name !== displayPlate && (
                                                                <span className="block truncate text-[11px] text-slate-400">
                                                                    {v.vehicle_name}
                                                                </span>
                                                            )}
                                                        </div>
                                                        <div className="shrink-0 text-right">
                                                            <span className="block text-xs font-bold text-slate-900 tabular-nums">
                                                                {v.total_idle_seconds == null ? "—" : formatDuration(v.total_idle_seconds)}
                                                            </span>
                                                            <div className="mt-0.5 flex items-center justify-end gap-1.5">
                                                                <span className="text-[10px] text-slate-400 tabular-nums">
                                                                    {v.idle_events ?? "—"} ev
                                                                </span>
                                                                <FleetStatusBadge status={v.status} size="xs" />
                                                            </div>
                                                        </div>
                                                    </button>
                                                )
                                            })}
                                        </div>
                                    </div>
                                )}
                            </SectionCard>
                        )}

                        {/* ─── 04 ANALYTICS CHARTS (2x2 Grid) ────────────────────── */}
                        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                            <div className="[&>div]:h-full [&>div]:rounded-xl [&>div]:border-slate-200/70 [&>div]:shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] [&>div]:transition-all">
                                <AnalyticsChart
                                    title="Idle Duration Trend"
                                    subtitle={eventsNote || undefined}
                                    data={trendChartData}
                                    dataKey="value"
                                    nameKey="label"
                                    availableTypes={["line", "area", "bar"]}
                                    defaultType="area"
                                    unit="min"
                                    height={260}
                                />
                            </div>

                            <div className="[&>div]:h-full [&>div]:rounded-xl [&>div]:border-slate-200/70 [&>div]:shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] [&>div]:transition-all">
                                <AnalyticsChart
                                    title="Idle by Customer Group"
                                    data={groupChartData}
                                    dataKey="value"
                                    nameKey="label"
                                    availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                    defaultType="horizontal-bar"
                                    unit="min"
                                    height={260}
                                    onItemClick={(d) => {
                                        if (d?.activePayload?.[0]?.payload?.group_id != null) {
                                            setSelectedGroupId(d.activePayload[0].payload.group_id)
                                        }
                                    }}
                                />
                            </div>

                            <div className="[&>div]:h-full [&>div]:rounded-xl [&>div]:border-slate-200/70 [&>div]:shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] [&>div]:transition-all">
                                <AnalyticsChart
                                    title="Top Idle Vehicles"
                                    data={vehicleChartData}
                                    dataKey="value"
                                    nameKey="label"
                                    availableTypes={["horizontal-bar", "bar", "pie", "donut"]}
                                    defaultType="horizontal-bar"
                                    unit="min"
                                    height={260}
                                    onItemClick={(d) => {
                                        const payload = d?.activePayload?.[0]?.payload
                                        if (payload?.fullLabel || payload?.label) {
                                            setSearchText(payload.fullLabel || payload.label)
                                        }
                                    }}
                                />
                            </div>

                            {locationChartData.length > 0 && (
                                <div className="[&>div]:h-full [&>div]:rounded-xl [&>div]:border-slate-200/70 [&>div]:shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] [&>div]:transition-all">
                                    <AnalyticsChart
                                        title="Idle Location Hotspots"
                                        subtitle={eventsNote ? `Top 10 locations · ${eventsNote}` : undefined}
                                        data={locationChartData}
                                        dataKey="value"
                                        nameKey="label"
                                        availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                        defaultType="bar"
                                        unit="min"
                                        height={260}
                                    />
                                </div>
                            )}
                        </div>

                        {/* ─── 06 LOCATION HOTSPOTS ──────────────────────────────── */}
                        {byLocation.length > 0 && (
                            <SectionCard
                                title="Location Hotspots"
                                subtitle={eventsNote ? `Ranked idle locations · ${eventsNote}` : undefined}
                                flush
                                actions={
                                    <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold tabular-nums text-slate-700">
                                        {byLocation.length} hotspots
                                    </span>
                                }
                            >
                                <div className="max-h-[340px] overflow-auto">
                                    <table className="w-full min-w-[640px] text-xs text-left border-collapse">
                                        <thead className="sticky top-0 z-10 border-b border-slate-100 bg-slate-50/90 backdrop-blur-xs text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                            <tr>
                                                <th scope="col" className="px-4 py-2.5 w-12 text-center">#</th>
                                                <th scope="col" className="px-4 py-2.5">Location</th>
                                                <th scope="col" className="px-3 py-2.5 text-right">Vehicles</th>
                                                <th scope="col" className="px-3 py-2.5 text-right">Events</th>
                                                <th scope="col" className="px-4 py-2.5 text-right">Total Idle Time</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {byLocation.map((loc, idx) => (
                                                <tr key={loc.location_key} className="transition-colors hover:bg-slate-50/70">
                                                    <td className="px-4 py-2 text-center font-bold tabular-nums text-slate-400">
                                                        {idx + 1}
                                                    </td>
                                                    <td className="px-4 py-2">
                                                        {locationButton(loc.latitude, loc.longitude, () => setMapEvent(loc), "max-w-[340px]")}
                                                    </td>
                                                    <td className="px-3 py-2 text-right tabular-nums text-slate-700 font-medium">
                                                        {loc.vehicle_count}
                                                    </td>
                                                    <td className="px-3 py-2 text-right tabular-nums text-slate-700 font-medium">
                                                        {loc.event_count}
                                                    </td>
                                                    <td className="px-4 py-2 text-right font-bold tabular-nums text-slate-900">
                                                        {formatDuration(loc.total_idle_seconds)}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </SectionCard>
                        )}
                    </>
                ) : null}
            </div>
        </AppShell>
    )
}
