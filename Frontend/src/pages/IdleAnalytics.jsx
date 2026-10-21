import { useEffect, useMemo, useState, useCallback, useRef } from "react"
import {
    Clock3,
    Truck,
    Timer,
    BarChart3,
    MapPin,
    AlertTriangle,
    CheckCircle2,
    ArrowUpRight,
    Download,
    Layers,
    X,
    ChevronRight,
    TrendingUp,
    Users,
    Calendar,
} from "lucide-react"

import { getFleetIdleAnalytics } from "../api/fleetApi"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import FilterSelect from "../components/Common/FilterSelect"
import Pagination from "../components/Common/Pagination"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import { EmptyState, ErrorBanner, ErrorState, LoadingState } from "../components/Common/StateViews"
import AnalyticsChart from "../components/Analytics/AnalyticsChart"
import IdleMapModal from "../components/Analytics/IdleMapModal"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import { LocationName } from "../utils/reverseGeocode"

import { formatUAEDateTime, formatUAETime, getUAETodayStr, getUAEDateStr } from "../utils/datetime"

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
        "Vehicle Name", "Vehicle ID", "Group", "Idle Start", "Idle End",
        "Duration (min)", "Duration (HH:MM:SS)", "Latitude", "Longitude",
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

const PRESET_OPTIONS = [
    { label: "Today", timeframe: "today", hours: 0 },
    { label: "Last 1h", hours: 1 },
    { label: "Last 6h", hours: 6 },
    { label: "Last 12h", hours: 12 },
    { label: "Last 48h", hours: 48 },
    { label: "Last 7 days", hours: 168 },
    { label: "Last 30 days", hours: 720 },
]

const MIN_IDLE_OPTIONS = [
    { label: "Any duration", value: 0 },
    { label: "> 5 min", value: 300 },
    { label: "> 10 min", value: 600 },
    { label: "> 15 min", value: 900 },
    { label: "> 30 min", value: 1800 },
    { label: "> 1 hour", value: 3600 },
]

// ─── Main Page ────────────────────────────────────────────────

