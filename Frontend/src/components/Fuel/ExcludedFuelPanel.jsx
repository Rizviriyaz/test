import { useEffect, useState } from "react"
import { AlertTriangle, ChevronDown, ChevronUp, Loader2, CheckCircle2 } from "lucide-react"
import { getFuelExcluded } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/useFuelFilters"
import { EXCLUDED_REASON_LABELS, excludedSummary } from "./fuelLabels"

function fmt(val, decimals = 1) {
    return Number(val || 0).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

// Transactions left out of the fuel KPIs (no single Wialon unit, or suspected re-imports). Nothing is deleted.
export default function ExcludedFuelPanel() {
    const { queryParams } = useFuelFilters()
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [open, setOpen] = useState(false)

    useEffect(() => {
        let active = true
        async function load() {
            try {
                setLoading(true)
                setError(null)
                const res = await getFuelExcluded({ ...queryParams, page: 1, page_size: 50 })
                if (active) setData(res)
            } catch (err) {
                if (active) setError(err.message || "Failed to load excluded transactions.")
            } finally {
                if (active) setLoading(false)
            }
        }
        load()
        return () => { active = false }
    }, [queryParams])

    if (loading) {
        return (
            <div role="status" className="mb-5 flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400 motion-reduce:animate-none" />
                <span>Reconciling transactions excluded from KPIs…</span>
            </div>
        )
    }
    if (error) {
        return (
            <div role="alert" className="mb-5 flex items-center gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-600" />
                <span>{error}</span>
            </div>
        )
    }

    const summary = excludedSummary(data?.excluded_scope)
    if (!summary) {
        return (
            <div className="mb-5 flex items-center gap-2 px-1 text-xs text-slate-500">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-status-moving" />
                <span>Every transaction in this period is attributed to exactly one Wialon unit; nothing excluded from KPIs.</span>
            </div>
        )
    }

    const groups = Array.isArray(data?.data) ? data.data : []
    return (
        <div className="mb-5 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-amber-900">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600" />
                    <span className="font-medium">
                        {summary.count.toLocaleString()} of {summary.allCount.toLocaleString()} transactions excluded from KPIs
                        ({fmt(summary.litres)} L, AED {fmt(summary.cost, 0)})
                    </span>
                </div>
                <button
                    type="button"
                    onClick={() => setOpen((o) => !o)}
                    aria-expanded={open}
                    className="inline-flex h-7 items-center gap-1 rounded-md border border-amber-200 bg-white px-2 font-medium text-amber-900 transition-colors hover:bg-amber-100 cursor-pointer"
                >
                    {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    {open ? "Hide list" : "Show list"}
                </button>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 pl-5 text-[11px] text-amber-900">
                <span>No Wialon unit <b className="font-semibold tabular-nums">{summary.unmatched}</b></span>
                <span>Several Wialon units <b className="font-semibold tabular-nums">{summary.ambiguous}</b></span>
                <span>Suspected re-imports <b className="font-semibold tabular-nums">{summary.duplicates}</b></span>
            </div>
            {open && (
                <div className="mt-2.5 max-h-72 overflow-auto rounded-md border border-amber-200 bg-white">
                    {groups.length === 0 ? (
                        <div className="p-4 text-center text-slate-400">No excluded groups to list.</div>
                    ) : (
                        <table className="w-full text-left text-[11px]">
                            <thead className="sticky top-0 bg-slate-50 text-[11px] text-slate-500">
                                <tr>
                                    <th className="px-3 py-2">Identifier</th>
                                    <th className="px-3 py-2">Reason</th>
                                    <th className="px-3 py-2 text-right">Tx</th>
                                    <th className="px-3 py-2 text-right">Litres</th>
                                    <th className="px-3 py-2 text-right">AED</th>
                                    <th className="px-3 py-2">Dates</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {groups.map((g) => (
                                    <tr key={`${g.reason}-${g.vehicle_identifier}`}>
                                        <td className="px-3 py-1.5 font-mono font-medium text-slate-900">{g.vehicle_identifier || "(blank)"}</td>
                                        <td className="px-3 py-1.5 text-slate-600">
                                            {EXCLUDED_REASON_LABELS[g.reason] || g.reason}
                                            {g.candidate_unit_names?.length ? ` (${g.candidate_unit_names.join(", ")})` : ""}
                                        </td>
                                        <td className="px-3 py-1.5 text-right tabular-nums">{g.transaction_count}</td>
                                        <td className="px-3 py-1.5 text-right tabular-nums">{fmt(g.litres)}</td>
                                        <td className="px-3 py-1.5 text-right tabular-nums">{fmt(g.cost, 0)}</td>
                                        <td className="px-3 py-1.5 text-slate-500 whitespace-nowrap">
                                            {g.first_date || "—"}{g.last_date && g.last_date !== g.first_date ? ` → ${g.last_date}` : ""}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                    {data?.total > groups.length && (
                        <div className="p-2 text-center text-[10px] text-slate-400">
                            Showing {groups.length} of {data.total} groups.
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
