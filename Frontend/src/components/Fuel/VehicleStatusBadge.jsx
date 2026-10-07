import { CheckCircle2, AlertCircle, HelpCircle, MinusCircle } from "lucide-react"

/**
 * Standardized Vehicle Status Badge matching backend semantics exactly:
 * - matched: GPS tracking active with telemetry
 * - not_tracked: No Wialon unit associated
 * - no_distance_data: Wialon unit resolved but 0 distance/trips recorded
 * - ambiguous: Multiple Wialon units matched
 */
export default function VehicleStatusBadge({ status, className = "" }) {
    const raw = String(status || "").toLowerCase().trim()

    switch (raw) {
        case "matched":
            return (
                <span className={`inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 border border-emerald-200/70 shadow-2xs ${className}`}>
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                    <span>Tracked</span>
                </span>
            )

        case "no_distance_data":
            return (
                <span className={`inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700 border border-amber-200/70 shadow-2xs ${className}`}>
                    <AlertCircle className="h-3.5 w-3.5 text-amber-600 shrink-0" />
                    <span>No Mileage Telemetry</span>
                </span>
            )

        case "ambiguous":
            return (
                <span className={`inline-flex items-center gap-1.5 rounded-full bg-rose-50 px-2.5 py-0.5 text-xs font-semibold text-rose-700 border border-rose-200/70 shadow-2xs ${className}`}>
                    <AlertCircle className="h-3.5 w-3.5 text-rose-600 shrink-0" />
                    <span>Ambiguous Unit</span>
                </span>
            )

        case "not_tracked":
        default:
            return (
                <span className={`inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-600 border border-slate-200 shadow-2xs ${className}`}>
                    <MinusCircle className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                    <span>Untracked</span>
                </span>
            )
    }
}
