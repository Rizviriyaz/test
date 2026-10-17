import { useState, useEffect, useCallback } from "react"
import { Link } from "react-router-dom"
import {
    Truck,
    MapPin,
    Clock,
    Activity,
    Route,
    Gauge,
    Loader2,
    Radio,
    Battery,
    Key,
    User,
    ArrowUpRight,
} from "lucide-react"
import { getVehicleEfficiencyList, getVehicleTrips, getFleetIdleAnalytics } from "../../api/fleetApi"
import { formatUAETime, getUAETodayBounds } from "../../utils/datetime"
import FleetStatusBadge from "../Common/FleetStatusBadge"
import LiveIdleTimer, { formatElapsedDuration } from "../Common/LiveIdleTimer"

import {
    MorphingDialog,
    MorphingDialogContent,
    MorphingDialogTitle,
    MorphingDialogSubtitle,
    MorphingDialogClose,
    MorphingDialogContainer,
} from "@/components/core/morphing-dialog"

import { ScrollArea } from "@/components/website/scroll-area"

export default function VehicleDetailDrawer({ vehicle, onClose, idleEvents = null }) {
    // Keep activeVehicle cached so the exit animation runs smoothly even when vehicle prop transitions to null
    const [activeVehicle, setActiveVehicle] = useState(vehicle)
    const [isOpen, setIsOpen] = useState(Boolean(vehicle))

    useEffect(() => {
        if (vehicle) {
            setActiveVehicle(vehicle)
            setIsOpen(true)
        } else {
            setIsOpen(false)
        }
    }, [vehicle])

    // Internal state preserved for data-fetching and background matching
    const [fuelData, setFuelData] = useState(null)
    const [loadingFuel, setLoadingFuel] = useState(false)
    const [tripsData, setTripsData] = useState(null)
    const [loadingTrips, setLoadingTrips] = useState(false)
    const [vehicleIdleEvents, setVehicleIdleEvents] = useState(null)
    const [loadingIdleEvents, setLoadingIdleEvents] = useState(false)

    useEffect(() => {
        if (!activeVehicle) return
        let active = true

        // Preserve registration-based comparison and backend matching logic internally
        async function fetchVehicleFuel() {
            try {
                setLoadingFuel(true)
                const plate = activeVehicle.registration_plate || activeVehicle.plate || activeVehicle.plate_number || activeVehicle.vehicle_name || activeVehicle.name || ""
                const vidNum = Number(activeVehicle.vehicle_id || activeVehicle.id)
                const res = await getVehicleEfficiencyList({
                    vehicle_identifier: plate,
                    page: 1,
                    page_size: 10,
                })
                if (!active) return
                if (res?.data && res.data.length > 0) {
                    const matchedRecord = res.data.find(item =>
                        (item.wialon_unit_id && Number(item.wialon_unit_id) === vidNum) ||
                        (item.registration_plate && plate && item.registration_plate.trim().toLowerCase() === plate.trim().toLowerCase()) ||
                        (item.vehicle_identifier && plate && item.vehicle_identifier.trim().toLowerCase() === plate.trim().toLowerCase())
                    )
                    setFuelData(matchedRecord || null)
                } else {
                    setFuelData(null)
                }
            } catch (err) {
                console.error("Failed to fetch vehicle fuel details:", err)
                if (active) setFuelData(null)
            } finally {
                if (active) setLoadingFuel(false)
            }
        }

        async function fetchVehicleTripsData() {
            const vid = activeVehicle.vehicle_id || activeVehicle.id
            if (!vid) return
            try {
                setLoadingTrips(true)
                const { from_time, to_time } = getUAETodayBounds()
                const res = await getVehicleTrips(vid, { from_time, to_time })
                if (!active) return
                setTripsData(res)
            } catch (err) {
                console.error("Failed to fetch vehicle trips:", err)
                if (active) setTripsData(null)
            } finally {
                if (active) setLoadingTrips(false)
            }
        }

        async function fetchVehicleIdleEvents() {
            const vid = activeVehicle.vehicle_id || activeVehicle.id
            if (!vid) return
            if (idleEvents && Array.isArray(idleEvents)) {
                setVehicleIdleEvents(idleEvents.filter(e => String(e.vehicle_id) === String(vid)))
                return
            }
            try {
                setLoadingIdleEvents(true)
                const res = await getFleetIdleAnalytics({ vehicle_id: vid, timeframe: "today", include_events: true })
                if (!active) return
                const evs = (res?.events || []).filter(e => String(e.vehicle_id) === String(vid))
                setVehicleIdleEvents(evs)
            } catch (err) {
                console.error("Failed to fetch vehicle idle events:", err)
                if (active) setVehicleIdleEvents([])
            } finally {
                if (active) setLoadingIdleEvents(false)
            }
        }

        fetchVehicleFuel()
        fetchVehicleTripsData()
        fetchVehicleIdleEvents()
        return () => { active = false }
    }, [activeVehicle, idleEvents])

    const handleClose = useCallback(() => {
        setIsOpen(false)
    }, [])

    const handleExitComplete = useCallback(() => {
        if (!isOpen) {
            onClose?.()
        }
    }, [isOpen, onClose])

    if (!activeVehicle && !vehicle) return null

    const currentVehicle = activeVehicle || vehicle
    const vehicleId = currentVehicle?.vehicle_id || currentVehicle?.id || "active"
    const morphId = `vehicle-${vehicleId}`

    const statusStr = String(currentVehicle.status || currentVehicle.normalizedStatus || (currentVehicle.is_offline ? "OFFLINE" : "STOPPED")).toUpperCase()

    // Customer-facing identifier: unit name only. Registration plate and unit ID are omitted from visible UI.
    const vehicleName = currentVehicle.vehicle_name || currentVehicle.name || "Fleet Vehicle"
    const vehicleGroup = currentVehicle.group || (Array.isArray(currentVehicle.groups) && currentVehicle.groups[0]) || "General Fleet"
    const unitId = currentVehicle.vehicle_id || currentVehicle.id || ""
    const hardwareType = currentVehicle.hardware_type || currentVehicle.device_type || currentVehicle.hw_type || null

    const lat = currentVehicle.latitude ?? currentVehicle.location?.latitude ?? null
    const lon = currentVehicle.longitude ?? currentVehicle.location?.longitude ?? null
    const satellites = currentVehicle.location?.satellites ?? currentVehicle.satellites ?? null
    const speed = Math.round(Number(currentVehicle.speed ?? currentVehicle.location?.speed ?? 0))

    const batteryVoltage = currentVehicle.sensors?.battery_voltage ?? currentVehicle.battery?.voltage ?? currentVehicle.battery_voltage ?? null
    const ignitionState = currentVehicle.ignition !== undefined ? (currentVehicle.ignition ? "ON" : "OFF") : (currentVehicle.sensors?.ignition !== undefined ? (currentVehicle.sensors.ignition ? "ON" : "OFF") : null)
    const driverId = currentVehicle.driver?.id || currentVehicle.driver_id || currentVehicle.sensors?.driver_id || null
    const cleanDriverId = driverId && !["0000000000000000", "0", "---", "none", "null"].includes(String(driverId).toLowerCase().trim()) ? String(driverId) : null

    // Activity metrics
    const tripCount = tripsData?.trip_count ?? (tripsData?.trips ? tripsData.trips.length : (currentVehicle.trip_count ?? currentVehicle.trips_count ?? null))
    const tripsDistance = tripsData?.trips && tripsData.trips.length > 0
        ? tripsData.trips.reduce((sum, t) => sum + Number(t.distance_km || 0), 0)
        : (tripsData?.summary?.total_distance_km ?? (tripsData ? 0 : null))
    const todayDistance = currentVehicle.today_mileage ?? tripsDistance

    return (
        <MorphingDialog
            open={isOpen}
            onClose={handleClose}
            layoutId={morphId}
            transition={{
                type: "spring",
                stiffness: 200,
                damping: 24,
            }}
        >
            <MorphingDialogContainer onExitComplete={handleExitComplete}>
                <MorphingDialogContent
                    layoutId={morphId}
                    className="relative flex flex-col w-full max-w-[480px] max-h-[85vh] rounded-2xl bg-white border border-slate-200 shadow-2xl overflow-hidden"
                >
                    {/* ─── HEADER ─── */}
                    <div className="flex items-center justify-between border-b border-slate-200/90 px-5 py-4 bg-white shrink-0">
                        <div className="flex items-center gap-3 min-w-0">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#33758F]/10 text-[#33758F]">
                                <Truck className="h-5 w-5" />
                            </div>
                            <div className="min-w-0">
                                <MorphingDialogTitle className="text-base font-bold text-slate-900 truncate">
                                    {vehicleName}
                                </MorphingDialogTitle>
                                <MorphingDialogSubtitle className="flex items-center gap-2 mt-0.5">
                                    <FleetStatusBadge status={statusStr} size="xs" />
                                    <span className="text-[11px] font-semibold text-slate-500 truncate">
                                        {vehicleGroup}
                                    </span>
                                </MorphingDialogSubtitle>
                            </div>
                        </div>

                        <MorphingDialogClose onClick={handleClose} />
                    </div>

                    {/* ─── SCROLLABLE CONTENT BODY ─── */}
                    <ScrollArea className="flex-1 overflow-y-auto px-5 py-4 space-y-3.5 bg-slate-50/40">
                        {/* 1. VEHICLE IDENTITY & CURRENT STATUS */}
                        <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 space-y-2.5 shadow-2xs">
                            <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                                <Activity className="h-3.5 w-3.5 text-[#33758F]" />
                                Vehicle Status
                            </h3>

                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block">Unit Name</span>
                                    <span className="font-semibold text-slate-900 truncate block">{vehicleName}</span>
                                </div>
                                <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block">Current Speed</span>
                                    <span className="text-sm font-black text-slate-900">
                                        {speed} <span className="text-[10px] font-semibold text-slate-400">km/h</span>
                                    </span>
                                </div>
                                {hardwareType && (
                                    <div className="col-span-2 rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                        <span className="text-[10px] font-semibold text-slate-400 uppercase block">Hardware Device</span>
                                        <span className="font-semibold text-slate-800">{hardwareType}</span>
                                    </div>
                                )}
                                <div className="col-span-2 rounded-lg bg-slate-50/80 p-2.5 border border-slate-100 flex items-center justify-between">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase">Last Update</span>
                                    <span className="font-semibold text-slate-800 text-[11px]">
                                        {formatUAETime(currentVehicle.last_update || currentVehicle.last_message_timestamp || currentVehicle.timestamp)}
                                    </span>
                                </div>
                            </div>
                        </section>

                        {/* 2. GPS TELEMETRY */}
                        <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 space-y-2.5 shadow-2xs">
                            <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                                <MapPin className="h-3.5 w-3.5 text-[#33758F]" />
                                GPS Telemetry
                            </h3>

                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block">Coordinates</span>
                                    <span className="font-mono font-bold text-slate-800 text-[11px]">
                                        {lat !== null && lon !== null ? `${Number(lat).toFixed(4)}, ${Number(lon).toFixed(4)}` : "No GPS fix"}
                                    </span>
                                </div>
                                <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block">Satellites</span>
                                    <span className="font-bold text-slate-800 flex items-center gap-1">
                                        <Radio className="h-3 w-3 text-[#33758F]" />
                                        {satellites !== null ? `${satellites} locked` : "GPS active"}
                                    </span>
                                </div>
                            </div>
                        </section>

                        {/* 3. VEHICLE SENSORS */}
                        {(batteryVoltage !== null || ignitionState !== null || cleanDriverId !== null) && (
                            <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 space-y-2.5 shadow-2xs">
                                <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                                    <Gauge className="h-3.5 w-3.5 text-[#33758F]" />
                                    Vehicle Sensors
                                </h3>

                                <div className="grid grid-cols-2 gap-2 text-xs">
                                    {batteryVoltage !== null && (
                                        <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                            <span className="text-[10px] font-semibold text-slate-400 uppercase block flex items-center gap-1">
                                                <Battery className="h-3 w-3 text-emerald-600" /> Battery
                                            </span>
                                            <span className="font-bold text-slate-900 text-sm">
                                                {Number(batteryVoltage).toFixed(2)} <span className="text-[10px] font-normal text-slate-400">V</span>
                                            </span>
                                        </div>
                                    )}
                                    {ignitionState !== null && (
                                        <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                            <span className="text-[10px] font-semibold text-slate-400 uppercase block flex items-center gap-1">
                                                <Key className="h-3 w-3 text-amber-600" /> Ignition
                                            </span>
                                            <span className={`font-bold text-sm ${ignitionState === "ON" ? "text-emerald-600" : "text-slate-600"}`}>
                                                {ignitionState}
                                            </span>
                                        </div>
                                    )}
                                    {cleanDriverId !== null && (
                                        <div className="col-span-2 rounded-lg bg-slate-50/80 p-2.5 border border-slate-100 flex items-center justify-between">
                                            <span className="text-[10px] font-semibold text-slate-400 uppercase flex items-center gap-1">
                                                <User className="h-3 w-3 text-slate-400" /> Assigned Driver
                                            </span>
                                            <span className="font-semibold text-slate-800 text-xs">{cleanDriverId}</span>
                                        </div>
                                    )}
                                </div>
                            </section>
                        )}

                        {/* 4. TODAY'S ACTIVITY */}
                        <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 space-y-2.5 shadow-2xs">
                            <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                                <Route className="h-3.5 w-3.5 text-[#33758F]" />
                                Today's Activity
                            </h3>

                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block">Distance Today</span>
                                    <span className="font-bold text-slate-900 text-sm">
                                        {loadingTrips ? <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" /> : (todayDistance !== null ? `${Number(todayDistance).toFixed(1)} km` : "—")}
                                    </span>
                                </div>
                                <div className="rounded-lg bg-slate-50/80 p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block">Trips Today</span>
                                    <span className="font-bold text-slate-900 text-sm">
                                        {loadingTrips ? <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" /> : (tripCount !== null ? tripCount : "—")}
                                    </span>
                                </div>
                            </div>
                        </section>

                        {/* 5. IDLE STATUS & DIAGNOSTICS */}
                        <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 space-y-2.5 shadow-2xs">
                            <div className="flex items-center justify-between">
                                <h3 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                                    <Clock className="h-3.5 w-3.5 text-amber-600" />
                                    Idle Status & Diagnostics
                                </h3>
                                <span className="text-[10px] font-semibold text-slate-400">
                                    UAE Timezone
                                </span>
                            </div>

                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="rounded-lg bg-amber-50/60 p-2.5 border border-amber-200/70">
                                    <span className="text-[10px] font-semibold text-amber-800 uppercase block">Idle Start Time</span>
                                    <span className="font-bold text-amber-950 text-xs">
                                        {statusStr === "IDLING" && currentVehicle.idle_start
                                            ? `Idling since ${formatUAETime(currentVehicle.idle_start)}`
                                            : (statusStr === "IDLING" ? "Start time unavailable" : "Not currently idling")}
                                    </span>
                                </div>
                                <div className="rounded-lg bg-amber-50/60 p-2.5 border border-amber-200/70">
                                    <span className="text-[10px] font-semibold text-amber-800 uppercase block">Elapsed Idle Duration</span>
                                    <div className="font-bold text-amber-950 text-xs flex items-center gap-1.5">
                                        {statusStr === "IDLING" ? (
                                            <>
                                                <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
                                                <LiveIdleTimer
                                                    startTime={currentVehicle.idle_start}
                                                    baseDuration={currentVehicle.idle_duration}
                                                    status={statusStr}
                                                />
                                            </>
                                        ) : (
                                            <span>
                                                {currentVehicle.idle_duration
                                                    ? formatElapsedDuration(currentVehicle.idle_duration)
                                                    : (currentVehicle.total_idle_seconds ? formatElapsedDuration(currentVehicle.total_idle_seconds) : "0s")}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>

                            {/* Event History for Selected Vehicle */}
                            <div className="pt-2 border-t border-slate-100">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-[11px] font-bold text-slate-700">
                                        Idle Events ({vehicleIdleEvents ? vehicleIdleEvents.length : 0})
                                    </span>
                                    {loadingIdleEvents && (
                                        <span className="flex items-center gap-1 text-[10px] text-slate-400">
                                            <Loader2 className="h-3 w-3 animate-spin text-[#33758F]" /> Loading…
                                        </span>
                                    )}
                                </div>

                                {loadingIdleEvents ? (
                                    <div className="py-3 text-center">
                                        <Loader2 className="mx-auto h-4 w-4 animate-spin text-slate-400" />
                                    </div>
                                ) : !vehicleIdleEvents || vehicleIdleEvents.length === 0 ? (
                                    <div className="rounded-lg bg-slate-50/80 p-2.5 text-center text-xs text-slate-500">
                                        No idle events recorded for this vehicle today.
                                    </div>
                                ) : (
                                    <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                                        {vehicleIdleEvents.map((ev, idx) => {
                                            const dur = Number(ev.duration || 0)
                                            return (
                                                <div
                                                    key={ev.id || idx}
                                                    className="flex items-center justify-between rounded-lg border border-slate-100 bg-slate-50/60 p-2 text-xs"
                                                >
                                                    <div className="min-w-0 pr-2">
                                                        <p className="font-semibold text-slate-800 text-[11px]">
                                                            {formatUAETime(ev.start_time)} → {ev.end_time ? formatUAETime(ev.end_time) : "Ongoing"}
                                                        </p>
                                                        {ev.address && (
                                                            <p className="text-[10px] text-slate-400 truncate">
                                                                {ev.address}
                                                            </p>
                                                        )}
                                                    </div>
                                                    <span className="font-mono font-bold text-amber-700 text-[10px] shrink-0 bg-amber-100/60 px-2 py-0.5 rounded">
                                                        {formatElapsedDuration(dur)}
                                                    </span>
                                                </div>
                                            )
                                        })}
                                    </div>
                                )}
                            </div>
                        </section>

                        {/* 6. DEDICATED PAGE NAVIGATION ACTIONS */}
                        <section className="space-y-2 pt-1 border-t border-slate-100">
                            <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                                Navigate to Detailed Pages
                            </h3>
                            <div className="grid grid-cols-2 gap-2">
                                <Link
                                    to={unitId ? `/tracking?vehicleId=${encodeURIComponent(unitId)}` : "/tracking"}
                                    className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-800 hover:border-[#33758F] hover:text-[#33758F] transition-colors shadow-2xs"
                                >
                                    <span>Live Tracking</span>
                                    <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                                </Link>
                                <Link
                                    to="/vehicle-health"
                                    className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-800 hover:border-[#33758F] hover:text-[#33758F] transition-colors shadow-2xs"
                                >
                                    <span>Vehicle Health</span>
                                    <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                                </Link>
                                <Link
                                    to="/trips"
                                    className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-800 hover:border-[#33758F] hover:text-[#33758F] transition-colors shadow-2xs"
                                >
                                    <span>Trips & Activity</span>
                                    <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                                </Link>
                                <Link
                                    to="/idle-analytics"
                                    className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-800 hover:border-[#33758F] hover:text-[#33758F] transition-colors shadow-2xs"
                                >
                                    <span>Idle Reports</span>
                                    <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                                </Link>
                            </div>
                        </section>
                    </ScrollArea>
                </MorphingDialogContent>
            </MorphingDialogContainer>
        </MorphingDialog>
    )
}
