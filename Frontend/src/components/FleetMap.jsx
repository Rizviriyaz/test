import { useEffect, useMemo, useRef } from "react"
import {
    MapContainer,
    TileLayer,
    Marker,
    Popup,
    useMap,
} from "react-leaflet"
import {
    Truck,
    Clock3,
    Gauge,
    Power,
    Users,
    MapPin,
    ArrowUpRight,
    ExternalLink,
} from "lucide-react"

import L from "leaflet"
import "leaflet/dist/leaflet.css"
import FleetStatusBadge from "./Common/FleetStatusBadge"
import { MorphingDialogTrigger } from "./core/morphing-dialog"

export function isValidCoordinate(lat, lng) {
    if (lat === null || lat === undefined || lng === null || lng === undefined) return false
    const numLat = Number(lat)
    const numLng = Number(lng)
    if (isNaN(numLat) || isNaN(numLng)) return false
    if (numLat === 0 && numLng === 0) return false
    if (numLat < -90 || numLat > 90 || numLng < -180 || numLng > 180) return false
    return true
}

function getMarkerIcon(status, selected = false) {
    const colors = {
        MOVING: "#22c55e",
        IDLING: "#f97316",
        STOPPED: "#94a3b8",
        OFFLINE: "#dc2626",
    }

    const color = colors[status] || "#64748b"
    const size = selected ? 24 : 18
    const border = selected ? 3 : 2.5
    const anchor = size / 2

    return L.divIcon({
        className: "ats-fleet-marker-icon",
        html: `
            <div
                style="
                    position: relative;
                    width: ${size}px;
                    height: ${size}px;
                    cursor: pointer;
                "
            >
                ${
                    selected
                        ? `
                            <div
                                style="
                                    position: absolute;
                                    inset: -6px;
                                    border: 2px solid #33758F;
                                    border-radius: 9999px;
                                    opacity: 0.7;
                                    animation: ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;
                                "
                            ></div>
                            <div
                                style="
                                    position: absolute;
                                    inset: -4px;
                                    border: 2px solid #33758F;
                                    border-radius: 9999px;
                                    box-shadow: 0 0 6px rgba(51, 117, 143, 0.5);
                                "
                            ></div>
                        `
                        : ""
                }

                ${
                    status === "MOVING" && !selected
                        ? `
                            <div
                                style="
                                    position: absolute;
                                    inset: -4px;
                                    border: 2px solid ${color};
                                    border-radius: 9999px;
                                    opacity: 0.3;
                                "
                            ></div>
                        `
                        : ""
                }

                <div
                    style="
                        position: relative;
                        width: ${size}px;
                        height: ${size}px;
                        background: ${color};
                        border: ${border}px solid white;
                        border-radius: 9999px;
                        box-sizing: border-box;
                        box-shadow: 0 2px 6px rgba(0,0,0,0.3);
                    "
                ></div>
            </div>
        `,
        iconSize: [size, size],
        iconAnchor: [anchor, anchor],
        popupAnchor: [0, -anchor - 6],
    })
}

function SelectedVehicleFocus({ selectedVehicle }) {
    const map = useMap()
    const lastFocusedId = useRef(null)

    useEffect(() => {
        if (!selectedVehicle) return
        const vehicleId = selectedVehicle.vehicle_id || selectedVehicle.id
        if (!vehicleId) return

        if (isValidCoordinate(selectedVehicle.latitude, selectedVehicle.longitude)) {
            const lat = Number(selectedVehicle.latitude)
            const lng = Number(selectedVehicle.longitude)

            // Fly to vehicle if different selection or user focused
            if (lastFocusedId.current !== vehicleId) {
                lastFocusedId.current = vehicleId
                map.flyTo([lat, lng], Math.max(map.getZoom(), 14), {
                    duration: 0.7,
                })
            }
        }
    }, [selectedVehicle, map])

    return null
}

