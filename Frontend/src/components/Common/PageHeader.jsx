import React from "react"
import { RefreshCw } from "lucide-react"

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
    const defaultMargin = !hasCustomMargin ? "mb-6" : ""

    return (
        <header className={`flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between ${defaultBorder} ${defaultPadding} ${defaultMargin} ${className}`}>
            <div>
                <h1 className="text-xl sm:text-2xl font-black tracking-tight text-slate-900 uppercase flex items-center gap-2.5">
                    {Icon && <Icon className="h-6 w-6 text-[#33758F] shrink-0" />}
                    <span>{title}</span>
                </h1>
                {subtitle && (
                    <p className="text-xs text-slate-500 font-medium mt-0.5">
                        {subtitle}
                    </p>
                )}
            </div>

            <div className="flex flex-wrap items-center gap-2.5">
                {children}

                {lastUpdated && (
                    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-2xs">
                        <span className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                        </span>
                        <span className="text-[11px] text-slate-500 font-semibold">
                            {lastUpdated}
                        </span>
                    </div>
                )}

                {onRefresh && (
                    <button
                        type="button"
                        onClick={onRefresh}
                        disabled={loading}
                        className="inline-flex items-center gap-1.5 rounded-lg bg-[#33758F] hover:bg-[#285E73] text-white px-3.5 py-1.5 text-xs font-bold shadow-xs transition-colors cursor-pointer disabled:opacity-50"
                    >
                        <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                        <span>{loading ? "Updating…" : "Refresh"}</span>
                    </button>
                )}
            </div>
        </header>
    )
}
