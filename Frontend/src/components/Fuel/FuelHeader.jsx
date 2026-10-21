import { useState, useRef, useEffect } from "react"
import { Calendar, ChevronDown, Check, X, RotateCcw, Fuel } from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"
import PageHeader from "../Common/PageHeader"

const PERIOD_OPTIONS = [
    { id: "today", label: "Today (12:00 AM – Now)" },
    { id: "7d", label: "Last 7 Days" },
    { id: "30d", label: "Last 30 Days" },
    { id: "custom", label: "Custom Range…" },
    { id: "all", label: "All Historical Data", dividerBefore: true },
]

const controlBase = "h-8 rounded-lg border text-xs font-semibold shadow-2xs transition-colors cursor-pointer"
const controlState = (active) => active
    ? "border-[#33758F]/60 bg-[#F0F6F9] text-[#33758F]"
    : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"

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
                icon={Fuel}
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
                                <Calendar className="h-3.5 w-3.5 shrink-0 text-[#33758F]" />
                                <span>{periodLabel}</span>
                                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
                            </button>

                            {isPeriodOpen && (
                                <div className="absolute left-0 top-full z-40 mt-1.5 w-56 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg sm:left-auto sm:right-0">
                                    <div className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                                        Period (UAE time)
                                    </div>
                                    {PERIOD_OPTIONS.map((opt) => (
                                        <div key={opt.id}>
                                            {opt.dividerBefore && <div className="my-1 border-t border-slate-100" />}
                                            <button
                                                type="button"
                                                onClick={() => handleSelectPeriod(opt.id)}
                                                className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-left text-xs font-semibold transition-colors cursor-pointer ${
                                                    period === opt.id
                                                        ? "bg-[#33758F] text-white"
                                                        : "text-slate-700 hover:bg-slate-50"
                                                }`}
                                            >
                                                <span>{opt.label}</span>
                                                {period === opt.id && <Check className="h-3.5 w-3.5" />}
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
                    className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-xs"
                    onClick={() => setIsCustomModalOpen(false)}
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="fuel-custom-range-title"
                        className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
                            <h3 id="fuel-custom-range-title" className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                                <Calendar className="h-4 w-4 text-[#33758F]" />
                                Custom Date Range
                            </h3>
                            <button
                                type="button"
                                onClick={() => setIsCustomModalOpen(false)}
                                className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 cursor-pointer"
                                aria-label="Close"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>

                        <form onSubmit={handleApplyCustom} className="mt-4 space-y-3.5">
                            <div>
                                <label htmlFor="fuel-range-start" className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                    Start Date
                                </label>
                                <input
                                    id="fuel-range-start"
                                    type="date"
                                    required
                                    value={tempStart}
                                    onChange={(e) => setTempStart(e.target.value)}
                                    className="w-full rounded-lg border border-slate-200 bg-slate-50/50 px-3 py-2 text-xs font-semibold text-slate-800 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                                />
                            </div>

                            <div>
                                <label htmlFor="fuel-range-end" className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                    End Date
                                </label>
                                <input
                                    id="fuel-range-end"
                                    type="date"
                                    required
                                    value={tempEnd}
                                    onChange={(e) => setTempEnd(e.target.value)}
                                    className="w-full rounded-lg border border-slate-200 bg-slate-50/50 px-3 py-2 text-xs font-semibold text-slate-800 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                                />
                            </div>

                            {rangeError && (
                                <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700">
                                    {rangeError}
                                </p>
                            )}

                            <div className="flex items-center justify-end gap-2 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setIsCustomModalOpen(false)}
                                    className="h-8 rounded-lg border border-slate-200 bg-white px-3.5 text-xs font-semibold text-slate-600 hover:bg-slate-50 cursor-pointer"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="h-8 rounded-lg bg-[#33758F] px-4 text-xs font-semibold text-white shadow-xs hover:bg-[#285E73] cursor-pointer"
                                >
                                    Apply Range
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </>
    )
}
