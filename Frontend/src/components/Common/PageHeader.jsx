import { RefreshCw } from "lucide-react"
import { MobileNavButton } from "../Layout/Sidebar"

/**
 * Page title row: title + one-line context on the left; filters, data freshness and
 * refresh on the right. `icon` is accepted for API compatibility but not rendered.
 */
export default function PageHeader({ title, subtitle, onRefresh, loading = false, lastUpdated = null, children, className = "" }) {
    const hasCustomMargin = className.includes("mb-") || className.includes("m-") || className.includes("my-")
    const hasCustomPadding = className.includes("pb-") || className.includes("p-") || className.includes("py-")
    const hasCustomBorder = className.includes("border-b-0") || className.includes("border-none")

    const defaultBorder = !hasCustomBorder ? "border-b border-slate-200" : ""
    const defaultPadding = !hasCustomPadding ? "pb-4" : ""
    const defaultMargin = !hasCustomMargin ? "mb-5" : ""

    return (
        <header className={`flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between ${defaultBorder} ${defaultPadding} ${defaultMargin} ${className}`}>
            <div className="flex min-w-0 items-center gap-3">
                <MobileNavButton />
                <div className="min-w-0">
                    <h1 className="truncate text-lg font-semibold tracking-tight text-slate-900">
                        {title}
                    </h1>
                    {subtitle && (
                        <p className="truncate text-[13px] text-slate-500">
                            {subtitle}
                        </p>
                    )}
                </div>
            </div>

            {(children || lastUpdated || onRefresh) && (
                <div className="flex flex-wrap items-center gap-2">
                    {children}

                    {lastUpdated && (
                        <span className="inline-flex h-8 items-center gap-1.5 px-1 text-xs tabular-nums text-slate-500" title="Last updated (UAE time)">
                            <span className="h-1.5 w-1.5 rounded-full bg-status-moving" aria-hidden="true" />
                            Updated {lastUpdated}
                        </span>
                    )}

                    {onRefresh && (
                        <button
                            type="button"
                            onClick={onRefresh}
                            disabled={loading}
                            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30 disabled:cursor-default disabled:opacity-60"
                        >
                            <RefreshCw className={`h-3.5 w-3.5 text-slate-500 ${loading ? "animate-spin motion-reduce:animate-none" : ""}`} />
                            <span>{loading ? "Updating…" : "Refresh"}</span>
                        </button>
                    )}
                </div>
            )}
        </header>
    )
}
