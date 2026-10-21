import { RefreshCw } from "lucide-react"
import { MobileNavButton } from "../Layout/Sidebar"

/**
 * Enterprise Page Header.
 * Standardizes title, subtitle, live status indicator, action buttons, and filters
 * across all ATS Fleet Management pages.
 */
export default function PageHeader({
    title,
    subtitle,
    icon: Icon,
    onRefresh,
    loading = false,
    lastUpdated = null,
    children,
    className = "",
}) {
    const hasCustomMargin = className.includes("mb-") || className.includes("m-") || className.includes("my-")
    const hasCustomPadding = className.includes("pb-") || className.includes("p-") || className.includes("py-")
    const hasCustomBorder = className.includes("border-b-0") || className.includes("border-none")

    const defaultBorder = !hasCustomBorder ? "border-b border-slate-200/80" : ""
    const defaultPadding = !hasCustomPadding ? "pb-4" : ""
    const defaultMargin = !hasCustomMargin ? "mb-5" : ""

    return (
        <header className={`flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between ${defaultBorder} ${defaultPadding} ${defaultMargin} ${className}`}>
            <div className="flex min-w-0 items-center gap-3">
                <MobileNavButton />
                {Icon && (
                    <span className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[#D1E2EA] bg-[#F0F6F9] text-[#33758F] sm:flex">
                        <Icon className="h-4.5 w-4.5" />
                    </span>
                )}
                <div className="min-w-0">
                    <h1 className="truncate text-lg font-bold tracking-tight text-slate-900 sm:text-xl">
                        {title}
                    </h1>
                    {subtitle && (
                        <p className="truncate text-xs font-medium text-slate-500">
                            {subtitle}
                        </p>
                    )}
                </div>
            </div>

            {(children || lastUpdated || onRefresh) && (
                <div className="flex flex-wrap items-center gap-2">
                    {children}

                    {lastUpdated && (
                        <div className="flex h-8 items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 shadow-2xs" title="Last updated (UAE time)">
                            <span className="relative flex h-2 w-2">
                                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75 motion-reduce:animate-none" />
                                <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                            </span>
                            <span className="text-[11px] font-semibold tabular-nums text-slate-500">
                                {lastUpdated}
                            </span>
                        </div>
                    )}

                    {onRefresh && (
                        <button
                            type="button"
                            onClick={onRefresh}
                            disabled={loading}
                            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-[#33758F] px-3 text-xs font-semibold text-white shadow-xs transition-colors hover:bg-[#285E73] cursor-pointer disabled:opacity-50"
                        >
                            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                            <span>{loading ? "Updating…" : "Refresh"}</span>
                        </button>
                    )}
                </div>
            )}
        </header>
    )
}
