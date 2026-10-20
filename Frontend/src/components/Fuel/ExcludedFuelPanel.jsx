import { useEffect, useState } from "react"
import { AlertTriangle, ChevronDown, ChevronUp, Loader2, CheckCircle2 } from "lucide-react"
import { getFuelExcluded } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/FuelFilterContext"
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
            <div className="rounded-2xl border border-slate-200 bg-white p-4 mb-6 text-xs text-slate-500 flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-[#33758F]" />
                <span>Reconciling transactions excluded from KPIs…</span>
            </div>
        )
    }
    if (error) {
        return (
            <div className="rounded-2xl border border-rose-200 bg-rose-50/70 p-4 mb-6 text-xs font-semibold text-rose-700 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <span>{error}</span>
            </div>
        )
    }

    const summary = excludedSummary(data?.excluded_scope)
    if (!summary) {
        return (
            <div className="rounded-2xl border border-emerald-200 bg-emerald-50/50 p-3 mb-6 text-xs text-emerald-800 flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>Every transaction in this period is attributed to exactly one Wialon unit; nothing excluded from KPIs.</span>
            </div>
        )
    }

    const groups = Array.isArray(data?.data) ? data.data : []
    return (
        <div className="rounded-2xl border border-amber-200 bg-amber-50/40 p-4 mb-6 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-amber-900">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                    <span className="font-bold">
                        {summary.count.toLocaleString()} of {summary.allCount.toLocaleString()} transactions excluded from KPIs
                        ({fmt(summary.litres)} L, AED {fmt(summary.cost, 0)})
                    </span>
                </div>
                <button
                    type="button"
                    onClick={() => setOpen((o) => !o)}
                    className="inline-flex items-center gap-1 rounded-lg border border-amber-200 bg-white px-2.5 py-1 font-bold text-amber-800 hover:bg-amber-100 cursor-pointer"
                >
                    {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    {open ? "Hide list" : "Show list"}
                </button>
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                <span className="rounded-md bg-white border border-amber-200 px-2 py-0.5">No Wialon unit: <b>{summary.unmatched}</b></span>
                <span className="rounded-md bg-white border border-amber-200 px-2 py-0.5">Several Wialon units: <b>{summary.ambiguous}</b></span>
                <span className="rounded-md bg-white border border-amber-200 px-2 py-0.5">Suspected re-imports: <b>{summary.duplicates}</b></span>
            </div>
            {open && (
                <div className="mt-3 max-h-72 overflow-auto rounded-xl border border-amber-200 bg-white">
                    {groups.length === 0 ? (
                        <div className="p-4 text-center text-slate-400">No excluded groups to list.</div>
                    ) : (
                        <table className="w-full text-left text-[11px]">
                            <thead className="bg-slate-50 text-[10px] uppercase text-slate-500">
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
                                        <td className="px-3 py-1.5 font-mono font-bold text-slate-800">{g.vehicle_identifier || "(blank)"}</td>
                                        <td className="px-3 py-1.5 text-slate-600">
                                            {EXCLUDED_REASON_LABELS[g.reason] || g.reason}
                                            {g.candidate_unit_names?.length ? ` (${g.candidate_unit_names.join(", ")})` : ""}
                                        </td>
                                        <td className="px-3 py-1.5 text-right">{g.transaction_count}</td>
                                        <td className="px-3 py-1.5 text-right">{fmt(g.litres)}</td>
                                        <td className="px-3 py-1.5 text-right">{fmt(g.cost, 0)}</td>
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
