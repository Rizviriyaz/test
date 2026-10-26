import { useState, useRef, useEffect } from "react"
import { Calendar, ChevronDown, Check, X, RotateCcw, Sliders } from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"
import PageHeader from "../Common/PageHeader"

const PERIOD_OPTIONS = [
    { id: "today", label: "Today (12:00 AM – Now)" },
    { id: "7d", label: "Last 7 Days" },
    { id: "30d", label: "Last 30 Days" },
    { id: "custom", label: "Custom Range…" },
    { id: "all", label: "All Historical Data", dividerBefore: true },
]

const IDLE_PRESETS = [
    { label: "1.5", val: 1.5 },
    { label: "2.0 std", val: 2.0 },
    { label: "2.5", val: 2.5 },
    { label: "3.0 AC", val: 3.0 },
]

const controlBase = "h-8 rounded-md border text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
const controlState = (active) => active
    ? "border-brand/50 bg-brand-light text-slate-900"
    : "border-slate-200 bg-white text-slate-800 hover:border-slate-300"

export default function FuelHeader({
    title = "Fuel & Performance",
    subtitle,
    showFilters = true,
    actions,
    idleRate = 2.0,
    setIdleRate,
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
    const [isIdleOpen, setIsIdleOpen] = useState(false)
    const [tempStart, setTempStart] = useState(dateFrom || "")
    const [tempEnd, setTempEnd] = useState(dateTo || "")
    const [rangeError, setRangeError] = useState(null)

    const periodRef = useRef(null)
    const idleRef = useRef(null)

    // Close dropdowns on click outside
    useEffect(() => {
        function handleClickOutside(e) {
            if (periodRef.current && !periodRef.current.contains(e.target)) {
                setIsPeriodOpen(false)
            }
            if (idleRef.current && !idleRef.current.contains(e.target)) {
                setIsIdleOpen(false)
            }
        }
        document.addEventListener("mousedown", handleClickOutside)
        return () => document.removeEventListener("mousedown", handleClickOutside)
    }, [])

    // Escape closes open dropdowns or modals
    useEffect(() => {
        if (!isPeriodOpen && !isCustomModalOpen && !isIdleOpen) return undefined
        function handleKey(e) {
            if (e.key !== "Escape") return
            setIsPeriodOpen(false)
            setIsCustomModalOpen(false)
            setIsIdleOpen(false)
        }
        document.addEventListener("keydown", handleKey)
        return () => document.removeEventListener("keydown", handleKey)
    }, [isPeriodOpen, isCustomModalOpen, isIdleOpen])

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

    const hasActiveFilters = Boolean(vehicle || fuelType || period !== "today" || (idleRate && idleRate !== 2.0))

    const handleResetAll = () => {
        resetFilters()
        if (setIdleRate) setIdleRate(2.0)
    }

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
                                className={`${controlBase} ${controlState(Boolean(vehicle))} max-w-[170px] appearance-none truncate pl-2.5 pr-7`}
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

                        {/* 4. Compact Collapsible Idle Rate Control */}
                        {setIdleRate && (
                            <div className="relative" ref={idleRef}>
                                <button
                                    type="button"
                                    onClick={() => setIsIdleOpen(!isIdleOpen)}
                                    aria-haspopup="dialog"
                                    aria-expanded={isIdleOpen}
                                    title="Configure idle burn rate assumption"
                                    className={`${controlBase} ${controlState(idleRate !== 2.0)} inline-flex items-center gap-1.5 px-2.5`}
                                >
                                    <Sliders className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                    <span>Idle: {idleRate.toFixed(1)} L/h</span>
                                    <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
                                </button>

                                {isIdleOpen && (
                                    <div
                                        role="dialog"
                                        aria-label="Idle burn rate configuration"
                                        className="absolute right-0 top-full z-40 mt-1 w-64 rounded-lg border border-slate-200 bg-white p-3 shadow-pop"
                                    >
                                        <div className="mb-2 flex items-center justify-between border-b border-slate-100 pb-1.5">
                                            <span className="text-xs font-semibold text-slate-900">
                                                Idle Burn Rate
                                            </span>
                                            <span className="font-mono text-xs font-bold text-brand">
                                                {idleRate.toFixed(1)} L/h
                                            </span>
                                        </div>
                                        <p className="mb-2.5 text-[11px] leading-relaxed text-slate-500">
                                            Basis for stationary idle fuel and cost estimates.
                                        </p>

                                        {/* Presets */}
                                        <div className="mb-2.5 flex items-center justify-between gap-1">
                                            {IDLE_PRESETS.map((preset) => (
                                                <button
                                                    key={preset.val}
                                                    type="button"
                                                    onClick={() => setIdleRate(preset.val)}
                                                    className={`flex-1 rounded py-1 text-[11px] font-medium transition-colors cursor-pointer ${
                                                        idleRate === preset.val
                                                            ? "bg-brand text-white font-semibold"
                                                            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                                                    }`}
                                                >
                                                    {preset.label}
                                                </button>
                                            ))}
                                        </div>

                                        {/* Slider */}
                                        <div className="space-y-1">
                                            <input
                                                type="range"
                                                min="0.5"
                                                max="5.0"
                                                step="0.1"
                                                value={idleRate}
                                                onChange={(e) => setIdleRate(parseFloat(e.target.value))}
                                                className="w-full accent-brand cursor-pointer"
                                                aria-label="Idle burn rate (litres per hour)"
                                            />
                                            <div className="flex justify-between font-mono text-[10px] text-slate-400">
                                                <span>0.5 L/h</span>
                                                <span className="text-slate-600">Default: 2.0</span>
                                                <span>5.0 L/h</span>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* 5. Reset Filters button */}
                        {hasActiveFilters && (
                            <button
                                type="button"
                                onClick={handleResetAll}
                                title="Reset all filters to default"
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
