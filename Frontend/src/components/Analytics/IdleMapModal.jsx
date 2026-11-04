import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { MapContainer, TileLayer, Marker, Popup } from "react-leaflet"
import { X, MapPin, Truck, Clock3, Users, ArrowUpRight, Calendar, Navigation, User, Phone, Building2 } from "lucide-react"
import L from "leaflet"
import "leaflet/dist/leaflet.css"
import { getIdleEventDriver } from "../../api/fleetApi"
import { formatUAEDate, formatUAETime } from "../../utils/datetime"

function getIdleMarkerIcon() {
    const size = 26
    const anchor = size / 2
    return L.divIcon({
        className: "",
        html: `
            <div style="position: relative; width: ${size}px; height: ${size}px; cursor: pointer;">
                <div style="position: relative; width: ${size}px; height: ${size}px; background: #D97706; border: 3px solid white; border-radius: 9999px; box-sizing: border-box; box-shadow: 0 1px 3px rgba(15,23,42,0.35); display: flex; align-items: center; justify-content: center;">
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

// Driver card: an idle event's driver is looked up on click (vehicle + event start, Wialon binding history); the
// phone is shown only when the backend matched the driver reliably
function DriverCard({ event }) {
    const driverName = String(event.driver || "").trim()
    const eventKey = `${event.vehicle_id}-${event.start_time}`
    // { key, status: "loading" | "done" | "error", data } for the event it was read for
    const [lookup, setLookup] = useState(null)
    const inFlightRef = useRef(null)
    const current = lookup && lookup.key === eventKey ? lookup : null

    const reveal = useCallback(async () => {
        if (!driverName || inFlightRef.current === eventKey || current?.status === "done") return
        inFlightRef.current = eventKey
        setLookup({ key: eventKey, status: "loading", data: null })
        try {
            const data = await getIdleEventDriver({ vehicle_id: event.vehicle_id, start_time: event.start_time, driver_name: driverName })
            setLookup({ key: eventKey, status: "done", data })
        } catch (err) {
            console.error("Failed to load idle event driver:", err)
            setLookup({ key: eventKey, status: "error", data: null })
        } finally {
            if (inFlightRef.current === eventKey) inFlightRef.current = null
        }
    }, [driverName, eventKey, event.vehicle_id, event.start_time, current?.status])

    const label = (
        <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
            <User className="h-3.5 w-3.5 text-slate-500" />
            <span>Driver</span>
        </div>
    )

    if (!driverName) {
        return (
            <div className="rounded-lg border border-slate-200/80 bg-slate-50 p-3 opacity-70" aria-disabled="true">
                {label}
                <p className="mt-1 text-xs font-medium text-slate-500">No driver details found.</p>
            </div>
        )
    }

    const loading = current?.status === "loading"
    const matched = current?.status === "done" && current.data?.status === "matched"
    return (
        <button
            type="button"
            onClick={reveal}
            disabled={loading}
            aria-expanded={current?.status === "done"}
            className="rounded-lg border border-slate-200/80 bg-white p-3 text-left transition hover:border-brand/40 hover:bg-slate-50 cursor-pointer disabled:cursor-wait"
            title={current ? undefined : "Show driver details"}
        >
            {label}
            <p className="mt-1 text-sm font-semibold text-slate-900 truncate">
                {matched ? current.data.driver_name : driverName}
            </p>
            {loading ? (
                <p role="status" aria-label="Loading driver details" className="mt-1.5 flex items-center gap-1.5">
                    <span aria-hidden="true" className="h-3 w-24 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                    <span aria-hidden="true" className="h-3 w-16 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                </p>
            ) : current?.status === "error" ? (
                <p className="mt-1 text-[11px] font-medium text-red-700">Driver details could not be loaded. Click to retry.</p>
            ) : current?.status === "done" ? (
                <>
                {matched && (
                    <p className="mt-0.5 text-[11px] tabular-nums text-slate-500">
                        {current.data.driver_id ? `Employee ID ${current.data.driver_id}` : "Employee ID not available."}
                    </p>
                )}
                {matched && current.data.phone ? (
                    <a
                        href={`tel:${current.data.phone}`}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline"
                    >
                        <Phone className="h-3 w-3" aria-hidden="true" /> {current.data.phone}
                    </a>
                ) : (
                    <p className="mt-1 text-[11px] text-slate-500">Phone number not available.</p>
                )}
                </>
            ) : (
                <p className="mt-1 text-[11px] text-slate-500">Show contact details</p>
            )}
        </button>
    )
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
    // An individual idle event (a hotspot selection has no vehicle or start time): only events get the
    // Group, Driver and Department cards
    const isIdleEvent = event.vehicle_id != null && event.start_time != null && event.location_key == null
    // The vehicle's actual Wialon groups; a vehicle in none is shown as General Fleet
    const groupNames = Array.isArray(event.vehicle_groups)
        ? event.vehicle_groups
        : (event.group_name && event.group_id ? [event.group_name] : [])
    const department = String(event.vehicle_department || "").trim()

    return createPortal(
        <div
            className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/60 p-4 animate-in fade-in duration-200"
            onClick={onClose}
        >
            <div
                className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="idle-modal-title"
                onClick={(e) => e.stopPropagation()}
            >
                {/* Modal Header */}
                <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 bg-white">
                    <div className="flex items-center gap-3">
                        <div>
                            <h3 id="idle-modal-title" className="text-[15px] font-semibold text-slate-900 leading-tight">
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
                                        <p className="font-semibold text-slate-900">
                                            {event.vehicle_name || event.label || "Idle Event"}
                                        </p>
                                        {event.duration && (
                                            <p className="mt-0.5 font-semibold text-amber-800">
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
                            <div className="rounded-lg border border-slate-200/80 bg-white p-3">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                    <Truck className="h-3.5 w-3.5 text-slate-500" />
                                    <span>Vehicle</span>
                                </div>
                                <p className="mt-1 text-sm font-semibold text-slate-900 truncate">
                                    {event.vehicle_name}
                                </p>
                            </div>
                        )}

                        {/* Idle Duration */}
                        {event.duration !== undefined && (
                            <div className="rounded-md border border-slate-200 p-3">
                                <div className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
                                    <Clock3 className="h-3.5 w-3.5" />
                                    <span>Idle Duration</span>
                                </div>
                                <p className="mt-1 text-sm font-semibold tabular-nums text-amber-800">
                                    {formatDuration(event.duration || event.total_idle_seconds)}
                                </p>
                            </div>
                        )}

                        {/* Event Date (UAE Time) */}
                        <div className="rounded-lg border border-slate-200/80 bg-white p-3">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                <Calendar className="h-3.5 w-3.5 text-slate-500" />
                                <span>Event Date</span>
                            </div>
                            <p className="mt-1 text-sm font-semibold text-slate-900">
                                {event.start_time ? formatUAEDate(event.start_time) : "—"}
                            </p>
                        </div>

                        {/* Wialon Group */}
                        {isIdleEvent && (
                            <div className="rounded-lg border border-slate-200/80 bg-white p-3">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                    <Users className="h-3.5 w-3.5 text-slate-500" />
                                    <span>Group</span>
                                </div>
                                {groupNames.length > 0 ? (
                                    <p className="mt-1 text-sm font-semibold text-slate-900 truncate" title={groupNames.join(", ")}>
                                        {groupNames.join(", ")}
                                    </p>
                                ) : (
                                    <p className="mt-1 text-sm font-semibold text-slate-900 truncate">General Fleet</p>
                                )}
                            </div>
                        )}

                        {/* Driver (details on click) */}
                        {isIdleEvent && <DriverCard event={event} />}

                        {/* Department (unit Profile) */}
                        {isIdleEvent && (
                            <div className="rounded-lg border border-slate-200/80 bg-white p-3">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                    <Building2 className="h-3.5 w-3.5 text-slate-500" />
                                    <span>Department</span>
                                </div>
                                {department ? (
                                    <p className="mt-1 text-sm font-semibold text-slate-900 truncate" title={department}>
                                        {department}
                                    </p>
                                ) : (
                                    <p className="mt-1 text-xs font-medium text-slate-500">Department not available.</p>
                                )}
                            </div>
                        )}

                        {/* Start Time (UAE Time) */}
                        <div className="rounded-lg border border-slate-200/80 bg-white p-3">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                <span>Engine On</span>
                            </div>
                            <p className="mt-1 text-sm font-semibold text-slate-800">
                                {event.start_time ? formatUAETime(event.start_time) : "—"}
                            </p>
                        </div>

                        {/* End Time (UAE Time) */}
                        <div className="rounded-lg border border-slate-200/80 bg-white p-3">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                <span>Engine Off</span>
                            </div>
                            <p className="mt-1 text-sm font-semibold text-slate-800">
                                {event.end_time ? formatUAETime(event.end_time) : "—"}
                            </p>
                        </div>
                    </div>

                    {/* Address / Location Row */}
                    <div className="rounded-lg border border-slate-200/80 bg-white p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-400">
                                <MapPin className="h-3.5 w-3.5 text-brand" />
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
                                className="inline-flex items-center justify-center gap-1.5 rounded-md bg-brand px-3 py-1.5 text-xs font-medium text-white transition hover:bg-[#285d73] shrink-0"
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
