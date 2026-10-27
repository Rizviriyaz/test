import { useState, useEffect, useCallback } from "react"
import { Link } from "react-router-dom"
import { Loader2, ArrowUpRight, X } from "lucide-react"
import { getVehicleTrips, getFleetIdleAnalytics } from "../../api/fleetApi"
import { formatUAETime, getUAETodayBounds } from "../../utils/datetime"
import FleetStatusBadge from "../Common/FleetStatusBadge"
import { isPositionStale, lastSeenLabel, normalizeVehicleStatus } from "../../utils/vehicleStatus"
import LiveIdleTimer from "../Common/LiveIdleTimer"
import { formatElapsedDuration } from "../../utils/datetime"
import { formatKm, sumKnown, tripDistanceOf, tripsDistanceIncomplete, tripsDistanceKm } from "../../utils/knownTotals"
import { idleDurationStatus } from "../../utils/fleetRules"

// While the drawer stays open on one vehicle, its trips and idle events are re-read this often (the live record
// itself refreshes with the page's poll; the per-vehicle reports do not need to follow every poll)
const DRAWER_REFRESH_MS = 60000

import {
    MorphingDialog,
    MorphingDialogContent,
    MorphingDialogTitle,
    MorphingDialogSubtitle,
    MorphingDialogClose,
    MorphingDialogContainer,
} from "@/components/core/morphing-dialog"

import { ScrollArea } from "@/components/website/scroll-area"

function Section({ title, children }) {
    return (
        <section className="px-5 py-3.5">
            <h3 className="mb-1.5 text-xs font-semibold text-slate-900">{title}</h3>
            <dl className="divide-y divide-slate-100">{children}</dl>
        </section>
    )
}

function Row({ label, children, hint }) {
    return (
        <div className="flex items-baseline justify-between gap-4 py-1.5 text-xs">
            <dt className="shrink-0 text-slate-500">{label}</dt>
            <dd className="min-w-0 text-right font-medium text-slate-900">
                {children}
                {hint && <span className="block text-[11px] font-normal text-amber-700">{hint}</span>}
            </dd>
        </div>
    )
}

