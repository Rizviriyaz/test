import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Truck,
    User,
    ChevronRight,
    Map,
    List,
    X,
} from "lucide-react"

import { getVehicles } from "../api/fleetApi"
import FleetMap from "../components/FleetMap"
import { isValidCoordinate } from "../utils/coordinates"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import PageHeader from "../components/Common/PageHeader"
import SearchInput from "../components/Common/SearchInput"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import { EmptyState, ErrorBanner, LoadingState } from "../components/Common/StateViews"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import { MorphingDialogTrigger } from "../components/core/morphing-dialog"

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
    const [drawerVehicleSnapshot, setDrawerVehicle] = useState(null)
    const [mobileTab, setMobileTab] = useState("map") // "map" | "list" on small screens

    // Synchronize query parameters (adjusted during render when the URL changes)
    const [syncedSearchParams, setSyncedSearchParams] = useState(searchParams)
    if (searchParams !== syncedSearchParams) {
        setSyncedSearchParams(searchParams)
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (param) {
            const numeric = Number(param)
            setSelectedVehicleId(Number.isNaN(numeric) ? param : numeric)
        }
        const sParam = searchParams.get("status")
        if (sParam && ["ALL", "MOVING", "IDLING", "STOPPED", "OFFLINE"].includes(sParam.toUpperCase())) {
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

    // Show the latest telemetry for the open drawer vehicle (falls back to the last known snapshot)
    const drawerVehicle = useMemo(() => {
        if (!drawerVehicleSnapshot) return null
        const fresh = vehicles.find(
            v => String(v.vehicle_id || v.id) === String(drawerVehicleSnapshot.vehicle_id || drawerVehicleSnapshot.id)
        )
        return fresh || drawerVehicleSnapshot
    }, [vehicles, drawerVehicleSnapshot])

    /*
        Filter vehicles
    */
    const filteredVehicles = useMemo(() => {
        const query = searchTerm.trim().toLowerCase()
        return vehicles.filter(
            vehicle => {
                const plateStr = String(vehicle.registration_plate || vehicle.plate || "").toLowerCase()
                const name = String(vehicle.vehicle_name || "").toLowerCase()
                const idStr = String(vehicle.vehicle_id || vehicle.id || "").toLowerCase()
                const groupStr = String(vehicle.group || "").toLowerCase()
                const matchesSearch = !query || plateStr.includes(query) || name.includes(query) || idStr.includes(query) || groupStr.includes(query)

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

    const statusTabs = [
        { key: "ALL", label: "All", count: counts.ALL, dot: null },
        { key: "MOVING", label: "Moving", count: counts.MOVING, dot: "bg-status-moving" },
        { key: "IDLING", label: "Idling", count: counts.IDLING, dot: "bg-status-idling" },
        { key: "STOPPED", label: "Stopped", count: counts.STOPPED, dot: "bg-status-stopped" },
        { key: "OFFLINE", label: "Offline", count: counts.OFFLINE, dot: "bg-status-offline" },
    ]

    const viewToggleBtn = (active) =>
        `inline-flex h-full items-center gap-1.5 rounded px-2.5 font-medium transition-colors cursor-pointer ${
            active ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]" : "text-slate-600 hover:text-slate-900"
        }`

    return (
        <SidebarProvider>
            <div className="flex h-dvh overflow-hidden bg-canvas font-sans text-slate-900 antialiased">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
                    <div className="shrink-0 border-b border-slate-200/70 bg-canvas px-4 py-3 sm:px-6">
                        <PageHeader
                            title="Live Tracking"
                            subtitle="Real-time vehicle location and status"
                            onRefresh={() => loadVehicles(true)}
                            loading={refreshing}
                            lastUpdated={lastSyncTime}
                            className="mb-0 pb-0 border-b-0"
                        >
                            {/* Mobile view toggle */}
                            <div className="flex h-8 items-center rounded-md bg-slate-100 p-0.5 text-xs lg:hidden">
                                <button type="button" onClick={() => setMobileTab("map")} aria-pressed={mobileTab === "map"} className={viewToggleBtn(mobileTab === "map")}>
                                    <Map className="h-3.5 w-3.5" />
                                    Map
                                </button>
                                <button type="button" onClick={() => setMobileTab("list")} aria-pressed={mobileTab === "list"} className={viewToggleBtn(mobileTab === "list")}>
                                    <List className="h-3.5 w-3.5" />
                                    List ({filteredVehicles.length})
                                </button>
                            </div>
                        </PageHeader>
                    </div>

                    <main className="flex min-h-0 flex-1 overflow-hidden p-3 pt-0 gap-3">
                        {/* ─── VEHICLE LIST ─────────────────────────────────────────── */}
                        <aside
                            className={`h-full w-full shrink-0 flex-col overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] lg:w-[380px] xl:w-[420px] ${
                                mobileTab === "list" ? "flex" : "hidden lg:flex"
                            }`}
                        >
                            <div className="shrink-0 space-y-2.5 border-b border-slate-100 p-3">
                                <SearchInput
                                    value={searchTerm}
                                    onChange={setSearchTerm}
                                    onClear={() => setSearchTerm("")}
                                    placeholder="Search plate, name, ID or group…"
                                    className="w-full"
                                />

                                <div role="group" aria-label="Filter by status" className="grid grid-cols-5 gap-0.5 rounded-md bg-slate-100 p-0.5">
                                    {statusTabs.map(item => {
                                        const isActive = statusFilter === item.key
                                        return (
                                            <button
                                                key={item.key}
                                                type="button"
                                                onClick={() => handleFilterChange(item.key)}
                                                aria-pressed={isActive}
                                                title={`${item.label}: ${item.count}`}
                                                className={`flex min-w-0 flex-col items-center rounded px-1 py-1 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                                    isActive ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]" : "text-slate-500 hover:text-slate-900"
                                                }`}
                                            >
                                                <span className="flex items-center gap-1 text-[11px] font-medium">
                                                    {item.dot && <span className={`h-1.5 w-1.5 rounded-full ${item.dot}`} aria-hidden="true" />}
                                                    <span className="truncate">{item.label}</span>
                                                </span>
                                                <span className="text-[13px] font-semibold tabular-nums">{item.count}</span>
                                            </button>
                                        )
                                    })}
                                </div>

                                <div className="flex items-center justify-between text-xs text-slate-500">
                                    <span className="tabular-nums">
                                        <span className="font-medium text-slate-800">{filteredVehicles.length}</span> of {vehicles.length} units
                                    </span>
                                    {selectedVehicle && (
                                        <button
                                            type="button"
                                            onClick={() => selectVehicle(null)}
                                            className="font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                        >
                                            Clear selection
                                        </button>
                                    )}
                                </div>
                            </div>

                            <div className="flex-1 divide-y divide-slate-100 overflow-y-auto">
                                {loading && (
                                    <LoadingState label="Loading vehicle telemetry…" className="m-3 border-none" />
                                )}

                                {error && (
                                    <ErrorBanner message={error} onRetry={() => loadVehicles(true)} className="m-3" />
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
                                            className={`relative cursor-pointer select-none px-4 py-2.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40 ${
                                                isSelected
                                                    ? "bg-brand-light before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-brand"
                                                    : "hover:bg-slate-50"
                                            }`}
                                        >
                                            <div className="flex items-baseline justify-between gap-3">
                                                <h3 className="min-w-0 truncate font-mono text-[13px] font-semibold text-slate-900">
                                                    {vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name || `Unit #${vid}`}
                                                </h3>
                                                <FleetStatusBadge status={statusKey} size="xs" />
                                            </div>
                                            <p className="mt-0.5 truncate text-xs text-slate-500">
                                                {vehicle.vehicle_name ? `${vehicle.vehicle_name} · ` : ""}{vehicle.group || "General Fleet"} · ID {vid}
                                            </p>

                                            <div className="mt-1.5 flex items-center justify-between gap-3 text-xs">
                                                <dl className="flex min-w-0 items-center gap-x-3 text-slate-600">
                                                    <div className="flex items-baseline gap-1">
                                                        <dt className="sr-only">Speed</dt>
                                                        <dd className="font-semibold tabular-nums text-slate-900">{speed}</dd>
                                                        <span className="text-[11px] text-slate-500">km/h</span>
                                                    </div>
                                                    <div>
                                                        <dt className="sr-only">Ignition</dt>
                                                        <dd className={vehicle.ignition ? "text-slate-700" : "text-slate-400"}>
                                                            Ign. {vehicle.ignition ? "on" : "off"}
                                                        </dd>
                                                    </div>
                                                    {statusKey === "IDLING" && vehicle.idle_duration_formatted ? (
                                                        <div>
                                                            <dt className="sr-only">Idle time</dt>
                                                            <dd className="font-medium tabular-nums text-amber-800">{vehicle.idle_duration_formatted}</dd>
                                                        </div>
                                                    ) : !hasCoords ? (
                                                        <div>
                                                            <dt className="sr-only">GPS</dt>
                                                            <dd className="text-rose-700">No GPS fix</dd>
                                                        </div>
                                                    ) : null}
                                                    <div className="flex min-w-0 items-center gap-1 text-slate-500">
                                                        <dt className="sr-only">Driver</dt>
                                                        <User className="h-3 w-3 shrink-0 text-slate-400" aria-hidden="true" />
                                                        <dd className="truncate">{driverName ? String(driverName) : "Unassigned"}</dd>
                                                    </div>
                                                </dl>

                                                <MorphingDialogTrigger
                                                    layoutId={`vehicle-${vehicle.vehicle_id || vehicle.id}`}
                                                    onClick={(e) => {
                                                        e.stopPropagation()
                                                        setDrawerVehicle(vehicle)
                                                    }}
                                                    className="inline-flex shrink-0 items-center gap-0.5 rounded font-medium text-brand hover:text-brand-hover hover:underline underline-offset-2 cursor-pointer"
                                                >
                                                    Details
                                                    <ChevronRight className="h-3.5 w-3.5" />
                                                </MorphingDialogTrigger>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        </aside>

                        {/* ─── MAP ──────────────────────────────────────────────────── */}
                        <div
                            className={`relative h-full flex-1 overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] ${
                                mobileTab === "map" ? "flex" : "hidden lg:flex"
                            }`}
                        >
                            <FleetMap
                                vehicles={mapVehicles}
                                selectedVehicleId={selectedVehicleId}
                                selectedVehicle={selectedVehicle}
                                onVehicleSelect={selectVehicle}
                                onInspectVehicle={(v) => setDrawerVehicle(v)}
                                className=""
                            />

                            {/* Map coverage (bottom left) */}
                            <div className="absolute bottom-3 left-3 z-[400] rounded-xl border border-slate-200/70 bg-white px-2.5 py-1 text-xs tabular-nums text-slate-700 shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)]">
                                <span className="font-medium text-slate-900">{mapVehicles.length}</span> of {vehicles.length} on map
                                {statusFilter !== "ALL" && (
                                    <span className="text-slate-500"> · {statusFilter.toLowerCase()}</span>
                                )}
                            </div>

                            {/* Selected vehicle (top left) */}
                            {selectedVehicle && (
                                <div className="absolute left-3 right-3 top-3 z-[400] rounded-xl border border-slate-200/70 bg-white p-3 shadow-[0_4px_16px_-4px_rgba(15,23,42,0.1)] sm:right-auto sm:w-80">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <h4 className="truncate font-mono text-[13px] font-semibold text-slate-900">
                                                {selectedVehicle.registration_plate || selectedVehicle.plate || selectedVehicle.vehicle_name || `Unit #${selectedVehicle.vehicle_id || selectedVehicle.id}`}
                                            </h4>
                                            <p className="mt-0.5 truncate text-xs text-slate-500">
                                                {selectedVehicle.vehicle_name ? `${selectedVehicle.vehicle_name} · ` : ""}{selectedVehicle.group || "General Fleet"} · ID {selectedVehicle.vehicle_id || selectedVehicle.id}
                                            </p>
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => selectVehicle(null)}
                                            className="rounded p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                                            title="Clear selected unit"
                                            aria-label="Clear selected unit"
                                        >
                                            <X className="h-4 w-4" />
                                        </button>
                                    </div>

                                    <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-slate-100 pt-2.5 text-xs">
                                        <FleetStatusBadge status={selectedVehicle.status} size="xs" />
                                        <span className="tabular-nums text-slate-600">
                                            <span className="font-semibold text-slate-900">{Math.round(Number(selectedVehicle.speed) || 0)}</span> km/h
                                        </span>
                                        <MorphingDialogTrigger
                                            layoutId={`vehicle-${selectedVehicle.vehicle_id || selectedVehicle.id}`}
                                            onClick={() => setDrawerVehicle(selectedVehicle)}
                                            className="flex items-center justify-center rounded-md bg-brand px-2.5 py-1 text-xs font-medium text-white transition-colors hover:bg-brand-hover cursor-pointer"
                                        >
                                            Full details
                                        </MorphingDialogTrigger>
                                    </div>
                                </div>
                            )}
                        </div>
                    </main>
                </div>

                <VehicleDetailDrawer
                    vehicle={drawerVehicle}
                    onClose={() => setDrawerVehicle(null)}
                />
            </div>
        </SidebarProvider>
    )
}

export default Tracking
