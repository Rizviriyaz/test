/**
 * Compact toolbar select (optional icon + label) with the standard 32px control height.
 * `onChange` receives the raw string value.
 */
export default function FilterSelect({
    icon: Icon,
    label,
    value,
    onChange,
    ariaLabel,
    children,
    active = false,
    className = "",
    selectClassName = "",
}) {
    return (
        <label
            className={`inline-flex h-8 min-w-0 items-center gap-1.5 rounded-lg border px-2.5 shadow-2xs transition focus-within:border-[#33758F] focus-within:ring-2 focus-within:ring-[#33758F]/15 ${
                active ? "border-[#33758F]/60 bg-[#F0F6F9]" : "border-slate-200 bg-white hover:border-slate-300"
            } ${className}`}
        >
            {Icon && <Icon className="h-3.5 w-3.5 shrink-0 text-slate-400" />}
            {label && <span className="shrink-0 text-[11px] font-semibold text-slate-400">{label}</span>}
            <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                aria-label={ariaLabel || label}
                className={`min-w-0 cursor-pointer truncate bg-transparent pr-0.5 text-xs font-semibold text-slate-700 outline-none ${selectClassName}`}
            >
                {children}
            </select>
        </label>
    )
}
