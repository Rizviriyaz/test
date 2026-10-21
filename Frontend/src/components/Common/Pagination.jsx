import { ChevronLeft, ChevronRight } from "lucide-react"

const navBtn = "inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900 cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"

/**
 * Table footer: "x–y of z" summary, optional extra controls, and prev/next paging.
 */
export default function Pagination({ page, totalPages, onPageChange, start, end, total, noun = "rows", children, className = "" }) {
    return (
        <div className={`flex flex-col gap-2 border-t border-slate-200 px-4 py-2 sm:flex-row sm:items-center sm:justify-between ${className}`}>
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span className="tabular-nums">
                    {total === 0 ? `0 ${noun}` : (
                        <>
                            <span className="font-medium text-slate-800">{start}–{end}</span> of{" "}
                            <span className="font-medium text-slate-800">{total.toLocaleString()}</span> {noun}
                        </>
                    )}
                </span>
                {children}
            </div>

            {totalPages > 1 && (
                <div className="flex items-center gap-1.5">
                    <button type="button" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={page <= 1} className={navBtn} aria-label="Previous page">
                        <ChevronLeft className="h-3.5 w-3.5" />
                    </button>
                    <span className="min-w-12 text-center text-xs tabular-nums text-slate-600">
                        {page} / {totalPages}
                    </span>
                    <button type="button" onClick={() => onPageChange(Math.min(totalPages, page + 1))} disabled={page >= totalPages} className={navBtn} aria-label="Next page">
                        <ChevronRight className="h-3.5 w-3.5" />
                    </button>
                </div>
            )}
        </div>
    )
}
