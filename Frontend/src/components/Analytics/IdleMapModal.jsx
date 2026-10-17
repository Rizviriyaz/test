import { useEffect } from "react"
import { createPortal } from "react-dom"
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet"
import { X, MapPin, Truck, Clock3, Users, ArrowUpRight, Calendar, Navigation } from "lucide-react"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { formatUAEDate, formatUAETime } from "../../utils/datetime"

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
    if (h > 0) return `${h}h ${m}m ${sec}s`
    if (m > 0) return `${m}m ${sec}s`
    return `${sec}s`
}

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

    const rawLat = event.location?.latitude ?? event.latitude
    const rawLng = event.location?.longitude ?? event.longitude
    const hasCoordinates =
        rawLat != null &&
        rawLng != null &&
        !isNaN(Number(rawLat)) &&
        !isNaN(Number(rawLng)) &&
        (Number(rawLat) !== 0 || Number(rawLng) !== 0)

    const lat = hasCoordinates ? Number(rawLat) : null
    const lng = hasCoordinates ? Number(rawLng) : null
    const mapPosition = hasCoordinates ? [lat, lng] : null
    const googleMapsUrl = hasCoordinates ? `https://www.google.com/maps?q=${lat},${lng}` : null
    const address = event.address || (event.location && event.location.address) || ""

    return createPortal(
        <div
            className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/60 p-4 backdrop-blur-xs animate-in fade-in duration-200"
            onClick={onClose}
        >
            <div
                className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"
                role="dialog"
                aria-modal="true"
                aria-labelledby="idle-modal-title"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Modal Header */}
                <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 bg-white">
                    <div className="flex items-center gap-3">
                        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-50 text-orange-600 border border-orange-100 shadow-2xs">
                            <MapPin className="h-5 w-5" />
                        </div>
                        <div>
                            <h3 id="idle-modal-title" className="text-base font-bold text-slate-900 leading-tight">
                                {event.vehicle_name || event.label || "Idle Event Details"}
                            </h3>
                            <p className="text-xs text-slate-500 mt-0.5">
                                {hasCoordinates
                                    ? `${lat.toFixed(5)}, ${lng.toFixed(5)}`
                                    : `${formatUAEDate(event.start_time)} · Idle Event`}
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                        title="Close modal"
                        aria-label="Close modal"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                {/* Map View or Fallback Banner */}
                {hasCoordinates ? (
                    <div className="relative h-72 sm:h-80 w-full bg-slate-100">
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
                                    <div className="text-xs p-1">
                                        <p className="font-bold text-slate-900">
                                            {event.vehicle_name || event.label || "Idle Event"}
                                        </p>
                                        {event.duration && (
                                            <p className="text-orange-600 font-semibold mt-0.5">
                                                Duration: {formatDuration(event.duration)}
                                            </p>
                                        )}
                                        {address && (
                                            <p className="text-slate-500 mt-1 max-w-[220px]">
                                                {address}
                                            </p>
                                        )}
                                    </div>
                                </Popup>
                            </Marker>
                        </MapContainer>
                    </div>
                ) : (
                    <div className="flex flex-col items-center justify-center gap-2 py-10 px-6 bg-slate-50 border-b border-slate-100 text-center">
                        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400">
                            <Navigation className="h-6 w-6" />
                        </div>
                        <p className="text-sm font-semibold text-slate-700">Map Coordinates Unavailable</p>
                        <p className="text-xs text-slate-500 max-w-sm">
                            GPS coordinates were not recorded for this discrete idle event. All recorded timing details are displayed below.
                        </p>
                    </div>
                )}

                {/* Event Details Card */}
                <div className="flex flex-col gap-4 border-t border-slate-200 bg-slate-50/70 p-5 sm:p-6 overflow-y-auto max-h-[45vh]">
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                        {/* Vehicle Unit Name */}
                        {event.vehicle_name && (
                            <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-2xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                                    <Truck className="h-3.5 w-3.5 text-slate-500" />
                                    <span>Vehicle</span>
                                </div>
                                <p className="mt-1 text-sm font-bold text-slate-900 truncate">
                                    {event.vehicle_name}
                                </p>
                            </div>
                        )}

                        {/* Idle Duration */}
                        {event.duration !== undefined && (
                            <div className="rounded-xl border border-orange-200/80 bg-orange-50/40 p-3 shadow-2xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-orange-600 uppercase tracking-wider">
                                    <Clock3 className="h-3.5 w-3.5" />
                                    <span>Idle Duration</span>
                                </div>
                                <p className="mt-1 text-sm font-bold text-orange-950">
                                    {formatDuration(event.duration || event.total_idle_seconds)}
                                </p>
                            </div>
                        )}

                        {/* Event Date (UAE Time) */}
                        <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-2xs">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                                <Calendar className="h-3.5 w-3.5 text-slate-500" />
                                <span>Event Date</span>
                            </div>
                            <p className="mt-1 text-sm font-bold text-slate-900">
                                {event.start_time ? formatUAEDate(event.start_time) : "—"}
                            </p>
                        </div>

                        {/* Customer Group */}
                        {event.group_name ? (
                            <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-2xs">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                                    <Users className="h-3.5 w-3.5 text-slate-500" />
                                    <span>Group</span>
                                </div>
                                <p className="mt-1 text-sm font-bold text-slate-900 truncate">
                                    {event.group_name}
                                </p>
                            </div>
                        ) : null}

                        {/* Start Time (UAE Time) */}
                        <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-2xs">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                                <span>Start Time (UAE)</span>
                            </div>
                            <p className="mt-1 text-sm font-semibold text-slate-800">
                                {event.start_time ? formatUAETime(event.start_time) : "—"}
                            </p>
                        </div>

                        {/* End Time (UAE Time) */}
                        <div className="rounded-xl border border-slate-200/80 bg-white p-3 shadow-2xs">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                                <span>End Time (UAE)</span>
                            </div>
                            <p className="mt-1 text-sm font-semibold text-slate-800">
                                {event.end_time ? formatUAETime(event.end_time) : "—"}
                            </p>
                        </div>
                    </div>

                    {/* Address / Location Row */}
                    <div className="rounded-xl border border-slate-200/80 bg-white p-3.5 shadow-2xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                                <MapPin className="h-3.5 w-3.5 text-[#33758F]" />
                                <span>Location / Address</span>
                            </div>
                            <p className="mt-1 text-xs font-medium text-slate-800 leading-snug break-words">
                                {address || (hasCoordinates ? `${lat.toFixed(5)}, ${lng.toFixed(5)}` : "Address not recorded")}
                            </p>
                        </div>

                        {googleMapsUrl && (
                            <a
                                href={googleMapsUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-[#33758F] px-3.5 py-1.5 text-xs font-semibold text-white shadow-2xs transition hover:bg-[#285d73] shrink-0"
                            >
                                <span>Google Maps</span>
                                <ArrowUpRight className="h-3.5 w-3.5" />
                            </a>
                        )}
                    </div>
                </div>
            </div>
        </div>,
        document.body
    )
}
