/**
 * Standard content card with a title row (icon, title, optional subtitle and actions).
 * `flush` removes body padding and separates the header with a border (for tables and lists).
 */
export default function SectionCard({
    title,
    icon: Icon,
    iconClassName = "text-[#33758F]",
    subtitle,
    actions,
    children,
    flush = false,
    className = "",
    bodyClassName = "",
}) {
    const hasHeader = Boolean(title || actions)

    return (
        <section className={`rounded-xl border border-slate-200 bg-white shadow-xs ${flush ? "overflow-hidden" : "p-4 sm:p-5"} ${className}`}>
            {hasHeader && (
                <div className={`flex flex-wrap items-center justify-between gap-2 ${flush ? "border-b border-slate-200 px-4 py-3 sm:px-5" : "mb-4"}`}>
                    <div className="min-w-0">
                        <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                            {Icon && <Icon className={`h-4 w-4 shrink-0 ${iconClassName}`} />}
                            <span className="truncate">{title}</span>
                        </h2>
                        {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
                    </div>
                    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
                </div>
            )}
            <div className={bodyClassName}>{children}</div>
        </section>
    )
}
