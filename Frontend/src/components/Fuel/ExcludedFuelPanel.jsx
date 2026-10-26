import { useEffect, useState, useMemo } from "react"
import { AlertTriangle, ChevronDown, ChevronUp, Loader2, CheckCircle2, Search, ShieldAlert } from "lucide-react"
import { getFuelExcluded } from "../../api/fleetApi"
import { useFuelFilters } from "../../context/useFuelFilters"
import { EXCLUDED_REASON_LABELS, excludedSummary } from "./fuelLabels"

function fmt(val, decimals = 1) {
    if (val === null || val === undefined || isNaN(val)) return "—"
    return Number(val).toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

const REASON_EXPLANATIONS = {
    unmatched: "No matching Wialon GPS telematics unit found for this vehicle identifier.",
    ambiguous: "Identifier corresponds to multiple distinct Wialon telematics units.",
    suspected_duplicate: "Identified as a duplicate or re-imported fuel transaction.",
}

export default function ExcludedFuelPanel() {
    const { queryParams } = useFuelFilters()
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [open, setOpen] = useState(false)
    const [searchQuery, setSearchQuery] = useState("")
    const [selectedReason, setSelectedReason] = useState("all")

    useEffect(() => {
        let active = true
        async function load() {
            try {
                setLoading(true)
                setError(null)
                const res = await getFuelExcluded({ ...queryParams, page: 1, page_size: 100 })
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

    const summary = excludedSummary(data?.excluded_scope)
    const allGroups = useMemo(() => Array.isArray(data?.data) ? data.data : [], [data])

    const filteredGroups = useMemo(() => {
        return allGroups.filter((g) => {
            const matchesReason = selectedReason === "all" || g.reason === selectedReason
            const query = searchQuery.trim().toLowerCase()
            const matchesSearch = !query ||
                (g.vehicle_identifier && g.vehicle_identifier.toLowerCase().includes(query)) ||
                (g.candidate_unit_names && g.candidate_unit_names.some((u) => u.toLowerCase().includes(query)))
            return matchesReason && matchesSearch
        })
    }, [allGroups, selectedReason, searchQuery])

    if (loading) {
        return (
            <div role="status" className="mb-4 flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3.5 py-2.5 text-xs text-slate-500 shadow-xs">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-brand motion-reduce:animate-none" />
                <span>Reconciling transaction matching and data quality…</span>
            </div>
        )
    }

    if (error) {
        return (
            <div role="alert" className="mb-4 flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-800 shadow-xs">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-600" />
                <span>{error}</span>
            </div>
        )
    }

    if (!summary) {
        return (
            <div className="mb-4 flex items-center justify-between rounded-lg border border-emerald-200 bg-emerald-50/50 px-3.5 py-2 text-xs text-emerald-900 shadow-xs">
                <div className="flex items-center gap-2">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                    <span className="font-medium">
                        Complete Data Integrity: 100% of fuel transactions in this period are matched to verified Wialon units.
                    </span>
                </div>
            </div>
        )
    }

    const matchedCount = Math.max(0, summary.allCount - summary.count)
    const matchedPct = summary.allCount > 0 ? ((matchedCount / summary.allCount) * 100).toFixed(1) : 0
    const excludedPct = summary.allCount > 0 ? ((summary.count / summary.allCount) * 100).toFixed(1) : 0

    return (
        <section aria-labelledby="data-quality-heading" className="mb-4 rounded-lg border border-slate-200 bg-white p-3.5 shadow-xs">
            {/* Header & High-level Summary */}
            <div className="flex flex-col gap-2.5 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2.5">
                    <ShieldAlert className="h-4 w-4 text-amber-600 shrink-0" />
                    <div>
                        <div className="flex items-center gap-2">
                            <h2 id="data-quality-heading" className="text-xs font-semibold text-slate-900">
                                Transaction Matching & Scope Reconciliation
                            </h2>
                            <span className="rounded bg-amber-50 border border-amber-200 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 tabular-nums">
                                {summary.count.toLocaleString()} excluded ({excludedPct}%)
                            </span>
                        </div>
                        <p className="mt-0.5 text-[11px] text-slate-500">
                            KPIs strictly include vehicles matched to exactly one Wialon unit to ensure mathematical accuracy.
                        </p>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={() => setOpen((o) => !o)}
                    aria-expanded={open}
                    className="inline-flex h-7 items-center gap-1.5 self-start sm:self-auto rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 cursor-pointer"
                >
                    <span>{open ? "Hide details" : "Investigate excluded"}</span>
                    {open ? <ChevronUp className="h-3.5 w-3.5 text-slate-400" /> : <ChevronDown className="h-3.5 w-3.5 text-slate-400" />}
                </button>
            </div>

            {/* Metrics Chips Grid */}
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6 border-t border-slate-100 pt-3 text-xs">
                <div className="rounded-md border border-emerald-100 bg-emerald-50/60 p-2">
                    <span className="block text-[11px] text-emerald-800">Matched in KPIs</span>
                    <span className="font-semibold tabular-nums text-emerald-900">
                        {matchedCount.toLocaleString()} tx ({matchedPct}%)
                    </span>
                </div>

                <div className="rounded-md border border-slate-200 bg-slate-50/70 p-2">
                    <span className="block text-[11px] text-slate-500">No Wialon Unit</span>
                    <span className="font-semibold tabular-nums text-slate-900">
                        {summary.unmatched.toLocaleString()} tx
                    </span>
                </div>

                <div className="rounded-md border border-slate-200 bg-slate-50/70 p-2">
                    <span className="block text-[11px] text-slate-500">Multiple Units</span>
                    <span className="font-semibold tabular-nums text-slate-900">
                        {summary.ambiguous.toLocaleString()} tx
                    </span>
                </div>

                <div className="rounded-md border border-slate-200 bg-slate-50/70 p-2">
                    <span className="block text-[11px] text-slate-500">Suspected Duplicates</span>
                    <span className="font-semibold tabular-nums text-slate-900">
                        {summary.duplicates.toLocaleString()} tx
                    </span>
                </div>

                <div className="rounded-md border border-amber-200/70 bg-amber-50/40 p-2">
                    <span className="block text-[11px] text-amber-800">Excluded Volume</span>
                    <span className="font-semibold tabular-nums text-amber-900">
                        {fmt(summary.litres)} L
                    </span>
                </div>

                <div className="rounded-md border border-amber-200/70 bg-amber-50/40 p-2">
                    <span className="block text-[11px] text-amber-800">Excluded Spend</span>
                    <span className="font-semibold tabular-nums text-amber-900">
                        AED {fmt(summary.cost, 0)}
                    </span>
                </div>
            </div>

            {/* Expandable Investigation View */}
            {open && (
                <div className="mt-3.5 space-y-3 rounded-lg border border-slate-200 bg-slate-50/50 p-3">
                    {/* Filters Toolbar */}
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                            {/* Search Input */}
                            <div className="relative">
                                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                                <input
                                    type="text"
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    placeholder="Filter by plate or unit…"
                                    className="h-7 w-48 rounded-md border border-slate-200 bg-white pl-8 pr-2.5 text-xs text-slate-800 placeholder-slate-400 focus:border-brand focus:outline-hidden"
                                />
                            </div>

                            {/* Reason Filter Buttons */}
                            <div role="group" aria-label="Filter by exclusion reason" className="inline-flex rounded-md bg-white border border-slate-200 p-0.5 text-xs">
                                {[
                                    { id: "all", label: "All Reasons" },
                                    { id: "unmatched", label: "No Wialon Unit" },
                                    { id: "ambiguous", label: "Multiple Units" },
                                    { id: "suspected_duplicate", label: "Duplicates" },
                                ].map((tab) => (
                                    <button
                                        key={tab.id}
                                        type="button"
                                        onClick={() => setSelectedReason(tab.id)}
                                        aria-pressed={selectedReason === tab.id}
                                        className={`rounded px-2 py-0.5 text-[11px] font-medium transition-colors cursor-pointer ${
                                            selectedReason === tab.id
                                                ? "bg-slate-100 text-slate-900 font-semibold"
                                                : "text-slate-600 hover:text-slate-900"
                                        }`}
                                    >
                                        {tab.label}
                                    </button>
                                ))}
                            </div>
                        </div>

                        <span className="text-[11px] text-slate-500 tabular-nums">
                            Showing {filteredGroups.length} of {allGroups.length} excluded groups
                        </span>
                    </div>

                    {/* Excluded Items Table */}
                    <div className="max-h-72 overflow-auto rounded-md border border-slate-200 bg-white shadow-xs">
                        {filteredGroups.length === 0 ? (
                            <div className="p-6 text-center text-xs text-slate-400">
                                No excluded transactions matching the current filters.
                            </div>
                        ) : (
                            <table className="w-full text-left text-xs">
                                <thead className="sticky top-0 bg-slate-50 text-[11px] text-slate-500 border-b border-slate-200">
                                    <tr>
                                        <th className="px-3 py-2 font-medium">Vehicle Identifier</th>
                                        <th className="px-3 py-2 font-medium">Exclusion Reason</th>
                                        <th className="px-3 py-2 font-medium">Candidate Units / Notes</th>
                                        <th className="px-3 py-2 text-right font-medium">Transactions</th>
                                        <th className="px-3 py-2 text-right font-medium">Volume (L)</th>
                                        <th className="px-3 py-2 text-right font-medium">Spend (AED)</th>
                                        <th className="px-3 py-2 font-medium">Date Range</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 text-[11px]">
                                    {filteredGroups.map((g) => (
                                        <tr key={`${g.reason}-${g.vehicle_identifier}`} className="hover:bg-slate-50/80 transition-colors">
                                            <td className="px-3 py-2 font-mono font-semibold text-slate-900">
                                                {g.vehicle_identifier || "(blank)"}
                                            </td>
                                            <td className="px-3 py-2">
                                                <span className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                                    g.reason === "unmatched"
                                                        ? "bg-slate-100 text-slate-700"
                                                        : g.reason === "ambiguous"
                                                            ? "bg-amber-100 text-amber-800"
                                                            : "bg-rose-100 text-rose-800"
                                                }`}>
                                                    {EXCLUDED_REASON_LABELS[g.reason] || g.reason}
                                                </span>
                                            </td>
                                            <td className="px-3 py-2 text-slate-500 max-w-[200px] truncate" title={REASON_EXPLANATIONS[g.reason]}>
                                                {g.candidate_unit_names?.length
                                                    ? g.candidate_unit_names.join(", ")
                                                    : REASON_EXPLANATIONS[g.reason]}
                                            </td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                                                {g.transaction_count}
                                            </td>
                                            <td className="px-3 py-2 text-right tabular-nums font-medium text-slate-900">
                                                {fmt(g.litres)}
                                            </td>
                                            <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                                                {fmt(g.cost, 0)}
                                            </td>
                                            <td className="px-3 py-2 text-slate-500 whitespace-nowrap">
                                                {g.first_date || "—"}{g.last_date && g.last_date !== g.first_date ? ` → ${g.last_date}` : ""}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </div>
            )}
        </section>
    )
}

