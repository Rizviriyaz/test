/**
 * Telemetry match status for a fuel vehicle, matching backend semantics exactly:
 * - matched: GPS tracking active with telemetry
 * - not_tracked: No Wialon unit associated
 * - no_distance_data: Wialon unit resolved but 0 distance/trips recorded
 * - ambiguous: Multiple Wialon units matched
 * Rendered as marker + label so state never depends on color alone.
 */
const STATUS = {
    matched: { label: "Tracked", dot: "bg-status-moving", text: "text-slate-600" },
    no_distance_data: { label: "No mileage telemetry", dot: "bg-status-idling", text: "text-amber-800" },
    ambiguous: { label: "Ambiguous unit", dot: "bg-status-offline", text: "text-rose-700" },
    not_tracked: { label: "Untracked", dot: "bg-slate-300", text: "text-slate-500" },
}

export default function VehicleStatusBadge({ status, className = "" }) {
    const cfg = STATUS[String(status || "").toLowerCase().trim()] || STATUS.not_tracked
    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium ${cfg.text} ${className}`}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cfg.dot}`} aria-hidden="true" />
            {cfg.label}
        </span>
    )
}
