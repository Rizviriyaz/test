import { useEffect, useState } from "react"
import { FRESHNESS_LABELS, freshnessState, freshnessTitle } from "../../utils/freshness"
import { formatUAETime } from "../../utils/datetime"

const TONES = {
    fresh: "bg-emerald-50 text-emerald-800 border-emerald-200",
    cached: "bg-slate-50 text-slate-700 border-slate-200",
    delayed: "bg-amber-50 text-amber-800 border-amber-200",
    stale: "bg-amber-50 text-amber-900 border-amber-300",
    refreshing: "bg-slate-50 text-slate-600 border-slate-200",
    failed: "bg-rose-50 text-rose-800 border-rose-200",
    unavailable: "bg-rose-50 text-rose-800 border-rose-200",
}
const DOTS = {
    fresh: "bg-emerald-500", cached: "bg-slate-400", delayed: "bg-amber-500", stale: "bg-amber-600",
    refreshing: "bg-slate-300 animate-pulse motion-reduce:animate-none", failed: "bg-rose-500", unavailable: "bg-rose-500",
}

/**
 * Data freshness of one response (Fresh / Cached / Delayed / Stale / Refreshing / Refresh failed / Unavailable), shown
 * next to the page's own update time. `label` names the data when a page shows several sources (e.g. "Telematics").
 * Re-evaluated every 15 s so a response that stays on screen turns Delayed without a new request.
 */
export default function FreshnessBadge({ freshness, loading = false, error = null, hasData = true, label = null }) {
    const [nowMs, setNowMs] = useState(() => Date.now())
    useEffect(() => {
        const timer = setInterval(() => setNowMs(Date.now()), 15000)
        return () => clearInterval(timer)
    }, [])
    const state = freshnessState({ freshness, loading, error, hasData, nowMs })
    if (!state) return null
    const fetched = freshness?.fetched_at
    return (
        <span
            className={`inline-flex h-6 shrink-0 items-center gap-1.5 rounded-full border px-2 text-[11px] font-medium tabular-nums ${TONES[state]}`}
            title={freshnessTitle(freshness, state, (d) => formatUAETime(d))}
            role="status"
            aria-label={`${label ? `${label}: ` : ""}${FRESHNESS_LABELS[state]}`}
        >
            <span className={`h-1.5 w-1.5 rounded-full ${DOTS[state]}`} aria-hidden="true" />
            {label && <span className="font-normal opacity-80">{label}</span>}
            <span>{FRESHNESS_LABELS[state]}</span>
            {fetched && state !== "refreshing" && state !== "unavailable" && (
                <span className="font-normal opacity-80">· {formatUAETime(fetched)}</span>
            )}
        </span>
    )
}
