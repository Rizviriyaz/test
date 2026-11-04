import { STATUS_LABELS } from "./driverFilters"

const TONES = {
    assigned: "border-emerald-200 bg-emerald-50 text-emerald-800",
    unassigned: "border-slate-200 bg-slate-50 text-slate-600",
    ambiguous: "border-amber-200 bg-amber-50 text-amber-800",
    vehicle_not_found: "border-rose-200 bg-rose-50 text-rose-800",
}

/** Assignment status pill of one driver. */
export function DriverStatusBadge({ status }) {
    return (
        <span className={`inline-flex h-5 items-center whitespace-nowrap rounded-full border px-2 text-[11px] font-medium ${TONES[status] || TONES.unassigned}`}>
            {STATUS_LABELS[status] || "Unknown"}
        </span>
    )
}
