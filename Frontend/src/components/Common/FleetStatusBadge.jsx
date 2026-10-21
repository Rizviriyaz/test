
/**
 * Standardized operational fleet status badge.
 * Represents real-time vehicle movement/ignition states:
 * - MOVING: speed > 0, ignition ON (Emerald green)
 * - IDLING: speed = 0, ignition ON (Amber)
 * - STOPPED: stationary, ignition OFF (Neutral slate)
 * - OFFLINE: telemetry stale / disconnected (Rose red)
 */
export default function FleetStatusBadge({ status, className = "", size = "sm", showDot = true }) {
    const raw = String(status || "").toUpperCase().trim()

    let config = {
        label: "Stopped",
        badgeCls: "bg-slate-100 text-slate-700 border-slate-200",
        dotCls: "bg-slate-400",
    }

    if (raw === "MOVING") {
        config = {
            label: "Moving",
            badgeCls: "bg-emerald-50 text-emerald-700 border-emerald-200",
            dotCls: "bg-emerald-500",
        }
    } else if (raw === "IDLING") {
        config = {
            label: "Idling",
            badgeCls: "bg-amber-50 text-amber-700 border-amber-200",
            dotCls: "bg-amber-500",
        }
    } else if (raw === "OFFLINE") {
        config = {
            label: "Offline",
            badgeCls: "bg-rose-50 text-rose-700 border-rose-200",
            dotCls: "bg-rose-500",
        }
    } else if (raw === "STOPPED") {
        config = {
            label: "Stopped",
            badgeCls: "bg-slate-100 text-slate-700 border-slate-200",
            dotCls: "bg-slate-400",
        }
    }

    const sizeCls = size === "xs"
        ? "px-2 py-0.5 text-[10px]"
        : size === "md"
        ? "px-3 py-1 text-xs"
        : "px-2.5 py-0.5 text-[11px]"

    return (
        <span
            className={`inline-flex items-center gap-1.5 rounded-full border font-semibold select-none transition-colors ${config.badgeCls} ${sizeCls} ${className}`}
        >
            {showDot && (
                <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${config.dotCls}`} />
            )}
            <span>{config.label}</span>
        </span>
    )
}
