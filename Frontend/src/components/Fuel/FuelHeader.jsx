import { useState, useRef, useEffect } from "react"
import { Calendar, ChevronDown, Check, X, RotateCcw } from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"
import PageHeader from "../Common/PageHeader"

const PERIOD_OPTIONS = [
    { id: "today", label: "Today (12:00 AM – Now)" },
    { id: "7d", label: "Last 7 Days" },
    { id: "30d", label: "Last 30 Days" },
    { id: "custom", label: "Custom Range…" },
    { id: "all", label: "All Historical Data", dividerBefore: true },
]

const controlBase = "h-8 rounded-md border text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
const controlState = (active) => active
    ? "border-brand/50 bg-brand-light text-slate-900"
    : "border-slate-200 bg-white text-slate-800 hover:border-slate-300"

export default function FuelHeader({
    title = "ATS Fleet Fuel Performance",
    subtitle,
    showFilters = true,
    actions,
}) {
    const {
        period,
        setPeriod,
        dateFrom,
        dateTo,
        periodLabel,
        applyCustomRange,
        vehicle,
        setVehicle,
        fuelType,
        setFuelType,
        vehiclesList,
        fuelTypesList,
        resetFilters,
    } = useFuelFilters()

    const [isPeriodOpen, setIsPeriodOpen] = useState(false)
    const [isCustomModalOpen, setIsCustomModalOpen] = useState(false)
    const [tempStart, setTempStart] = useState(dateFrom || "")
    const [tempEnd, setTempEnd] = useState(dateTo || "")
    const [rangeError, setRangeError] = useState(null)

    const periodRef = useRef(null)

    // Close period dropdown on click outside
    useEffect(() => {
        function handleClickOutside(e) {
            if (periodRef.current && !periodRef.current.contains(e.target)) {
                setIsPeriodOpen(false)
            }
        }
        document.addEventListener("mousedown", handleClickOutside)
        return () => document.removeEventListener("mousedown", handleClickOutside)
    }, [])

    // Escape closes the open dropdown or custom range dialog
    useEffect(() => {
        if (!isPeriodOpen && !isCustomModalOpen) return undefined
        function handleKey(e) {
            if (e.key !== "Escape") return
            setIsPeriodOpen(false)
            setIsCustomModalOpen(false)
        }
        document.addEventListener("keydown", handleKey)
        return () => document.removeEventListener("keydown", handleKey)
    }, [isPeriodOpen, isCustomModalOpen])

    const handleSelectPeriod = (p) => {
        if (p === "custom") {
            setRangeError(null)
            setIsCustomModalOpen(true)
            setIsPeriodOpen(false)
            return
        }
        setPeriod(p)
        setIsPeriodOpen(false)
    }

    const handleApplyCustom = (e) => {
        e.preventDefault()
        if (tempStart && tempEnd && tempStart > tempEnd) {
            setRangeError("Start date must be before or equal to the end date.")
            return
        }
        setRangeError(null)
        applyCustomRange(tempStart, tempEnd)
        setIsCustomModalOpen(false)
    }

    const hasActiveFilters = Boolean(vehicle || fuelType || period !== "today")

    return (
        <>
            <PageHeader
                title={title}
                subtitle={subtitle || `Fleet Fuel Performance · ${periodLabel}`}
            >
                {showFilters && (
                    <>
                        {/* 1. Global Period Selector */}
                        <div className="relative" ref={periodRef}>
                            <button
                                type="button"
                                onClick={() => setIsPeriodOpen(!isPeriodOpen)}
                                aria-haspopup="listbox"
                                aria-expanded={isPeriodOpen}
                                className={`${controlBase} ${controlState(period !== "today")} inline-flex items-center gap-1.5 px-2.5`}
                            >
                                <Calendar className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                <span>{periodLabel}</span>
                                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
                            </button>

                            {isPeriodOpen && (
                                <div role="listbox" className="absolute left-0 top-full z-40 mt-1 w-56 rounded-md border border-slate-200 bg-white p-1 shadow-pop sm:left-auto sm:right-0">
                                    <div className="px-2 py-1.5 text-[11px] text-slate-500">
                                        Period (UAE time)
                                    </div>
                                    {PERIOD_OPTIONS.map((opt) => (
                                        <div key={opt.id}>
                                            {opt.dividerBefore && <div className="my-1 border-t border-slate-100" />}
                                            <button
                                                type="button"
                                                onClick={() => handleSelectPeriod(opt.id)}
                                                role="option"
                                                aria-selected={period === opt.id}
                                                className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs transition-colors cursor-pointer ${
                                                    period === opt.id
                                                        ? "font-medium text-slate-900"
                                                        : "text-slate-700 hover:bg-slate-50"
                                                }`}
                                            >
                                                <span>{opt.label}</span>
                                                {period === opt.id && <Check className="h-3.5 w-3.5 text-brand" />}
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* 2. Vehicle Selector */}
                        <div className="relative">
                            <select
                                value={vehicle}
                                onChange={(e) => setVehicle(e.target.value)}
                                aria-label="Filter by vehicle"
                                className={`${controlBase} ${controlState(Boolean(vehicle))} max-w-[180px] appearance-none truncate pl-2.5 pr-7`}
                            >
                                <option value="">All Vehicles ({vehiclesList.length > 0 ? vehiclesList.length : "…"})</option>
                                {vehiclesList.map((v) => (
                                    <option key={v} value={v}>
                                        {v}
                                    </option>
                                ))}
                            </select>
                            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                        </div>

                        {/* 3. Fuel Type Selector */}
                        <div className="relative">
                            <select
                                value={fuelType}
                                onChange={(e) => setFuelType(e.target.value)}
                                aria-label="Filter by fuel type"
                                className={`${controlBase} ${controlState(Boolean(fuelType))} appearance-none pl-2.5 pr-7`}
                            >
                                <option value="">All Fuel Types</option>
                                {fuelTypesList.map((ft) => (
                                    <option key={ft} value={ft}>
                                        {ft}
                                    </option>
                                ))}
                            </select>
                            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                        </div>

                        {/* Reset Filters button */}
                        {hasActiveFilters && (
                            <button
                                type="button"
                                onClick={resetFilters}
                                title="Reset all filters to Today / All"
                                className={`${controlBase} inline-flex items-center gap-1.5 border-slate-200 bg-white px-2.5 text-slate-500 hover:bg-slate-50 hover:text-slate-800`}
                            >
                                <RotateCcw className="h-3.5 w-3.5 text-slate-400" />
                                <span>Reset</span>
                            </button>
                        )}
                    </>
                )}
                {actions}
            </PageHeader>

            {/* Custom Range Dialog */}
            {isCustomModalOpen && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
                    onClick={() => setIsCustomModalOpen(false)}
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="fuel-custom-range-title"
                        className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-5 shadow-dialog"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                            <h3 id="fuel-custom-range-title" className="text-[15px] font-semibold text-slate-900">
                                Custom date range
                            </h3>
                            <button
                                type="button"
                                onClick={() => setIsCustomModalOpen(false)}
                                className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                                aria-label="Close"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>

                        <form onSubmit={handleApplyCustom} className="mt-4 space-y-3.5">
                            <div>
                                <label htmlFor="fuel-range-start" className="mb-1 block text-xs text-slate-600">
                                    Start date
                                </label>
                                <input
                                    id="fuel-range-start"
                                    type="date"
                                    required
                                    value={tempStart}
                                    onChange={(e) => setTempStart(e.target.value)}
                                    className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-800 focus:border-brand focus:outline-hidden focus:ring-2 focus:ring-brand/15"
                                />
                            </div>

                            <div>
                                <label htmlFor="fuel-range-end" className="mb-1 block text-xs text-slate-600">
                                    End date
                                </label>
                                <input
                                    id="fuel-range-end"
                                    type="date"
                                    required
                                    value={tempEnd}
                                    onChange={(e) => setTempEnd(e.target.value)}
                                    className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-xs text-slate-800 focus:border-brand focus:outline-hidden focus:ring-2 focus:ring-brand/15"
                                />
                            </div>

                            {rangeError && (
                                <p role="alert" className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                                    {rangeError}
                                </p>
                            )}

                            <div className="flex items-center justify-end gap-2 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setIsCustomModalOpen(false)}
                                    className="h-8 rounded-md border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 hover:bg-slate-50 cursor-pointer"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="h-8 rounded-md bg-brand px-3 text-xs font-medium text-white hover:bg-brand-hover cursor-pointer"
                                >
                                    Apply range
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </>
    )
}
