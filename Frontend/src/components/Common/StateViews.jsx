import { AlertCircle, Inbox, Loader2, RefreshCw } from "lucide-react"

/**
 * Standard loading / error / empty states shared by every data component.
 */

const frame = "flex flex-col items-center justify-center rounded-xl border px-6 text-center"

export function LoadingState({ label = "Loading…", className = "" }) {
    return (
        <div role="status" className={`${frame} border-slate-200 bg-white py-16 ${className}`}>
            <Loader2 className="h-6 w-6 animate-spin text-[#33758F]" />
            <p className="mt-3 text-xs font-semibold text-slate-600">{label}</p>
        </div>
    )
}

export function ErrorState({ title = "Unable to load data", message, onRetry, className = "" }) {
    return (
        <div role="alert" className={`${frame} border-rose-200 bg-rose-50/50 py-12 ${className}`}>
            <AlertCircle className="h-6 w-6 text-rose-500" />
            <p className="mt-3 text-sm font-semibold text-rose-800">{title}</p>
            {message && <p className="mt-1 max-w-md text-xs text-rose-700">{message}</p>}
            {onRetry && (
                <button
                    type="button"
                    onClick={onRetry}
                    className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 text-xs font-semibold text-rose-700 shadow-2xs transition hover:bg-rose-50 cursor-pointer"
                >
                    <RefreshCw className="h-3.5 w-3.5" />
                    Retry
                </button>
            )}
        </div>
    )
}

/** Compact inline error shown above content that is still usable (e.g. stale data). */
export function ErrorBanner({ message, onRetry, className = "" }) {
    return (
        <div role="alert" className={`flex items-center gap-3 rounded-xl border border-rose-200 bg-rose-50/70 px-4 py-2.5 text-xs text-rose-700 ${className}`}>
            <AlertCircle className="h-4 w-4 shrink-0 text-rose-500" />
            <span className="flex-1 font-medium">{message}</span>
            {onRetry && (
                <button
                    type="button"
                    onClick={onRetry}
                    className="shrink-0 font-semibold text-rose-700 underline-offset-2 hover:underline cursor-pointer"
                >
                    Retry
                </button>
            )}
        </div>
    )
}

export function EmptyState({ icon: Icon = Inbox, title, message, action, className = "" }) {
    return (
        <div className={`${frame} border-slate-200 bg-white py-14 ${className}`}>
            <Icon className="h-7 w-7 text-slate-300" />
            {title && <p className="mt-3 text-sm font-semibold text-slate-800">{title}</p>}
            {message && <p className="mt-1 max-w-sm text-xs text-slate-500">{message}</p>}
            {action && <div className="mt-4">{action}</div>}
        </div>
    )
}