// `idleEvents` must cover today (UAE); `idleEventsComplete` false marks that list as partial (Wialon events unread)
export default function VehicleDetailDrawer({
    vehicle,
    open,
    onClose,
    idleEvents = null,
    idleEventsComplete = true,
    asDrawer = false,
    className = "",
}) {
    // Keep activeVehicle cached so the exit animation runs smoothly even when vehicle prop transitions to null
    const [activeVehicle, setActiveVehicle] = useState(vehicle)
    const [isOpen, setIsOpen] = useState(open !== undefined ? open : Boolean(vehicle))

    // Adjust state during render when the vehicle prop changes
    const [prevVehicle, setPrevVehicle] = useState(vehicle)
    if (vehicle !== prevVehicle) {
        setPrevVehicle(vehicle)
        if (vehicle) {
            setActiveVehicle(vehicle)
            if (open === undefined) setIsOpen(true)
        } else {
            if (open === undefined) setIsOpen(false)
        }
    }

    const [tripsData, setTripsData] = useState(null)
    const [loadingTrips, setLoadingTrips] = useState(false)
    const [vehicleIdleEvents, setVehicleIdleEvents] = useState(null)
    const [loadingIdleEvents, setLoadingIdleEvents] = useState(false)
    // "complete" | "partial" (some Wialon idle events unread, list is a lower bound) | "error" (list unavailable)
    const [idleEventsStatus, setIdleEventsStatus] = useState("complete")

    // Keyed by vehicle id: a live poll replaces the vehicle object every few seconds, which must not refetch the
    // vehicle's trips and fleet idle report each time
    const activeVehicleId = activeVehicle?.vehicle_id || activeVehicle?.id || null
    const [refreshTick, setRefreshTick] = useState(0)
    useEffect(() => {
        if (!activeVehicleId) return undefined
        const timer = setInterval(() => setRefreshTick(t => t + 1), DRAWER_REFRESH_MS)
        return () => clearInterval(timer)
    }, [activeVehicleId])

    useEffect(() => {
        if (!activeVehicleId) return
        let active = true

        async function fetchVehicleTripsData() {
            const vid = activeVehicleId
            try {
                setLoadingTrips(true)
                const { from_time, to_time } = getUAETodayBounds()
                const res = await getVehicleTrips(vid, { from_time, to_time })
                if (!active) return
                setTripsData(res)
            } catch (err) {
                console.error("Failed to fetch vehicle trips:", err)
                if (active) setTripsData(null)
            } finally {
                if (active) setLoadingTrips(false)
            }
        }

        async function fetchVehicleIdleEvents() {
            const vid = activeVehicleId
            if (idleEvents && Array.isArray(idleEvents)) {
                setVehicleIdleEvents(idleEvents.filter(e => String(e.vehicle_id) === String(vid)))
                setIdleEventsStatus(idleEventsComplete === false ? "partial" : "complete")
                return
            }
            try {
                setLoadingIdleEvents(true)
                const res = await getFleetIdleAnalytics({ vehicle_id: vid, timeframe: "today", include_events: true })
                if (!active) return
                const evs = (res?.events || []).filter(e => String(e.vehicle_id) === String(vid))
                setVehicleIdleEvents(evs)
                // Scoped to this vehicle, so events_complete describes its event list alone
                setIdleEventsStatus(res?.events_complete === false ? "partial" : "complete")
            } catch (err) {
                console.error("Failed to fetch vehicle idle events:", err)
                if (active) {
                    setVehicleIdleEvents(null)
                    setIdleEventsStatus("error")
                }
            } finally {
                if (active) setLoadingIdleEvents(false)
            }
        }

        fetchVehicleTripsData()
        fetchVehicleIdleEvents()
        return () => { active = false }
    }, [activeVehicleId, refreshTick, idleEvents, idleEventsComplete])

    const isDrawerVisible = open !== undefined ? open : isOpen

    const handleClose = useCallback((e) => {
        if (e) {
            e.stopPropagation?.()
            e.preventDefault?.()
        }
        setIsOpen(false)
        onClose?.()
    }, [onClose])

    const handleExitComplete = useCallback(() => {
        if (!isOpen) {
            onClose?.()
        }
    }, [isOpen, onClose])

    if (!activeVehicle && !vehicle) return null

    const currentVehicle = activeVehicle || vehicle
    const vehicleId = currentVehicle?.vehicle_id || currentVehicle?.id || "active"
    const morphId = `vehicle-${vehicleId}`

    const statusStr = normalizeVehicleStatus({ ...currentVehicle, status: currentVehicle.status || currentVehicle.normalizedStatus })
    // Telemetry-stale or OFFLINE: status, speed and ignition are the last reported values, not the current state
    const lastSeen = lastSeenLabel(currentVehicle)
    // Last seen Moving with a GPS fix older than 10 min although messages are recent
    const positionStale = isPositionStale(currentVehicle)

    // Customer-facing identifier: unit name only. Registration plate and unit ID are omitted from visible UI.
    const vehicleName = currentVehicle.vehicle_name || currentVehicle.name || "Fleet Vehicle"
    const vehicleGroup = currentVehicle.group || (Array.isArray(currentVehicle.groups) && currentVehicle.groups[0]) || "General Fleet"
    const unitId = currentVehicle.vehicle_id || currentVehicle.id || ""
    const hardwareType = currentVehicle.hardware_type || currentVehicle.device_type || currentVehicle.hw_type || null

    const lat = currentVehicle.latitude ?? currentVehicle.location?.latitude ?? null
    const lon = currentVehicle.longitude ?? currentVehicle.location?.longitude ?? null
    const satellites = currentVehicle.location?.satellites ?? currentVehicle.satellites ?? null
    const speed = Math.round(Number(currentVehicle.speed ?? currentVehicle.location?.speed ?? 0))

    const batteryVoltage = currentVehicle.sensors?.battery_voltage ?? currentVehicle.battery?.voltage ?? currentVehicle.battery_voltage ?? null
    // A missing or invalid ignition reading arrives as null: shown as Unknown, never Off
    const rawIgnition = currentVehicle.ignition !== undefined ? currentVehicle.ignition : currentVehicle.sensors?.ignition
    const ignitionState = rawIgnition === undefined ? null : (rawIgnition === null ? "UNKNOWN" : (rawIgnition ? "ON" : "OFF"))
    const driverId = currentVehicle.driver?.id || currentVehicle.driver_id || currentVehicle.sensors?.driver_id || null
    const cleanDriverId = driverId && !["0000000000000000", "0", "---", "none", "null"].includes(String(driverId).toLowerCase().trim()) ? String(driverId) : null

    // Activity metrics
    const tripCount = tripsData?.trip_count ?? (tripsData?.trips ? tripsData.trips.length : (currentVehicle.trip_count ?? currentVehicle.trips_count ?? null))
    // Trips whose distance cell was unreadable are left out of the sum and marked, never counted as 0 km
    const tripsDistanceSum = tripsData?.trips && tripsData.trips.length > 0 ? sumKnown(tripsData.trips, tripDistanceOf) : null
    const tripsDistance = tripsDistanceKm(tripsData)
    const todayDistance = currentVehicle.today_mileage ?? tripsDistance
    const distanceUnknownTrips = currentVehicle.today_mileage == null && tripsDistanceSum ? tripsDistanceSum.unknown : 0
    // A trip row Wialon sent malformed was dropped: the distance sum (and trip count) misses it
    const distanceIncomplete = currentVehicle.today_mileage == null && tripsDistanceIncomplete(tripsData)
    const tripRowsDropped = (tripsData?.incomplete_fields || []).includes("row_shape")
    const distanceHint = distanceUnknownTrips > 0
        ? `Partial: ${distanceUnknownTrips} trip(s) unknown`
        : (distanceIncomplete ? "Partial: some Wialon trip rows could not be read" : null)

    const spinner = <Loader2 className="inline h-3.5 w-3.5 animate-spin text-slate-400 motion-reduce:animate-none" aria-label="Loading" />
    const isIdling = statusStr === "IDLING"
    const idleStatus = idleDurationStatus(currentVehicle)

    const pageLinks = [
        { label: "Live Tracking", to: unitId ? `/tracking?vehicleId=${encodeURIComponent(unitId)}` : "/tracking" },
        { label: "Vehicle Health", to: "/vehicle-health" },
        { label: "Idle Reports", to: "/idle-analytics" },
    ]

    const drawerScrollArea = (
        <ScrollArea className="flex-1 divide-y divide-slate-200 overflow-y-auto">
                        <Section title="Status">
                            <Row label="Unit name"><span className="block truncate">{vehicleName}</span></Row>
                            {hardwareType && <Row label="Hardware device">{hardwareType}</Row>}
                            <Row label="Last update">
                                <span className="tabular-nums">{formatUAETime(currentVehicle.last_update || currentVehicle.last_message_timestamp || currentVehicle.timestamp)}</span>
                            </Row>
                            {/* GPS fix time is separate from the last message: a recent message may carry an old position */}
                            {/* Boolean(): a 0 timestamp (no position time, as the backend treats it) must not render a stray "0" */}
                            {Boolean(currentVehicle.position_timestamp) && (
                                <Row label="Last GPS fix">
                                    <span className={`tabular-nums ${positionStale ? "text-violet-800" : ""}`}>
                                        {formatUAETime(currentVehicle.position_timestamp)}{positionStale ? " (stale)" : ""}
                                    </span>
                                </Row>
                            )}
                            {lastSeen && (
                                <Row label="Telemetry"><span className="text-violet-800">{lastSeen}</span></Row>
                            )}
                            {ignitionState !== null && (
                                <Row label={lastSeen ? "Last ignition" : "Ignition"}>
                                    <span className={ignitionState === "ON" ? "text-slate-900" : "text-slate-500"}>{ignitionState === "ON" ? "On" : (ignitionState === "UNKNOWN" ? "Unknown" : "Off")}</span>
                                </Row>
                            )}
                            {batteryVoltage !== null && (
                                <Row label={lastSeen ? "Last battery" : "Battery"}><span className="tabular-nums">{Number(batteryVoltage).toFixed(2)} V</span></Row>
                            )}
                            {cleanDriverId !== null && <Row label="Assigned driver">{cleanDriverId}</Row>}
                        </Section>

                        <Section title="GPS">
                            {/* Stale GPS fix: coordinates and satellites describe the last fix, not the current position */}
                            <Row label={positionStale ? "Last coordinates" : "Coordinates"}>
                                <span className="font-mono text-[11px]">
                                    {lat !== null && lon !== null ? `${Number(lat).toFixed(4)}, ${Number(lon).toFixed(4)}` : "No GPS fix"}
                                </span>
                            </Row>
                            <Row label={positionStale ? "Last satellites" : "Satellites"}>
                                <span className="tabular-nums">
                                    {positionStale
                                        ? (satellites !== null ? `${satellites} at last fix` : "Unknown")
                                        : (satellites !== null ? `${satellites} locked` : "GPS active")}
                                </span>
                            </Row>
                        </Section>

                        <Section title="Today">
                            <Row
                                label="Distance"
                                hint={!loadingTrips ? distanceHint : null}
                            >
                                <span className="tabular-nums">
                                    {loadingTrips ? spinner : (formatKm(todayDistance) ?? "—")}
                                </span>
                            </Row>
                            <Row label="Trips" hint={!loadingTrips && tripRowsDropped ? "Partial: some Wialon trip rows could not be read" : null}>
                                <span className="tabular-nums">{loadingTrips ? spinner : (tripCount !== null ? tripCount : "—")}</span>
                            </Row>
                        </Section>

                        <section className="px-5 py-3.5">
                            <div className="mb-1.5 flex items-baseline justify-between gap-2">
                                <h3 className="text-xs font-semibold text-slate-900">Idling</h3>
                                <span className="text-[11px] text-slate-400">UAE time</span>
                            </div>
                            <dl className="divide-y divide-slate-100">
                                <Row label="Started">
                                    {isIdling && currentVehicle.idle_start
                                        ? <span className="tabular-nums">{idleStatus === "lower_bound" ? "Before " : ""}{formatUAETime(currentVehicle.idle_start)}</span>
                                        : (isIdling ? "Start time unavailable" : <span className="font-normal text-slate-500">{lastSeen ? "Unknown (telemetry delayed)" : "Not currently idling"}</span>)}
                                </Row>
                                {/* A vehicle that is not idling has no current idle duration (the backend sends 0), so no row */}
                                {isIdling && (
                                    <Row label="Elapsed">
                                        {idleStatus === "unknown" ? (
                                            <span className="font-normal text-slate-500" title="The idle start could not be read from Wialon history">Unknown</span>
                                        ) : (
                                            <span className="inline-flex items-center gap-1.5 text-amber-800" title={idleStatus === "lower_bound" ? "Idling began before the history searched; at least this long" : undefined}>
                                                <span className="h-1.5 w-1.5 rounded-full bg-status-idling" aria-hidden="true" />
                                                <LiveIdleTimer
                                                    startTime={currentVehicle.idle_start}
                                                    baseDuration={currentVehicle.idle_duration}
                                                    status={statusStr}
                                                    endTime={currentVehicle.last_message_timestamp}
                                                />
                                                {idleStatus === "lower_bound" && <span>+</span>}
                                            </span>
                                        )}
                                    </Row>
                                )}
                            </dl>

                            <div className="mt-3">
                                <div className="mb-1 flex items-center justify-between">
                                    <span className="text-[11px] font-medium text-slate-600">
                                        Idle events today ({idleEventsStatus === "error" ? "—" : `${vehicleIdleEvents ? vehicleIdleEvents.length : 0}${idleEventsStatus === "partial" ? "+" : ""}`})
                                    </span>
                                    {loadingIdleEvents && spinner}
                                </div>
                                {!loadingIdleEvents && idleEventsStatus === "partial" && (
                                    <p className="mb-1 text-[11px] text-amber-700">Partial: some Wialon idle events could not be read</p>
                                )}

                                {loadingIdleEvents ? null : idleEventsStatus === "error" ? (
                                    <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
                                        Idle events could not be loaded.
                                    </p>
                                ) : !vehicleIdleEvents || vehicleIdleEvents.length === 0 ? (
                                    idleEventsStatus === "partial" ? null :
                                    <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">
                                        No idle events recorded for this vehicle today.
                                    </p>
                                ) : (
                                    <ul className="max-h-40 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200">
                                        {vehicleIdleEvents.map((ev, idx) => (
                                            <li key={ev.id || idx} className="flex items-center justify-between gap-3 px-3 py-1.5 text-xs">
                                                <div className="min-w-0">
                                                    {/* Start/end bound the engine-on period; the duration is the idling within it */}
                                                    <p className="tabular-nums text-slate-800" title="Engine-on period">
                                                        Engine on {formatUAETime(ev.start_time)} → {ev.end_time ? formatUAETime(ev.end_time) : "ongoing"}
                                                    </p>
                                                    {ev.address && <p className="truncate text-[11px] text-slate-500">{ev.address}</p>}
                                                </div>
                                                <span className="shrink-0 font-semibold tabular-nums text-amber-800" title="Idle time within the engine-on period">
                                                    {formatElapsedDuration(Number(ev.duration || 0))} idle
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        </section>

                        <nav aria-label="Open in" className="grid grid-cols-2 gap-px bg-slate-200">
                            {pageLinks.map(link => (
                                <Link
                                    key={link.label}
                                    to={link.to}
                                    className="flex items-center justify-between bg-white px-5 py-2.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-brand"
                                >
                                    {link.label}
                                    <ArrowUpRight className="h-3.5 w-3.5 text-slate-400" />
                                </Link>
                            ))}
                        </nav>
                    </ScrollArea>
    )

    if (asDrawer) {
        if (!isDrawerVisible || !currentVehicle) return null
        return (
            <aside
                aria-label="Vehicle details drawer"
                className={`absolute right-3 top-3 bottom-3 sm:right-4 sm:top-4 sm:bottom-4 z-20 flex flex-col w-[340px] sm:w-[380px] max-w-[calc(100vw-1.5rem)] rounded-2xl border border-slate-200/90 bg-white/95 backdrop-blur-md shadow-[0_12px_36px_-6px_rgba(15,23,42,0.18),0_4px_12px_-2px_rgba(15,23,42,0.06)] overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${className}`}
            >
                {/* Header */}
                <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5 sm:py-3.5">
                    <div className="min-w-0">
                        <h2 className="truncate text-base font-semibold text-slate-900 font-jakarta">
                            {vehicleName}
                        </h2>
                        <div className="mt-1 flex items-center gap-2 text-xs">
                            <FleetStatusBadge status={statusStr} size="xs" />
                            <span className="text-slate-300" aria-hidden="true">·</span>
                            <span className="truncate text-slate-500">{vehicleGroup}</span>
                            <span className="text-slate-300" aria-hidden="true">·</span>
                            <span className="shrink-0 tabular-nums text-slate-900" title={lastSeen ? "Last reported speed" : undefined}>{speed} <span className="text-slate-500">km/h{lastSeen ? " (last)" : ""}</span></span>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={handleClose}
                        aria-label="Close drawer"
                        className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors cursor-pointer shrink-0"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                {drawerScrollArea}
            </aside>
        )
    }

    return (
        <MorphingDialog
            open={isOpen}
            onClose={handleClose}
            layoutId={morphId}
            transition={{
                type: "spring",
                stiffness: 200,
                damping: 24,
            }}
        >
            <MorphingDialogContainer onExitComplete={handleExitComplete}>
                <MorphingDialogContent
                    layoutId={morphId}
                    className="relative flex max-h-[85vh] w-full max-w-[460px] flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-dialog"
                >
                    {/* Header */}
                    <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200 px-5 py-4">
                        <div className="min-w-0">
                            <MorphingDialogTitle className="truncate text-base font-semibold text-slate-900">
                                {vehicleName}
                            </MorphingDialogTitle>
                            <MorphingDialogSubtitle className="mt-1 flex items-center gap-2 text-xs">
                                <FleetStatusBadge status={statusStr} size="xs" />
                                <span className="text-slate-300" aria-hidden="true">·</span>
                                <span className="truncate text-slate-500">{vehicleGroup}</span>
                                <span className="text-slate-300" aria-hidden="true">·</span>
                                <span className="shrink-0 tabular-nums text-slate-900" title={lastSeen ? "Last reported speed" : undefined}>{speed} <span className="text-slate-500">km/h{lastSeen ? " (last)" : ""}</span></span>
                            </MorphingDialogSubtitle>
                        </div>
                        <MorphingDialogClose onClick={handleClose} />
                    </div>

                    {drawerScrollArea}
                </MorphingDialogContent>
            </MorphingDialogContainer>
        </MorphingDialog>
    )
}
