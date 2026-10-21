import { useEffect, useMemo, useRef } from "react"
import {
    MapContainer,
    TileLayer,
    Marker,
    Popup,
    useMap,
} from "react-leaflet"
import { ArrowUpRight } from "lucide-react"

import L from "leaflet"
import "leaflet/dist/leaflet.css"
import FleetStatusBadge from "./Common/FleetStatusBadge"
import { MorphingDialogTrigger } from "./core/morphing-dialog"
import { isValidCoordinate } from "../utils/coordinates"

function getMarkerIcon(status, selected = false) {
    // Same status colors as the rest of the UI (index.css --color-status-*)
    const colors = {
        MOVING: "#059669",
        IDLING: "#D97706",
        STOPPED: "#94A3B8",
        OFFLINE: "#E11D48",
    }

    const color = colors[status] || "#64748B"
    const size = selected ? 22 : 16
    const border = 2
    const anchor = size / 2

    return L.divIcon({
        className: "ats-fleet-marker-icon",
        html: `
            <div style="position: relative; width: ${size}px; height: ${size}px; cursor: pointer;">
                ${
                    selected
                        ? `<div style="position: absolute; inset: -5px; border: 2px solid #33758F; border-radius: 9999px;"></div>`
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
                        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.35);
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
                className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 shadow-pop transition-colors hover:text-brand cursor-pointer"
            >
                Fit all units
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

    const rows = [
        ["Speed", <span key="s" className="tabular-nums">{Math.round(Number(vehicle.speed) || 0)} km/h</span>],
        ["Ignition", vehicle.ignition ? "On" : "Off"],
        ["Idle time", <span key="i" className="tabular-nums">{vehicle.idle_duration_formatted || "00:00:00"}</span>],
        ["Driver", <span key="d" className="block max-w-[140px] truncate" title={driverName}>{driverName}</span>],
    ]

    return (
        <div className="min-w-[240px] select-none p-3 text-slate-900">
            <div className="flex items-start justify-between gap-3 border-b border-slate-100 pb-2.5">
                <div className="min-w-0">
                    <h4 className="truncate font-mono text-[13px] font-semibold leading-tight text-slate-900">
                        {vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name || `Unit #${vehicle.vehicle_id}`}
                    </h4>
                    <p className="mt-0.5 truncate text-[11px] text-slate-500">
                        {vehicle.vehicle_name ? `${vehicle.vehicle_name} · ` : ""}{vehicle.group || "General Fleet"} · ID {vehicle.vehicle_id}
                    </p>
                </div>
                <FleetStatusBadge status={statusStr} size="xs" />
            </div>

            <dl className="divide-y divide-slate-100 text-xs">
                {rows.map(([label, value]) => (
                    <div key={label} className="flex items-center justify-between gap-3 py-1.5">
                        <dt className="text-slate-500">{label}</dt>
                        <dd className="font-medium text-slate-900">{value}</dd>
                    </div>
                ))}
                {hasCoords && (
                    <div className="flex items-center justify-between gap-3 py-1.5">
                        <dt className="font-mono text-[11px] text-slate-500">
                            {Number(vehicle.latitude).toFixed(4)}, {Number(vehicle.longitude).toFixed(4)}
                        </dt>
                        <dd>
                            <a
                                href={`https://www.google.com/maps?q=${vehicle.latitude},${vehicle.longitude}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-0.5 font-medium text-brand hover:underline underline-offset-2"
                            >
                                Google Maps <ArrowUpRight className="h-3 w-3" />
                            </a>
                        </dd>
                    </div>
                )}
            </dl>

            {onInspect && (
                <MorphingDialogTrigger
                    layoutId={`vehicle-${vehicle.vehicle_id || vehicle.id}`}
                    onClick={() => onInspect(vehicle)}
                    className="mt-2 flex w-full items-center justify-center rounded-md bg-brand py-1.5 text-xs font-medium text-white transition-colors hover:bg-brand-hover cursor-pointer"
                >
                    View vehicle details
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
        <div className={`relative h-full w-full overflow-hidden bg-white ${className}`}>
            <MapContainer
                center={center}
                zoom={10}
                className="h-full w-full"
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
