import { Link } from "react-router-dom"
import { ArrowUpRight } from "lucide-react"

/**
 * Enterprise White-Surface KPI Card.
 * Complies with ATS Fleet design system:
 * - Pure white background (#FFFFFF)
 * - Subtle neutral border (#E2E8F0)
 * - Bold legible typography with clear visual hierarchy
 * - Interactive link or button capability with ATS brand accent (#33758F)
 */
export default function KpiCard({
    label,
    value,
    sub,
    icon: Icon,
    iconBg = "bg-slate-50 text-[#33758F] border border-slate-100",
    valueColor = "text-slate-900",
    badge,
    badgeCls = "bg-slate-100 text-slate-600 border-slate-200",
    to,
    onClick,
    selected = false,
    loading = false,
    className = "",
    title,
}) {
    const cardContent = (
        <>
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block truncate">
                        {label}
                    </span>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                    {badge && (
                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${badgeCls}`}>
                            {badge}
                        </span>
                    )}
                    {Icon && (
                        <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconBg}`}>
                            <Icon className="h-4 w-4" />
                        </div>
                    )}
                    {to && (
                        <ArrowUpRight className="h-3.5 w-3.5 text-slate-400 group-hover:text-[#33758F] group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all" />
                    )}
                </div>
            </div>

            <div className="my-2.5">
                {loading ? (
                    <div className="h-8 w-24 bg-slate-100 animate-pulse rounded-lg my-1" />
                ) : (
                    <p className={`text-2xl sm:text-3xl font-black tracking-tight ${valueColor}`}>
                        {value ?? "—"}
                    </p>
                )}
            </div>

            {sub && (
                <div className="text-[11px] font-medium text-slate-500 truncate">
                    {sub}
                </div>
            )}
        </>
    )

    const baseClasses = `flex flex-col justify-between rounded-xl border bg-white p-4 sm:p-5 shadow-xs transition-all text-left ${
        selected
            ? "border-[#33758F] ring-2 ring-[#33758F]/20 shadow-sm"
            : "border-slate-200/90 hover:border-slate-300"
    } ${className}`

    if (to) {
        return (
            <Link
                to={to}
                title={title || label}
                className={`${baseClasses} group hover:border-[#33758F]/40 hover:shadow-sm cursor-pointer`}
            >
                {cardContent}
            </Link>
        )
    }

    if (onClick) {
        return (
            <button
                type="button"
                onClick={onClick}
                title={title || label}
                className={`${baseClasses} cursor-pointer group hover:border-[#33758F]/40 hover:shadow-sm`}
            >
                {cardContent}
            </button>
        )
    }

    return (
        <div className={baseClasses} title={title || label}>
            {cardContent}
        </div>
    )
}
