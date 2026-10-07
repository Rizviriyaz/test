import { useState, useEffect } from "react"
import { Link } from "react-router-dom"
import {
    X,
    Truck,
    MapPin,
    Clock,
    Activity,
    Fuel,
    DollarSign,
    Route,
    Gauge,
    ExternalLink,
    Loader2,
} from "lucide-react"
import { getVehicleEfficiencyList } from "../../api/fleetApi"
import { formatUAETime } from "../../utils/datetime"
import VehicleStatusBadge from "../Fuel/VehicleStatusBadge"

export default function VehicleDetailDrawer({ vehicle, onClose }) {
    const [fuelData, setFuelData] = useState(null)
    const [loadingFuel, setLoadingFuel] = useState(false)

    useEffect(() => {
        if (!vehicle) return
        let active = true

        async function fetchVehicleFuel() {
            try {
                setLoadingFuel(true)
                const plate = vehicle.name || vehicle.plate || vehicle.plate_number || ""
                const res = await getVehicleEfficiencyList({
                    vehicle_identifier: plate,
                    page: 1,
                    page_size: 5,
                })
                if (!active) return
                if (res?.data && res.data.length > 0) {
                    setFuelData(res.data[0])
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

        fetchVehicleFuel()
        return () => { active = false }
    }, [vehicle])

    if (!vehicle) return null

    const statusStr = String(vehicle.status || vehicle.normalizedStatus || "OFFLINE").toUpperCase()
    const statusBg =
        statusStr === "MOVING"
            ? "bg-emerald-50 text-emerald-700 border-emerald-200"
            : statusStr === "IDLING"
            ? "bg-amber-50 text-amber-700 border-amber-200"
            : statusStr === "STOPPED"
            ? "bg-slate-100 text-slate-700 border-slate-200"
            : "bg-rose-50 text-rose-700 border-rose-200"

    return (
        <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col bg-white shadow-2xl border-l border-slate-200 animate-in slide-in-from-right duration-200">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-slate-100 p-5 bg-slate-50/50">
                <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#1C5E7A]/10 text-[#1C5E7A]">
                        <Truck className="h-5 w-5" />
                    </div>
                    <div>
                        <h2 className="font-mono text-base font-black text-slate-900">
                            {vehicle.name || `Vehicle #${vehicle.vehicle_id}`}
                        </h2>
                        <span className={`inline-block mt-0.5 rounded-full px-2 py-0.5 text-[10px] font-bold border ${statusBg}`}>
                            {statusStr}
                        </span>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={onClose}
                    className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                >
                    <X className="h-5 w-5" />
                </button>
            </div>

            {/* Content Body */}
            <div className="flex-1 overflow-y-auto p-5 space-y-6">
                {/* ─── LIVE WIALON FLEET TELEMETRY ──────────────────────────── */}
                <section className="space-y-3">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                        <Activity className="h-3.5 w-3.5 text-[#1C5E7A]" />
                        Live Fleet Telematics
                    </h3>

                    <div className="grid grid-cols-2 gap-2.5">
                        <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                            <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">Speed</span>
                            <span className="text-base font-black text-slate-900">
                                {Number(vehicle.speed || 0)} <span className="text-xs text-slate-400 font-semibold">km/h</span>
                            </span>
                        </div>

                        <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                            <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">Last Update</span>
                            <span className="text-xs font-bold text-slate-800">
                                {formatUAETime(vehicle.last_update || vehicle.timestamp)}
                            </span>
                        </div>
                    </div>

                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-xs space-y-1.5">
                        <div className="flex items-start gap-2">
                            <MapPin className="h-4 w-4 text-slate-400 shrink-0 mt-0.5" />
                            <div>
                                <span className="font-bold text-slate-700 block">Coordinates</span>
                                <span className="text-[11px] text-slate-500 font-mono">
                                    {vehicle.latitude !== null && vehicle.longitude !== null
                                        ? `${vehicle.latitude?.toFixed(5)}, ${vehicle.longitude?.toFixed(5)}`
                                        : "No GPS fix"}
                                </span>
                            </div>
                        </div>

                        {vehicle.idle_duration > 0 && (
                            <div className="flex items-center justify-between pt-1 border-t border-slate-200/60 text-[11px]">
                                <span className="text-amber-700 font-semibold">Idle Duration:</span>
                                <span className="font-bold text-amber-800">
                                    {Math.round(vehicle.idle_duration / 60)} minutes
                                </span>
                            </div>
                        )}
                    </div>
                </section>

                {/* ─── FUEL & EFFICIENCY TELEMETRY ──────────────────────────── */}
                <section className="space-y-3">
                    <div className="flex items-center justify-between">
                        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                            <Fuel className="h-3.5 w-3.5 text-amber-600" />
                            Fuel & Performance Records
                        </h3>
                        {fuelData && <VehicleStatusBadge status={fuelData.status} />}
                    </div>

                    {loadingFuel ? (
                        <div className="rounded-xl border border-slate-100 p-6 text-center text-xs text-slate-400">
                            <Loader2 className="mx-auto h-5 w-5 animate-spin text-[#1C5E7A] mb-1.5" />
                            Loading fuel performance…
                        </div>
                    ) : fuelData ? (
                        <div className="space-y-2.5">
                            <div className="grid grid-cols-2 gap-2.5">
                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                    <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">Fuel Consumed</span>
                                    <span className="text-base font-black text-slate-900">
                                        {Number(fuelData.litres || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} <span className="text-xs text-slate-400 font-semibold">L</span>
                                    </span>
                                </div>

                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                    <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">Fuel Cost</span>
                                    <span className="text-base font-black text-emerald-700">
                                        AED {Number(fuelData.total_cost || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                    </span>
                                </div>

                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                    <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">Tracked Distance</span>
                                    <span className="text-base font-black text-sky-700">
                                        {fuelData.distance_km !== null
                                            ? `${Number(fuelData.distance_km).toLocaleString(undefined, { maximumFractionDigits: 1 })} km`
                                            : "—"}
                                    </span>
                                </div>

                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                                    <span className="text-[10px] font-bold text-slate-400 uppercase block mb-0.5">Efficiency</span>
                                    <span className="text-base font-black text-[#1C5E7A]">
                                        {fuelData.fuel_efficiency_l_per_100km !== null
                                            ? `${fuelData.fuel_efficiency_l_per_100km.toFixed(1)} L/100km`
                                            : "—"}
                                    </span>
                                </div>
                            </div>

                            {fuelData.fuel_cost_per_km !== null && (
                                <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3 flex items-center justify-between text-xs">
                                    <span className="text-slate-500 font-medium">Cost Per Kilometer:</span>
                                    <span className="font-bold text-slate-800">
                                        AED {fuelData.fuel_cost_per_km.toFixed(2)} / km
                                    </span>
                                </div>
                            )}

                            <Link
                                to={`/fuel?vehicle=${encodeURIComponent(fuelData.vehicle_identifier)}`}
                                className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-[#1C5E7A]/20 bg-[#1C5E7A]/5 py-2.5 text-xs font-bold text-[#1C5E7A] hover:bg-[#1C5E7A]/10 transition-colors"
                            >
                                <span>Inspect in Fuel & Performance</span>
                                <ExternalLink className="h-3.5 w-3.5" />
                            </Link>
                        </div>
                    ) : (
                        <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4 text-center text-xs text-slate-500">
                            No fuel transaction records linked to this unit.
                        </div>
                    )}
                </section>
            </div>
        </div>
    )
}
