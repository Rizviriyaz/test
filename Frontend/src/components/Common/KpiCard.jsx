import { Link } from "react-router-dom"

/**
 * One metric cell. Place cells inside <KpiStrip> so they share a single panel with
 * hairline dividers instead of floating as separate cards.
 * - `dot`: a status color class (e.g. "bg-status-moving") shown before the label
 * - `valueColor`: only when the value itself signals a state (e.g. offline > 0)
 * - `to` / `onClick` make the cell interactive; `selected` marks an active filter
 * `icon`, `iconBg`, `badge`, `badgeCls` are accepted for API compatibility but not rendered.
 */
export default function KpiCard({
    label,
    value,
    sub,
    dot,
    valueColor = "text-slate-900",
    to,
    onClick,
    selected = false,
    loading = false,
    className = "",
    title,
}) {
    const body = (
        <>
            <span className="flex min-w-0 items-center gap-1.5 text-xs font-medium text-slate-500">
                {dot && <span className={`h-2 w-2 shrink-0 rounded-[2px] ${dot}`} aria-hidden="true" />}
                <span className="truncate">{label}</span>
            </span>
            {loading ? (
                <span className="mt-1.5 block h-6 w-16 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" />
            ) : (
                <span className={`mt-1 block truncate text-[22px] font-semibold leading-7 tracking-tight tabular-nums ${valueColor}`}>
                    {value ?? "—"}
                </span>
            )}
            {sub && <span className="mt-0.5 block truncate text-xs text-slate-500">{sub}</span>}
        </>
    )

    const base = `relative flex min-w-0 flex-col bg-white px-4 py-3 text-left ${className}`
    const interactive = `${base} cursor-pointer transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40 ${
        selected ? "bg-brand-light/60 shadow-[inset_0_-2px_0_var(--color-brand)] hover:bg-brand-light/60" : ""
    }`

    if (to) {
        return <Link to={to} title={title || label} className={interactive}>{body}</Link>
    }
    if (onClick) {
        return (
            <button type="button" onClick={onClick} title={title || label} aria-pressed={selected} className={interactive}>
                {body}
            </button>
        )
    }
    return <div className={base} title={title || label}>{body}</div>
}

/** Single panel that lays metric cells out on a grid with 1px hairline dividers. */
export function KpiStrip({ children, className = "", label }) {
    return (
        <section
            aria-label={label}
            className={`grid gap-px overflow-hidden rounded-lg border border-slate-200 bg-slate-200 ${className}`}
        >
            {children}
        </section>
    )
}
