import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Truck,
    X,
    Search,
} from "lucide-react"

import { getVehicles } from "../api/fleetApi"
import FleetMap from "../components/FleetMap"
import { isValidCoordinate } from "../utils/coordinates"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import PageHeader from "../components/Common/PageHeader"
import { EmptyState, ErrorBanner, LoadingState } from "../components/Common/StateViews"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import { LIVE_STATUSES, countByStatus, formatAge, isPositionStale, normalizeVehicleStatus } from "../utils/vehicleStatus"

function formatLastUpdate(vehicle) {
    // A recent message with an old GPS fix: show the fix age, never "Just now"
    if (isPositionStale(vehicle)) {
        const fixAge = formatAge(vehicle.seconds_since_position)
        return fixAge ? `GPS fix ${fixAge} ago` : "GPS fix stale"
    }
    if (typeof vehicle.seconds_since_update === "number" && !isNaN(vehicle.seconds_since_update)) {
        const secs = vehicle.seconds_since_update
        if (secs < 60) return "Just now"
        if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
        if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
        return `${Math.floor(secs / 86400)}d ago`
    }
    if (vehicle.last_message_timestamp) {
        const diffSecs = Math.max(0, Math.floor(Date.now() / 1000 - Number(vehicle.last_message_timestamp)))
        if (diffSecs < 60) return "Just now"
        if (diffSecs < 3600) return `${Math.floor(diffSecs / 60)}m ago`
        if (diffSecs < 86400) return `${Math.floor(diffSecs / 3600)}h ago`
        return `${Math.floor(diffSecs / 86400)}d ago`
    }
    return null
}

