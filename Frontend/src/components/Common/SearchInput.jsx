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
            <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
            <input
                id={id}
                type="search"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                placeholder={placeholder}
                aria-label={placeholder.replace(/…$/, "")}
                autoFocus={autoFocus}
                className="h-8 w-full rounded-md border border-slate-200 bg-white pl-8 pr-8 text-xs text-slate-800 placeholder:text-slate-400 outline-none transition-colors hover:border-slate-300 focus:border-brand focus:ring-2 focus:ring-brand/15 [&::-webkit-search-cancel-button]:hidden"
            />
            {value && (
                <button
                    type="button"
                    onClick={() => {
                        if (onClear) onClear()
                        else onChange("")
                    }}
                    className="absolute right-1.5 rounded p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                    aria-label="Clear search"
                    title="Clear search"
                >
                    <X className="h-3.5 w-3.5" />
                </button>
            )}
        </div>
    )
}