export default function IdleAnalytics() {
    // ── Time filters ──
    const [rangeMode, setRangeMode] = useState("preset")
    const [selectedTimeframe, setSelectedTimeframe] = useState("today")
    const [hours, setHours] = useState(168)
    const todayStr = getUAETodayStr()
    const sevenDaysAgoStr = getUAEDateStr(-7)
    const [startDate, setStartDate] = useState(sevenDaysAgoStr)
    const [endDate, setEndDate] = useState(todayStr)

    // ── Other filters ──
    const [filterGroupId, setFilterGroupId] = useState("")
    const [filterVehicleId, setFilterVehicleId] = useState("")
    const [filterMinIdle, setFilterMinIdle] = useState(0)
    const [searchText, setSearchText] = useState("")

    // ── Data ──
    const [analytics, setAnalytics] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")
    const [lastUpdated, setLastUpdated] = useState(null)

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
    const load = useCallback(async (isManualRefresh = false) => {
        const currentReq = ++reqSeqRef.current
        setLoading(true)
        setError("")
        if (rangeMode === "custom" && startDate && endDate && startDate > endDate) {
            setError("End date must be on or after the start date.")
            setLoading(false)
            return
        }
        try {
            const opts = {}
            if (rangeMode === "custom" && startDate && endDate) {
                opts.from_date = startDate
                opts.to_date = endDate
            } else if (selectedTimeframe === "today") {
                opts.timeframe = "today"
            } else {
                opts.hours = hours
            }
            if (isManualRefresh) {
                opts.skip_cache = true
                opts.refresh = true
            }
            // Pass server-side group/vehicle filter only when drilling
            if (filterGroupId) opts.group_id = Number(filterGroupId)
            if (filterVehicleId) opts.vehicle_id = Number(filterVehicleId)
            const data = await getFleetIdleAnalytics(opts)
            if (currentReq === reqSeqRef.current) {
                setAnalytics(data)
                setLastUpdated(new Date())
            }
        } catch (err) {
            if (currentReq === reqSeqRef.current) {
                console.error(err)
                setError(err.message || "Failed to load idle analytics. Please try again.")
            }
        } finally {
            if (currentReq === reqSeqRef.current) {
                setLoading(false)
            }
        }
    }, [hours, selectedTimeframe, rangeMode, startDate, endDate, filterGroupId, filterVehicleId])

    useEffect(() => {
        const timer = setTimeout(() => { void load() }, 0)
        return () => clearTimeout(timer)
    }, [load])

    // ── Derived / filtered data ──
    const kpis = analytics?.kpis || {}
    const groups = analytics?.groups || []
    const allEvents = useMemo(() => analytics?.events || [], [analytics])
    const byVehicle = useMemo(() => analytics?.by_vehicle || [], [analytics])
    const byGroup = useMemo(() => analytics?.by_group || [], [analytics])
    const byLocation = useMemo(() => analytics?.by_location || [], [analytics])
    const trend = useMemo(() => analytics?.trend || [], [analytics])

    // Filtered vehicle options for the vehicle dropdown
    const vehicleOptions = useMemo(() => {
        if (!byVehicle || byVehicle.length === 0) return []
        return byVehicle.filter((v) => !filterGroupId || String(v.group_id) === String(filterGroupId))
    }, [byVehicle, filterGroupId])

    // Reset page to 1 whenever any filter or sort changes (adjusted during render)
    const pageResetKey = JSON.stringify([drillVehicleId, filterVehicleId, filterGroupId, filterMinIdle, searchText, sortBy, sortDir, rangeMode, selectedTimeframe, startDate, endDate])
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
        if (filterMinIdle > 0) {
            evs = evs.filter((e) => Number(e.duration || 0) >= filterMinIdle)
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
    }, [allEvents, drillVehicleId, filterVehicleId, filterMinIdle, searchText, sortBy, sortDir])

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
        if (sortBy !== col) return <span className="ml-0.5 text-slate-300">↕</span>
        return <span className="ml-0.5 text-[#33758F] font-bold">{sortDir === "asc" ? "↑" : "↓"}</span>
    }

    const resetFilters = () => {
        setRangeMode("preset")
        setSelectedTimeframe("today")
        setHours(168)
        setFilterGroupId("")
        setFilterVehicleId("")
        setFilterMinIdle(0)
        setSearchText("")
        setSelectedGroupId(null)
        setDrillVehicleId(null)
    }

    const activeFilterCount = [
        filterGroupId !== "",
        filterVehicleId !== "",
        filterMinIdle > 0,
        searchText.trim() !== "",
    ].filter(Boolean).length

    return (
        <AppShell
            overlay={
                <>
                    {mapEvent && <IdleMapModal event={mapEvent} onClose={() => setMapEvent(null)} />}
                    {drawerVehicle && (
                        <VehicleDetailDrawer
                            vehicle={drawerVehicle}
                            idleEvents={analytics?.events}
                            onClose={() => setDrawerVehicle(null)}
                        />
                    )}
                </>
            }
        >
            <PageHeader
                title="Idle Analytics"
                subtitle="Idling by vehicle, group and location"
                icon={Clock3}
                onRefresh={() => load(true)}
                loading={loading}
                lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
            />

            <div className="space-y-5">
                {/* ── Compact Filter Toolbar ── */}
                <section className="rounded-xl border border-slate-200 bg-slate-50/70 p-2 sm:p-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        {/* Left Side: Filter Controls */}
                        <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
                            {/* Presets / Custom Range Segmented Toggle */}
                            <div className="flex h-8 items-center rounded-lg border border-slate-200 bg-white p-0.5 shadow-2xs">
                                {["preset", "custom"].map((mode) => (
                                    <button
                                        key={mode}
                                        type="button"
                                        onClick={() => setRangeMode(mode)}
                                        aria-pressed={rangeMode === mode}
                                        className={`h-full rounded-md px-2.5 text-xs font-semibold capitalize transition cursor-pointer ${
                                            rangeMode === mode
                                                ? "bg-[#33758F] text-white shadow-2xs"
                                                : "text-slate-600 hover:text-slate-900"
                                        }`}
                                    >
                                        {mode === "preset" ? "Presets" : "Custom"}
                                    </button>
                                ))}
                            </div>

                            {/* Preset Selector */}
                            {rangeMode === "preset" ? (
                                <FilterSelect
                                    icon={Timer}
                                    value={selectedTimeframe === "today" ? "today" : hours}
                                    onChange={(val) => {
                                        if (val === "today") {
                                            setSelectedTimeframe("today")
                                        } else {
                                            setSelectedTimeframe("preset")
                                            setHours(Number(val))
                                        }
                                    }}
                                    ariaLabel="Preset timeframe"
                                >
                                    {PRESET_OPTIONS.map((o) => (
                                        <option key={o.timeframe || o.hours} value={o.timeframe || o.hours}>
                                            {o.label}
                                        </option>
                                    ))}
                                </FilterSelect>
                            ) : (
                                <div className="flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 shadow-2xs">
                                    <Calendar className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                                    <input
                                        type="date"
                                        value={startDate}
                                        onChange={(e) => setStartDate(e.target.value)}
                                        className="bg-transparent text-xs font-medium text-slate-800 outline-none cursor-pointer"
                                        aria-label="Start date"
                                    />
                                    <span className="text-xs text-slate-400">→</span>
                                    <input
                                        type="date"
                                        value={endDate}
                                        onChange={(e) => setEndDate(e.target.value)}
                                        className="bg-transparent text-xs font-medium text-slate-800 outline-none cursor-pointer"
                                        aria-label="End date"
                                    />
                                </div>
                            )}

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

                            <FilterSelect
                                icon={Clock3}
                                value={filterMinIdle}
                                onChange={(val) => setFilterMinIdle(Number(val))}
                                ariaLabel="Minimum idle duration"
                                active={filterMinIdle > 0}
                            >
                                {MIN_IDLE_OPTIONS.map((o) => (
                                    <option key={o.value} value={o.value}>{o.label}</option>
                                ))}
                            </FilterSelect>

                            <SearchInput
                                value={searchText}
                                onChange={setSearchText}
                                placeholder="Search vehicle or group…"
                                className="w-full sm:w-52 [&_input]:h-8 [&_input]:py-0"
                            />

                            {/* Active Filters Clear Button */}
                            {activeFilterCount > 0 && (
                                <button
                                    type="button"
                                    onClick={() => {
                                        setFilterGroupId("")
                                        setFilterVehicleId("")
                                        setFilterMinIdle(0)
                                        setSearchText("")
                                    }}
                                    className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-semibold text-slate-500 hover:bg-slate-200/60 hover:text-slate-800 transition cursor-pointer"
                                    title="Clear active filters"
                                >
                                    <X className="h-3 w-3" />
                                    <span>Clear</span>
                                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[#33758F] text-[9px] font-bold text-white">
                                        {activeFilterCount}
                                    </span>
                                </button>
                            )}
                        </div>

                        {/* Right Side: Export */}
                        <div className="ml-auto flex shrink-0 items-center">
                            {/* Export CSV Button */}
                            <button
                                type="button"
                                onClick={() => exportToCSV(filteredEvents)}
                                disabled={filteredEvents.length === 0}
                                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 shadow-2xs transition hover:bg-slate-50 cursor-pointer disabled:opacity-40"
                                title="Export filtered idle events to CSV"
                                aria-label="Export filtered idle events to CSV"
                            >
                                <Download className="h-3.5 w-3.5" />
                                <span>Export CSV</span>
                            </button>
                        </div>
                    </div>
                </section>

                {error && analytics && <ErrorBanner message={error} onRetry={() => load(true)} />}

                {loading && !analytics ? (
                    <LoadingState label="Analyzing fleet idle history…" />
                ) : !analytics && error ? (
                    <ErrorState message={error} onRetry={() => load(true)} />
                ) : analytics ? (
                    <>
                        {/* ── KPI Cards ── */}
                        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
                            <KpiCard
                                icon={Truck}
                                iconBg="bg-blue-50 text-blue-600 border border-blue-100"
                                label="Total Vehicles"
                                value={kpis.total_vehicles ?? 0}
                            />
                            <KpiCard
                                icon={Clock3}
                                iconBg="bg-orange-50 text-orange-600 border border-orange-100"
                                label="Currently Idling"
                                value={kpis.currently_idling_vehicles ?? 0}
                                sub={kpis.idling_fleet_ratio != null ? `${kpis.idling_fleet_ratio}% of fleet` : undefined}
                            />
                            <KpiCard
                                icon={BarChart3}
                                iconBg="bg-purple-50 text-purple-600 border border-purple-100"
                                label="Idle Events"
                                value={kpis.idle_event_count ?? "—"}
                            />
                            <KpiCard
                                icon={Timer}
                                iconBg="bg-teal-50 text-teal-600 border border-teal-100"
                                label="Total Idle Time"
                                value={formatDuration(kpis.total_idle_seconds)}
                                sub={kpis.total_idle_partial ? "Partial: some vehicles unreadable" : undefined}
                            />
                            <KpiCard
                                icon={TrendingUp}
                                iconBg="bg-indigo-50 text-indigo-600 border border-indigo-100"
                                label="Avg Idle Duration"
                                value={kpis.average_idle_seconds == null ? "—" : formatDuration(kpis.average_idle_seconds)}
                            />
                            <KpiCard
                                icon={AlertTriangle}
                                iconBg="bg-red-50 text-red-600 border border-red-100"
                                label="Highest Idle"
                                value={kpis.longest_idle_seconds == null ? "—" : formatDuration(kpis.longest_idle_seconds)}
                                sub={
                                    highestIdleEvent
                                        ? highestIdleEvent.vehicle_name
                                        : kpis.longest_idle_seconds == null
                                            ? "Idle events incomplete"
                                            : "No idling in period"
                                }
                                badge={highestIdleEvent ? "Inspect" : undefined}
                                badgeCls="bg-red-100/70 text-red-800 border-red-200"
                                onClick={highestIdleEvent ? () => setMapEvent(highestIdleEvent) : undefined}
                                title={
                                    highestIdleEvent
                                        ? `Inspect highest idle event for vehicle: ${highestIdleEvent.vehicle_name}`
                                        : "Highest idle event"
                                }
                            />
                        </section>

                        {/* ── Customer Group Cards ── */}
                        {byGroup.length > 0 && (
                            <section>
                                <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                                    <Users className="h-4 w-4 text-[#33758F]" />
                                    Customer Groups
                                </h2>
                                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                                    {byGroup.map((g) => {
                                        const isSelected = selectedGroupId === g.group_id
                                        return (
                                            <button
                                                key={g.group_id}
                                                type="button"
                                                onClick={() => {
                                                    setSelectedGroupId(isSelected ? null : g.group_id)
                                                    setDrillVehicleId(null)
                                                }}
                                                aria-pressed={isSelected}
                                                className={`rounded-xl border p-4 text-left shadow-xs transition cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#33758F]/40 ${isSelected ? "border-[#33758F] bg-[#F0F6F9] ring-2 ring-[#33758F]/20" : "border-slate-200 bg-white hover:border-slate-300"}`}
                                            >
                                                <div className="flex items-start justify-between gap-2">
                                                    <div className="min-w-0">
                                                        <p className="truncate text-sm font-semibold text-slate-900">{g.group_name}</p>
                                                        <p className="mt-0.5 text-xs text-slate-400">{g.vehicle_count} vehicles</p>
                                                    </div>
                                                    {g.currently_idling > 0 && (
                                                        <span className="flex shrink-0 items-center gap-1 rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
                                                            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500 motion-reduce:animate-none" />
                                                            {g.currently_idling} idling
                                                        </span>
                                                    )}
                                                </div>
                                                <div className="mt-3 grid grid-cols-3 gap-2 tabular-nums">
                                                    <div>
                                                        <p className="text-[10px] text-slate-400">Total Idle</p>
                                                        <p className="text-sm font-semibold text-slate-800">{g.total_idle_seconds == null ? "—" : formatDuration(g.total_idle_seconds)}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-[10px] text-slate-400">Events</p>
                                                        <p className="text-sm font-semibold text-slate-800">{g.idle_events ?? "—"}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-[10px] text-slate-400">Avg / Event</p>
                                                        <p className="text-sm font-semibold text-slate-800">{g.average_idle_minutes == null ? "—" : `${g.average_idle_minutes}m`}</p>
                                                    </div>
                                                </div>
                                            </button>
                                        )
                                    })}
                                </div>
                            </section>
                        )}

                        {/* ── Group Drill-Down Vehicle List ── */}
                        {selectedGroupData && (
                            <section className="rounded-xl border border-[#D1E2EA] bg-[#F0F6F9]/60 p-4">
                                <div className="mb-3 flex items-center justify-between gap-2">
                                    <h3 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-slate-900">
                                        <ChevronRight className="h-4 w-4 shrink-0 text-[#33758F]" />
                                        <span className="truncate">{selectedGroupData.group_name} · {selectedGroupData.vehicles.length} vehicles</span>
                                    </h3>
                                    <button
                                        type="button"
                                        onClick={() => setSelectedGroupId(null)}
                                        className="rounded-lg p-1 text-slate-500 transition hover:bg-white hover:text-slate-900 cursor-pointer"
                                        aria-label="Close group drill-down"
                                    >
                                        <X className="h-4 w-4" />
                                    </button>
                                </div>
                                <div className="max-h-[360px] space-y-1.5 overflow-y-auto">
                                    {selectedGroupData.vehicles.map((v) => (
                                        <button
                                            key={v.vehicle_id}
                                            type="button"
                                            aria-pressed={drillVehicleId === v.vehicle_id}
                                            onClick={() => setDrillVehicleId(drillVehicleId === v.vehicle_id ? null : v.vehicle_id)}
                                            className={`flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border px-3.5 py-2.5 text-left transition cursor-pointer ${drillVehicleId === v.vehicle_id ? "border-[#33758F] bg-white" : "border-slate-200 bg-white/70 hover:bg-white"}`}
                                        >
                                            <div className="min-w-0">
                                                <p className="truncate font-mono text-xs font-bold text-slate-900">{v.registration_plate || v.plate || v.vehicle_name}</p>
                                                <p className="truncate text-[11px] text-slate-400">{v.vehicle_name ? `${v.vehicle_name} · ` : ""}ID {v.vehicle_id}</p>
                                            </div>
                                            <div className="flex items-center gap-5 text-right tabular-nums">
                                                <div>
                                                    <p className="text-[10px] text-slate-400">Idle Time</p>
                                                    <p className="text-xs font-semibold text-slate-800">{v.total_idle_seconds == null ? "—" : formatDuration(v.total_idle_seconds)}</p>
                                                </div>
                                                <div>
                                                    <p className="text-[10px] text-slate-400">Events</p>
                                                    <p className="text-xs font-semibold text-slate-800">{v.idle_events ?? "—"}</p>
                                                </div>
                                                <FleetStatusBadge status={v.status} size="xs" />
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            </section>
                        )}

                        {/* ── Charts Row 1: Trend + Group ── */}
                        <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                            <AnalyticsChart
                                title="Idle Duration Trend"
                                data={trendChartData}
                                dataKey="value"
                                nameKey="label"
                                availableTypes={["line", "area", "bar"]}
                                defaultType="area"
                                unit="min"
                                color="#F97316"
                                height={260}
                            />
                            <AnalyticsChart
                                title="Idle by Customer Group"
                                data={groupChartData}
                                dataKey="value"
                                nameKey="label"
                                availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                defaultType="donut"
                                unit="min"
                                color="#8B5CF6"
                                height={260}
                                onItemClick={(d) => {
                                    if (d?.activePayload?.[0]?.payload?.group_id) {
                                        setSelectedGroupId(d.activePayload[0].payload.group_id)
                                    }
                                }}
                            />
                        </section>

                        {/* ── Charts Row 2: Vehicles + Locations ── */}
                        <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
                            <AnalyticsChart
                                title="Top Idle Vehicles"
                                subtitle="Click a bar to filter events"
                                data={vehicleChartData}
                                dataKey="value"
                                nameKey="label"
                                availableTypes={["horizontal-bar", "bar", "pie", "donut"]}
                                defaultType="horizontal-bar"
                                unit="min"
                                color="#3B82F6"
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
                                    title="Idle Location Hotspots"
                                    subtitle="Top 10 locations"
                                    data={locationChartData}
                                    dataKey="value"
                                    nameKey="label"
                                    availableTypes={["bar", "horizontal-bar", "pie", "donut"]}
                                    defaultType="bar"
                                    unit="min"
                                    color="#10B981"
                                    height={300}
                                />
                            )}
                        </section>

                        {/* ── Idle Events Table ── */}
                        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xs">
                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3 sm:px-5">
                                <h2 className="text-sm font-semibold text-slate-900">Idle Events</h2>
                                <p className="text-xs text-slate-500">
                                    <strong className="font-semibold text-slate-800">{filteredEvents.length}</strong> events{filterMinIdle > 0 ? ` · min ${formatDuration(filterMinIdle)}` : ""}
                                </p>
                            </div>

                            {/* Active Search / Filter Banner */}
                            {activeSearchStats && (
                                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-[#F0F6F9] px-4 py-2.5 text-xs text-[#33758F] sm:px-5">
                                    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                                        <Truck className="h-4 w-4 shrink-0" />
                                        <span className="font-semibold">
                                            {searchText ? `"${searchText}"` : "Filtered vehicle"}
                                        </span>
                                        <span className="text-slate-600">
                                             — {activeSearchStats.eventCount} event{activeSearchStats.eventCount !== 1 ? "s" : ""} · Total: <strong className="text-slate-900">{formatDuration(activeSearchStats.totalSeconds)}</strong> · Longest: <strong className="text-slate-900">{formatDuration(activeSearchStats.longestSeconds)}</strong>
                                        </span>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setSearchText("")
                                            setDrillVehicleId(null)
                                            setFilterVehicleId("")
                                        }}
                                        className="inline-flex items-center gap-1 font-semibold text-[#33758F] hover:underline cursor-pointer"
                                    >
                                        <X className="h-3.5 w-3.5" />
                                        Clear
                                    </button>
                                </div>
                            )}

                            {filteredEvents.length === 0 ? (
                                <EmptyState
                                    icon={CheckCircle2}
                                    title={allEvents.length === 0 ? "No idle events in this period" : "No idle events match your filters"}
                                    className="rounded-none border-none"
                                    action={allEvents.length > 0 && (
                                        <button type="button" onClick={resetFilters} className="text-xs font-semibold text-[#33758F] hover:underline cursor-pointer">
                                            Reset filters
                                        </button>
                                    )}
                                />
                            ) : (
                                <div className="overflow-x-auto">
                                    <table className="w-full min-w-[900px]">
                                        <thead>
                                            <tr className="border-b border-slate-100 bg-slate-50/70">
                                                {[
                                                    ["vehicle_name", "Vehicle"],
                                                    ["group_name", "Group"],
                                                    ["start_time", "Idle Start"],
                                                    ["end_time", "Idle End"],
                                                    ["duration", "Duration"],
                                                    [null, "Location"],
                                                ].map(([col, label]) => (
                                                    <th
                                                        key={label}
                                                        aria-sort={col && sortBy === col ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
                                                        className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500"
                                                    >
                                                        {col ? (
                                                            <button
                                                                type="button"
                                                                onClick={() => handleSort(col)}
                                                                className="inline-flex items-center uppercase tracking-wider hover:text-slate-800 cursor-pointer"
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
                                                    <tr key={ev.id || idx} className="transition hover:bg-slate-50">
                                                        <td className="px-4 py-2.5">
                                                            <button
                                                                type="button"
                                                                onClick={() => setDrawerVehicle(ev)}
                                                                className="group flex items-center gap-2 text-left cursor-pointer"
                                                                title="Click to inspect vehicle details & idle history"
                                                            >
                                                                <Truck className="h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-[#33758F]" />
                                                                <div className="min-w-0">
                                                                    <span className="text-xs font-bold font-mono text-slate-900 block truncate group-hover:text-[#33758F]">
                                                                        {displayPlate || ev.vehicle_name}
                                                                    </span>
                                                                    <span className="text-[10px] text-slate-400 block truncate">
                                                                        {displayPlate && ev.vehicle_name ? `${ev.vehicle_name} · ` : ""}ID {ev.vehicle_id}
                                                                    </span>
                                                                </div>
                                                            </button>
                                                        </td>
                                                        <td className="max-w-[160px] truncate px-4 py-2.5 text-xs font-medium text-slate-600">{ev.group_name || "—"}</td>
                                                        <td className="whitespace-nowrap px-4 py-2.5 text-xs tabular-nums text-slate-700">{formatDateTime(ev.start_time)}</td>
                                                        <td className="whitespace-nowrap px-4 py-2.5 text-xs tabular-nums text-slate-700">{formatDateTime(ev.end_time)}</td>
                                                        <td className="px-4 py-2.5">
                                                            <span className="whitespace-nowrap rounded border border-amber-200/60 bg-amber-50 px-2 py-0.5 text-xs font-bold tabular-nums text-amber-700">
                                                                {formatDuration(ev.duration)}
                                                            </span>
                                                        </td>
                                                        <td className="px-4 py-2.5">
                                                            {hasLoc ? (
                                                                <div className="flex items-center gap-1.5">
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => setMapEvent(ev)}
                                                                        className="group inline-flex w-full max-w-[240px] items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-left text-xs font-medium text-slate-800 shadow-2xs transition hover:border-[#33758F] hover:bg-slate-50 cursor-pointer"
                                                                        title="View on Interactive Map"
                                                                    >
                                                                        <MapPin className="h-3.5 w-3.5 shrink-0 text-amber-500 transition-transform group-hover:scale-110" />
                                                                        <span className="truncate">
                                                                            <LocationName lat={ev.location.latitude} lng={ev.location.longitude} />
                                                                        </span>
                                                                    </button>
                                                                    <a
                                                                        href={`https://www.google.com/maps?q=${ev.location.latitude},${ev.location.longitude}`}
                                                                        target="_blank"
                                                                        rel="noopener noreferrer"
                                                                        className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                                                        title="Open in Google Maps"
                                                                        aria-label="Open in Google Maps"
                                                                    >
                                                                        <ArrowUpRight className="h-3.5 w-3.5" />
                                                                    </a>
                                                                </div>
                                                            ) : (
                                                                <span className="text-xs text-slate-400">No GPS</span>
                                                            )}
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
                                        <span className="text-slate-400">Rows</span>
                                        <select
                                            value={pageSize}
                                            onChange={(e) => {
                                                setPageSize(Number(e.target.value))
                                                setPage(1)
                                            }}
                                            className="rounded-md border border-slate-200 bg-white px-1.5 py-0.5 text-xs font-semibold text-slate-700 outline-none cursor-pointer"
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

                        {/* ── Location Hotspots Detail ── */}
                        {byLocation.length > 0 && (
                            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xs">
                                <div className="border-b border-slate-200 px-4 py-3 sm:px-5">
                                    <h2 className="text-sm font-semibold text-slate-900">Location Hotspots</h2>
                                </div>
                                <div className="grid grid-cols-1 divide-y divide-slate-100">
                                    {byLocation.map((loc, idx) => (
                                        <div key={loc.location_key} className="flex items-center justify-between gap-3 px-4 py-2.5 transition hover:bg-slate-50 sm:px-5">
                                            <div className="flex min-w-0 items-center gap-3">
                                                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-amber-50">
                                                    <span className="text-xs font-bold text-amber-600">#{idx + 1}</span>
                                                </div>
                                                <div className="min-w-0">
                                                    <div className="flex items-center gap-1.5">
                                                        <button
                                                            type="button"
                                                            onClick={() => setMapEvent(loc)}
                                                            className="group inline-flex w-full max-w-[260px] items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-left text-xs font-medium text-slate-800 shadow-2xs transition hover:border-[#33758F] hover:bg-slate-50 cursor-pointer"
                                                            title="View on Interactive Map"
                                                        >
                                                            <MapPin className="h-3.5 w-3.5 shrink-0 text-amber-500 transition-transform group-hover:scale-110" />
                                                            <span className="truncate">
                                                                <LocationName lat={loc.latitude} lng={loc.longitude} />
                                                            </span>
                                                        </button>
                                                        <a
                                                            href={`https://www.google.com/maps?q=${loc.latitude},${loc.longitude}`}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="shrink-0 rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                                            title="Open in Google Maps"
                                                            aria-label="Open in Google Maps"
                                                        >
                                                            <ArrowUpRight className="h-3.5 w-3.5" />
                                                        </a>
                                                    </div>
                                                    <p className="mt-0.5 text-xs text-slate-400">
                                                        {loc.vehicle_count} vehicle{loc.vehicle_count !== 1 ? "s" : ""} · {loc.event_count} event{loc.event_count !== 1 ? "s" : ""}
                                                    </p>
                                                </div>
                                            </div>
                                            <div className="shrink-0 text-right">
                                                <p className="text-sm font-semibold tabular-nums text-slate-800">{formatDuration(loc.total_idle_seconds)}</p>
                                                <p className="text-[11px] text-slate-400">total idle</p>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </section>
                        )}
                    </>
                ) : null}
            </div>
        </AppShell>
    )
}
