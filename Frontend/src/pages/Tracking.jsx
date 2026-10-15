import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Navigation,
    RefreshCw,
    AlertCircle,
    Truck,
    MapPin,
    Gauge,
    Power,
    Clock3,
    User,
    ChevronRight,
    Map,
    List,
    X,
    Radio,
    SlidersHorizontal,
} from "lucide-react"

import { getVehicles } from "../api/fleetApi"
import FleetMap, { isValidCoordinate } from "../components/FleetMap"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import PageHeader from "../components/Common/PageHeader"
import SearchInput from "../components/Common/SearchInput"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"

function Tracking() {
    const [searchParams, setSearchParams] = useSearchParams()

    const [vehicles, setVehicles] = useState([])

    const [selectedVehicleId, setSelectedVehicleId] = useState(() => {
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (!param) return null
        const numeric = Number(param)
        return Number.isNaN(numeric) ? param : numeric
    })

    const [statusFilter, setStatusFilter] = useState(() => {
        const param = searchParams.get("status")
        if (param && ["ALL", "MOVING", "IDLING", "STOPPED", "OFFLINE"].includes(param.toUpperCase())) {
            return param.toUpperCase()
        }
        return "ALL"
    })

    const [searchTerm, setSearchTerm] = useState("")
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState(null)
    const [lastSyncTime, setLastSyncTime] = useState(null)
    const [drawerVehicle, setDrawerVehicle] = useState(null)
    const [mobileTab, setMobileTab] = useState("map") // "map" | "list" on small screens

    // Synchronize query parameters
    useEffect(() => {
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (param) {
            const numeric = Number(param)
            setSelectedVehicleId(Number.isNaN(numeric) ? param : numeric)
        }
        const sParam = searchParams.get("status")
        if (sParam && ["ALL", "MOVING", "IDLING", "STOPPED", "OFFLINE"].includes(sParam.toUpperCase())) {
            setStatusFilter(sParam.toUpperCase())
        }
    }, [searchParams])

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

        return () => {
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

    // Update drawerVehicle if it is open and new telemetry arrives
    useEffect(() => {
        if (!drawerVehicle) return
        const fresh = vehicles.find(
            v => String(v.vehicle_id || v.id) === String(drawerVehicle.vehicle_id || drawerVehicle.id)
        )
        if (fresh) {
            setDrawerVehicle(fresh)
        }
    }, [vehicles, drawerVehicle])

    /*
        Filter vehicles
    */
    const filteredVehicles = useMemo(() => {
        const query = searchTerm.trim().toLowerCase()
        return vehicles.filter(
            vehicle => {
                const name = String(vehicle.vehicle_name || "").toLowerCase()
                const idStr = String(vehicle.vehicle_id || vehicle.id || "").toLowerCase()
                const groupStr = String(vehicle.group || "").toLowerCase()
                const matchesSearch = !query || name.includes(query) || idStr.includes(query) || groupStr.includes(query)

                const vStatus = String(vehicle.status || "").toUpperCase()
                const isOffline = vehicle.connection_status === "OFFLINE" || Boolean(vehicle.is_offline) || vStatus === "OFFLINE"
                const normalizedStatus = isOffline ? "OFFLINE" : (vStatus === "MOVING" ? "MOVING" : (vStatus === "IDLING" ? "IDLING" : "STOPPED"))

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
    const counts = useMemo(() => {
        let moving = 0, idling = 0, stopped = 0, offline = 0
        vehicles.forEach(v => {
            const isOff = v.connection_status === "OFFLINE" || Boolean(v.is_offline) || String(v.status || "").toUpperCase() === "OFFLINE"
            if (isOff) {
                offline++
            } else {
                const s = String(v.status || "").toUpperCase()
                if (s === "MOVING") moving++
                else if (s === "IDLING") idling++
                else stopped++
            }
        })
        return {
            ALL: vehicles.length,
            MOVING: moving,
            IDLING: idling,
            STOPPED: stopped,
            OFFLINE: offline,
        }
    }, [vehicles])

    /*
        Select vehicle handler
    */
    const selectVehicle = useCallback((vehicle) => {
        if (!vehicle) {
            setSelectedVehicleId(null)
            return
        }
        const vid = vehicle.vehicle_id || vehicle.id
        setSelectedVehicleId(vid)
        // If on mobile list view, switch to map to see location
        if (mobileTab === "list") {
            setMobileTab("map")
        }
    }, [mobileTab])

    const handleFilterChange = (filterKey) => {
        setStatusFilter(filterKey)
        // Update URL search param seamlessly
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

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-white text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white">
                    {/* Header Bar */}
                    <div className="shrink-0 border-b border-slate-200 bg-white px-6 py-4">
                        <PageHeader
                            title="Live Fleet Tracking"
                            subtitle="Real-time GPS telemetry, status monitoring, and asset location"
                            icon={Navigation}
                            onRefresh={() => loadVehicles(true)}
                            loading={refreshing}
                            lastUpdated={lastSyncTime ? `Updated ${lastSyncTime}` : null}
                            className="mb-0 pb-0 border-b-0"
                        >
                            {/* Mobile View Toggle */}
                            <div className="flex lg:hidden items-center rounded-lg border border-slate-200 bg-slate-50 p-1 text-xs">
                                <button
                                    type="button"
                                    onClick={() => setMobileTab("map")}
                                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1 font-bold transition ${
                                        mobileTab === "map"
                                            ? "bg-[#33758F] text-white shadow-2xs"
                                            : "text-slate-600 hover:text-slate-900"
                                    }`}
                                >
                                    <Map className="h-3.5 w-3.5" />
                                    <span>Map</span>
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setMobileTab("list")}
                                    className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1 font-bold transition ${
                                        mobileTab === "list"
                                            ? "bg-[#33758F] text-white shadow-2xs"
                                            : "text-slate-600 hover:text-slate-900"
                                    }`}
                                >
                                    <List className="h-3.5 w-3.5" />
                                    <span>List ({filteredVehicles.length})</span>
                                </button>
                            </div>
                        </PageHeader>
                    </div>

                    {/* Main Split-Screen Workspace */}
                    <main className="flex min-h-0 flex-1 overflow-hidden">
                        {/* ─── LEFT / SIDE PANEL: SEARCH, FILTERS & VEHICLE LIST ──────────── */}
                        <aside
                            className={`w-full lg:w-[400px] xl:w-[440px] shrink-0 border-r border-slate-200 bg-white flex flex-col h-full overflow-hidden ${
                                mobileTab === "list" ? "flex" : "hidden lg:flex"
                            }`}
                        >
                            {/* Controls: Search & Filter Tabs */}
                            <div className="p-4 border-b border-slate-200/90 bg-white space-y-3 shrink-0">
                                <SearchInput
                                    value={searchTerm}
                                    onChange={setSearchTerm}
                                    onClear={() => setSearchTerm("")}
                                    placeholder="Search by name, ID, or fleet group…"
                                    className="w-full"
                                />

                                {/* Filter Buttons */}
                                <div className="flex flex-wrap gap-1.5">
                                    {[
                                        { key: "ALL", label: "All", count: counts.ALL, dot: null },
                                        { key: "MOVING", label: "Moving", count: counts.MOVING, dot: "bg-emerald-500" },
                                        { key: "IDLING", label: "Idling", count: counts.IDLING, dot: "bg-amber-500" },
                                        { key: "STOPPED", label: "Stopped", count: counts.STOPPED, dot: "bg-slate-400" },
                                        { key: "OFFLINE", label: "Offline", count: counts.OFFLINE, dot: "bg-rose-500" },
                                    ].map(item => {
                                        const isActive = statusFilter === item.key
                                        return (
                                            <button
                                                key={item.key}
                                                type="button"
                                                onClick={() => handleFilterChange(item.key)}
                                                className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-bold transition cursor-pointer ${
                                                    isActive
                                                        ? "bg-[#33758F] text-white shadow-xs"
                                                        : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:border-slate-300"
                                                }`}
                                            >
                                                {item.dot && (
                                                    <span className={`h-1.5 w-1.5 rounded-full ${item.dot}`} />
                                                )}
                                                <span>{item.label}</span>
                                                <span
                                                    className={`rounded-full px-1.5 py-0.2 text-[10px] font-semibold ${
                                                        isActive
                                                            ? "bg-white/20 text-white"
                                                            : "bg-slate-100 text-slate-600"
                                                    }`}
                                                >
                                                    {item.count}
                                                </span>
                                            </button>
                                        )
                                    })}
                                </div>

                                <div className="flex items-center justify-between text-[11px] text-slate-500 pt-1">
                                    <span>
                                        Showing <strong className="text-slate-800">{filteredVehicles.length}</strong> of {vehicles.length} units
                                    </span>
                                    {selectedVehicle && (
                                        <button
                                            type="button"
                                            onClick={() => selectVehicle(null)}
                                            className="text-[#33758F] hover:underline font-semibold cursor-pointer"
                                        >
                                            Clear selection
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* Vehicles List Container */}
                            <div className="flex-1 overflow-y-auto divide-y divide-slate-100 bg-white">
                                {loading && (
                                    <div className="p-10 text-center space-y-2">
                                        <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-slate-300 border-t-[#33758F]" />
                                        <p className="text-xs font-semibold text-slate-500">
                                            Loading vehicle telemetry stream…
                                        </p>
                                    </div>
                                )}

                                {error && (
                                    <div className="m-4 rounded-xl border border-rose-200 bg-rose-50 p-4 text-xs space-y-2">
                                        <div className="flex items-center gap-2 text-rose-800 font-bold">
                                            <AlertCircle className="h-4 w-4 shrink-0" />
                                            <span>Telemetry Connection Error</span>
                                        </div>
                                        <p className="text-rose-700">{error}</p>
                                        <button
                                            type="button"
                                            onClick={() => loadVehicles(true)}
                                            className="rounded-lg bg-rose-600 px-3 py-1.5 font-bold text-white hover:bg-rose-700 transition"
                                        >
                                            Retry
                                        </button>
                                    </div>
                                )}

                                {!loading && !error && filteredVehicles.length === 0 && (
                                    <div className="p-10 text-center space-y-3">
                                        <Truck className="mx-auto h-8 w-8 text-slate-300" />
                                        <p className="text-sm font-bold text-slate-700">No vehicles match filters</p>
                                        <p className="text-xs text-slate-400">
                                            Try adjusting your search query or status filter.
                                        </p>
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setSearchTerm("")
                                                handleFilterChange("ALL")
                                            }}
                                            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50 transition"
                                        >
                                            Reset Filters
                                        </button>
                                    </div>
                                )}

                                {!loading && !error && filteredVehicles.map(vehicle => {
                                    const vid = vehicle.vehicle_id || vehicle.id
                                    const isSelected = String(vid) === String(selectedVehicleId)
                                    const vStatus = String(vehicle.status || "").toUpperCase()
                                    const isOffline = vehicle.connection_status === "OFFLINE" || Boolean(vehicle.is_offline) || vStatus === "OFFLINE"
                                    const statusKey = isOffline ? "OFFLINE" : (vStatus === "MOVING" ? "MOVING" : (vStatus === "IDLING" ? "IDLING" : "STOPPED"))
                                    const hasCoords = isValidCoordinate(vehicle.latitude, vehicle.longitude)
                                    const speed = Math.round(Number(vehicle.speed) || 0)
                                    const driverName = vehicle.driver?.name || vehicle.driver?.id || vehicle.sensors?.driver_id || null

                                    return (
                                        <div
                                            key={vid}
                                            onClick={() => selectVehicle(vehicle)}
                                            className={`p-3.5 transition cursor-pointer select-none border-l-4 ${
                                                isSelected
                                                    ? "bg-[#F0F6F9] border-l-[#33758F]"
                                                    : "bg-white hover:bg-slate-50 border-l-transparent"
                                            }`}
                                        >
                                            {/* Top Line: Unit ID, Name & Status Badge */}
                                            <div className="flex items-center justify-between gap-2">
                                                <div className="min-w-0 flex items-center gap-2">
                                                    <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                                                        isSelected ? "bg-[#33758F] text-white" : "bg-slate-100 text-slate-600"
                                                    }`}>
                                                        <Truck className="h-3.5 w-3.5" />
                                                    </div>
                                                    <div className="min-w-0">
                                                        <h3 className="text-xs font-bold text-slate-900 truncate font-mono">
                                                            {vehicle.vehicle_name || `Unit #${vid}`}
                                                        </h3>
                                                        <p className="text-[10px] text-slate-400 truncate">
                                                            {vehicle.group || "General Fleet"} • ID {vid}
                                                        </p>
                                                    </div>
                                                </div>

                                                <div className="flex items-center gap-1.5 shrink-0">
                                                    <FleetStatusBadge status={statusKey} size="xs" />
                                                </div>
                                            </div>

                                            {/* Telemetry Row */}
                                            <div className="mt-2.5 grid grid-cols-3 gap-1.5 rounded-lg bg-slate-50/70 p-2 border border-slate-100 text-[11px]">
                                                {/* Speed */}
                                                <div>
                                                    <span className="text-[9px] font-semibold text-slate-400 uppercase block">Speed</span>
                                                    <span className="font-bold text-slate-900">
                                                        {speed} <span className="text-[9px] font-normal text-slate-500">km/h</span>
                                                    </span>
                                                </div>

                                                {/* Ignition */}
                                                <div>
                                                    <span className="text-[9px] font-semibold text-slate-400 uppercase block">Ignition</span>
                                                    <span className={`font-bold ${vehicle.ignition ? "text-emerald-600" : "text-slate-500"}`}>
                                                        {vehicle.ignition ? "Active" : "Off"}
                                                    </span>
                                                </div>

                                                {/* Idle or GPS */}
                                                <div className="truncate">
                                                    {statusKey === "IDLING" && vehicle.idle_duration_formatted ? (
                                                        <>
                                                            <span className="text-[9px] font-semibold text-amber-700 uppercase block">Idle Time</span>
                                                            <span className="font-bold text-amber-800 truncate block">
                                                                {vehicle.idle_duration_formatted}
                                                            </span>
                                                        </>
                                                    ) : (
                                                        <>
                                                            <span className="text-[9px] font-semibold text-slate-400 uppercase block">GPS Fix</span>
                                                            <span className="font-mono text-[10px] text-slate-600 truncate block">
                                                                {hasCoords ? "Locked" : "No Signal"}
                                                            </span>
                                                        </>
                                                    )}
                                                </div>
                                            </div>

                                            {/* Driver & Inspect Action Line */}
                                            <div className="mt-2 flex items-center justify-between text-[11px] pt-1">
                                                <div className="flex items-center gap-1 text-slate-500 truncate max-w-[200px]">
                                                    <User className="h-3 w-3 text-slate-400 shrink-0" />
                                                    <span className="truncate text-[10px]">
                                                        {driverName ? String(driverName) : "Unassigned"}
                                                    </span>
                                                </div>

                                                <button
                                                    type="button"
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        setDrawerVehicle(vehicle)
                                                    }}
                                                    className="inline-flex items-center gap-1 text-[11px] font-bold text-[#33758F] hover:text-[#285E73] hover:underline cursor-pointer"
                                                >
                                                    <span>Details</span>
                                                    <ChevronRight className="h-3.5 w-3.5" />
                                                </button>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        </aside>

                        {/* ─── RIGHT PANEL: INTERACTIVE FLEET MAP ───────────────────────── */}
                        <div
                            className={`flex-1 relative h-full bg-slate-50 overflow-hidden ${
                                mobileTab === "map" ? "flex" : "hidden lg:flex"
                            }`}
                        >
                            <FleetMap
                                vehicles={mapVehicles}
                                selectedVehicleId={selectedVehicleId}
                                selectedVehicle={selectedVehicle}
                                onVehicleSelect={selectVehicle}
                                onInspectVehicle={(v) => setDrawerVehicle(v)}
                            />

                            {/* Floating Map Status Overlay (Bottom Left) */}
                            <div className="absolute bottom-4 left-4 z-[400] flex items-center gap-2 rounded-xl border border-slate-200 bg-white/95 px-3.5 py-2 shadow-xs backdrop-blur-xs">
                                <span className="relative flex h-2 w-2">
                                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                                    <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                                </span>
                                <span className="text-xs font-bold text-slate-800">
                                    {mapVehicles.length} of {vehicles.length} Units on Map
                                </span>
                                {statusFilter !== "ALL" && (
                                    <span className="text-[11px] text-slate-500 font-medium">
                                        ({statusFilter.toLowerCase()})
                                    </span>
                                )}
                            </div>

                            {/* Floating Selected Vehicle Overlay (Top Left) */}
                            {selectedVehicle && (
                                <div className="absolute top-4 left-4 z-[400] max-w-sm rounded-xl border border-slate-200 bg-white/95 p-3.5 shadow-md backdrop-blur-xs animate-in fade-in slide-in-from-top-2 duration-150">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <div className="flex items-center gap-1.5">
                                                <Truck className="h-4 w-4 text-[#33758F] shrink-0" />
                                                <h4 className="font-mono text-sm font-bold text-slate-900 truncate">
                                                    {selectedVehicle.vehicle_name || `Unit #${selectedVehicle.vehicle_id || selectedVehicle.id}`}
                                                </h4>
                                            </div>
                                            <p className="text-[10px] text-slate-500 mt-0.5">
                                                {selectedVehicle.group || "General Fleet"} • ID {selectedVehicle.vehicle_id || selectedVehicle.id}
                                            </p>
                                        </div>

                                        <button
                                            type="button"
                                            onClick={() => selectVehicle(null)}
                                            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition"
                                            title="Clear selected unit"
                                        >
                                            <X className="h-4 w-4" />
                                        </button>
                                    </div>

                                    <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-slate-100 pt-2 text-xs">
                                        <FleetStatusBadge status={selectedVehicle.status} size="xs" />
                                        <span className="font-black text-slate-800">
                                            {Math.round(Number(selectedVehicle.speed) || 0)} km/h
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => setDrawerVehicle(selectedVehicle)}
                                            className="rounded-lg bg-[#33758F] hover:bg-[#285E73] text-white px-2.5 py-1 text-[11px] font-bold transition shadow-2xs cursor-pointer"
                                        >
                                            Full Details
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    </main>
                </div>

                {/* ─── VEHICLE DETAIL SLIDE-OUT DRAWER ─────────────────────────────────── */}
                <VehicleDetailDrawer
                    vehicle={drawerVehicle}
                    onClose={() => setDrawerVehicle(null)}
                />
            </div>
        </SidebarProvider>
    )
}

export default Tracking
