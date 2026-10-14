import { useEffect, useMemo, useState, useCallback } from "react"
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
    RefreshCw,
    Layers,
    X,
    SlidersHorizontal,
    ChevronRight,
    Users,
    TrendingUp,
    Search,
} from "lucide-react"

import { getFleetIdleAnalytics } from "../api/fleetApi"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import AnalyticsChart from "../components/Analytics/AnalyticsChart"
import IdleMapModal from "../components/Analytics/IdleMapModal"
import { LocationName } from "../utils/reverseGeocode"

import { formatUAEDateTime, getUAETodayStr, getUAEDateStr } from "../utils/datetime"

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

import KpiCard from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"

// ─── Filter Bar ───────────────────────────────────────────────

const PRESET_OPTIONS = [
    { label: "Today", timeframe: "today", hours: 0 },
    { label: "Last 24h", hours: 24 },
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
    const [hours, setHours] = useState(24)
    const todayStr = getUAETodayStr()
    const sevenDaysAgoStr = getUAEDateStr(-7)
    const [startDate, setStartDate] = useState(sevenDaysAgoStr)
    const [endDate, setEndDate] = useState(todayStr)

    // ── Other filters ──
    const [filterGroupId, setFilterGroupId] = useState("")
    const [filterVehicleId, setFilterVehicleId] = useState("")
    const [filterMinIdle, setFilterMinIdle] = useState(0)
    const [searchText, setSearchText] = useState("")
    const [filtersOpen, setFiltersOpen] = useState(false)

    // ── Data ──
    const [analytics, setAnalytics] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState("")
    const [lastUpdated, setLastUpdated] = useState(null)

    // ── UI state ──
    const [selectedGroupId, setSelectedGroupId] = useState(null)
    const [drillVehicleId, setDrillVehicleId] = useState(null)
    const [mapEvent, setMapEvent] = useState(null)
    const [sortBy, setSortBy] = useState("start_time")
    const [sortDir, setSortDir] = useState("desc")

    // ── Load analytics ──
    const load = useCallback(async () => {
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
            // Pass server-side group/vehicle filter only when drilling
            if (filterGroupId) opts.group_id = Number(filterGroupId)
            if (filterVehicleId) opts.vehicle_id = Number(filterVehicleId)
            const data = await getFleetIdleAnalytics(opts)
            setAnalytics(data)
            setLastUpdated(new Date())
        } catch (err) {
            console.error(err)
            setError(err.message || "Failed to load idle analytics. Please try again.")
        } finally {
            setLoading(false)
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
                const vName = (e.vehicle_name || "").toLowerCase()
                const vId = String(e.vehicle_id || "")
                const gName = (e.group_name || "").toLowerCase()
                return vName.includes(q) || vId.includes(q) || gName.includes(q)
            })
        }
        // Sort
        evs = [...evs].sort((a, b) => {
            let aVal = a[sortBy] ?? 0
            let bVal = b[sortBy] ?? 0
            if (sortDir === "asc") return aVal > bVal ? 1 : -1
            return aVal < bVal ? 1 : -1
        })
        return evs
    }, [allEvents, drillVehicleId, filterVehicleId, filterMinIdle, searchText, sortBy, sortDir])

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
                value: Math.round(v.idle_minutes),
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
                value: Math.round(g.idle_minutes),
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
        setHours(24)
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
        <SidebarProvider>
        <div className="flex min-h-screen bg-white">
            <Sidebar />
            <div className="flex min-w-0 flex-1 flex-col bg-white">
                <main className="flex-1 space-y-6 p-6 lg:p-8 bg-white">

                    {/* ── Header ── */}
                    <section className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between border-b border-slate-200 pb-5">
                        <div>
                            <div className="flex items-center gap-2">
                                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#33758F]/10">
                                    <Clock3 className="h-5 w-5 text-[#33758F]" />
                                </div>
                                <h1 className="text-xl font-bold text-slate-900">Idle Analytics</h1>
                            </div>
                            <p className="mt-1 text-xs text-slate-500">
                                Fleet-wide idling analysis, customer groups & location hotspots
                            </p>
                        </div>

                        <div className="flex flex-wrap items-center gap-2">
                            {/* Time Range Switcher */}
                            <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 p-1 shadow-2xs">
                                {["preset", "custom"].map((mode) => (
                                    <button
                                        key={mode}
                                        type="button"
                                        onClick={() => setRangeMode(mode)}
                                        className={`rounded-lg px-3 py-1.5 text-xs font-bold capitalize transition cursor-pointer ${rangeMode === mode ? "bg-[#33758F] text-white shadow-2xs" : "text-slate-600 hover:text-slate-900"}`}
                                    >
                                        {mode === "preset" ? "Presets" : "Custom Range"}
                                    </button>
                                ))}
                            </div>

                            {rangeMode === "preset" ? (
                                <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-1.5 shadow-2xs">
                                    <Timer className="h-4 w-4 text-slate-400" />
                                    <select
                                        value={selectedTimeframe === "today" ? "today" : hours}
                                        onChange={(e) => {
                                            const val = e.target.value
                                            if (val === "today") {
                                                setSelectedTimeframe("today")
                                            } else {
                                                setSelectedTimeframe("preset")
                                                setHours(Number(val))
                                            }
                                        }}
                                        className="bg-transparent text-xs font-bold text-slate-800 outline-none cursor-pointer"
                                    >
                                        {PRESET_OPTIONS.map((o) => (
                                            <option key={o.timeframe || o.hours} value={o.timeframe || o.hours}>{o.label}</option>
                                        ))}
                                    </select>
                                </div>
                            ) : (
                                <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-1.5 shadow-2xs">
                                    <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="bg-transparent text-xs font-semibold text-slate-800 outline-none" />
                                    <span className="text-xs text-slate-400">to</span>
                                    <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="bg-transparent text-xs font-semibold text-slate-800 outline-none" />
                                </div>
                            )}

                            {/* Filters toggle */}
                            <button
                                type="button"
                                onClick={() => setFiltersOpen(!filtersOpen)}
                                className={`inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-bold shadow-2xs transition cursor-pointer ${filtersOpen || activeFilterCount > 0 ? "border-[#33758F] bg-[#33758F]/10 text-[#33758F]" : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"}`}
                            >
                                <SlidersHorizontal className="h-3.5 w-3.5" />
                                Filters
                                {activeFilterCount > 0 && (
                                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-[#33758F] text-[9px] font-bold text-white">
                                        {activeFilterCount}
                                    </span>
                                )}
                            </button>

                            {/* Export CSV */}
                            <button
                                type="button"
                                onClick={() => exportToCSV(filteredEvents)}
                                disabled={filteredEvents.length === 0}
                                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 shadow-2xs transition hover:bg-slate-50 cursor-pointer disabled:opacity-40"
                            >
                                <Download className="h-3.5 w-3.5" />
                                Export CSV
                            </button>

                            {/* Refresh */}
                            <button
                                type="button"
                                onClick={load}
                                disabled={loading}
                                className="inline-flex items-center gap-1.5 rounded-xl bg-[#33758F] hover:bg-[#285E73] text-white px-3.5 py-2 text-xs font-bold shadow-xs transition cursor-pointer disabled:opacity-50"
                            >
                                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                                {loading ? "Loading…" : "Refresh"}
                            </button>
                        </div>
                    </section>

                    {/* ── Expanded Filters Panel ── */}
                    {filtersOpen && (
                        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                            <div className="mb-4 flex items-center justify-between">
                                <h3 className="text-sm font-semibold text-slate-900">Filters</h3>
                                <button
                                    type="button"
                                    onClick={resetFilters}
                                    className="inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-900"
                                >
                                    <X className="h-3 w-3" />
                                    Reset all
                                </button>
                            </div>
                            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                                {/* Group filter */}
                                <div>
                                    <label className="mb-1.5 block text-xs font-medium text-slate-600">Customer Group</label>
                                    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                        <Layers className="h-3.5 w-3.5 text-slate-400" />
                                        <select
                                            value={filterGroupId}
                                            onChange={(e) => { setFilterGroupId(e.target.value); setFilterVehicleId("") }}
                                            className="w-full bg-transparent text-xs text-slate-800 outline-none"
                                        >
                                            <option value="">All Groups</option>
                                            {groups.map((g) => (
                                                <option key={g.id} value={g.id}>{g.name}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>

                                {/* Vehicle filter */}
                                <div>
                                    <label className="mb-1.5 block text-xs font-medium text-slate-600">Vehicle</label>
                                    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                        <Truck className="h-3.5 w-3.5 text-slate-400" />
                                        <select
                                            value={filterVehicleId}
                                            onChange={(e) => setFilterVehicleId(e.target.value)}
                                            className="w-full bg-transparent text-xs text-slate-800 outline-none"
                                        >
                                            <option value="">All Vehicles</option>
                                            {byVehicle
                                                .filter((v) => !filterGroupId || String(v.group_id) === String(filterGroupId))
                                                .map((v) => (
                                                    <option key={v.vehicle_id} value={v.vehicle_id}>
                                                        {v.vehicle_name}
                                                    </option>
                                                ))}
                                        </select>
                                    </div>
                                </div>

                                {/* Min idle duration */}
                                <div>
                                    <label className="mb-1.5 block text-xs font-medium text-slate-600">Min Idle Duration</label>
                                    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                                        <Timer className="h-3.5 w-3.5 text-slate-400" />
                                        <select
                                            value={filterMinIdle}
                                            onChange={(e) => setFilterMinIdle(Number(e.target.value))}
                                            className="w-full bg-transparent text-xs text-slate-800 outline-none"
                                        >
                                            {MIN_IDLE_OPTIONS.map((o) => (
                                                <option key={o.value} value={o.value}>{o.label}</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>

                                {/* Search */}
                                <div>
                                    <label className="mb-1.5 block text-xs font-medium text-slate-600">Search Vehicle / Group</label>
                                    <div className="relative flex items-center">
                                        <Search className="pointer-events-none absolute left-3 h-3.5 w-3.5 text-slate-400" />
                                        <input
                                            type="text"
                                            placeholder="Search vehicle number, ID or group…"
                                            value={searchText}
                                            onChange={(e) => setSearchText(e.target.value)}
                                            className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-7 text-xs text-slate-800 outline-none placeholder:text-slate-400 focus:border-[#33758F] focus:bg-white"
                                        />
                                        {searchText && (
                                            <button
                                                type="button"
                                                onClick={() => setSearchText("")}
                                                className="absolute right-2.5 rounded-full p-0.5 text-slate-400 hover:text-slate-700"
                                                title="Clear search"
                                            >
                                                <X className="h-3.5 w-3.5" />
                                            </button>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </section>
                    )}

                    {/* ── Error ── */}
                    {error && (
                        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                            {error}
                        </div>
                    )}

                    {/* ── Loading ── */}
                    {loading && !analytics ? (
                        <div className="rounded-xl border border-slate-200 bg-white px-6 py-20 text-center shadow-sm">
                            <Clock3 className="mx-auto h-8 w-8 animate-pulse text-slate-300" />
                            <p className="mt-3 text-sm font-medium text-slate-700">Loading idle analytics…</p>
                            <p className="mt-1 text-xs text-slate-400">Analyzing fleet history across all vehicles</p>
                        </div>
                    ) : analytics ? (
                        <>
                            {/* ── KPI Cards ── */}
                            <section className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
                                <KpiCard
                                    icon={Truck}
                                    iconBg="bg-blue-50"
                                    iconColor="text-blue-500"
                                    label="Total Vehicles"
                                    value={kpis.total_vehicles ?? 0}
                                />
                                <KpiCard
                                    icon={Clock3}
                                    iconBg="bg-orange-50"
                                    iconColor="text-orange-500"
                                    label="Currently Idling"
                                    value={kpis.currently_idling_vehicles ?? 0}
                                    sub={kpis.idling_fleet_ratio != null ? `${kpis.idling_fleet_ratio}% of fleet` : undefined}
                                />
                                <KpiCard
                                    icon={BarChart3}
                                    iconBg="bg-purple-50"
                                    iconColor="text-purple-500"
                                    label="Idle Events"
                                    value={kpis.idle_event_count ?? 0}
                                />
                                <KpiCard
                                    icon={Timer}
                                    iconBg="bg-teal-50"
                                    iconColor="text-teal-500"
                                    label="Total Idle Time"
                                    value={formatDuration(kpis.total_idle_seconds)}
                                />
                                <KpiCard
                                    icon={TrendingUp}
                                    iconBg="bg-indigo-50"
                                    iconColor="text-indigo-500"
                                    label="Avg Idle Duration"
                                    value={formatDuration(kpis.average_idle_seconds)}
                                />
                                <KpiCard
                                    icon={AlertTriangle}
                                    iconBg="bg-red-50"
                                    iconColor="text-red-500"
                                    label="Longest Idle"
                                    value={formatDuration(kpis.longest_idle_seconds)}
                                />
                            </section>

                            {/* ── Customer Group Cards ── */}
                            {byGroup.length > 0 && (
                                <section>
                                    <div className="mb-3 flex items-center gap-2">
                                        <Users className="h-4 w-4 text-slate-500" />
                                        <h2 className="text-sm font-semibold text-slate-900">Customer Vehicle Groups</h2>
                                        <span className="text-xs text-slate-400">— click to drill down</span>
                                    </div>
                                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
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
                                                    className={`rounded-xl border p-5 text-left shadow-sm transition ${isSelected ? "border-[#33758F] bg-[#33758F]/5 ring-2 ring-[#33758F]/20" : "border-slate-200 bg-white hover:border-slate-300"}`}
                                                >
                                                    <div className="flex items-start justify-between">
                                                        <div>
                                                            <p className="text-sm font-semibold text-slate-900">{g.group_name}</p>
                                                            <p className="mt-0.5 text-xs text-slate-400">{g.vehicle_count} vehicles</p>
                                                        </div>
                                                        {g.currently_idling > 0 && (
                                                            <span className="flex items-center gap-1 rounded-full bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
                                                                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-orange-500" />
                                                                {g.currently_idling} idling
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div className="mt-4 grid grid-cols-3 gap-2">
                                                        <div>
                                                            <p className="text-[10px] text-slate-400">Total Idle</p>
                                                            <p className="text-sm font-semibold text-slate-800">{formatDuration(g.total_idle_seconds)}</p>
                                                        </div>
                                                        <div>
                                                            <p className="text-[10px] text-slate-400">Events</p>
                                                            <p className="text-sm font-semibold text-slate-800">{g.idle_events}</p>
                                                        </div>
                                                        <div>
                                                            <p className="text-[10px] text-slate-400">Avg / Event</p>
                                                            <p className="text-sm font-semibold text-slate-800">{g.average_idle_minutes}m</p>
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
                                <section className="rounded-xl border border-[#33758F]/30 bg-[#33758F]/5 p-5 shadow-sm">
                                    <div className="mb-4 flex items-center justify-between">
                                        <div className="flex items-center gap-2">
                                            <ChevronRight className="h-4 w-4 text-[#33758F]" />
                                            <h3 className="text-sm font-semibold text-slate-900">{selectedGroupData.group_name} — Vehicles</h3>
                                        </div>
                                        <button type="button" onClick={() => setSelectedGroupId(null)} className="text-xs text-slate-500 hover:text-slate-900">
                                            <X className="h-4 w-4" />
                                        </button>
                                    </div>
                                    <div className="space-y-2">
                                        {selectedGroupData.vehicles.map((v) => (
                                            <div
                                                key={v.vehicle_id}
                                                className={`flex items-center justify-between rounded-lg border px-4 py-3 transition ${drillVehicleId === v.vehicle_id ? "border-[#33758F] bg-white" : "border-slate-200 bg-white/70 hover:bg-white"}`}
                                                role="button"
                                                onClick={() => setDrillVehicleId(drillVehicleId === v.vehicle_id ? null : v.vehicle_id)}
                                            >
                                                <div className="flex items-center gap-3">
                                                    <Truck className="h-4 w-4 text-slate-400" />
                                                    <div>
                                                        <p className="text-sm font-medium text-slate-900">{v.vehicle_name}</p>
                                                        <p className="text-xs text-slate-400">ID {v.vehicle_id}</p>
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-6 text-right">
                                                    <div>
                                                        <p className="text-xs text-slate-400">Idle Time</p>
                                                        <p className="text-sm font-semibold text-slate-800">{formatDuration(v.total_idle_seconds)}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-xs text-slate-400">Events</p>
                                                        <p className="text-sm font-semibold text-slate-800">{v.idle_events}</p>
                                                    </div>
                                                    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium ${v.status === "MOVING" ? "bg-green-50 text-green-700" : v.status === "IDLING" ? "bg-orange-50 text-orange-700" : v.status === "OFFLINE" ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-600"}`}>
                                                        {v.status}
                                                    </span>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </section>
                            )}

                            {/* ── Charts Row 1: Trend + Group ── */}
                            <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                                <AnalyticsChart
                                    title="Idle Duration Trend"
                                    subtitle="Total idle minutes per time bucket"
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
                                    subtitle="Total idle minutes per dynamic customer group"
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
                            <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                                <AnalyticsChart
                                    title="Top Idle Vehicles"
                                    subtitle="Idle minutes per vehicle (click bar to filter vehicle events)"
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
                                        subtitle="Total idle minutes by coordinate cluster (top 10)"
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
                            <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
                                <div className="flex flex-col gap-3 border-b border-slate-200 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
                                    <div>
                                        <h2 className="text-sm font-semibold text-slate-900">Idle Events</h2>
                                        <p className="mt-0.5 text-xs text-slate-500">
                                            {filteredEvents.length} events{filterMinIdle > 0 ? ` · min ${formatDuration(filterMinIdle)}` : ""}{searchText ? ` · "${searchText}"` : ""}
                                        </p>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        <div className="relative flex items-center">
                                            <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-slate-400" />
                                            <input
                                                type="text"
                                                placeholder="Filter vehicle number…"
                                                value={searchText}
                                                onChange={(e) => setSearchText(e.target.value)}
                                                className="w-44 rounded-lg border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-7 text-xs text-slate-800 outline-none placeholder:text-slate-400 focus:border-[#33758F] focus:bg-white"
                                            />
                                            {searchText && (
                                                <button
                                                    type="button"
                                                    onClick={() => setSearchText("")}
                                                    className="absolute right-2 rounded-full p-0.5 text-slate-400 hover:text-slate-700"
                                                    title="Clear search"
                                                >
                                                    <X className="h-3 w-3" />
                                                </button>
                                            )}
                                        </div>
                                        {lastUpdated && (
                                            <span className="hidden text-xs text-slate-400 sm:inline">Updated {formatUAEDateTime(lastUpdated)}</span>
                                        )}
                                    </div>
                                </div>

                                {/* Active Search / Filter Banner */}
                                {activeSearchStats && (
                                    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-[#33758F]/5 px-5 py-2.5 text-xs text-[#33758F]">
                                        <div className="flex items-center gap-2">
                                            <Truck className="h-4 w-4 shrink-0" />
                                            <span className="font-semibold">
                                                {searchText ? `Vehicle Search: "${searchText}"` : "Filtered Vehicle"}
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
                                            className="inline-flex items-center gap-1 font-semibold text-[#33758F] hover:underline"
                                        >
                                            <X className="h-3.5 w-3.5" />
                                            Clear vehicle filter
                                        </button>
                                    </div>
                                )}

                                {filteredEvents.length === 0 ? (
                                    <div className="px-5 py-16 text-center">
                                        <CheckCircle2 className="mx-auto h-8 w-8 text-green-400" />
                                        <p className="mt-3 text-sm font-medium text-slate-700">No idle events match your filters</p>
                                        <button type="button" onClick={resetFilters} className="mt-2 text-xs text-[#33758F] underline">Reset filters</button>
                                    </div>
                                ) : (
                                    <div className="overflow-x-auto">
                                        <table className="w-full min-w-[960px]">
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
                                                            onClick={col ? () => handleSort(col) : undefined}
                                                            className={`px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400 ${col ? "cursor-pointer select-none hover:text-slate-600" : ""}`}
                                                        >
                                                            {label}
                                                            {col && <SortIcon col={col} />}
                                                        </th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-slate-100">
                                                {filteredEvents.slice(0, 100).map((ev, idx) => {
                                                    const hasLoc = ev.location?.latitude != null && ev.location?.longitude != null
                                                    return (
                                                        <tr key={ev.id || idx} className="transition hover:bg-slate-50">
                                                            <td className="px-5 py-3.5">
                                                                <button
                                                                    type="button"
                                                                    onClick={() => setSearchText(ev.vehicle_name)}
                                                                    className="group flex items-center gap-2 text-left"
                                                                    title="Click to view all idle events for this vehicle"
                                                                >
                                                                    <Truck className="h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-[#33758F]" />
                                                                    <span className="text-sm font-medium text-slate-800 transition group-hover:text-[#33758F] group-hover:underline">
                                                                        {ev.vehicle_name}
                                                                    </span>
                                                                </button>
                                                            </td>
                                                            <td className="px-5 py-3.5 text-sm text-slate-600">{ev.group_name || "—"}</td>
                                                            <td className="px-5 py-3.5 text-sm text-slate-600">{formatDateTime(ev.start_time)}</td>
                                                            <td className="px-5 py-3.5 text-sm text-slate-600">{formatDateTime(ev.end_time)}</td>
                                                            <td className="px-5 py-3.5">
                                                                <span className="text-sm font-semibold text-slate-800">{formatDuration(ev.duration)}</span>
                                                            </td>
                                                                                                                  <td className="px-5 py-3.5">
                                                                {hasLoc ? (
                                                                    <div className="flex items-center gap-1.5">
                                                                        <button
                                                                            type="button"
                                                                            onClick={() => setMapEvent(ev)}
                                                                            className="group inline-flex w-[260px] items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-800 shadow-sm transition hover:border-[#33758F] hover:bg-slate-50 text-center"
                                                                            title="View on Interactive Map"
                                                                        >
                                                                            <MapPin className="h-3.5 w-3.5 shrink-0 text-amber-500 transition-transform group-hover:scale-110" />
                                                                            <span className="truncate text-center">
                                                                                <LocationName lat={ev.location.latitude} lng={ev.location.longitude} />
                                                                            </span>
                                                                        </button>
                                                                        <a
                                                                            href={`https://www.google.com/maps?q=${ev.location.latitude},${ev.location.longitude}`}
                                                                            target="_blank"
                                                                            rel="noopener noreferrer"
                                                                            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                                                            title="Open in Google Maps"
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
                                        {filteredEvents.length > 100 && (
                                            <div className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
                                                Showing first 100 of {filteredEvents.length} events. Use Export CSV to get all records.
                                            </div>
                                        )}
                                    </div>
                                )}
                            </section>

                            {/* ── Location Hotspots Detail ── */}
                            {byLocation.length > 0 && (
                                <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
                                    <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
                                        <div>
                                            <h2 className="text-sm font-semibold text-slate-900">Location Hotspots</h2>
                                            <p className="mt-0.5 text-xs text-slate-500">Top 10 most frequent idle locations</p>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-1 divide-y divide-slate-100">
                                        {byLocation.map((loc, idx) => (
                                            <div key={loc.location_key} className="flex items-center justify-between px-5 py-3.5 transition hover:bg-slate-50">
                                                <div className="flex items-center gap-4">
                                                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-50">
                                                        <span className="text-xs font-bold text-amber-600">#{idx + 1}</span>
                                                    </div>
                                                    <div>
                                                        <div className="flex items-center gap-2">
                                                            <button
                                                                type="button"
                                                                onClick={() => setMapEvent(loc)}
                                                                className="group inline-flex w-[260px] items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-800 shadow-sm transition hover:border-[#33758F] hover:bg-slate-50 text-center"
                                                                title="View on Interactive Map"
                                                            >
                                                                <MapPin className="h-3.5 w-3.5 shrink-0 text-amber-500 transition-transform group-hover:scale-110" />
                                                                <span className="truncate text-center">
                                                                    <LocationName lat={loc.latitude} lng={loc.longitude} />
                                                                </span>
                                                            </button>
                                                            <a
                                                                href={`https://www.google.com/maps?q=${loc.latitude},${loc.longitude}`}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                                                title="Open in Google Maps"
                                                            >
                                                                <ArrowUpRight className="h-3.5 w-3.5" />
                                                            </a>
                                                        </div>
                                                        <p className="mt-0.5 text-xs text-slate-400">
                                                            {loc.vehicle_count} vehicle{loc.vehicle_count !== 1 ? "s" : ""} · {loc.event_count} event{loc.event_count !== 1 ? "s" : ""}
                                                        </p>
                                                    </div>
                                                </div>
                                                <div className="text-right">
                                                    <p className="text-sm font-semibold text-slate-800">{formatDuration(loc.total_idle_seconds)}</p>
                                                    <p className="text-xs text-slate-400">total idle</p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </section>
                            )}
                        </>
                    ) : null}
                </main>
            </div>
            {mapEvent && <IdleMapModal event={mapEvent} onClose={() => setMapEvent(null)} />}
        </div>
        </SidebarProvider>
    )
}
