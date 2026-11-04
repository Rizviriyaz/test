import { useEffect, useRef } from "react"
import { createPortal } from "react-dom"
import { CheckCircle2, X } from "lucide-react"

import { EmptyState, ErrorState, LoadingState } from "../Common/StateViews"
import { formatUAEDateTime } from "../../utils/datetime"

const formatDuration = (seconds) => {
    const s = Math.max(0, Number(seconds || 0))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = Math.floor(s % 60)
    if (h > 0) return `${h}h ${m}m`
    if (m > 0) return `${m}m ${sec}s`
    return `${sec}s`
}

const formatTimestamp = (ts) => (ts == null ? "—" : formatUAEDateTime(ts))

// One vehicle's idle events, read from the page's already-loaded idle report (no request of its own).
// `events` arrives filtered to the vehicle and sorted newest first; `unavailable` marks a vehicle whose
// idle events Wialon could not report.
export default function VehicleIdleEventsModal({
    vehicle,
    groupName,
    events,
    loading = false,
    error = "",
    unavailable = false,
    note = "",
    onRetry,
    onClose,
    renderLocation,
    escapeDisabled = false,
}) {
    const closeRef = useRef(null)

    useEffect(() => {
        closeRef.current?.focus()
    }, [vehicle])

    useEffect(() => {
        if (escapeDisabled) return
        const onKey = (e) => { if (e.key === "Escape") onClose?.() }
        document.addEventListener("keydown", onKey)
        return () => document.removeEventListener("keydown", onKey)
    }, [onClose, escapeDisabled])

    if (!vehicle) return null

    const displayPlate = vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name || "Unknown vehicle"
    const showName = vehicle.vehicle_name && vehicle.vehicle_name !== displayPlate
    const rows = events || []

    let body
    if (loading) {
        body = <LoadingState label="Loading idle events…" />
    } else if (error) {
        body = <ErrorState message={error} onRetry={onRetry} className="border-none" />
    } else if (unavailable) {
        body = (
            <ErrorState
                title="Idle events unavailable"
                message="Wialon did not report idle events for this vehicle in the selected period."
                onRetry={onRetry}
                className="border-none"
            />
        )
    } else if (rows.length === 0) {
        body = <EmptyState icon={CheckCircle2} title="No idle events recorded for this vehicle in this period" className="border-none py-12" />
    } else {
        body = (
            <table className="w-full min-w-[720px] text-xs text-left border-collapse">
                <thead className="sticky top-0 z-10 border-b border-slate-200/80 bg-slate-50/95 backdrop-blur-xs text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                    <tr>
                        <th scope="col" className="px-4 py-2.5">Engine On</th>
                        <th scope="col" className="px-4 py-2.5">Engine Off</th>
                        <th scope="col" className="px-4 py-2.5 text-right">Idle Duration</th>
                        <th scope="col" className="px-4 py-2.5">Location</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                    {rows.map((ev, idx) => {
                        const hasLoc = ev.location?.latitude != null && ev.location?.longitude != null
                        return (
                            <tr key={ev.id || `${ev.start_time}-${idx}`} className="transition-colors hover:bg-slate-50/70">
                                <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-700">{formatTimestamp(ev.start_time)}</td>
                                <td className="whitespace-nowrap px-4 py-2 tabular-nums text-slate-700">{formatTimestamp(ev.end_time)}</td>
                                <td className="whitespace-nowrap px-4 py-2 text-right font-bold tabular-nums text-amber-800">
                                    {ev.duration == null ? "—" : formatDuration(ev.duration)}
                                </td>
                                <td className="px-4 py-2">
                                    {hasLoc && renderLocation
                                        ? renderLocation(ev)
                                        : <span className="text-xs text-slate-400">No GPS fix</span>}
                                </td>
                            </tr>
                        )
                    })}
                </tbody>
            </table>
        )
    }

    return createPortal(
        <div
            className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/40 p-3 sm:p-6 animate-in fade-in duration-200"
            onClick={onClose}
        >
            <div
                role="dialog"
                aria-modal="true"
                aria-labelledby="vehicle-idle-events-title"
                className="relative flex max-h-[88vh] w-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-dialog sm:w-[88vw]"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3.5 sm:items-center sm:px-5">
                    <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                            <h3 id="vehicle-idle-events-title" className="truncate font-mono text-base font-semibold text-slate-900">
                                {displayPlate}
                            </h3>
                            {!loading && !error && !unavailable && (
                                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold tabular-nums text-slate-700">
                                    {rows.length} {rows.length === 1 ? "event" : "events"}
                                </span>
                            )}
                        </div>
                        <p className="mt-0.5 truncate text-xs text-slate-500">
                            {showName ? `${vehicle.vehicle_name} · ` : ""}{groupName || "No group"} · Idle events, newest first
                        </p>
                        {note && !loading && <p className="mt-0.5 text-xs font-medium text-amber-700">{note}</p>}
                    </div>
                    <button
                        ref={closeRef}
                        type="button"
                        onClick={onClose}
                        className="shrink-0 rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                        aria-label="Close idle events"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                <div className="min-h-0 flex-1 overflow-auto">{body}</div>
            </div>
        </div>,
        document.body
    )
}
