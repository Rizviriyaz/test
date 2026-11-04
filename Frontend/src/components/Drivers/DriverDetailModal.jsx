import { useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { AlertTriangle, ChevronDown, Clock3, Phone, Truck, X } from "lucide-react"
import { SkeletonLines } from "../Common/Skeleton"

import { formatElapsedDuration, formatUAEDateTime, formatUAETime } from "../../utils/datetime"
import { NOT_AVAILABLE, detailRows } from "./driverFilters"
import { IDLE_UNATTRIBUTABLE_MESSAGES, eventVehicleLabel } from "./driverIdle"
import { DriverStatusBadge } from "./DriverStatusBadge"

const note = "rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500"

function EventDetail({ label, children }) {
    return (
        <div className="flex items-baseline justify-between gap-4 py-1">
            <dt className="shrink-0 text-slate-500">{label}</dt>
            <dd className="min-w-0 break-words text-right font-medium text-slate-900">
                {children ?? <span className="font-normal text-slate-400">{NOT_AVAILABLE}</span>}
            </dd>
        </div>
    )
}

// One idle event: the same summary line as the vehicle drawer's idle events list, expandable to its details
function IdleEventRow({ ev, expanded, onToggle }) {
    const vehicle = eventVehicleLabel(ev)
    const groups = Array.isArray(ev.vehicle_groups) && ev.vehicle_groups.length ? ev.vehicle_groups.join(", ") : "General Fleet"
    const address = ev.address && ev.address !== "Unknown Location" ? ev.address : null
    return (
        <li>
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={expanded}
                className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-xs transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
            >
                <div className="min-w-0">
                    {/* Start/end bound the engine-on period; the duration is the idling within it */}
                    <p className="tabular-nums text-slate-800" title="Engine-on period">
                        Engine on {formatUAETime(ev.start_time)} → {ev.ongoing ? "ongoing" : ev.end_time ? formatUAETime(ev.end_time) : "—"}
                    </p>
                    <p className="truncate text-[11px] text-slate-500">{vehicle || NOT_AVAILABLE}</p>
                </div>
                <span className="flex shrink-0 items-center gap-1.5">
                    <span className="font-semibold tabular-nums text-amber-800" title="Idle time within the engine-on period">
                        {formatElapsedDuration(Number(ev.duration || 0))} idle
                    </span>
                    <ChevronDown className={`h-3.5 w-3.5 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />
                </span>
            </button>
            {expanded && (
                <dl className="divide-y divide-slate-100 border-t border-slate-100 bg-slate-50/60 px-3 py-1 text-xs">
                    <EventDetail label="Engine on">{ev.start_time ? formatUAEDateTime(ev.start_time) : null}</EventDetail>
                    <EventDetail label="Engine off">{ev.ongoing ? "Ongoing" : ev.end_time ? formatUAEDateTime(ev.end_time) : null}</EventDetail>
                    <EventDetail label="Idle duration">{`${formatElapsedDuration(Number(ev.duration || 0))}${ev.ongoing ? " (still idling)" : ""}`}</EventDetail>
                    <EventDetail label="Vehicle">
                        {vehicle ? (
                            <>
                                <span className="font-mono">{vehicle}</span>
                                {ev.registration_plate && ev.vehicle_name && ev.vehicle_name !== ev.registration_plate && (
                                    <span className="block text-[11px] font-normal text-slate-500">{ev.vehicle_name}</span>
                                )}
                            </>
                        ) : null}
                    </EventDetail>
                    <EventDetail label="Vehicle group">{groups}</EventDetail>
                    <EventDetail label="Department">{ev.vehicle_department || null}</EventDetail>
                    <EventDetail label="Location">{address}</EventDetail>
                    <EventDetail label="Driver (Idle Report)">{ev.driver || null}</EventDetail>
                </dl>
            )}
        </li>
    )
}

// Today's (UAE) idle events attributed to this driver by the Idle Report's driver name; never by current assignment
function IdleEventsSection({ idle }) {
    const [expandedId, setExpandedId] = useState(null)
    const summary = idle?.summary
    const ok = summary?.status === "ok"
    const plus = ok && summary.partial ? "+" : ""
    const countLabel = idle?.loading && !summary ? "…" : ok && !idle?.error ? `${summary.count}${plus}` : "—"

    return (
        <section>
            <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-1.5">
                <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-700">
                    <Clock3 className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
                    Idle events today ({countLabel})
                </h3>
                {idle?.loading ? (
                    <span role="status" aria-label="Loading idle events" className="h-3 w-16 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
                ) : ok && !idle?.error ? (
                    <span className="text-[11px] font-semibold tabular-nums text-amber-800">{formatElapsedDuration(summary.totalSeconds)}{plus} idle</span>
                ) : null}
            </div>

            <div className="mt-2">
                {idle?.loading && !summary ? <SkeletonLines lines={3} label="Loading idle events" /> : idle?.error ? (
                    <p className={note}>Idle events could not be loaded.</p>
                ) : !ok ? (
                    <p className={note}>{IDLE_UNATTRIBUTABLE_MESSAGES[summary?.status] || "Idle events are not available."}</p>
                ) : (
                    <>
                        {summary.partial && <p className="mb-1 text-[11px] text-amber-700">Partial: some Wialon idle events could not be read</p>}
                        {summary.events.length === 0 ? (
                            summary.partial ? null : <p className={note}>No idle events recorded for this driver today.</p>
                        ) : (
                            <ul className="max-h-64 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
                                {summary.events.map((ev, idx) => {
                                    const id = ev.id || `${ev.vehicle_id}-${ev.start_time}-${idx}`
                                    return (
                                        <IdleEventRow
                                            key={id}
                                            ev={ev}
                                            expanded={expandedId === id}
                                            onToggle={() => setExpandedId((cur) => (cur === id ? null : id))}
                                        />
                                    )
                                })}
                            </ul>
                        )}
                    </>
                )}
            </div>
        </section>
    )
}

function DetailSection({ icon: Icon, title, children }) {
    return (
        <section>
            <h3 className="flex items-center gap-1.5 border-b border-slate-100 pb-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-700">
                <Icon className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
                {title}
            </h3>
            {children}
        </section>
    )
}

function Rows({ rows, status }) {
    return (
        <dl className="divide-y divide-slate-100">
            {rows.map((row) => (
                <div key={row.label} className="flex items-start justify-between gap-4 py-2">
                    <dt className="shrink-0 text-slate-500">{row.label}</dt>
                    <dd className={`min-w-0 break-words text-right font-medium text-slate-900 ${row.mono ? "font-mono" : ""}`}>
                        {row.label === "Status" && status ? (
                            <DriverStatusBadge status={status} />
                        ) : row.value == null ? (
                            <span className="font-sans font-normal text-slate-400">{NOT_AVAILABLE}</span>
                        ) : row.href ? (
                            <a href={row.href} className="text-brand hover:underline underline-offset-2">{row.value}</a>
                        ) : (
                            row.value
                        )}
                    </dd>
                </div>
            ))}
        </dl>
    )
}

/**
 * Contact, current vehicle assignment and today's idle events of one driver. Never shows Wialon's internal driver
 * record id. `idle`: { loading, error, summary } with summary from driverIdleSummary (the same one the table shows).
 */
export default function DriverDetailModal({ driver, idle, onClose }) {
    const closeRef = useRef(null)

    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === "Escape") onClose()
        }
        window.addEventListener("keydown", handleKeyDown)
        closeRef.current?.focus()
        return () => window.removeEventListener("keydown", handleKeyDown)
    }, [onClose])

    const { contact, assignment, issues } = detailRows(driver, (ts) => formatUAEDateTime(ts))

    return createPortal(
        <div
            className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px] animate-in fade-in duration-200"
            onClick={onClose}
        >
            <div
                className="relative flex max-h-[90vh] w-full max-w-md flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="driver-modal-title"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-3.5">
                    <div className="min-w-0">
                        <h2 id="driver-modal-title" className="truncate text-base font-bold text-slate-900">
                            {driver.name || NOT_AVAILABLE}
                        </h2>
                        <p className="truncate text-xs text-slate-500">
                            Employee ID <span className="font-mono">{driver.driver_id || NOT_AVAILABLE}</span>
                        </p>
                    </div>
                    <button
                        ref={closeRef}
                        type="button"
                        onClick={onClose}
                        className="-mr-1.5 shrink-0 rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                        aria-label="Close driver details"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                <div className="space-y-4 overflow-y-auto px-5 py-4 text-xs">
                    <DetailSection icon={Phone} title="Contact">
                        <Rows rows={contact} />
                    </DetailSection>

                    <DetailSection icon={Truck} title="Vehicle assignment">
                        <Rows rows={assignment} status={driver.assignment?.status} />
                    </DetailSection>

                    <IdleEventsSection idle={idle} />

                    {issues.length > 0 && (
                        <DetailSection icon={AlertTriangle} title="Data issues">
                            <ul className="mt-2 space-y-1.5">
                                {issues.map((issue) => (
                                    <li key={issue} className="flex items-start gap-1.5 text-amber-800">
                                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />
                                        {issue}
                                    </li>
                                ))}
                            </ul>
                        </DetailSection>
                    )}
                </div>
            </div>
        </div>,
        document.body
    )
}
