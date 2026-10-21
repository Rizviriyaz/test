/**
 * Standard content panel with a title row (title, optional subtitle and actions).
 * `flush` removes body padding and separates the header with a hairline (for tables and lists).
 * `icon` / `iconClassName` are accepted for API compatibility but not rendered: titles carry meaning.
 */
export default function SectionCard({
    title,
    subtitle,
    actions,
    children,
    flush = false,
    className = "",
    bodyClassName = "",
}) {
    const hasHeader = Boolean(title || actions)

    return (
        <section className={`min-w-0 rounded-lg border border-slate-200 bg-white ${flush ? "overflow-hidden" : "p-4"} ${className}`}>
            {hasHeader && (
                <div className={`flex flex-wrap items-start justify-between gap-x-3 gap-y-2 ${flush ? "border-b border-slate-200 px-4 py-3" : "mb-3"}`}>
                    <div className="min-w-0">
                        {title && <h2 className="truncate text-[13px] font-semibold text-slate-900">{title}</h2>}
                        {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
                    </div>
                    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
                </div>
            )}
            <div className={bodyClassName}>{children}</div>
        </section>
    )
}