function FleetMapControls({ vehicles }) {
    const map = useMap()

    const resetView = () => {
        const validVehicles = vehicles.filter(v => isValidCoordinate(v?.latitude, v?.longitude))

        if (validVehicles.length > 0) {
            const bounds = L.latLngBounds(
                validVehicles.map(vehicle => [
                    Number(vehicle.latitude),
                    Number(vehicle.longitude),
                ])
            )

            map.fitBounds(bounds, {
                padding: [50, 50],
                maxZoom: 13,
                duration: 0.8,
            })
            return
        }

        map.flyTo([25.2048, 55.2708], 10, { duration: 0.8 })
    }

    return (
        <div className="absolute right-3 top-3 z-[500] flex flex-col gap-2">
            <button
                type="button"
                onClick={resetView}
                title="Reset map view to fit all vehicles"
                className="rounded-lg border border-slate-200 bg-white/95 px-3 py-1.5 text-xs font-semibold text-slate-700 shadow-xs backdrop-blur-xs transition hover:border-[#33758F] hover:text-[#33758F] hover:bg-white active:scale-95 cursor-pointer"
            >
                Fit All Units
            </button>
        </div>
    )
}

function formatDriver(vehicle) {
    const driverId = vehicle.driver?.name ?? vehicle.driver?.id ?? vehicle.driver_id ?? vehicle.sensors?.driver_id
    return driverId ? String(driverId) : "Unassigned"
}

