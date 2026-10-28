import { Calendar, ChevronLeft, ChevronRight, X } from "lucide-react"
import { canGoNext, monthLabel, shiftMonth } from "./fuelPeriod"
import { getUAETodayStr } from "../../utils/datetime"

const stepBtn = "inline-flex h-8 w-8 items-center justify-center text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-900 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/30 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-white"

/**
 * Previous / current / next month control. While a custom range is active (`customLabel`), the middle shows that
 * range with a clear button, and the arrows step from `month` back into month mode.
 */
export default function MonthNavigator({ month, onChange, customLabel, onClearCustom }) {
    const today = getUAETodayStr()
    const nextAllowed = canGoNext(month, today)

    return (
        <div role="group" aria-label="Reporting month" className="inline-flex h-8 items-stretch overflow-hidden rounded-md border border-slate-200 bg-white text-xs font-medium">
            <button
                type="button"
                onClick={() => onChange(shiftMonth(month, -1))}
                className={`${stepBtn} border-r border-slate-200`}
                aria-label="Previous month"
                title="Previous month"
            >
                <ChevronLeft className="h-4 w-4" />
            </button>

            {customLabel ? (
                <span className="inline-flex items-center gap-1.5 bg-brand-light px-2.5 text-slate-900">
                    <Calendar className="h-3.5 w-3.5 shrink-0 text-brand" />
                    <span className="truncate tabular-nums">{customLabel}</span>
                    <button
                        type="button"
                        onClick={onClearCustom}
                        className="rounded-sm p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 cursor-pointer"
                        aria-label="Clear custom range"
                        title="Back to month view"
                    >
                        <X className="h-3 w-3" />
                    </button>
                </span>
            ) : (
                <span className="inline-flex min-w-[96px] items-center justify-center gap-1.5 px-2.5 text-slate-900 tabular-nums" aria-live="polite">
                    <Calendar className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                    {monthLabel(month)}
                </span>
            )}

            <button
                type="button"
                onClick={() => onChange(shiftMonth(month, 1))}
                disabled={!nextAllowed}
                className={`${stepBtn} border-l border-slate-200`}
                aria-label="Next month"
                title={nextAllowed ? "Next month" : "No later month"}
            >
                <ChevronRight className="h-4 w-4" />
            </button>
        </div>
    )
}
