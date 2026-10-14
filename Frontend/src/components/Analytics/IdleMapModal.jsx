import { useEffect } from "react"
import { createPortal } from "react-dom"
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet"
import { X, MapPin, Truck, Clock3, Users, ArrowUpRight } from "lucide-react"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { formatUAEDateTime } from "../../utils/datetime"

function getIdleMarkerIcon() {
    const size = 26
    const anchor = size / 2
    return L.divIcon({
        className: "",
        html: `
            <div style="position: relative; width: ${size}px; height: ${size}px; cursor: pointer;">
                <div style="position: absolute; inset: -6px; border: 2px solid #F97316; border-radius: 9999px; opacity: 0.4; animation: ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite;"></div>
                <div style="position: relative; width: ${size}px; height: ${size}px; background: #F97316; border: 3px solid white; border-radius: 9999px; box-sizing: border-box; box-shadow: 0 4px 10px rgba(249,115,22,0.4); display: flex; align-items: center; justify-content: center;">
                    <div style="width: 6px; height: 6px; background: white; border-radius: 9999px;"></div>
                </div>
            </div>
        `,
        iconSize: [size, size],
        iconAnchor: [anchor, anchor],
        popupAnchor: [0, -anchor - 4],
    })
}

const formatDuration = (seconds) => {
    const s = Math.max(0, Number(seconds || 0))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = Math.floor(s % 60)
    if (h > 0) return `${h}h ${m}m`
    if (m > 0) return `${m}m ${sec}s`
    return `${sec}s`
}

const formatDateTime = (ts) => formatUAEDateTime(ts)

export default function IdleMapModal({ event, onClose }) {
    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === "Escape") {
                onClose?.()
            }
        }
        window.addEventListener("keydown", handleKeyDown)
        return () => window.removeEventListener("keydown", handleKeyDown)
    }, [onClose])

    if (!event) return null

    const lat = event.location?.latitude ?? event.latitude
    const lng = event.location?.longitude ?? event.longitude

    if (lat == null || lng == null) {
        return null
    }

    const mapPosition = [Number(lat), Number(lng)]
    const googleMapsUrl = `https://www.google.com/maps?q=${lat},${lng}`

    return createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs animate-in fade-in duration-200" onClick={onClose}>
            <div
                className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"
                role="dialog"
                aria-modal="true"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Modal Header */}
                <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
                    <div className="flex items-center gap-2.5">
                        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-orange-50 text-orange-600">
                            <MapPin className="h-5 w-5" />
                        </div>
                        <div>
                            <h3 className="text-base font-bold text-slate-900">
                                {event.vehicle_name || event.label || "Idle Event Location"}
                            </h3>
                            <p className="text-xs text-slate-500">
                                {Number(lat).toFixed(5)}, {Number(lng).toFixed(5)}
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                        title="Close modal"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                {/* Map View */}
                <div className="relative h-80 w-full bg-slate-100">
                    <MapContainer
                        center={mapPosition}
                        zoom={15}
                        scrollWheelZoom={true}
                        style={{ width: "100%", height: "100%" }}
                    >
                        <TileLayer
                            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                        />
                        <Marker position={mapPosition} icon={getIdleMarkerIcon()}>
                            <Popup>
                                <div className="text-xs">
                                    <p className="font-bold text-slate-900">
                                        {event.vehicle_name || event.label || "Idle Event"}
                                    </p>
                                    {event.duration && (
                                        <p className="text-slate-600">
                                            Duration: {formatDuration(event.duration)}
                                        </p>
                                    )}
                                </div>
                            </Popup>
                        </Marker>
                    </MapContainer>
                </div>

                {/* Event Details Card */}
                <div className="flex flex-col gap-4 border-t border-slate-200 bg-slate-50/70 p-6">
                    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                        {event.vehicle_name && (
                            <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
                                    <Truck className="h-3.5 w-3.5" />
                                    <span>Vehicle</span>
                                </div>
                                <p className="mt-1 text-sm font-semibold text-slate-800 truncate">
                                    {event.vehicle_name}
                                </p>
                            </div>
                        )}

                        {event.group_name && (
                            <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
                                    <Users className="h-3.5 w-3.5" />
                                    <span>Group</span>
                                </div>
                                <p className="mt-1 text-sm font-semibold text-slate-800 truncate">
                                    {event.group_name}
                                </p>
                            </div>
                        )}

                        {event.duration !== undefined && (
                            <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
                                    <Clock3 className="h-3.5 w-3.5" />
                                    <span>Duration</span>
                                </div>
                                <p className="mt-1 text-sm font-semibold text-slate-800">
                                    {formatDuration(event.duration || event.total_idle_seconds)}
                                </p>
                            </div>
                        )}

                        {(event.start_time || event.event_count !== undefined) && (
                            <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-400">
                                    <span>{event.start_time ? "Start Time" : "Events"}</span>
                                </div>
                                <p className="mt-1 text-sm font-semibold text-slate-800">
                                    {event.start_time ? formatDateTime(event.start_time) : `${event.event_count} events`}
                                </p>
                            </div>
                        )}
                    </div>

                    <div className="flex items-center justify-between pt-2">
                        <div className="text-xs text-slate-500">
                            Coordinates: <span className="font-mono text-slate-700">{Number(lat).toFixed(5)}, {Number(lng).toFixed(5)}</span>
                        </div>
                        <a
                            href={googleMapsUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 rounded-xl bg-[#33758F] px-4 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-[#285d73]"
                        >
                            Open in Google Maps
                            <ArrowUpRight className="h-3.5 w-3.5" />
                        </a>
                    </div>
                </div>
            </div>
        </div>,
        document.body
    )
}
