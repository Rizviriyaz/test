import { ChevronLeft, ChevronRight } from "lucide-react"

const navBtn = "inline-flex h-8 items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-semibold text-slate-700 shadow-2xs transition hover:bg-slate-50 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"

/**
 * Table footer: "Showing x–y of z" summary, optional extra controls, and prev/next paging.
 */
export default function Pagination({ page, totalPages, onPageChange, start, end, total, noun = "rows", children, className = "" }) {
    return (
        <div className={`flex flex-col gap-2 border-t border-slate-200 bg-slate-50/60 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:px-5 ${className}`}>
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span className="tabular-nums">
                    {total === 0 ? `0 ${noun}` : (
                        <>
                            <strong className="font-semibold text-slate-800">{start}–{end}</strong> of{" "}
                            <strong className="font-semibold text-slate-800">{total.toLocaleString()}</strong> {noun}
                        </>
                    )}
                </span>
                {children}
            </div>

            {totalPages > 1 && (
                <div className="flex items-center gap-1">
                    <button type="button" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={page <= 1} className={navBtn} aria-label="Previous page">
                        <ChevronLeft className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Prev</span>
                    </button>
                    <span className="px-2 text-xs font-semibold tabular-nums text-slate-600">
                        {page} / {totalPages}
                    </span>
                    <button type="button" onClick={() => onPageChange(Math.min(totalPages, page + 1))} disabled={page >= totalPages} className={navBtn} aria-label="Next page">
                        <span className="hidden sm:inline">Next</span>
                        <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                </div>
            )}
        </div>
    )
}
