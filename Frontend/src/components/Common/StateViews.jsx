import { AlertCircle, Inbox, RefreshCw } from "lucide-react"
import { SkeletonChart, SkeletonLines, SkeletonTableRows } from "./Skeleton"

/**
 * Standard loading / error / empty states shared by every data component.
 */

// Framed by default; callers embedding a state inside a panel pass "border-0"/"border-none" to drop the frame
const isFrameless = (className) => /\bborder-(0|none)\b/.test(className)
const frameFor = (className) =>
    `flex flex-col items-center justify-center px-6 text-center ${isFrameless(className) ? "" : "rounded-lg border border-slate-200 bg-white"}`

// A section's first load: a skeleton of the content it will show (table rows, a chart or text lines), never a spinner.
// `label` is announced to assistive technology only.
export function LoadingState({ label = "Loading…", className = "", variant = "table", rows = 6, columns = 5, height = 240 }) {
    const frame = isFrameless(className) ? "" : "rounded-lg border border-slate-200 bg-white"
    return (
        <div className={`${frame} ${className}`}>
            {variant === "chart" ? (
                <SkeletonChart height={height} label={label} className="m-4" />
            ) : variant === "lines" ? (
                <SkeletonLines lines={rows} label={label} className="space-y-2.5 p-4" />
            ) : (
                <SkeletonTableRows rows={rows} columns={columns} label={label} />
            )}
        </div>
    )
}

function cleanErrorMessage(msg) {
    if (!msg || typeof msg !== "string") return "Data temporarily unavailable."
    const trimmed = msg.trim()
    const lower = trimmed.toLowerCase()
    if (
        lower.includes("failed to fetch") ||
        lower.includes("networkerror") ||
        lower.includes("internal server error") ||
        lower.includes("traceback") ||
        lower.includes("status code 5") ||
        lower.includes("status code 4") ||
        lower.includes("econnrefused") ||
        lower.includes("typeerror") ||
        lower.includes("syntaxerror")
    ) {
        return "Data temporarily unavailable. Please try again."
    }
    return trimmed
}

export function ErrorState({ title = "Unable to load data", message, onRetry, className = "" }) {
    return (
        <div role="alert" className={`${frameFor(className)} py-12 ${className}`}>
            <AlertCircle className="h-5 w-5 text-rose-600" />
            <p className="mt-2.5 text-[13px] font-semibold text-slate-900">{title}</p>
            {message && <p className="mt-1 max-w-md text-xs text-slate-500">{cleanErrorMessage(message)}</p>}
            {onRetry && (
                <button
                    type="button"
                    onClick={onRetry}
                    className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
                >
                    <RefreshCw className="h-3.5 w-3.5 text-slate-500" />
                    Try again
                </button>
            )}
        </div>
    )
}

/** Compact inline error shown above content that is still usable (e.g. stale data). */
export function ErrorBanner({ message, onRetry, className = "" }) {
    return (
        <div role="alert" className={`flex items-center gap-2.5 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 ${className}`}>
            <AlertCircle className="h-4 w-4 shrink-0 text-rose-600" />
            <span className="flex-1">{cleanErrorMessage(message)}</span>
            {onRetry && (
                <button
                    type="button"
                    onClick={onRetry}
                    className="shrink-0 font-medium text-rose-800 underline underline-offset-2 hover:text-rose-950 cursor-pointer"
                >
                    Retry
                </button>
            )}
        </div>
    )
}

export function EmptyState({ icon: Icon = Inbox, title, message, action, className = "" }) {
    return (
        <div className={`${frameFor(className)} py-12 ${className}`}>
            <Icon className="h-5 w-5 text-slate-300" />
            {title && <p className="mt-2.5 text-[13px] font-medium text-slate-800">{title}</p>}
            {message && <p className="mt-1 max-w-sm text-xs text-slate-500">{message}</p>}
            {action && <div className="mt-3">{action}</div>}
        </div>
    )
}
