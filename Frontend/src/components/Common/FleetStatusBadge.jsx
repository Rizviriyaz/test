/**
 * Operational vehicle status: a colored marker plus label (never color alone).
 * - MOVING: speed > 0, ignition on
 * - IDLING: speed = 0, ignition on
 * - STOPPED: stationary, ignition off
 * - OFFLINE: telemetry stale / disconnected
 * Unknown values render as Stopped.
 */
const STATUS = {
    MOVING: { label: "Moving", dot: "bg-status-moving", text: "text-emerald-800" },
    IDLING: { label: "Idling", dot: "bg-status-idling", text: "text-amber-800" },
    STOPPED: { label: "Stopped", dot: "bg-status-stopped", text: "text-slate-600" },
    OFFLINE: { label: "Offline", dot: "bg-status-offline", text: "text-rose-700" },
}

export default function FleetStatusBadge({ status, className = "", size = "sm", showDot = true }) {
    const config = STATUS[String(status || "").toUpperCase().trim()] || STATUS.STOPPED
    const sizeCls = size === "xs" ? "text-[11px]" : size === "md" ? "text-[13px]" : "text-xs"

    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap font-medium select-none ${config.text} ${sizeCls} ${className}`}>
            {showDot && <span className={`h-2 w-2 shrink-0 rounded-full ${config.dot}`} aria-hidden="true" />}
            <span>{config.label}</span>
        </span>
    )
}
