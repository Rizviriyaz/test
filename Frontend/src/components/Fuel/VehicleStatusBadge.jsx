/**
 * Telemetry match status for a fuel vehicle, matching backend semantics exactly:
 * - matched: GPS tracking active with telemetry
 * - not_tracked: No Wialon unit matches the identifier (business status unconfirmed; not assumed untracked)
 * - no_distance_data: Wialon unit resolved but 0 distance/trips recorded
 * - ambiguous: Multiple Wialon units matched
 * - any other value: Unknown
 * Rendered as marker + label so state never depends on color alone.
 */
const STATUS = {
    matched: { label: "Tracked", dot: "bg-status-moving", text: "text-slate-600" },
    no_distance_data: { label: "No mileage telemetry", dot: "bg-status-idling", text: "text-amber-800" },
    ambiguous: { label: "Ambiguous unit", dot: "bg-status-offline", text: "text-rose-700" },
    not_tracked: { label: "No Wialon unit", dot: "bg-slate-300", text: "text-slate-500" },
}
const UNKNOWN = { label: "Unknown", dot: "bg-slate-300", text: "text-slate-500" }

export default function VehicleStatusBadge({ status, className = "" }) {
    const cfg = STATUS[String(status || "").toLowerCase().trim()] || UNKNOWN
    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[11px] font-medium ${cfg.text} ${className}`}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${cfg.dot}`} aria-hidden="true" />
            {cfg.label}
        </span>
    )
}
