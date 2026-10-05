import { useEffect, useRef } from "react"
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
} from "lucide-react"

import L from "leaflet"
import "leaflet/dist/leaflet.css"

function getMarkerIcon(status, selected = false) {
    const colors = {
        MOVING: "#22c55e",
        IDLING: "#f97316",
        STOPPED: "#94a3b8",
        OFFLINE: "#dc2626",
    }

    const color = colors[status] || "#64748b"

    const size = selected ? 24 : 18
    const border = selected ? 4 : 3
    const anchor = size / 2

    return L.divIcon({
        className: "",
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
                                    border: 2px solid ${color};
                                    border-radius: 9999px;
                                    opacity: 0.35;
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
                                    opacity: 0.2;
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
                        box-shadow: 0 2px 7px rgba(0,0,0,0.35);
                    "
                ></div>
            </div>
        `,
        iconSize: [size, size],
        iconAnchor: [anchor, anchor],
        popupAnchor: [0, -anchor - 4],
    })
}

/**
 * Handles saving map view state prior to opening a popup
 * and restoring the exact center/zoom when the popup is closed.
 */
function MapPopupHandler({ onVehicleSelect }) {
    const map = useMap()
    const savedViewRef = useRef(null)
    const isClosingRef = useRef(false)

    useEffect(() => {
        const handlePopupOpen = () => {
            // Save the current center and zoom level before autoPan shifts the map
            if (!savedViewRef.current && !isClosingRef.current) {
                savedViewRef.current = {
                    center: map.getCenter(),
                    zoom: map.getZoom(),
                }
            }
        }

        const handlePopupClose = () => {
            isClosingRef.current = true
            if (savedViewRef.current) {
                const { center, zoom } = savedViewRef.current
                savedViewRef.current = null
                map.flyTo(center, zoom, { duration: 0.6 })
            }

            if (onVehicleSelect) {
                onVehicleSelect(null)
            }

            setTimeout(() => {
                isClosingRef.current = false
            }, 650)
        }

        map.on("popupopen", handlePopupOpen)
        map.on("popupclose", handlePopupClose)

        return () => {
            map.off("popupopen", handlePopupOpen)
            map.off("popupclose", handlePopupClose)
        }
    }, [map, onVehicleSelect])

    return null
}

function FleetMapControls({ vehicles }) {
    const map = useMap()

    const validVehicles = vehicles.filter(
        vehicle =>
            vehicle.latitude !== null &&
            vehicle.longitude !== null
    )

    const resetView = () => {
        if (validVehicles.length > 0) {
            const bounds = L.latLngBounds(
                validVehicles.map(vehicle => [
                    vehicle.latitude,
                    vehicle.longitude,
                ])
            )

            map.fitBounds(bounds, {
                padding: [50, 50],
                maxZoom: 12,
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
                className="rounded-lg border border-slate-200 bg-white/95 px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm backdrop-blur-xs transition hover:bg-slate-50 active:scale-95"
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

function formatStatusBadge(status) {
    switch (status) {
        case "MOVING":
            return { label: "Moving", bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200", dot: "bg-emerald-500" }
        case "IDLING":
            return { label: "Idling", bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200", dot: "bg-orange-500" }
        case "STOPPED":
            return { label: "Stopped", bg: "bg-slate-100", text: "text-slate-600", border: "border-slate-200", dot: "bg-slate-400" }
        case "OFFLINE":
            return { label: "Offline", bg: "bg-red-50", text: "text-red-700", border: "border-red-200", dot: "bg-red-500" }
        default:
            return { label: "Unknown", bg: "bg-slate-50", text: "text-slate-500", border: "border-slate-200", dot: "bg-slate-400" }
    }
}

function VehiclePopupCard({ vehicle }) {
    const statusInfo = formatStatusBadge(vehicle.status)
    const driverName = formatDriver(vehicle)

    return (
        <div className="p-3.5 text-slate-900 select-none">
            {/* Header */}
            <div className="flex items-start justify-between gap-2 border-b border-slate-100 pb-2.5">
                <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                        <Truck className="h-3.5 w-3.5 text-[#1C5E7A] shrink-0" />
                        <h4 className="truncate text-sm font-bold text-slate-900 leading-tight">
                            {vehicle.vehicle_name}
                        </h4>
                    </div>
                    <p className="mt-0.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
                        {vehicle.group || "General Fleet"} • ID {vehicle.vehicle_id}
                    </p>
                </div>

                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold border ${statusInfo.bg} ${statusInfo.text} ${statusInfo.border} shrink-0`}>
                    <span className={`h-1.5 w-1.5 rounded-full ${statusInfo.dot}`} />
                    {statusInfo.label}
                </span>
            </div>

            {/* Metrics 2x2 Grid */}
            <div className="mt-2.5 grid grid-cols-2 gap-2 text-xs">
                {/* Speed */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-medium text-slate-400">
                        <Gauge className="h-3 w-3 text-slate-400" />
                        <span>Speed</span>
                    </div>
                    <p className="mt-0.5 text-sm font-black text-slate-800">
                        {Math.round(vehicle.speed || 0)} <span className="text-[10px] font-normal text-slate-500">km/h</span>
                    </p>
                </div>

                {/* Ignition */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-medium text-slate-400">
                        <Power className="h-3 w-3 text-slate-400" />
                        <span>Ignition</span>
                    </div>
                    <p className={`mt-0.5 text-xs font-bold ${vehicle.ignition ? "text-emerald-600" : "text-slate-500"}`}>
                        {vehicle.ignition ? "ON (Active)" : "OFF"}
                    </p>
                </div>

                {/* Idle Time */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-medium text-slate-400">
                        <Clock3 className="h-3 w-3 text-slate-400" />
                        <span>Idle Time</span>
                    </div>
                    <p className="mt-0.5 text-xs font-bold text-orange-600 truncate">
                        {vehicle.idle_duration_formatted || "00:00:00"}
                    </p>
                </div>

                {/* Driver */}
                <div className="rounded-lg bg-slate-50 border border-slate-100 p-2">
                    <div className="flex items-center gap-1 text-[10px] font-medium text-slate-400">
                        <Users className="h-3 w-3 text-slate-400" />
                        <span>Driver</span>
                    </div>
                    <p className="mt-0.5 text-xs font-bold text-slate-700 truncate" title={driverName}>
                        {driverName}
                    </p>
                </div>
            </div>

            {/* Coordinates & Location Link */}
            {vehicle.latitude !== null && vehicle.longitude !== null && (
                <div className="mt-2.5 flex items-center justify-between rounded-lg bg-slate-50 border border-slate-100 px-2.5 py-1.5 text-[11px]">
                    <div className="flex items-center gap-1 text-slate-500">
                        <MapPin className="h-3 w-3 text-amber-600 shrink-0" />
                        <span className="font-medium">
                            {Number(vehicle.latitude).toFixed(4)}, {Number(vehicle.longitude).toFixed(4)}
                        </span>
                    </div>
                    <a
                        href={`https://www.google.com/maps?q=${vehicle.latitude},${vehicle.longitude}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 text-[10px] font-bold text-[#1C5E7A] hover:underline"
                    >
                        Map <ArrowUpRight className="h-2.5 w-2.5" />
                    </a>
                </div>
            )}
        </div>
    )
}

function FleetMap({
    vehicles = [],
    selectedVehicleId = null,
    onVehicleSelect = () => {},
}) {
    const vehiclesWithLocation = vehicles.filter(
        vehicle =>
            vehicle.latitude !== null &&
            vehicle.longitude !== null
    )

    const center =
        vehiclesWithLocation.length > 0
            ? [
                vehiclesWithLocation.reduce(
                    (sum, vehicle) =>
                        sum + vehicle.latitude,
                    0
                ) / vehiclesWithLocation.length,

                vehiclesWithLocation.reduce(
                    (sum, vehicle) =>
                        sum + vehicle.longitude,
                    0
                ) / vehiclesWithLocation.length,
            ]
            : [25.2048, 55.2708]

    return (
        <div className="relative h-full w-full overflow-hidden">
            <MapContainer
                center={center}
                zoom={10}
                className="h-full w-full"
            >
                <MapPopupHandler onVehicleSelect={onVehicleSelect} />

                <FleetMapControls vehicles={vehiclesWithLocation} />

                <TileLayer
                    attribution="&copy; OpenStreetMap contributors"
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />

                {vehiclesWithLocation.map(vehicle => (
                    <Marker
                        key={vehicle.vehicle_id}
                        position={[
                            vehicle.latitude,
                            vehicle.longitude,
                        ]}
                        icon={getMarkerIcon(
                            vehicle.status,
                            String(vehicle.vehicle_id) === String(selectedVehicleId)
                        )}
                        eventHandlers={{
                            click: () => {
                                onVehicleSelect(vehicle)
                            },
                        }}
                    >
                        <Popup
                            className="ats-vehicle-popup"
                            autoPan={true}
                            autoPanPadding={[60, 60]}
                        >
                            <VehiclePopupCard vehicle={vehicle} />
                        </Popup>
                    </Marker>
                ))}
            </MapContainer>
        </div>
    )
}

export default FleetMap
