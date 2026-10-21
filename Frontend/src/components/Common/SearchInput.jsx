import { Search, X } from "lucide-react"

/**
 * Standardized search input with clear button and brand focus state.
 */
export default function SearchInput({
    value,
    onChange,
    onClear,
    placeholder = "Search…",
    className = "",
    id,
    autoFocus = false,
}) {
    return (
        <div className={`relative flex items-center ${className}`}>
            <Search className="pointer-events-none absolute left-3 h-3.5 w-3.5 text-slate-400" />
            <input
                id={id}
                type="text"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                placeholder={placeholder}
                autoFocus={autoFocus}
                className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-8 text-xs text-slate-800 placeholder:text-slate-400 shadow-2xs outline-none transition focus:border-[#33758F] focus:ring-2 focus:ring-[#33758F]/20"
            />
            {value && (
                <button
                    type="button"
                    onClick={() => {
                        if (onClear) onClear()
                        else onChange("")
                    }}
                    className="absolute right-2.5 rounded-full p-0.5 text-slate-400 hover:text-slate-700 transition"
                    title="Clear search"
                >
                    <X className="h-3.5 w-3.5" />
                </button>
            )}
        </div>
    )
}
