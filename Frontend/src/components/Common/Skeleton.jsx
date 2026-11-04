/**
 * Skeleton placeholders shared by every page: shown only until a section's first response arrives (never during a
 * background refresh of data already on screen). Each keeps the final component's dimensions, so nothing shifts when
 * the real content replaces it. Decorative: one role="status" label per placeholder group for assistive technology.
 */

const PULSE = "animate-pulse rounded bg-slate-100 motion-reduce:animate-none"

export function Skeleton({ className = "", style }) {
    return <span aria-hidden="true" className={`block ${PULSE} ${className}`} style={style} />
}

function Status({ label, className = "", children }) {
    return (
        <div role="status" aria-label={label} aria-busy="true" className={className}>
            {children}
            <span className="sr-only">{label}</span>
        </div>
    )
}

// Varied widths so rows read as text, not as a solid block
const ROW_WIDTHS = [72, 58, 84, 64, 76, 52, 68, 80]

/** Table body placeholder: `rows` rows of `columns` cells, in a real <table> structure when `asRows` (inside <tbody>). */
export function SkeletonTableRows({ rows = 6, columns = 5, asRows = false, label = "Loading rows" }) {
    const cells = (r) => Array.from({ length: columns }, (_, c) => (
        <Skeleton key={c} className="h-3" style={{ width: `${ROW_WIDTHS[(r + c) % ROW_WIDTHS.length]}%` }} />
    ))
    if (asRows) {
        return Array.from({ length: rows }, (_, r) => (
            <tr key={r} aria-hidden="true" className="border-b border-slate-100 last:border-0">
                {cells(r).map((cell, c) => <td key={c} className="px-4 py-3">{cell}</td>)}
            </tr>
        ))
    }
    return (
        <Status label={label} className="divide-y divide-slate-100">
            {Array.from({ length: rows }, (_, r) => (
                <div key={r} className="grid gap-4 px-4 py-3" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
                    {cells(r)}
                </div>
            ))}
        </Status>
    )
}

/** Chart placeholder: axis lines and bars at the chart's own height. */
export function SkeletonChart({ height = 240, label = "Loading chart", className = "" }) {
    const bars = [45, 70, 55, 85, 60, 40, 75, 50, 65, 80, 35, 58]
    return (
        <Status label={label} className={`flex items-end gap-2 border-b border-l border-slate-100 px-3 pb-2 ${className}`}>
            <div className="flex w-full items-end gap-2" style={{ height }}>
                {bars.map((h, i) => <Skeleton key={i} className="flex-1 rounded-b-none" style={{ height: `${h}%` }} />)}
            </div>
        </Status>
    )
}

/** KPI card placeholders laid out in the page's own grid classes. */
export function SkeletonCards({ count = 4, className = "grid grid-cols-2 gap-3 lg:grid-cols-4", cardClassName = "h-[92px]", label = "Loading summary" }) {
    return (
        <Status label={label} className={className}>
            {Array.from({ length: count }, (_, i) => (
                <div key={i} className={`rounded-lg border border-slate-200 bg-white p-4 ${cardClassName}`}>
                    <Skeleton className="h-3 w-20" />
                    <Skeleton className="mt-2 h-6 w-24" />
                    <Skeleton className="mt-2 h-3 w-16" />
                </div>
            ))}
        </Status>
    )
}

/** Text lines (lists, panels, drawer sections). */
export function SkeletonLines({ lines = 4, className = "space-y-2.5", lineClassName = "h-3", label = "Loading" }) {
    return (
        <Status label={label} className={className}>
            {Array.from({ length: lines }, (_, i) => (
                <Skeleton key={i} className={lineClassName} style={{ width: `${ROW_WIDTHS[i % ROW_WIDTHS.length]}%` }} />
            ))}
        </Status>
    )
}

/** Map placeholder filling the map's container. */
export function SkeletonMap({ className = "h-full w-full", label = "Loading map" }) {
    return (
        <Status label={label} className={`relative overflow-hidden bg-slate-50 ${className}`}>
            <div aria-hidden="true" className="absolute inset-0 animate-pulse bg-slate-100 motion-reduce:animate-none" />
        </Status>
    )
}

/**
 * The operational layout (sidebar, header, cards, chart and table) while the session is verified or a page's code
 * loads: no data and no protected content, only the structure, so the screen never goes blank.
 */
export function AppShellSkeleton({ label = "Loading" }) {
    return (
        <div role="status" aria-label={label} aria-busy="true" className="flex min-h-dvh bg-slate-50">
            <span className="sr-only">{label}</span>
            <aside aria-hidden="true" className="hidden w-60 shrink-0 border-r border-slate-200 bg-white p-4 lg:block">
                <Skeleton className="h-8 w-28" />
                <div className="mt-8 space-y-3">
                    {Array.from({ length: 7 }, (_, i) => <Skeleton key={i} className="h-7 w-full" />)}
                </div>
            </aside>
            <main aria-hidden="true" className="min-w-0 flex-1 p-4 sm:p-6">
                <Skeleton className="h-6 w-56" />
                <Skeleton className="mt-2 h-3 w-80 max-w-full" />
                <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {Array.from({ length: 4 }, (_, i) => (
                        <div key={i} className="h-[92px] rounded-lg border border-slate-200 bg-white p-4">
                            <Skeleton className="h-3 w-20" />
                            <Skeleton className="mt-2 h-6 w-24" />
                        </div>
                    ))}
                </div>
                <div className="mt-4 h-72 rounded-lg border border-slate-200 bg-white p-4"><Skeleton className="h-full w-full" /></div>
                <div className="mt-4 space-y-3 rounded-lg border border-slate-200 bg-white p-4">
                    {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-3" style={{ width: `${ROW_WIDTHS[i]}%` }} />)}
                </div>
            </main>
        </div>
    )
}