function VehiclePopupCard({ vehicle, onInspect }) {
    const statusStr = String(vehicle.status || (vehicle.is_offline ? "OFFLINE" : "STOPPED")).toUpperCase()
    const driverName = formatDriver(vehicle)
    const hasCoords = isValidCoordinate(vehicle.latitude, vehicle.longitude)

    return (
        <div className="p-3.5 text-slate-900 select-none min-w-[230px]">
            {/* Header */}
            <div className="flex items-start justify-between gap-2 border-b border-slate-100 pb-2.5">
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                        <Truck className="h-3.5 w-3.5 text-[#33758F] shrink-0" />
                        <h4 className="truncate text-sm font-bold text-slate-900 leading-tight">
                            {vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name || `Unit #${vehicle.vehicle_id}`}
                        </h4>
                    </div>
                    <p className="mt-0.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                        {vehicle.vehicle_name ? `${vehicle.vehicle_name} • ` : ""}{vehicle.group || "General Fleet"} • ID {vehicle.vehicle_id}
                    </p>
                </div>

                <FleetStatusBadge status={statusStr} size="xs" />
            </div>

            {/* Metrics 2x2 Grid */}
            <div className="mt-2.5 grid grid-cols-2 gap-2 text-xs">
                {/* Speed */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-semibold text-slate-400 uppercase">
                        <Gauge className="h-3 w-3 text-slate-400" />
                        <span>Speed</span>
                    </div>
                    <p className="mt-0.5 text-sm font-black text-slate-800">
                        {Math.round(Number(vehicle.speed) || 0)} <span className="text-[10px] font-normal text-slate-500">km/h</span>
                    </p>
                </div>

                {/* Ignition */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-semibold text-slate-400 uppercase">
                        <Power className="h-3 w-3 text-slate-400" />
                        <span>Ignition</span>
                    </div>
                    <p className={`mt-0.5 text-xs font-bold ${vehicle.ignition ? "text-emerald-600" : "text-slate-500"}`}>
                        {vehicle.ignition ? "ON" : "OFF"}
                    </p>
                </div>

                {/* Idle Time */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-semibold text-slate-400 uppercase">
                        <Clock3 className="h-3 w-3 text-slate-400" />
                        <span>Idle Time</span>
                    </div>
                    <p className="mt-0.5 text-xs font-bold text-amber-600 truncate">
                        {vehicle.idle_duration_formatted || "00:00:00"}
                    </p>
                </div>

                {/* Driver */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-semibold text-slate-400 uppercase">
                        <Users className="h-3 w-3 text-slate-400" />
                        <span>Driver</span>
                    </div>
                    <p className="mt-0.5 text-xs font-bold text-slate-700 truncate" title={driverName}>
                        {driverName}
                    </p>
                </div>
            </div>

            {/* Coordinates & Google Maps Link */}
            {hasCoords && (
                <div className="mt-2.5 flex items-center justify-between rounded-lg bg-slate-50 border border-slate-100 px-2.5 py-1.5 text-[11px]">
                    <div className="flex items-center gap-1 text-slate-500 min-w-0">
                        <MapPin className="h-3 w-3 text-amber-600 shrink-0" />
                        <span className="font-mono text-[10px] font-medium truncate">
                            {Number(vehicle.latitude).toFixed(4)}, {Number(vehicle.longitude).toFixed(4)}
                        </span>
                    </div>
                    <a
                        href={`https://www.google.com/maps?q=${vehicle.latitude},${vehicle.longitude}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 text-[10px] font-bold text-[#33758F] hover:underline shrink-0 ml-2"
                    >
                        Map <ArrowUpRight className="h-2.5 w-2.5" />
                    </a>
                </div>
            )}

            {/* Inspect details action */}
            {onInspect && (
                <MorphingDialogTrigger
                    layoutId={`vehicle-${vehicle.vehicle_id || vehicle.id}`}
                    onClick={() => onInspect(vehicle)}
                    className="mt-2.5 w-full rounded-lg bg-[#33758F] hover:bg-[#285E73] text-white py-1.5 text-xs font-bold transition-colors shadow-2xs cursor-pointer text-center flex items-center justify-center"
                >
                    View Vehicle Details
                </MorphingDialogTrigger>
            )}
        </div>
    )
}

function FleetMap({
    vehicles = [],
    selectedVehicleId = null,
    selectedVehicle = null,
    onVehicleSelect = () => {},
    onInspectVehicle = null,
    className = "",
}) {
    const vehiclesWithLocation = useMemo(() => {
        return (vehicles || []).filter(v => isValidCoordinate(v?.latitude, v?.longitude))
    }, [vehicles])

    const center = useMemo(() => {
        if (vehiclesWithLocation.length > 0) {
            const sumLat = vehiclesWithLocation.reduce((sum, v) => sum + Number(v.latitude), 0)
            const sumLng = vehiclesWithLocation.reduce((sum, v) => sum + Number(v.longitude), 0)
            const avgLat = sumLat / vehiclesWithLocation.length
            const avgLng = sumLng / vehiclesWithLocation.length
            if (!isNaN(avgLat) && !isNaN(avgLng)) {
                return [avgLat, avgLng]
            }
        }
        return [25.2048, 55.2708] // Default UAE Center
    }, [vehiclesWithLocation])

    return (
        <div className={`relative h-full w-full overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-xs ${className}`}>
            <MapContainer
                center={center}
                zoom={10}
                className="h-full w-full rounded-xl"
                scrollWheelZoom={true}
            >
                <SelectedVehicleFocus selectedVehicle={selectedVehicle} />

                <FleetMapControls vehicles={vehiclesWithLocation} />

                <TileLayer
                    attribution="&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />

                {vehiclesWithLocation.map(vehicle => {
                    const isSelected = String(vehicle.vehicle_id || vehicle.id) === String(selectedVehicleId)
                    return (
                        <Marker
                            key={vehicle.vehicle_id || vehicle.id}
                            position={[
                                Number(vehicle.latitude),
                                Number(vehicle.longitude),
                            ]}
                            icon={getMarkerIcon(vehicle.status, isSelected)}
                            eventHandlers={{
                                click: () => {
                                    onVehicleSelect(vehicle)
                                },
                            }}
                        >
                            <Popup
                                className="ats-vehicle-popup"
                                autoPan={true}
                                autoPanPadding={[50, 50]}
                            >
                                <VehiclePopupCard
                                    vehicle={vehicle}
                                    onInspect={onInspectVehicle}
                                />
                            </Popup>
                        </Marker>
                    )
                })}
            </MapContainer>
        </div>
    )
}

export default FleetMap
