import { RefreshCw } from "lucide-react"
import { MobileNavButton } from "../Layout/Sidebar"

/**
 * Page title row / top controls bar:
 * When title/subtitle are provided, renders title on the left and controls/actions on the right.
 * When title/subtitle are omitted (minimal UI), renders controls starting from the left with
 * data freshness and refresh actions on the right, keeping top spacing compact and balanced.
 */
// `lastUpdatedLabel` names what the time is: "Updated" (when the page fetched) or e.g. "Data as of" (when the
// backend read the data, which can be older than the fetch when the response was cached)
export default function PageHeader({
    title,
    subtitle,
    onRefresh,
    loading = false,
    lastUpdated = null,
    lastUpdatedLabel = "Updated",
    children,
    actions,
    className = "",
}) {
    const hasCustomMargin = className.includes("mb-") || className.includes("m-") || className.includes("my-")
    const hasCustomPadding = className.includes("pb-") || className.includes("p-") || className.includes("py-")
    const hasCustomBorder = className.includes("border-b-0") || className.includes("border-none")

    const hasHeading = Boolean(title || subtitle)

    const defaultBorder = !hasCustomBorder ? "border-b border-slate-200/80" : ""
    const defaultPadding = !hasCustomPadding ? (hasHeading ? "pb-3.5" : "pb-2 sm:pb-2.5") : ""
    const defaultMargin = !hasCustomMargin ? (hasHeading ? "mb-4 sm:mb-5" : "mb-3 sm:mb-3.5") : ""

    if (hasHeading) {
        return (
            <header className={`flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between ${defaultBorder} ${defaultPadding} ${defaultMargin} ${className}`}>
                <div className="flex min-w-0 items-center gap-3">
                    <MobileNavButton />
                    <div className="min-w-0">
                        {title && (
                            <h1 className="type-page-title font-jakarta truncate text-slate-900">
                                {title}
                            </h1>
                        )}
                        {subtitle && (
                            <p className="type-page-subtitle mt-0.5 truncate text-slate-500">
                                {subtitle}
                            </p>
                        )}
                    </div>
                </div>

                {(children || actions || lastUpdated || onRefresh) && (
                    <div className="flex flex-wrap items-center gap-2 lg:ml-auto">
                        {children}
                        {actions}

                        {lastUpdated && (
                            <span className="inline-flex h-8 items-center gap-1.5 px-1 text-xs tabular-nums text-slate-500" title={`${lastUpdatedLabel} (UAE time)`}>
                                <span className="h-1.5 w-1.5 rounded-full bg-status-moving" aria-hidden="true" />
                                {lastUpdatedLabel} {lastUpdated}
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

    return (
        <header className={`flex flex-wrap items-center justify-between gap-2.5 sm:gap-3 ${defaultBorder} ${defaultPadding} ${defaultMargin} ${className}`}>
            <div className="flex flex-wrap items-center gap-2 min-w-0">
                <MobileNavButton />
                {children}
            </div>

            {(actions || lastUpdated || onRefresh) && (
                <div className="flex items-center gap-2 ml-auto shrink-0">
                    {actions}

                    {lastUpdated && (
                        <span className="inline-flex h-8 items-center gap-1.5 px-1 text-xs tabular-nums text-slate-500" title={`${lastUpdatedLabel} (UAE time)`}>
                            <span className="h-1.5 w-1.5 rounded-full bg-status-moving" aria-hidden="true" />
                            {lastUpdatedLabel} {lastUpdated}
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
