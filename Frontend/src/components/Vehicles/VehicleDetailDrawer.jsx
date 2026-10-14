import { useState, useEffect } from "react"
import { Link } from "react-router-dom"
import {
    X,
    Truck,
    MapPin,
    Clock,
    Activity,
    Fuel,
    Route,
    Gauge,
    ExternalLink,
    Loader2,
    Compass,
    Radio,
    Battery,
    Key,
    User,
    ArrowUpRight,
    HeartPulse,
    FileText,
} from "lucide-react"
import { getVehicleEfficiencyList, getVehicleTrips } from "../../api/fleetApi"
import { formatUAETime, getUAETodayBounds } from "../../utils/datetime"
import VehicleStatusBadge from "../Fuel/VehicleStatusBadge"

export default function VehicleDetailDrawer({ vehicle, onClose }) {
    const [fuelData, setFuelData] = useState(null)
    const [loadingFuel, setLoadingFuel] = useState(false)
    const [tripsData, setTripsData] = useState(null)
    const [loadingTrips, setLoadingTrips] = useState(false)

    useEffect(() => {
        if (!vehicle) return
        let active = true

        async function fetchVehicleFuel() {
            try {
                setLoadingFuel(true)
                const plate = vehicle.vehicle_name || vehicle.name || vehicle.plate || vehicle.plate_number || ""
                const vidNum = Number(vehicle.vehicle_id || vehicle.id)
                const res = await getVehicleEfficiencyList({
                    vehicle_identifier: plate,
                    page: 1,
                    page_size: 10,
                })
                if (!active) return
                if (res?.data && res.data.length > 0) {
                    const matchedRecord = res.data.find(item =>
                        (item.wialon_unit_id && Number(item.wialon_unit_id) === vidNum) ||
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
            const vid = vehicle.vehicle_id || vehicle.id
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

        fetchVehicleFuel()
        fetchVehicleTripsData()
        return () => { active = false }
    }, [vehicle])

    if (!vehicle) return null

    const statusStr = String(vehicle.status || vehicle.normalizedStatus || (vehicle.is_offline ? "OFFLINE" : "STOPPED")).toUpperCase()
    const statusBg =
        statusStr === "MOVING"
            ? "bg-emerald-50 text-emerald-700 border-emerald-200"
            : statusStr === "IDLING"
            ? "bg-amber-50 text-amber-700 border-amber-200"
            : statusStr === "STOPPED"
            ? "bg-slate-100 text-slate-700 border-slate-200"
            : "bg-rose-50 text-rose-700 border-rose-200"

    const vehicleName = vehicle.vehicle_name || vehicle.name || `Vehicle #${vehicle.vehicle_id || vehicle.id}`
    const vehicleGroup = vehicle.group || (Array.isArray(vehicle.groups) && vehicle.groups[0]) || "General Fleet"
    const unitId = vehicle.vehicle_id || vehicle.id || "—"
    const hardwareType = vehicle.hardware_type || vehicle.device_type || vehicle.hw_type || null

    const lat = vehicle.latitude ?? vehicle.location?.latitude ?? null
    const lon = vehicle.longitude ?? vehicle.location?.longitude ?? null
    const altitude = vehicle.location?.altitude ?? vehicle.altitude ?? null
    const course = vehicle.location?.course ?? vehicle.course ?? null
    const satellites = vehicle.location?.satellites ?? vehicle.satellites ?? null
    const speed = Number(vehicle.speed ?? vehicle.location?.speed ?? 0)

    const batteryVoltage = vehicle.sensors?.battery_voltage ?? vehicle.battery?.voltage ?? vehicle.battery_voltage ?? null
    const ignitionState = vehicle.ignition !== undefined ? (vehicle.ignition ? "ON" : "OFF") : (vehicle.sensors?.ignition !== undefined ? (vehicle.sensors.ignition ? "ON" : "OFF") : null)
    const driverId = vehicle.driver?.id || vehicle.driver_id || vehicle.sensors?.driver_id || null
    const cleanDriverId = driverId && !["0000000000000000", "0", "---", "none", "null"].includes(String(driverId).toLowerCase().trim()) ? String(driverId) : null

    // Activity metrics: use actual trip distance today, never the lifetime hardware odometer
    const tripCount = tripsData?.trip_count ?? (tripsData?.trips ? tripsData.trips.length : (vehicle.trip_count ?? vehicle.trips_count ?? null))
    const tripsDistance = tripsData?.trips && tripsData.trips.length > 0
        ? tripsData.trips.reduce((sum, t) => sum + Number(t.distance_km || 0), 0)
        : (tripsData?.summary?.total_distance_km ?? (tripsData ? 0 : null))
    const todayDistance = vehicle.today_mileage ?? tripsDistance
    const idleMinutes = vehicle.idle_duration ? Math.round(vehicle.idle_duration / 60) : null

    return (
        <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col bg-white shadow-2xl border-l border-slate-200 animate-in slide-in-from-right duration-200">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-slate-100 p-5 bg-slate-50/70">
                <div className="flex items-center gap-3 min-w-0">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#1C5E7A]/10 text-[#1C5E7A]">
                        <Truck className="h-5 w-5" />
                    </div>
                    <div className="min-w-0">
                        <h2 className="font-mono text-base font-black text-slate-900 truncate">
                            {vehicleName}
                        </h2>
                        <div className="flex items-center gap-2 mt-0.5">
                            <span className={`inline-block rounded-full px-2 py-0.5 text-[10px] font-bold border ${statusBg}`}>
                                {statusStr}
                            </span>
                            <span className="text-[11px] font-semibold text-slate-500 truncate">
                                {vehicleGroup}
                            </span>
                        </div>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={onClose}
                    className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer shrink-0"
                    aria-label="Close vehicle details"
                >
                    <X className="h-5 w-5" />
                </button>
            </div>

            {/* Content Body */}
            <div className="flex-1 overflow-y-auto p-5 space-y-5">
                {/* ─── 1. VEHICLE IDENTITY & CURRENT STATUS ────────────────────────── */}
                <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4 space-y-3">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                        <Activity className="h-3.5 w-3.5 text-[#1C5E7A]" />
                        Vehicle Identity & Status
                    </h3>

                    <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase block">Unit ID</span>
                            <span className="font-mono font-bold text-slate-900">{unitId}</span>
                        </div>
                        <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase block">Current Speed</span>
                            <span className="text-sm font-black text-slate-900">
                                {speed} <span className="text-[10px] font-semibold text-slate-400">km/h</span>
                            </span>
                        </div>
                        {hardwareType && (
                            <div className="col-span-2 rounded-lg bg-white p-2.5 border border-slate-100">
                                <span className="text-[10px] font-semibold text-slate-400 uppercase block">Hardware Device</span>
                                <span className="font-semibold text-slate-800">{hardwareType}</span>
                            </div>
                        )}
                        <div className="col-span-2 rounded-lg bg-white p-2.5 border border-slate-100 flex items-center justify-between">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase">Last Update</span>
                            <span className="font-semibold text-slate-800 text-[11px]">
                                {formatUAETime(vehicle.last_update || vehicle.last_message_timestamp || vehicle.timestamp)}
                            </span>
                        </div>
                    </div>
                </section>

                {/* ─── 2. GPS TELEMETRY ────────────────────────────────────────────── */}
                <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4 space-y-3">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                        <MapPin className="h-3.5 w-3.5 text-[#1C5E7A]" />
                        GPS Telemetry
                    </h3>

                    <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="col-span-2 rounded-lg bg-white p-2.5 border border-slate-100">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase block">Coordinates</span>
                            <span className="font-mono font-bold text-slate-800 text-[11px]">
                                {lat !== null && lon !== null ? `${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)}` : "No GPS fix"}
                            </span>
                        </div>
                        {altitude !== null && (
                            <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                <span className="text-[10px] font-semibold text-slate-400 uppercase block">Altitude</span>
                                <span className="font-bold text-slate-800">{Math.round(altitude)} m</span>
                            </div>
                        )}
                        {course !== null && (
                            <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                <span className="text-[10px] font-semibold text-slate-400 uppercase block">Course</span>
                                <span className="font-bold text-slate-800">{course}°</span>
                            </div>
                        )}
                        {satellites !== null && (
                            <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                <span className="text-[10px] font-semibold text-slate-400 uppercase block">Satellites</span>
                                <span className="font-bold text-slate-800 flex items-center gap-1">
                                    <Radio className="h-3 w-3 text-sky-600" />
                                    {satellites} locked
                                </span>
                            </div>
                        )}
                    </div>
                </section>

                {/* ─── 3. VEHICLE SENSORS & HARDWARE DATA ──────────────────────────── */}
                {(batteryVoltage !== null || ignitionState !== null || cleanDriverId !== null) && (
                    <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4 space-y-3">
                        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                            <Gauge className="h-3.5 w-3.5 text-[#1C5E7A]" />
                            Vehicle Sensors
                        </h3>

                        <div className="grid grid-cols-2 gap-2 text-xs">
                            {batteryVoltage !== null && (
                                <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block flex items-center gap-1">
                                        <Battery className="h-3 w-3 text-emerald-600" /> Battery
                                    </span>
                                    <span className="font-bold text-slate-900 text-sm">
                                        {Number(batteryVoltage).toFixed(2)} <span className="text-[10px] font-normal text-slate-400">V</span>
                                    </span>
                                </div>
                            )}
                            {ignitionState !== null && (
                                <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase block flex items-center gap-1">
                                        <Key className="h-3 w-3 text-amber-600" /> Ignition
                                    </span>
                                    <span className={`font-bold text-sm ${ignitionState === "ON" ? "text-emerald-600" : "text-slate-600"}`}>
                                        {ignitionState}
                                    </span>
                                </div>
                            )}
                            {cleanDriverId !== null && (
                                <div className="col-span-2 rounded-lg bg-white p-2.5 border border-slate-100 flex items-center justify-between">
                                    <span className="text-[10px] font-semibold text-slate-400 uppercase flex items-center gap-1">
                                        <User className="h-3 w-3 text-slate-400" /> Assigned Driver
                                    </span>
                                    <span className="font-semibold text-slate-800 text-xs">{cleanDriverId}</span>
                                </div>
                            )}
                        </div>
                    </section>
                )}

                {/* ─── 4. TODAY'S ACTIVITY ─────────────────────────────────────────── */}
                <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4 space-y-3">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                        <Route className="h-3.5 w-3.5 text-[#1C5E7A]" />
                        Today's Activity
                    </h3>

                    <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase block">Distance Today</span>
                            <span className="font-bold text-slate-900 text-sm">
                                {loadingTrips ? <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" /> : (todayDistance !== null ? `${Number(todayDistance).toFixed(1)} km` : "—")}
                            </span>
                        </div>
                        <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                            <span className="text-[10px] font-semibold text-slate-400 uppercase block">Trips Today</span>
                            <span className="font-bold text-slate-900 text-sm">
                                {loadingTrips ? <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400" /> : (tripCount !== null ? tripCount : "—")}
                            </span>
                        </div>
                        {idleMinutes !== null && idleMinutes > 0 && (
                            <div className="col-span-2 rounded-lg bg-white p-2.5 border border-amber-200/80 bg-amber-50/30 flex items-center justify-between">
                                <span className="text-[11px] font-semibold text-amber-800">Current Idle Duration</span>
                                <span className="font-bold text-amber-900 text-xs">{idleMinutes} minutes</span>
                            </div>
                        )}
                    </div>
                </section>

                {/* ─── 5. FUEL & EFFICIENCY (IF AVAILABLE) ────────────────────────── */}
                {fuelData && (
                    <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4 space-y-3">
                        <div className="flex items-center justify-between">
                            <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                                <Fuel className="h-3.5 w-3.5 text-amber-600" />
                                Fuel Record
                            </h3>
                            <VehicleStatusBadge status={fuelData.status} />
                        </div>

                        <div className="grid grid-cols-2 gap-2 text-xs">
                            <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                <span className="text-[10px] font-semibold text-slate-400 uppercase block">Fuel Consumed</span>
                                <span className="font-bold text-slate-900">{Number(fuelData.litres || 0).toFixed(1)} L</span>
                            </div>
                            <div className="rounded-lg bg-white p-2.5 border border-slate-100">
                                <span className="text-[10px] font-semibold text-slate-400 uppercase block">Total Cost</span>
                                <span className="font-bold text-emerald-700">AED {Number(fuelData.total_cost || 0).toFixed(0)}</span>
                            </div>
                        </div>
                    </section>
                )}

                {/* ─── 6. DEDICATED PAGE NAVIGATION ACTIONS ────────────────────────── */}
                <section className="space-y-2 pt-2 border-t border-slate-100">
                    <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">
                        Navigate to Detailed Pages
                    </h3>
                    <div className="grid grid-cols-2 gap-2">
                        <Link
                            to={`/tracking?vehicleId=${encodeURIComponent(unitId)}`}
                            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-bold text-slate-800 hover:bg-slate-50 hover:border-slate-300 transition-colors shadow-2xs"
                        >
                            <span>Live Tracking</span>
                            <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                        </Link>
                        <Link
                            to="/vehicle-health"
                            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-bold text-slate-800 hover:bg-slate-50 hover:border-slate-300 transition-colors shadow-2xs"
                        >
                            <span>Vehicle Health</span>
                            <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                        </Link>
                        <Link
                            to="/trips"
                            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-bold text-slate-800 hover:bg-slate-50 hover:border-slate-300 transition-colors shadow-2xs"
                        >
                            <span>Trips & Activity</span>
                            <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                        </Link>
                        <Link
                            to="/idle-analytics"
                            className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-bold text-slate-800 hover:bg-slate-50 hover:border-slate-300 transition-colors shadow-2xs"
                        >
                            <span>Idle Reports</span>
                            <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                        </Link>
                    </div>
                </section>
            </div>
        </div>
    )
}

