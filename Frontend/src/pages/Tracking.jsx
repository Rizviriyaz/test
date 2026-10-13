import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Search,
    RefreshCw,
    AlertCircle,
} from "lucide-react"

import { getVehicles } from "../api/fleetApi"
import FleetMap, { isValidCoordinate } from "../components/FleetMap"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"

function Tracking() {
    const [searchParams] = useSearchParams()

    const [vehicles, setVehicles] = useState([])

    const [selectedVehicleId, setSelectedVehicleId] = useState(() => {
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (!param) return null
        const numeric = Number(param)
        return Number.isNaN(numeric) ? param : numeric
    })

    useEffect(() => {
        const param = searchParams.get("vehicleId") || searchParams.get("vehicle") || searchParams.get("v")
        if (param) {
            const numeric = Number(param)
            setSelectedVehicleId(Number.isNaN(numeric) ? param : numeric)
        }
    }, [searchParams])

    const [searchTerm, setSearchTerm] = useState("")
    const [statusFilter, setStatusFilter] = useState("ALL")
    const [loading, setLoading] = useState(true)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState(null)

    /*
        Load fleet vehicles
    */
    const loadVehicles = useCallback(async (isManual = false) => {
        if (isManual) setRefreshing(true)
        try {
            const data = await getVehicles()
            setVehicles(data?.vehicles || [])
            setError(null)
        } catch (err) {
            setError(err.message || "Failed to load vehicle telemetry")
        } finally {
            setLoading(false)
            if (isManual) setRefreshing(false)
        }
    }, [])

    useEffect(() => {
        let mounted = true

        const runInitialLoad = async () => {
            try {
                const data = await getVehicles()
                if (!mounted) return
                setVehicles(data?.vehicles || [])
                setError(null)
            } catch (err) {
                if (!mounted) return
                setError(err.message || "Failed to load vehicle telemetry")
            } finally {
                if (mounted) setLoading(false)
            }
        }

        runInitialLoad()
        const interval = setInterval(async () => {
            if (!mounted) return
            try {
                const data = await getVehicles()
                if (mounted) {
                    setVehicles(data?.vehicles || [])
                }
            } catch (err) {
                // Background poll fail: keep previous data if present
                console.warn("Telemetry refresh poll failed:", err.message)
            }
        }, 10000)

        return () => {
            mounted = false
            clearInterval(interval)
        }
    }, [])

    /*
        Keep selected vehicle
        synchronized with refresh
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

    /*
        Filter vehicles
    */
    const filteredVehicles = useMemo(() => {
        const query = searchTerm.trim().toLowerCase()
        return vehicles.filter(
            vehicle => {
                const name = String(vehicle.vehicle_name || "").toLowerCase()
                const matchesSearch = !query || name.includes(query) || String(vehicle.vehicle_id || "").includes(query)

                const vStatus = String(vehicle.status || "").toUpperCase()
                const isOffline = vehicle.connection_status === "OFFLINE" || Boolean(vehicle.is_offline) || vStatus === "OFFLINE"

                const matchesStatus =
                    statusFilter === "ALL" ||
                    (statusFilter === "OFFLINE" ? isOffline : vStatus === statusFilter)

                return matchesSearch && matchesStatus
            }
        )
    }, [vehicles, searchTerm, statusFilter])

    /*
        Only vehicles with coordinates
    */
    const mapVehicles = useMemo(() => {
        return filteredVehicles.filter(vehicle =>
            isValidCoordinate(vehicle?.latitude, vehicle?.longitude)
        )
    }, [filteredVehicles])

    /*
        Status counts
    */
    const counts = useMemo(() => ({
        ALL: vehicles.length,
        MOVING: vehicles.filter(v => String(v.status || "").toUpperCase() === "MOVING").length,
        IDLING: vehicles.filter(v => String(v.status || "").toUpperCase() === "IDLING").length,
        STOPPED: vehicles.filter(v => String(v.status || "").toUpperCase() === "STOPPED").length,
        OFFLINE: vehicles.filter(v => v.connection_status === "OFFLINE" || Boolean(v.is_offline) || String(v.status || "").toUpperCase() === "OFFLINE").length,
    }), [vehicles])

    /*
        Select vehicle
    */
    const selectVehicle = useCallback((vehicle) => {
        setSelectedVehicleId(vehicle ? (vehicle.vehicle_id || vehicle.id) : null)
    }, [])

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex flex-1 flex-col overflow-hidden">
                        {/* Header */}
                        <div className="shrink-0 border-b border-slate-200 bg-white px-6 py-4">
                            <div className="flex items-center justify-between">
                                <div>
                                    <h1 className="text-lg font-semibold text-slate-900">
                                        Live Tracking
                                    </h1>
                                    <p className="mt-1 text-xs text-slate-500">
                                        Real-time vehicle locations and telemetry stream
                                    </p>
                                </div>

                                <div className="flex items-center gap-3">
                                    <button
                                        type="button"
                                        onClick={() => loadVehicles(true)}
                                        disabled={loading || refreshing}
                                        title="Refresh fleet telemetry"
                                        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 shadow-2xs hover:bg-slate-50 active:scale-95 transition disabled:opacity-50"
                                    >
                                        <RefreshCw className={`h-3.5 w-3.5 text-slate-500 ${refreshing ? "animate-spin" : ""}`} />
                                        <span>Refresh</span>
                                    </button>

                                    <div className="flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 border border-emerald-200/60">
                                        <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                                        <span className="text-xs font-semibold text-emerald-700">
                                            Live Stream
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Workspace */}
                        <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50">
                            {/* Search & Filters */}
                            <div className="px-5 pt-5">
                                <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-2xs">
                                    <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                                        {/* Search */}
                                        <div className="relative w-full lg:w-[320px]">
                                            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                                            <input
                                                type="text"
                                                placeholder="Search vehicles by name or ID..."
                                                value={searchTerm}
                                                onChange={(e) => setSearchTerm(e.target.value)}
                                                className="w-full rounded-lg border border-slate-300 py-2.5 pl-9 pr-3 text-sm outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-100"
                                            />
                                        </div>

                                        {/* Filters */}
                                        <div className="flex flex-wrap gap-2">
                                            {[
                                                "ALL",
                                                "MOVING",
                                                "IDLING",
                                                "STOPPED",
                                                "OFFLINE",
                                            ].map(status => (
                                                <button
                                                    key={status}
                                                    type="button"
                                                    onClick={() => setStatusFilter(status)}
                                                    className={
                                                        statusFilter === status
                                                            ? "rounded-lg bg-slate-900 px-4 py-2 text-xs font-semibold text-white shadow-2xs"
                                                            : "rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-50 active:scale-95"
                                                    }
                                                >
                                                    {status === "ALL"
                                                        ? `All (${counts.ALL})`
                                                        : status === "MOVING"
                                                            ? `Moving (${counts.MOVING})`
                                                            : status === "IDLING"
                                                                ? `Idle (${counts.IDLING})`
                                                                : status === "STOPPED"
                                                                    ? `Stopped (${counts.STOPPED})`
                                                                    : `Offline (${counts.OFFLINE})`
                                                    }
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* MAP */}
                            <div className="relative h-[calc(100vh-360px)] min-h-[450px] p-5 pt-3">
                                <div className="relative h-full overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xs">
                                    <FleetMap
                                        vehicles={mapVehicles}
                                        selectedVehicleId={selectedVehicleId}
                                        selectedVehicle={selectedVehicle}
                                        onVehicleSelect={selectVehicle}
                                    />

                                    <div className="absolute bottom-4 left-4 z-[500] rounded-lg border border-slate-200 bg-white/95 px-3 py-2 shadow-md backdrop-blur-xs">
                                        <div className="flex items-center gap-2">
                                            <span className="h-2 w-2 rounded-full bg-emerald-500" />
                                            <span className="text-xs font-semibold text-slate-700">
                                                {mapVehicles.length} units plotted on map
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* VEHICLE LIST */}
                            <div className="px-5 pt-3 pb-5">
                                <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xs">
                                    <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
                                        <div>
                                            <h2 className="text-sm font-semibold text-slate-900">
                                                Vehicles Telemetry List
                                            </h2>
                                            <p className="mt-1 text-xs text-slate-500">
                                                Click any unit to fly and inspect its live telemetry on the map.
                                            </p>
                                        </div>

                                        <span className="text-xs text-slate-400">
                                            Showing <span className="font-semibold text-slate-700">{filteredVehicles.length}</span> of <span className="font-semibold text-slate-700">{vehicles.length}</span>
                                        </span>
                                    </div>

                                    {/* Vehicle Table / List */}
                                    <div className="divide-y divide-slate-100 max-h-[500px] overflow-y-auto">
                                        {loading && (
                                            <div className="p-8 text-center">
                                                <div className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-slate-300 border-t-slate-800 mb-2" />
                                                <p className="text-xs font-medium text-slate-500">
                                                    Loading fleet telemetry stream...
                                                </p>
                                            </div>
                                        )}

                                        {error && (
                                            <div className="m-4 flex items-center justify-between rounded-xl border border-red-200 bg-red-50 p-4">
                                                <div className="flex items-center gap-2.5">
                                                    <AlertCircle className="h-5 w-5 text-red-600 shrink-0" />
                                                    <div>
                                                        <p className="text-xs font-semibold text-red-800">
                                                            Unable to load vehicles
                                                        </p>
                                                        <p className="text-xs text-red-600 mt-0.5">
                                                            {error}
                                                        </p>
                                                    </div>
                                                </div>
                                                <button
                                                    type="button"
                                                    onClick={() => loadVehicles(true)}
                                                    className="rounded-lg bg-red-100 px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-200 transition"
                                                >
                                                    Retry
                                                </button>
                                            </div>
                                        )}

                                        {!loading && !error && filteredVehicles.length === 0 && (
                                            <div className="p-8 text-center">
                                                <p className="text-sm font-medium text-slate-500">
                                                    No vehicles match your current search/filter.
                                                </p>
                                            </div>
                                        )}

                                        {!loading && !error && filteredVehicles.map(vehicle => {
                                            const isSelected = String(vehicle.vehicle_id || vehicle.id) === String(selectedVehicleId)
                                            const vStatus = String(vehicle.status || "").toUpperCase()
                                            const isOffline = vehicle.connection_status === "OFFLINE" || Boolean(vehicle.is_offline) || vStatus === "OFFLINE"
                                            const hasCoords = isValidCoordinate(vehicle.latitude, vehicle.longitude)

                                            return (
                                                <button
                                                    key={vehicle.vehicle_id || vehicle.id}
                                                    type="button"
                                                    onClick={() => selectVehicle(vehicle)}
                                                    className={
                                                        isSelected
                                                            ? "w-full bg-slate-100/80 px-5 py-4 text-left transition border-l-4 border-slate-900"
                                                            : "w-full px-5 py-4 text-left transition hover:bg-slate-50 border-l-4 border-transparent"
                                                    }
                                                >
                                                    <div className="flex items-center justify-between">
                                                        <div className="flex items-center gap-3">
                                                            <span
                                                                className={
                                                                    vStatus === "MOVING"
                                                                        ? "h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-500"
                                                                        : vStatus === "IDLING"
                                                                            ? "h-2.5 w-2.5 shrink-0 rounded-full bg-orange-500"
                                                                            : isOffline
                                                                                ? "h-2.5 w-2.5 shrink-0 rounded-full bg-red-500"
                                                                                : "h-2.5 w-2.5 shrink-0 rounded-full bg-slate-400"
                                                                }
                                                            />

                                                            <div>
                                                                <p className="text-sm font-bold text-slate-900">
                                                                    {vehicle.vehicle_name || `Unit #${vehicle.vehicle_id}`}
                                                                </p>

                                                                <div className="mt-0.5 flex items-center gap-2 text-[11px]">
                                                                    <span
                                                                        className={
                                                                            vStatus === "MOVING"
                                                                                ? "font-semibold text-emerald-600"
                                                                                : vStatus === "IDLING"
                                                                                    ? "font-semibold text-orange-600"
                                                                                    : isOffline
                                                                                        ? "font-semibold text-red-600"
                                                                                        : "font-semibold text-slate-500"
                                                                        }
                                                                    >
                                                                        {isOffline ? "OFFLINE" : vStatus}
                                                                    </span>

                                                                    <span className="text-slate-300">•</span>

                                                                    <span className="text-slate-500">
                                                                        Ignition {vehicle.ignition ? "ON" : "OFF"}
                                                                    </span>

                                                                    {vStatus === "IDLING" && vehicle.idle_duration_formatted && (
                                                                        <>
                                                                            <span className="text-slate-300">•</span>
                                                                            <span className="text-orange-600 font-medium">
                                                                                Idle {vehicle.idle_duration_formatted}
                                                                            </span>
                                                                        </>
                                                                    )}
                                                                </div>
                                                            </div>
                                                        </div>

                                                        <div className="text-right">
                                                            <span className="text-sm font-bold text-slate-800">
                                                                {Math.round(Number(vehicle.speed) || 0)} km/h
                                                            </span>

                                                            <p className="text-[11px] text-slate-400 mt-0.5">
                                                                {hasCoords
                                                                    ? `${Number(vehicle.latitude).toFixed(4)}, ${Number(vehicle.longitude).toFixed(4)}`
                                                                    : "No GPS Signal"
                                                                }
                                                            </p>
                                                        </div>
                                                    </div>
                                                </button>
                                            )
                                        })}
                                    </div>

                                    {/* Panel footer */}
                                    <div className="border-t border-slate-200 px-5 py-3 bg-slate-50/50">
                                        <p className="text-[11px] text-slate-400">
                                            Showing <span className="font-semibold text-slate-600">{filteredVehicles.length}</span> of <span className="font-semibold text-slate-600">{vehicles.length}</span> fleet vehicles
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}

export default Tracking