function Tracking() {
    const [searchParams, setSearchParams] = useSearchParams()

    const [vehicles, setVehicles] = useState([])
    const [isPanelOpen, setIsPanelOpen] = useState(true)

    const [selectedVehicleId, setSelectedVehicleId] = useState(() => {
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (!param) return null
        const numeric = Number(param)
        return Number.isNaN(numeric) ? param : numeric
    })

    const [isDrawerOpen, setIsDrawerOpen] = useState(() => {
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        return Boolean(param)
    })

    const [statusFilter, setStatusFilter] = useState(() => {
        const param = searchParams.get("status")
        if (param && ["ALL", ...LIVE_STATUSES].includes(param.toUpperCase())) {
            return param.toUpperCase()
        }
        return "ALL"
    })

    const [searchTerm, setSearchTerm] = useState("")
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState(null)
    const [lastSyncTime, setLastSyncTime] = useState(null)

    // Synchronize query parameters without re-opening a closed drawer. Adjusted during render (React's pattern for
    // state derived from changing input), not in an effect, so a URL change never renders the stale state first.
    const [lastVehicleParam, setLastVehicleParam] = useState(() => searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v"))
    const [lastParamsKey, setLastParamsKey] = useState(() => searchParams.toString())
    const paramsKey = searchParams.toString()
    if (paramsKey !== lastParamsKey) {
        setLastParamsKey(paramsKey)
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (param && param !== lastVehicleParam) {
            setLastVehicleParam(param)
            const numeric = Number(param)
            setSelectedVehicleId(Number.isNaN(numeric) ? param : numeric)
            setIsDrawerOpen(true)
        } else if (!param && lastVehicleParam) {
            setLastVehicleParam(null)
        }
        const sParam = searchParams.get("status")
        if (sParam && ["ALL", ...LIVE_STATUSES].includes(sParam.toUpperCase())) {
            setStatusFilter(sParam.toUpperCase())
        }
    }

    /*
        Load fleet vehicles
    */
    const loadVehicles = useCallback(async (isManual = false) => {
        if (isManual) setRefreshing(true)
        try {
            const data = await getVehicles()
            setVehicles(data?.vehicles || [])
            setError(null)
            setLastSyncTime(new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }))
        } catch (err) {
            setError(err.message || "Failed to load vehicle telemetry")
        } finally {
            setLoading(false)
            if (isManual) setRefreshing(false)
        }
    }, [])

    useEffect(() => {
        let mounted = true
        let timerId = null
        let isFetching = false

        const fetchTelemetry = async () => {
            if (isFetching || !mounted) return
            // Polling pauses while the tab is hidden and resumes from the visibilitychange listener.
            if (document.hidden) return
            isFetching = true
            try {
                const data = await getVehicles()
                if (mounted) {
                    setVehicles(data?.vehicles || [])
                    setError(null)
                    setLastSyncTime(new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }))
                }
            } catch (err) {
                if (mounted) {
                    setError(err.message || "Failed to load vehicle telemetry")
                }
            } finally {
                isFetching = false
                if (mounted) {
                    setLoading(false)
                    timerId = setTimeout(fetchTelemetry, 10000)
                }
            }
        }

        fetchTelemetry()
        const onVisibilityChange = () => {
            if (document.hidden || !mounted) return
            if (timerId) clearTimeout(timerId)
            fetchTelemetry()
        }
        document.addEventListener("visibilitychange", onVisibilityChange)

        return () => {
            document.removeEventListener("visibilitychange", onVisibilityChange)
            mounted = false
            if (timerId) clearTimeout(timerId)
        }
    }, [])

    /*
        Keep selected vehicle synchronized with live refresh
    */
    const selectedVehicle = useMemo(() => {
        if (!selectedVehicleId) return null
        return (
            vehicles.find(
                vehicle =>
                    String(vehicle.vehicle_id || vehicle.id) ===
                    String(selectedVehicleId)
            ) || null
        )
    }, [vehicles, selectedVehicleId])

    // Keep cached vehicle telemetry so drawer and focus transitions are smooth during polling
    const [cachedVehicle, setCachedVehicle] = useState(null)
    if (selectedVehicle && selectedVehicle !== cachedVehicle) {
        setCachedVehicle(selectedVehicle)
    }
    const activeVehicle = selectedVehicle || cachedVehicle

    /*
        Filter vehicles
    */
    const filteredVehicles = useMemo(() => {
        const query = searchTerm.trim().toLowerCase()
        return vehicles.filter(
            vehicle => {
                const plateStr = String(vehicle.registration_plate || vehicle.plate || "").toLowerCase()
                const name = String(vehicle.vehicle_name || vehicle.unit_name || "").toLowerCase()
                const idStr = String(vehicle.vehicle_id || vehicle.id || "").toLowerCase()
                const groupStr = String(vehicle.group || (Array.isArray(vehicle.groups) ? vehicle.groups.join(" ") : "")).toLowerCase()
                const matchesSearch = !query || plateStr.includes(query) || name.includes(query) || idStr.includes(query) || groupStr.includes(query)

                const normalizedStatus = normalizeVehicleStatus(vehicle)

                const matchesStatus =
                    statusFilter === "ALL" || normalizedStatus === statusFilter

                return matchesSearch && matchesStatus
            }
        )
    }, [vehicles, searchTerm, statusFilter])

    /*
        Vehicles with coordinates plotted on map
    */
    const mapVehicles = useMemo(() => {
        return filteredVehicles.filter(vehicle =>
            isValidCoordinate(vehicle?.latitude, vehicle?.longitude)
        )
    }, [filteredVehicles])

    /*
        Status counts: clean mutually exclusive partitions
    */
    const counts = useMemo(() => countByStatus(vehicles), [vehicles])

    /*
        Select vehicle handler - highlights marker, focuses map, and opens single details drawer
    */
    const selectVehicle = useCallback((vehicle) => {
        if (!vehicle) {
            setSelectedVehicleId(null)
            setIsDrawerOpen(false)
            window.dispatchEvent(new CustomEvent("ats:drawer-change", { detail: { open: false } }))
            setSearchParams(prev => {
                const next = new URLSearchParams(prev)
                next.delete("vehicleId")
                next.delete("vehicle")
                next.delete("v")
                return next
            }, { replace: true })
            return
        }
        const vid = vehicle.vehicle_id || vehicle.id
        setSelectedVehicleId(vid)
        setIsDrawerOpen(true)
        window.dispatchEvent(new CustomEvent("ats:drawer-change", { detail: { open: true } }))
        setSearchParams(prev => {
            const next = new URLSearchParams(prev)
            next.set("vehicleId", String(vid))
            return next
        }, { replace: true })
    }, [setSearchParams])

    /*
        Close details drawer handler - closes drawer without clearing selected vehicle on map
    */
    const handleCloseDrawer = useCallback(() => {
        setIsDrawerOpen(false)
        window.dispatchEvent(new CustomEvent("ats:drawer-change", { detail: { open: false } }))
    }, [])

    const handleFilterChange = (filterKey) => {
        setStatusFilter(filterKey)
        setSearchParams(prev => {
            const next = new URLSearchParams(prev)
            if (filterKey === "ALL") {
                next.delete("status")
            } else {
                next.set("status", filterKey)
            }
            return next
        }, { replace: true })
    }

    const statusTabs = [
        { key: "ALL", label: "All", count: counts.ALL, dot: null },
        { key: "MOVING", label: "Moving", count: counts.MOVING, dot: "bg-status-moving" },
        { key: "IDLING", label: "Idling", count: counts.IDLING, dot: "bg-status-idling" },
        { key: "STOPPED", label: "Stopped", count: counts.STOPPED, dot: "bg-status-stopped" },
        { key: "DELAYED", label: "Delayed", count: counts.DELAYED, dot: "bg-status-delayed" },
        { key: "OFFLINE", label: "Offline", count: counts.OFFLINE, dot: "bg-status-offline" },
    ]

    return (
        <SidebarProvider>
            <div className="flex h-dvh overflow-hidden bg-canvas font-sans text-slate-900 antialiased">
                <Sidebar />

                <div className="relative z-0 flex min-w-0 flex-1 flex-col overflow-hidden">
                    {/* Header docked at top */}
                    <div className="shrink-0 border-b border-slate-200/80 bg-white/95 backdrop-blur-md px-4 py-2.5 sm:px-6 z-10">
                        <PageHeader
                            title="Live Tracking"
                            subtitle="Real-time vehicle location and status"
                            onRefresh={() => loadVehicles(true)}
                            loading={refreshing}
                            lastUpdated={lastSyncTime}
                            className="mb-0 pb-0 border-b-0"
                        >
                            <div className="hidden md:flex items-center gap-2 text-xs text-slate-600 bg-slate-100/90 rounded-lg px-2.5 py-1 font-medium">
                                <span className="font-semibold text-slate-900 tabular-nums">{vehicles.length}</span>
                                <span>total units</span>
                            </div>
                        </PageHeader>
                    </div>

                    {/* Edge-to-Edge Map Viewport */}
                    <main className="relative flex-1 min-h-0 w-full overflow-hidden">
                        {/* 1. Primary Full-Bleed Map */}
                        <FleetMap
                            vehicles={mapVehicles}
                            selectedVehicleId={selectedVehicleId}
                            selectedVehicle={activeVehicle}
                            onVehicleSelect={selectVehicle}
                            isPanelOpen={isPanelOpen}
                            isDrawerOpen={isDrawerOpen && Boolean(activeVehicle)}
                            className="absolute inset-0 h-full w-full"
                        />

                        {/* 2. Floating Reopen Button (when panel is collapsed) */}
                        <div
                            className={`absolute left-3 top-3 sm:left-4 sm:top-4 z-20 transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                                isPanelOpen
                                    ? "-translate-x-12 opacity-0 pointer-events-none"
                                    : "translate-x-0 opacity-100 pointer-events-auto"
                            }`}
                        >
                            <button
                                type="button"
                                onClick={() => setIsPanelOpen(true)}
                                aria-label="Open vehicle panel"
                                title="Open vehicle list"
                                className="group inline-flex items-center gap-2.5 rounded-xl border border-slate-200/90 bg-white/95 px-3.5 py-2.5 text-xs font-semibold text-slate-800 shadow-[0_8px_24px_-4px_rgba(15,23,42,0.18)] backdrop-blur-md transition-all hover:bg-white hover:text-brand hover:border-brand/40 hover:shadow-xl cursor-pointer"
                            >
                                <Truck className="h-4 w-4 text-brand transition-transform group-hover:scale-110" />
                                <span>Fleet Units</span>
                                <span className="inline-flex items-center justify-center rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-700 tabular-nums group-hover:bg-brand-light group-hover:text-brand">
                                    {filteredVehicles.length}
                                </span>
                            </button>
                        </div>

                        {/* 3. Floating Vehicle Panel */}
                        <aside
                            aria-label="Fleet vehicle control panel"
                            className={`absolute left-3 top-3 bottom-3 sm:left-4 sm:top-4 sm:bottom-4 z-20 flex flex-col w-[320px] sm:w-[340px] max-w-[calc(100vw-1.5rem)] rounded-2xl border border-slate-200/85 bg-white/95 backdrop-blur-md shadow-[0_12px_36px_-6px_rgba(15,23,42,0.18),0_4px_12px_-2px_rgba(15,23,42,0.06)] overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                                isPanelOpen
                                    ? "translate-x-0 opacity-100 pointer-events-auto"
                                    : "-translate-x-[calc(100%+2rem)] opacity-0 pointer-events-none"
                            }`}
                        >
                            {/* Panel Header */}
                            <div className="shrink-0 border-b border-slate-100 px-3 py-2.5">
                                <div className="flex items-center justify-between gap-2">
                                    <div className="flex items-center gap-2 min-w-0">
                                        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-light text-brand shrink-0">
                                            <Truck className="h-4 w-4" />
                                        </div>
                                        <h2 className="font-jakarta text-[13px] font-bold text-slate-900 tracking-tight truncate">
                                            Fleet Units
                                        </h2>
                                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600 tabular-nums shrink-0">
                                            {filteredVehicles.length} of {vehicles.length}
                                        </span>
                                    </div>

                                    <div className="flex items-center gap-1">
                                        {selectedVehicle && (
                                            <button
                                                type="button"
                                                onClick={() => selectVehicle(null)}
                                                className="text-[11px] font-medium text-brand hover:underline underline-offset-2 cursor-pointer mr-1"
                                                title="Clear current selection"
                                            >
                                                Clear focus
                                            </button>
                                        )}
                                        <button
                                            type="button"
                                            onClick={() => setIsPanelOpen(false)}
                                            aria-label="Collapse vehicle panel"
                                            title="Collapse panel to view map"
                                            className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors cursor-pointer"
                                        >
                                            <X className="h-4 w-4" />
                                        </button>
                                    </div>
                                </div>

                                {/* Search Bar */}
                                <div className="relative mt-2">
                                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                                    <input
                                        type="text"
                                        value={searchTerm}
                                        onChange={(e) => setSearchTerm(e.target.value)}
                                        placeholder="Search plate, name, ID or group…"
                                        className="h-8 w-full rounded-lg border border-slate-200/90 bg-slate-50/80 pl-8 pr-7 text-xs text-slate-900 placeholder-slate-400 transition-colors focus:border-brand focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand/20"
                                    />
                                    {searchTerm && (
                                        <button
                                            type="button"
                                            onClick={() => setSearchTerm("")}
                                            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-700 cursor-pointer"
                                            title="Clear search"
                                        >
                                            <X className="h-3.5 w-3.5" />
                                        </button>
                                    )}
                                </div>

                                {/* Status Filters */}
                                <div role="group" aria-label="Filter by status" className="mt-2 grid grid-cols-5 gap-1 rounded-lg bg-slate-100/90 p-1">
                                    {statusTabs.map(item => {
                                        const isActive = statusFilter === item.key
                                        return (
                                            <button
                                                key={item.key}
                                                type="button"
                                                onClick={() => handleFilterChange(item.key)}
                                                aria-pressed={isActive}
                                                title={`${item.label}: ${item.count}`}
                                                className={`flex min-w-0 flex-col items-center rounded-md px-1 py-1 transition-all cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                                    isActive
                                                        ? "bg-white text-slate-900 shadow-[0_1px_3px_rgba(15,23,42,0.1)] font-semibold"
                                                        : "text-slate-500 hover:text-slate-900 font-medium"
                                                }`}
                                            >
                                                <span className="flex items-center gap-1 text-[11px]">
                                                    {item.dot && <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${item.dot}`} aria-hidden="true" />}
                                                    <span className="truncate">{item.label}</span>
                                                </span>
                                                <span className={`text-[12px] tabular-nums ${isActive ? "text-brand font-bold" : "text-slate-600"}`}>
                                                    {item.count}
                                                </span>
                                            </button>
                                        )
                                    })}
                                </div>
                            </div>

                            {/* Vehicle List */}
                            <div className="flex-1 divide-y divide-slate-100 overflow-y-auto overscroll-contain pr-0.5">
                                {loading && (
                                    <LoadingState label="Loading vehicle telemetry…" className="m-3 border-none" />
                                )}

                                {error && (
                                    <ErrorBanner
                                        message={vehicles.length > 0 ? `${error} Showing the last loaded data${lastSyncTime ? ` (${lastSyncTime})` : ""}.` : error}
                                        onRetry={() => loadVehicles(true)}
                                        className="m-3"
                                    />
                                )}

                                {!loading && !error && filteredVehicles.length === 0 && (
                                    <EmptyState
                                        icon={Truck}
                                        title="No vehicles match your filters"
                                        className="m-3 border-none"
                                        action={
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    setSearchTerm("")
                                                    handleFilterChange("ALL")
                                                }}
                                                className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                            >
                                                Clear filters
                                            </button>
                                        }
                                    />
                                )}

                                {!loading && (!error || vehicles.length > 0) && filteredVehicles.map(vehicle => {
                                    const vid = vehicle.vehicle_id || vehicle.id
                                    const isSelected = String(vid) === String(selectedVehicleId)
                                    const lastUpdatedText = formatLastUpdate(vehicle) || "Recent"
                                    const vehicleNumber = vehicle.vehicle_name || vehicle.unit_name || vehicle.vehicle_number || String(vid)
                                    const vehicleGroup = vehicle.group || (Array.isArray(vehicle.groups) && vehicle.groups[0]) || "Unassigned"

                                    return (
                                        <div
                                            key={vid}
                                            role="button"
                                            tabIndex={0}
                                            aria-pressed={isSelected}
                                            onClick={() => selectVehicle(vehicle)}
                                            onKeyDown={(e) => {
                                                if (e.target !== e.currentTarget) return
                                                if (e.key === "Enter" || e.key === " ") {
                                                    e.preventDefault()
                                                    selectVehicle(vehicle)
                                                }
                                            }}
                                            className={`group relative cursor-pointer select-none px-3.5 py-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40 ${
                                                isSelected
                                                    ? "bg-brand-light/70 before:absolute before:inset-y-0 before:left-0 before:w-1 before:bg-brand shadow-2xs"
                                                    : "hover:bg-slate-50/80"
                                            }`}
                                        >
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="min-w-0 truncate font-mono text-[13px] font-semibold text-slate-900">
                                                    {vehicleNumber}
                                                </span>
                                                <span className="shrink-0 text-[11px] text-slate-400 tabular-nums">
                                                    {lastUpdatedText}
                                                </span>
                                            </div>
                                            <div className="mt-1 flex items-center justify-between gap-2">
                                                <span className="min-w-0 truncate text-xs text-slate-500">
                                                    {vehicleGroup}
                                                </span>
                                                <button
                                                    type="button"
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        selectVehicle(vehicle)
                                                    }}
                                                    className="inline-flex shrink-0 items-center gap-0.5 text-xs font-medium text-brand group-hover:text-brand-hover hover:underline underline-offset-2 cursor-pointer"
                                                >
                                                    Details →
                                                </button>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        </aside>

                        {/* 4. Single Vehicle Details Drawer */}
                        {isDrawerOpen && activeVehicle && (
                            <VehicleDetailDrawer
                                vehicle={activeVehicle}
                                open={isDrawerOpen}
                                onClose={handleCloseDrawer}
                                asDrawer={true}
                            />
                        )}
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}

export default Tracking
