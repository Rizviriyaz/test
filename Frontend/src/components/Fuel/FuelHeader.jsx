import { useState, useRef, useEffect } from "react"
import { Calendar, ChevronDown, Check, X, RotateCcw } from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"

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

    const handleSelectPeriod = (p) => {
        if (p === "custom") {
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
            alert("Start date must be before or equal to End date.")
            return
        }
        applyCustomRange(tempStart, tempEnd)
        setIsCustomModalOpen(false)
    }

    const hasActiveFilters = Boolean(vehicle || fuelType || period !== "today")

    return (
        <section className="mb-6 space-y-4">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                {/* Title & dynamic period description */}
                <div>
                    <div className="flex items-center gap-2">
                        <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#33758F]" />
                        <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                            ATS Fuel & Telemetry Intelligence
                        </p>
                    </div>
                    <h1 className="mt-1 text-2xl font-black tracking-tight text-slate-900">
                        {title}
                    </h1>
                    <p className="mt-1 text-xs text-slate-500">
                        {subtitle || `Fleet Fuel Performance · ${periodLabel}`}
                    </p>
                </div>

                {/* Filter Controls */}
                {showFilters && (
                    <div className="flex flex-wrap items-center gap-2.5">
                        {/* 1. Global Period Selector */}
                        <div className="relative" ref={periodRef}>
                            <button
                                type="button"
                                onClick={() => setIsPeriodOpen(!isPeriodOpen)}
                                className={`inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 text-xs font-semibold shadow-xs transition-all cursor-pointer ${
                                    period !== "today"
                                        ? "border-[#33758F] bg-[#33758F]/5 text-[#33758F]"
                                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                                }`}
                            >
                                <Calendar className="h-4 w-4 text-[#33758F] shrink-0" />
                                <span className="font-bold">{periodLabel}</span>
                                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
                            </button>

                            {isPeriodOpen && (
                                <div className="absolute right-0 top-full z-40 mt-1.5 w-56 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg animate-in fade-in zoom-in-95 duration-100">
                                    <div className="px-2.5 py-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                                        Select Period (UAE Time)
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => handleSelectPeriod("today")}
                                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs font-semibold text-left transition-colors cursor-pointer ${
                                            period === "today"
                                                ? "bg-[#33758F] text-white"
                                                : "text-slate-700 hover:bg-slate-50"
                                        }`}
                                    >
                                        <span>Today (12:00 AM – Now)</span>
                                        {period === "today" && <Check className="h-3.5 w-3.5" />}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleSelectPeriod("7d")}
                                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs font-semibold text-left transition-colors cursor-pointer ${
                                            period === "7d"
                                                ? "bg-[#33758F] text-white"
                                                : "text-slate-700 hover:bg-slate-50"
                                        }`}
                                    >
                                        <span>Last 7 Days</span>
                                        {period === "7d" && <Check className="h-3.5 w-3.5" />}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleSelectPeriod("30d")}
                                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs font-semibold text-left transition-colors cursor-pointer ${
                                            period === "30d"
                                                ? "bg-[#33758F] text-white"
                                                : "text-slate-700 hover:bg-slate-50"
                                        }`}
                                    >
                                        <span>Last 30 Days</span>
                                        {period === "30d" && <Check className="h-3.5 w-3.5" />}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => handleSelectPeriod("custom")}
                                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs font-semibold text-left transition-colors cursor-pointer ${
                                            period === "custom"
                                                ? "bg-[#33758F] text-white"
                                                : "text-slate-700 hover:bg-slate-50"
                                        }`}
                                    >
                                        <span>Custom Range…</span>
                                        {period === "custom" && <Check className="h-3.5 w-3.5" />}
                                    </button>
                                    <div className="my-1 border-t border-slate-100" />
                                    <button
                                        type="button"
                                        onClick={() => handleSelectPeriod("all")}
                                        className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs font-semibold text-left transition-colors cursor-pointer ${
                                            period === "all"
                                                ? "bg-[#33758F] text-white"
                                                : "text-slate-700 hover:bg-slate-50"
                                        }`}
                                    >
                                        <span>All Historical Data</span>
                                        {period === "all" && <Check className="h-3.5 w-3.5" />}
                                    </button>
                                </div>
                            )}
                        </div>

                        {/* 2. Vehicle Selector */}
                        <div className="relative">
                            <select
                                value={vehicle}
                                onChange={(e) => setVehicle(e.target.value)}
                                className={`rounded-xl border py-2 pl-3 pr-8 text-xs font-semibold shadow-xs transition-colors cursor-pointer appearance-none ${
                                    vehicle
                                        ? "border-[#33758F] bg-[#33758F]/5 text-[#33758F]"
                                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                                }`}
                            >
                                <option value="">All Vehicles ({vehiclesList.length > 0 ? vehiclesList.length : "…"})</option>
                                {vehiclesList.map((v) => (
                                    <option key={v} value={v}>
                                        {v}
                                    </option>
                                ))}
                            </select>
                            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
                        </div>

                        {/* 3. Fuel Type Selector */}
                        <div className="relative">
                            <select
                                value={fuelType}
                                onChange={(e) => setFuelType(e.target.value)}
                                className={`rounded-xl border py-2 pl-3 pr-8 text-xs font-semibold shadow-xs transition-colors cursor-pointer appearance-none ${
                                    fuelType
                                        ? "border-[#33758F] bg-[#33758F]/5 text-[#33758F]"
                                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
                                }`}
                            >
                                <option value="">All Fuel Types</option>
                                {fuelTypesList.map((ft) => (
                                    <option key={ft} value={ft}>
                                        {ft}
                                    </option>
                                ))}
                            </select>
                            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-400" />
                        </div>

                        {/* Reset Filters button */}
                        {hasActiveFilters && (
                            <button
                                type="button"
                                onClick={resetFilters}
                                title="Reset all filters to Today / All"
                                className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-2.5 py-2 text-xs font-semibold text-slate-500 hover:bg-slate-50 hover:text-slate-800 shadow-xs cursor-pointer transition-colors"
                            >
                                <RotateCcw className="h-3.5 w-3.5 text-slate-400" />
                                <span>Reset</span>
                            </button>
                        )}

                        {actions}
                    </div>
                )}
                {!showFilters && actions && (
                    <div className="flex items-center gap-2.5">
                        {actions}
                    </div>
                )}
            </div>

            {/* Custom Range Modal / Dropdown */}
            {isCustomModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-xs p-4">
                    <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-5 shadow-2xl">
                        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                            <h3 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                <Calendar className="h-4 w-4 text-[#33758F]" />
                                Set Custom Date Range
                            </h3>
                            <button
                                type="button"
                                onClick={() => setIsCustomModalOpen(false)}
                                className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        </div>

                        <form onSubmit={handleApplyCustom} className="mt-4 space-y-3.5">
                            <div>
                                <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">
                                    Start Date (YYYY-MM-DD)
                                </label>
                                <input
                                    type="date"
                                    required
                                    value={tempStart}
                                    onChange={(e) => setTempStart(e.target.value)}
                                    className="w-full rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2 text-xs font-semibold text-slate-800 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                                />
                            </div>

                            <div>
                                <label className="block text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-1">
                                    End Date (YYYY-MM-DD)
                                </label>
                                <input
                                    type="date"
                                    required
                                    value={tempEnd}
                                    onChange={(e) => setTempEnd(e.target.value)}
                                    className="w-full rounded-xl border border-slate-200 bg-slate-50/50 px-3 py-2 text-xs font-semibold text-slate-800 focus:border-[#33758F] focus:bg-white focus:outline-hidden"
                                />
                            </div>

                            <div className="flex items-center justify-end gap-2 pt-2">
                                <button
                                    type="button"
                                    onClick={() => setIsCustomModalOpen(false)}
                                    className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50 cursor-pointer"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="rounded-xl bg-[#33758F] px-4 py-2 text-xs font-bold text-white hover:bg-[#285d73] shadow-xs cursor-pointer"
                                >
                                    Apply Range
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </section>
    )
}
