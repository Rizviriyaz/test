import { useCallback, useEffect, useMemo, useRef } from "react"
import {
    MapContainer,
    TileLayer,
    Marker,
    useMap,
    ZoomControl,
} from "react-leaflet"
import { ArrowUpRight, Maximize2 } from "lucide-react"

import L from "leaflet"
import "leaflet/dist/leaflet.css"
import FleetStatusBadge from "./Common/FleetStatusBadge"
import { MorphingDialogTrigger } from "./core/morphing-dialog"
import { isValidCoordinate } from "../utils/coordinates"
import { lastSeenLabel, normalizeVehicleStatus } from "../utils/vehicleStatus"

function getMarkerIcon(status, selected = false) {
    // Same status colors as the rest of the UI (index.css --color-status-*)
    const colors = {
        MOVING: "#059669",
        IDLING: "#D97706",
        STOPPED: "#94A3B8",
        DELAYED: "#7C3AED",
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
                        ? `<div style="position: absolute; inset: -6px; border: 2.5px solid #33758F; border-radius: 9999px; background: rgba(51, 117, 143, 0.18); box-shadow: 0 0 10px rgba(51, 117, 143, 0.45);"></div>`
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
                        box-shadow: 0 2px 6px rgba(15, 23, 42, 0.35);
                    "
                ></div>
            </div>
        `,
        iconSize: [size, size],
        iconAnchor: [anchor, anchor],
        popupAnchor: [0, -anchor - 6],
    })
}

function SelectedVehicleFocus({ selectedVehicle, isPanelOpen = true, isDrawerOpen = false }) {
    const map = useMap()
    const lastFocusRef = useRef({ id: null, panel: null, drawer: null })

    const adjustViewport = useCallback((vehicle, isInitialSelection = false) => {
        if (!vehicle || !isValidCoordinate(vehicle.latitude, vehicle.longitude)) return

        const lat = Number(vehicle.latitude)
        const lng = Number(vehicle.longitude)
        const size = map.getSize()
        const W = size.x
        const H = size.y

        if (W <= 0 || H <= 0) return

        // Calculate padding from UI overlays
        // Fleet Units panel: w-[320px] (mobile) or 340px (sm) + left-3/4 (12/16px) + gap
        const isMobile = W < 640
        const panelWidth = isMobile ? Math.min(320, W - 24) : Math.min(340, W - 32)
        const drawerWidth = isMobile ? Math.min(340, W - 24) : Math.min(380, W - 32)

        const paddingLeft = isPanelOpen ? panelWidth + 24 : 64
        const paddingRight = isDrawerOpen ? drawerWidth + 24 : 24

        const visibleWidth = W - paddingLeft - paddingRight

        let targetCenterX = W / 2
        let targetCenterY = H / 2

        // Position vehicle in center of unobstructed space
        if (visibleWidth >= 80) {
            targetCenterX = paddingLeft + visibleWidth / 2
        } else if (isDrawerOpen && !isPanelOpen) {
            targetCenterX = Math.max(80, (W - paddingRight) / 2)
        } else if (isPanelOpen && !isDrawerOpen) {
            targetCenterX = Math.min(W - 80, paddingLeft + (W - paddingLeft) / 2)
        } else {
            targetCenterX = Math.max(60, W - paddingRight - 60)
        }

        const offsetX = targetCenterX - W / 2
        const offsetY = targetCenterY - H / 2

        const targetZoom = isInitialSelection ? Math.max(map.getZoom(), 15) : map.getZoom()
        const targetPoint = map.project([lat, lng], targetZoom)
        const centerPoint = L.point(targetPoint.x - offsetX, targetPoint.y - offsetY)
        const centerLatLng = map.unproject(centerPoint, targetZoom)

        map.flyTo(centerLatLng, targetZoom, {
            duration: isInitialSelection ? 0.7 : 0.4,
            easeLinearity: 0.25,
        })
    }, [map, isPanelOpen, isDrawerOpen])

    useEffect(() => {
        if (!selectedVehicle) {
            lastFocusRef.current = { id: null, panel: null, drawer: null }
            return
        }

        const vid = selectedVehicle.vehicle_id || selectedVehicle.id
        if (!vid) return

        const isNewVehicle = lastFocusRef.current.id !== vid
        const panelChanged = lastFocusRef.current.panel !== isPanelOpen
        const drawerChanged = lastFocusRef.current.drawer !== isDrawerOpen

        if (isNewVehicle || panelChanged || drawerChanged) {
            lastFocusRef.current = { id: vid, panel: isPanelOpen, drawer: isDrawerOpen }
            adjustViewport(selectedVehicle, isNewVehicle)
        }
    }, [selectedVehicle, isPanelOpen, isDrawerOpen, adjustViewport])

    return null
}

function FleetMapControls({ vehicles, isPanelOpen = true, isDrawerOpen = false }) {
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

            // Account for panels when fitting bounds so all markers are visible
            const padLeft = isPanelOpen ? 360 : 48
            const padRight = isDrawerOpen ? 400 : 48

            map.fitBounds(bounds, {
                paddingTopLeft: [padLeft, 48],
                paddingBottomRight: [padRight, 48],
                maxZoom: 14,
                duration: 0.8,
            })
            return
        }

        map.flyTo([25.2048, 55.2708], 10, { duration: 0.8 })
    }

    return (
        <div
            className={`absolute top-3 sm:top-4 z-[1000] flex items-center gap-2 pointer-events-auto transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                isDrawerOpen
                    ? "right-[calc(340px+1rem)] sm:right-[calc(380px+1.25rem)] max-w-[calc(100vw-360px)]"
                    : "right-3 sm:right-4"
            }`}
        >
            <div className="hidden sm:inline-flex items-center gap-1.5 rounded-xl border border-slate-200/90 bg-white/95 px-3 py-2 text-xs font-medium text-slate-600 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.12)] backdrop-blur-md select-none">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" aria-hidden="true" />
                <span className="font-semibold text-slate-900 tabular-nums">{vehicles.length}</span>
                <span>on map</span>
            </div>
            <button
                type="button"
                onClick={resetView}
                title="Fit all units in view"
                aria-label="Fit all units in view"
                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200/90 bg-white/95 px-3 py-2 text-xs font-semibold text-slate-700 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.12)] backdrop-blur-md transition-all hover:bg-white hover:text-brand hover:border-brand/40 hover:shadow-lg cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            >
                <Maximize2 className="h-3.5 w-3.5 text-brand" />
                <span>Fit all units</span>
            </button>
        </div>
    )
}

function formatDriver(vehicle) {
    const driverId = vehicle.driver?.name ?? vehicle.driver?.id ?? vehicle.driver_id ?? vehicle.sensors?.driver_id
    return driverId ? String(driverId) : "Unassigned"
}

function VehiclePopupCard({ vehicle, onInspect }) {
    const statusStr = normalizeVehicleStatus(vehicle)
    // Stale telemetry: speed and ignition are the last reported values, and no idle timer is running
    const lastSeen = lastSeenLabel(vehicle)
    const driverName = formatDriver(vehicle)
    const hasCoords = isValidCoordinate(vehicle.latitude, vehicle.longitude)

    const rows = [
        [lastSeen ? "Last speed" : "Speed", <span key="s" className="tabular-nums">{Math.round(Number(vehicle.speed) || 0)} km/h</span>],
        [lastSeen ? "Last ignition" : "Ignition", vehicle.ignition ? "On" : "Off"],
        ["Idle time", <span key="i" className="tabular-nums">{lastSeen ? "—" : (vehicle.idle_duration_formatted || "00:00:00")}</span>],
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
            {lastSeen && <p className="pt-1.5 text-[11px] font-medium text-violet-800">{lastSeen}</p>}

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
    isPanelOpen = true,
    isDrawerOpen = false,
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
        <div
            className={`relative isolate h-full w-full overflow-hidden bg-white shadow-[0_1px_4px_rgba(15,23,42,0.10)] [&_.leaflet-control-zoom]:!mb-20 sm:[&_.leaflet-control-zoom]:!mb-22 [&_.leaflet-control-attribution]:!mr-16 sm:[&_.leaflet-control-attribution]:!mr-20 ${
                isDrawerOpen ? "[&_.leaflet-bottom.leaflet-right]:!right-[calc(340px+1rem)] sm:[&_.leaflet-bottom.leaflet-right]:!right-[calc(380px+1.25rem)]" : ""
            } [&_.leaflet-bottom.leaflet-right]:transition-all [&_.leaflet-bottom.leaflet-right]:duration-300 ${className}`}
        >
            <MapContainer
                center={center}
                zoom={10}
                zoomControl={false}
                className="h-full w-full"
                scrollWheelZoom={true}
            >
                <ZoomControl position="bottomright" />

                <SelectedVehicleFocus
                    selectedVehicle={selectedVehicle}
                    isPanelOpen={isPanelOpen}
                    isDrawerOpen={isDrawerOpen}
                />

                <FleetMapControls
                    vehicles={vehiclesWithLocation}
                    isPanelOpen={isPanelOpen}
                    isDrawerOpen={isDrawerOpen}
                />

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
                            icon={getMarkerIcon(normalizeVehicleStatus(vehicle), isSelected)}
                            eventHandlers={{
                                click: () => {
                                    onVehicleSelect(vehicle)
                                },
                            }}
                        />
                    )
                })}
            </MapContainer>
        </div>
    )
}

export default FleetMap

