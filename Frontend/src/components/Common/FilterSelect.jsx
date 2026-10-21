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
            className={`inline-flex h-8 min-w-0 items-center gap-1.5 rounded-md border px-2.5 transition-colors focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15 ${
                active ? "border-brand/50 bg-brand-light" : "border-slate-200 bg-white hover:border-slate-300"
            } ${className}`}
        >
            {Icon && <Icon className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true" />}
            {label && <span className="shrink-0 text-xs text-slate-500">{label}</span>}
            <select
                value={value}
                onChange={(e) => onChange(e.target.value)}
                aria-label={ariaLabel || label}
                className={`min-w-0 cursor-pointer truncate bg-transparent pr-0.5 text-xs font-medium text-slate-800 outline-none ${selectClassName}`}
            >
                {children}
            </select>
        </label>
    )
}
