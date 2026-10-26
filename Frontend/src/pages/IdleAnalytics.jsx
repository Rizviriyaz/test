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
import SectionCard from "../components/Common/SectionCard"
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

const presetInputs = (id) => {
    const r = resolvePreset(id)
    return { from: toUaeInputValue(r.from), to: toUaeInputValue(r.to) }
}

// ─── Main Page ────────────────────────────────────────────────

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

    const editRangeInput = (field, value) => {
        setRangePreset("custom")
        setRangeInputs((prev) => ({ ...prev, [field]: value }))
    }

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
            if (summaryRes.status === "rejected") setError(message)
            else setEventsError(`Idle events could not be loaded: ${message}`)
        }
    }, [rangePreset, rangeInputs, filterGroupId, filterVehicleId])

    useEffect(() => {
        const timer = setTimeout(() => { void load() }, rangePreset === "custom" ? 600 : 0)
        return () => clearTimeout(timer)
    }, [load, rangePreset, refreshNonce])

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
        const { status, idle_start, idle_duration } = drawerVehicleRow
        return { ...drawerVehicle, status, idle_start, idle_duration }
    }, [drawerVehicle, drawerVehicleRow])
    const drawerEventsComplete = drawerVehicleRow ? drawerVehicleRow.idle_events !== null : analytics?.events_complete !== false

    const vehicleOptions = useMemo(() => {
        if (!byVehicle || byVehicle.length === 0) return []
        return byVehicle.filter((v) => !filterGroupId || String(v.group_id) === String(filterGroupId))
    }, [byVehicle, filterGroupId])

    const pageResetKey = JSON.stringify([drillVehicleId, filterVehicleId, filterGroupId, searchText, sortBy, sortDir, rangePreset, rangeInputs])
    const [lastPageResetKey, setLastPageResetKey] = useState(pageResetKey)
    if (pageResetKey !== lastPageResetKey) {
        setLastPageResetKey(pageResetKey)
        setPage(1)
    }

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
        evs = [...evs].sort((a, b) => {
            let aVal = a[sortBy] ?? 0
            let bVal = b[sortBy] ?? 0
            if (aVal !== bVal) {
                if (sortDir === "asc") return aVal > bVal ? 1 : -1
                return aVal < bVal ? 1 : -1
            }
            return (b.start_time || 0) - (a.start_time || 0) || String(b.id || "").localeCompare(String(a.id || ""))
        })
        return evs
    }, [allEvents, drillVehicleId, filterVehicleId, searchText, sortBy, sortDir])

    const totalEvents = filteredEvents.length
    const totalPages = Math.max(1, Math.ceil(totalEvents / pageSize))
    const safePage = Math.min(page, totalPages)
    const startIndex = (safePage - 1) * pageSize
    const paginatedEvents = useMemo(() => {
        return filteredEvents.slice(startIndex, startIndex + pageSize)
    }, [filteredEvents, startIndex, pageSize])
    const visibleStart = totalEvents === 0 ? 0 : startIndex + 1
    const visibleEnd = Math.min(startIndex + pageSize, totalEvents)

    const highestIdleEvent = useMemo(() => {
        return analytics?.kpis?.longest_idle_event || analytics?.kpis?.highest_idle_event || null
    }, [analytics])

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
        return <span className="ml-1 text-brand font-bold" aria-hidden="true">{sortDir === "asc" ? "↑" : "↓"}</span>
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
                    {mapEvent && <IdleMapModal event={mapEvent} onClose={() => setMapEvent(null)} />}
                    {drawerVehicle && (
                        <VehicleDetailDrawer
                            vehicle={drawerVehicleWithLive}
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
                subtitle="Idling analysis by vehicle, customer group, and location"
                onRefresh={refresh}
                loading={loading || eventsLoading}
                lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
            />

            <div className="space-y-5 pb-6">
                {/* ─── 01 FILTER TOOLBAR ─────────────────────────────────── */}
                <section
                    aria-label="Filter Controls"
                    className="rounded-xl border border-slate-200/70 bg-white p-3 sm:p-3.5 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]"
                >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        {/* Date Range Presets & Inputs */}
                        <div className="flex flex-wrap items-center gap-2.5">
                            <fieldset className="flex min-w-0 max-w-full flex-col gap-1.5">
                                <legend className="sr-only">Date range</legend>
                                <div
                                    role="group"
                                    aria-label="Date range presets"
                                    className="flex h-8 items-center rounded-lg bg-slate-100 p-0.5 border border-slate-200/50"
                                >
                                    {RANGE_PRESETS.map((p) => {
                                        const active = rangePreset === p.id
                                        return (
                                            <button
                                                key={p.id}
                                                type="button"
                                                onClick={() => applyPreset(p.id)}
                                                aria-pressed={active}
                                                className={`h-full rounded-md px-2.5 text-xs font-medium transition-all duration-150 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 whitespace-nowrap ${
                                                    active
                                                        ? "bg-white text-slate-900 font-semibold shadow-xs"
                                                        : "text-slate-600 hover:text-slate-900 hover:bg-white/50"
                                                }`}
                                            >
                                                {p.label}
                                            </button>
                                        )
                                    })}
                                    {rangePreset === "custom" && (
                                        <span className="inline-flex h-full items-center whitespace-nowrap rounded-md bg-white px-2.5 text-xs font-semibold text-slate-900 shadow-xs">
                                            Custom
                                        </span>
                                    )}
                                </div>
                            </fieldset>

                            <div className="flex flex-wrap items-center gap-1.5">
                                {[["from", "From"], ["to", "To"]].map(([field, label]) => (
                                    <label
                                        key={field}
                                        className={`inline-flex h-8 items-center gap-1.5 rounded-lg border bg-white px-2.5 transition-colors focus-within:ring-2 ${
                                            rangeError
                                                ? "border-rose-300 focus-within:border-rose-400 focus-within:ring-rose-100"
                                                : "border-slate-200/80 focus-within:border-brand focus-within:ring-brand/15 hover:border-slate-300"
                                        }`}
                                    >
                                        <Calendar className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />
                                        <span className="shrink-0 text-xs text-slate-400 font-medium">{label}</span>
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
                        </div>

                        {/* Dropdown Filters, Search, and Export */}
                        <div className="flex flex-wrap items-center gap-2">
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
                                selectClassName="max-w-[140px]"
                            >
                                <option value="">All Groups</option>
                                {groups.map((g) => (
                                    <option key={g.id} value={g.id}>{g.name}</option>
                                ))}
                            </FilterSelect>

                            <FilterSelect
                                icon={Truck}
                                label="Vehicle"
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
                                className="w-full sm:w-48"
                            />

                            {activeFilterCount > 0 && (
                                <button
                                    type="button"
                                    onClick={() => {
                                        setFilterGroupId("")
                                        setFilterVehicleId("")
                                        setSearchText("")
                                    }}
                                    className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-medium text-slate-500 hover:text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer"
                                    title="Clear active filters"
                                >
                                    <X className="h-3.5 w-3.5" />
                                    <span>Clear ({activeFilterCount})</span>
                                </button>
                            )}

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
                    </div>
                </section>

                {error && analytics && <ErrorBanner message={error} onRetry={refresh} />}
                {!error && eventsError && analytics && <ErrorBanner message={eventsError} onRetry={refresh} />}

                {loading && !analytics ? (
                    <LoadingState label="Analyzing fleet idle telemetry…" />
                ) : !analytics && error ? (
                    <ErrorState message={error} onRetry={refresh} />
                ) : analytics ? (
                    <>
                        {/* ─── 02 KPI METRIC CARDS ──────────────────────────────── */}
                        <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3 sm:gap-4">
                            {/* 1. Total Vehicles */}
                            <div className="flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300">
                                <dt className="text-xs font-medium text-slate-500">Total Vehicles</dt>
                                <dd className="mt-1">
                                    <span className="text-2xl font-bold tabular-nums text-slate-900 tracking-tight whitespace-nowrap">
                                        {kpis.total_vehicles ?? 0}
                                    </span>
                                    <span className="mt-0.5 block truncate text-[11px] text-slate-400 font-medium">
                                        Tracked in fleet
                                    </span>
                                </dd>
                            </div>

                            {/* 2. Currently Idling */}
                            <div className="flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300">
                                <dt className="flex items-center justify-between text-xs font-medium text-slate-500">
                                    <span>Currently Idling</span>
                                    <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" aria-hidden="true" />
                                </dt>
                                <dd className="mt-1">
                                    <span className="text-2xl font-bold tabular-nums text-amber-800 tracking-tight whitespace-nowrap">
                                        {kpis.currently_idling_vehicles ?? 0}
                                    </span>
                                    <span className="mt-0.5 block truncate text-[11px] text-slate-400 font-medium">
                                        {kpis.idling_fleet_ratio != null ? `${kpis.idling_fleet_ratio}% of active fleet` : "Live status"}
                                    </span>
                                </dd>
                            </div>

                            {/* 3. Idle Events */}
                            <div className="flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300">
                                <dt className="text-xs font-medium text-slate-500">Idle Events</dt>
                                <dd className="mt-1">
                                    <span className="text-2xl font-bold tabular-nums text-slate-900 tracking-tight whitespace-nowrap">
                                        {eventsPending ? "…" : (kpis.idle_event_count ?? "—")}
                                    </span>
                                    <span className="mt-0.5 block truncate text-[11px] text-slate-400 font-medium" title={eventsNote || undefined}>
                                        {eventsPending ? "Loading events…" : (eventsNote || "Recorded in period")}
                                    </span>
                                </dd>
                            </div>

                            {/* 4. Total Idle Time */}
                            <div className="flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300">
                                <dt className="text-xs font-medium text-slate-500">Total Idle Time</dt>
                                <dd className="mt-1">
                                    <span className="text-2xl font-bold tabular-nums text-slate-900 tracking-tight whitespace-nowrap">
                                        {formatDuration(kpis.total_idle_seconds)}
                                    </span>
                                    <span className={`mt-0.5 block truncate text-[11px] font-medium ${kpis.total_idle_partial ? "text-amber-700" : "text-slate-400"}`}>
                                        {kpis.total_idle_partial ? "Partial: unreadable units" : "Engine on & zero speed"}
                                    </span>
                                </dd>
                            </div>

                            {/* 5. Avg Idle Duration */}
                            <div className="flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300">
                                <dt className="text-xs font-medium text-slate-500">Avg Idle Duration</dt>
                                <dd className="mt-1">
                                    <span className="text-2xl font-bold tabular-nums text-slate-900 tracking-tight whitespace-nowrap">
                                        {eventsPending ? "…" : kpis.average_idle_seconds == null ? "—" : formatDuration(kpis.average_idle_seconds)}
                                    </span>
                                    <span className="mt-0.5 block truncate text-[11px] text-slate-400 font-medium">
                                        Per idle occurrence
                                    </span>
                                </dd>
                            </div>

                            {/* 6. Longest Idle Event */}
                            <div
                                onClick={highestIdleEvent ? () => setMapEvent(highestIdleEvent) : undefined}
                                className={`flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300 ${
                                    highestIdleEvent ? "cursor-pointer group" : ""
                                }`}
                                title={highestIdleEvent ? `Inspect longest idle event for: ${highestIdleEvent.vehicle_name}` : "Longest idle event"}
                            >
                                <dt className="flex items-center justify-between text-xs font-medium text-slate-500">
                                    <span>Longest Idle</span>
                                    {highestIdleEvent && (
                                        <ArrowUpRight className="h-3.5 w-3.5 text-slate-400 group-hover:text-brand transition-colors" />
                                    )}
                                </dt>
                                <dd className="mt-1">
                                    <span className={`text-2xl font-bold tabular-nums tracking-tight whitespace-nowrap ${highestIdleEvent ? "text-amber-800" : "text-slate-900"}`}>
                                        {eventsPending ? "…" : kpis.longest_idle_seconds == null ? "—" : formatDuration(kpis.longest_idle_seconds)}
                                    </span>
                                    <span className="mt-0.5 block truncate text-[11px] font-medium text-slate-400 group-hover:text-brand transition-colors">
                                        {highestIdleEvent
                                            ? `${highestIdleEvent.vehicle_name} · View map`
                                            : eventsPending
                                                ? "Loading events…"
                                                : kpis.longest_idle_seconds == null
                                                    ? "Incomplete data"
                                                    : "No idling in period"}
                                    </span>
                                </dd>
                            </div>
                        </div>

                        {/* ─── 03 CUSTOMER GROUPS DRILL-DOWN ─────────────────────── */}
                        {byGroup.length > 0 && (
                            <SectionCard
                                title="Customer Groups"
                                subtitle="Group-level accumulated idle time and fleet size · Select a group to drill down into its vehicles"
                                flush
                                actions={
                                    selectedGroupId ? (
                                        <button
                                            type="button"
                                            onClick={() => { setSelectedGroupId(null); setDrillVehicleId(null); }}
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
                                                const toggle = () => {
                                                    setSelectedGroupId(isSelected ? null : g.group_id)
                                                    setDrillVehicleId(null)
                                                }
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
                                                    <span className="font-normal text-slate-500"> · {selectedGroupData.vehicles.length} vehicles (select a vehicle to filter event list)</span>
                                                </h3>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => { setSelectedGroupId(null); setDrillVehicleId(null); }}
                                                className="rounded p-1 text-slate-400 hover:bg-white hover:text-slate-700 transition-colors cursor-pointer"
                                                aria-label="Close group drill-down"
                                            >
                                                <X className="h-3.5 w-3.5" />
                                            </button>
                                        </div>
                                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-[260px] overflow-y-auto pr-1">
                                            {selectedGroupData.vehicles.map((v) => {
                                                const isDrilled = drillVehicleId === v.vehicle_id
                                                const displayPlate = v.registration_plate || v.plate || v.vehicle_name
                                                return (
                                                    <button
                                                        key={v.vehicle_id}
                                                        type="button"
                                                        aria-pressed={isDrilled}
                                                        onClick={() => setDrillVehicleId(isDrilled ? null : v.vehicle_id)}
                                                        className={`flex items-center justify-between gap-3 p-2.5 rounded-lg border text-left transition-all cursor-pointer ${
                                                            isDrilled
                                                                ? "border-brand bg-white shadow-xs ring-1 ring-brand/40"
                                                                : "border-slate-200/70 bg-white hover:border-slate-300 hover:shadow-xs"
                                                        }`}
                                                    >
                                                        <div className="min-w-0 flex-1">
                                                            <span className="block truncate font-mono text-xs font-semibold text-slate-900">
                                                                {displayPlate}
                                                            </span>
                                                            <span className="block truncate text-[11px] text-slate-400">
                                                                {v.vehicle_name ? `${v.vehicle_name} · ` : ""}ID {v.vehicle_id}
                                                            </span>
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
                                    subtitle={eventsNote || "Accumulated idle minutes over time"}
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
                                    subtitle="Distribution of idle minutes across groups"
                                    data={groupChartData}
                                    dataKey="value"
                                    nameKey="label"
                                    availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                    defaultType="horizontal-bar"
                                    unit="min"
                                    height={260}
                                    onItemClick={(d) => {
                                        if (d?.activePayload?.[0]?.payload?.group_id) {
                                            setSelectedGroupId(d.activePayload[0].payload.group_id)
                                        }
                                    }}
                                />
                            </div>

                            <div className="[&>div]:h-full [&>div]:rounded-xl [&>div]:border-slate-200/70 [&>div]:shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] [&>div]:transition-all">
                                <AnalyticsChart
                                    title="Top Idle Vehicles"
                                    subtitle="Select a bar to filter events by vehicle"
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
                                        subtitle={eventsNote ? `Top 10 locations · ${eventsNote}` : "Top 10 accumulated idle locations"}
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

                        {/* ─── 05 IDLE EVENTS TABLE ──────────────────────────────── */}
                        <SectionCard
                            title="Idle Events"
                            subtitle="Individual engine-on periods with idle durations"
                            flush
                            actions={
                                <div className="flex items-center gap-2">
                                    <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold tabular-nums text-slate-700">
                                        {filteredEvents.length} {filteredEvents.length === 1 ? "event" : "events"}
                                    </span>
                                    {eventsNote && (
                                        <span className="text-xs text-amber-700 font-medium">
                                            {eventsNote}
                                        </span>
                                    )}
                                </div>
                            }
                        >
                            <div className="border-b border-slate-100 bg-slate-50/50 px-4 py-2 text-[11px] text-slate-500">
                                Each row reflects a Wialon engine-on period. Idle time represents zero-speed duration with ignition active.
                            </div>

                            {activeSearchStats && (
                                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 bg-brand-light/60 px-4 py-2 text-xs text-slate-700">
                                    <p className="min-w-0">
                                        <span className="font-semibold text-slate-900">
                                            {searchText ? `"${searchText}"` : "Filtered vehicle"}
                                        </span>
                                        {" "}· {activeSearchStats.eventCount} event{activeSearchStats.eventCount !== 1 ? "s" : ""} · total <span className="font-bold tabular-nums text-slate-900">{formatDuration(activeSearchStats.totalSeconds)}</span> · longest <span className="font-bold tabular-nums text-slate-900">{formatDuration(activeSearchStats.longestSeconds)}</span>
                                    </p>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setSearchText("")
                                            setDrillVehicleId(null)
                                            setFilterVehicleId("")
                                        }}
                                        className="inline-flex items-center gap-1 font-semibold text-brand hover:underline underline-offset-2 cursor-pointer"
                                    >
                                        <X className="h-3.5 w-3.5" />
                                        Clear filter
                                    </button>
                                </div>
                            )}

                            {eventsPending ? (
                                <LoadingState label="Loading idle events…" />
                            ) : filteredEvents.length === 0 ? (
                                <EmptyState
                                    icon={CheckCircle2}
                                    title={allEvents.length === 0 ? "No idle events recorded in this period" : "No idle events match your active filters"}
                                    className="border-none py-10"
                                    action={allEvents.length > 0 && (
                                        <button
                                            type="button"
                                            onClick={resetFilters}
                                            className="text-xs font-semibold text-brand hover:underline underline-offset-2 cursor-pointer"
                                        >
                                            Reset filters
                                        </button>
                                    )}
                                />
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full min-w-[860px] text-xs text-left border-collapse">
                                        <thead className="border-b border-slate-200/80 bg-slate-50/90 backdrop-blur-xs text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                            <tr>
                                                {[
                                                    ["vehicle_name", "Vehicle", "text-left"],
                                                    ["group_name", "Group", "text-left"],
                                                    ["start_time", "Engine On", "text-left"],
                                                    ["end_time", "Engine Off", "text-left"],
                                                    ["duration", "Idle Duration", "text-right"],
                                                    [null, "Location", "text-left"],
                                                ].map(([col, label, align]) => (
                                                    <th
                                                        key={label}
                                                        scope="col"
                                                        aria-sort={col && sortBy === col ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
                                                        className={`px-4 py-2.5 ${align}`}
                                                    >
                                                        {col ? (
                                                            <button
                                                                type="button"
                                                                onClick={() => handleSort(col)}
                                                                className={`inline-flex items-center hover:text-slate-900 cursor-pointer transition-colors ${
                                                                    sortBy === col ? "text-slate-900" : ""
                                                                }`}
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
                                                    <tr key={ev.id || idx} className="transition-colors hover:bg-slate-50/70">
                                                        <td className="px-4 py-2">
                                                            <button
                                                                type="button"
                                                                onClick={() => setDrawerVehicle(ev)}
                                                                className="group block max-w-[200px] rounded text-left cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                                                title="Inspect vehicle details & idle history"
                                                            >
                                                                <span className="block truncate font-mono text-xs font-semibold text-slate-900 group-hover:text-brand transition-colors">
                                                                    {displayPlate || ev.vehicle_name}
                                                                </span>
                                                                <span className="block truncate text-[11px] text-slate-400">
                                                                    {displayPlate && ev.vehicle_name ? `${ev.vehicle_name} · ` : ""}ID {ev.vehicle_id}
                                                                </span>
                                                            </button>
                                                        </td>
                                                        <td className="max-w-[150px] truncate px-4 py-2 text-slate-600 font-medium">
                                                            {ev.group_name || "—"}
                                                        </td>
                                                        <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-600">
                                                            {formatDateTime(ev.start_time)}
                                                        </td>
                                                        <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-600">
                                                            {formatDateTime(ev.end_time)}
                                                        </td>
                                                        <td className="whitespace-nowrap px-4 py-2 text-right font-bold tabular-nums text-amber-800">
                                                            {formatDuration(ev.duration)}
                                                        </td>
                                                        <td className="px-4 py-2">
                                                            {hasLoc
                                                                ? locationButton(ev.location.latitude, ev.location.longitude, () => setMapEvent(ev), "max-w-[260px]")
                                                                : <span className="text-slate-400 text-xs">No GPS fix</span>}
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
                                    className="border-slate-200/80"
                                >
                                    <label className="flex items-center gap-1.5 border-l border-slate-200 pl-3">
                                        <span className="text-slate-500 font-medium">Rows</span>
                                        <select
                                            value={pageSize}
                                            onChange={(e) => {
                                                setPageSize(Number(e.target.value))
                                                setPage(1)
                                            }}
                                            className="h-7 rounded-md border border-slate-200 bg-white px-2 text-xs font-semibold text-slate-700 outline-none cursor-pointer focus:border-brand"
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
                        </SectionCard>

                        {/* ─── 06 LOCATION HOTSPOTS ──────────────────────────────── */}
                        {byLocation.length > 0 && (
                            <SectionCard
                                title="Location Hotspots"
                                subtitle="Ranked geographic locations with recurring idle accumulation"
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
