import { useState, useRef, useEffect, useMemo } from "react"
import {
    Calendar,
    ChevronDown,
    Check,
    X,
    RotateCcw,
    Sliders,
    Search,
    Truck,
    UploadCloud,
    History,
} from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"
import { useAuth } from "../../context/useAuth"

// Roles the backend accepts for fuel import (FUEL_IMPORT_ROLES in Backend/app/fuel/routes.py); it stays the authority
const FUEL_IMPORT_ROLES = new Set(["admin", "manager", "fleet_manager"])
import PageHeader from "../Common/PageHeader"

const PERIOD_OPTIONS = [
    { id: "today", label: "Today" },
    { id: "yesterday", label: "Yesterday" },
    { id: "7d", label: "Last 7 Days" },
    { id: "30d", label: "Last 30 Days" },
    { id: "custom", label: "Custom Range…" },
    { id: "all", label: "All Time", dividerBefore: true },
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
    onOpenImport,
    onOpenHistory,
}) {
    const { profile } = useAuth()
    const canImport = FUEL_IMPORT_ROLES.has(profile?.role)
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
    const [isVehicleOpen, setIsVehicleOpen] = useState(false)
    const [vehicleSearch, setVehicleSearch] = useState("")
    const [isAdvancedOpen, setIsAdvancedOpen] = useState(false)

    const [tempStart, setTempStart] = useState(dateFrom || "")
    const [tempEnd, setTempEnd] = useState(dateTo || "")
    const [rangeError, setRangeError] = useState(null)

    const periodRef = useRef(null)
    const vehicleRef = useRef(null)
    const advancedRef = useRef(null)
    const vehicleSearchInputRef = useRef(null)

    // Filter vehicles by search term in combobox
    const filteredVehicles = useMemo(() => {
        if (!vehicleSearch.trim()) return vehiclesList
        const q = vehicleSearch.trim().toLowerCase()
        return vehiclesList.filter((v) => v.toLowerCase().includes(q))
    }, [vehiclesList, vehicleSearch])

    // Focus search input when vehicle dropdown opens (the search text is cleared when it is opened)
    useEffect(() => {
        if (isVehicleOpen) {
            setTimeout(() => vehicleSearchInputRef.current?.focus(), 50)
        }
    }, [isVehicleOpen])

    // Close dropdowns on click outside
    useEffect(() => {
        function handleClickOutside(e) {
            if (periodRef.current && !periodRef.current.contains(e.target)) {
                setIsPeriodOpen(false)
            }
            if (vehicleRef.current && !vehicleRef.current.contains(e.target)) {
                setIsVehicleOpen(false)
            }
            if (advancedRef.current && !advancedRef.current.contains(e.target)) {
                setIsAdvancedOpen(false)
            }
        }
        document.addEventListener("mousedown", handleClickOutside)
        return () => document.removeEventListener("mousedown", handleClickOutside)
    }, [])

    // Escape closes open dropdowns or modals
    useEffect(() => {
        if (!isPeriodOpen && !isCustomModalOpen && !isVehicleOpen && !isAdvancedOpen) return undefined
        function handleKey(e) {
            if (e.key !== "Escape") return
            setIsPeriodOpen(false)
            setIsCustomModalOpen(false)
            setIsVehicleOpen(false)
            setIsAdvancedOpen(false)
        }
        document.addEventListener("keydown", handleKey)
        return () => document.removeEventListener("keydown", handleKey)
    }, [isPeriodOpen, isCustomModalOpen, isVehicleOpen, isAdvancedOpen])

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

    const hasAdvancedActive = Boolean(fuelType || (idleRate && idleRate !== 2.0))
    const hasActiveFilters = Boolean(vehicle || fuelType || period !== "all" || (idleRate && idleRate !== 2.0))

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
                    <div className="flex flex-wrap items-center gap-2">
                        {/* 1. Date Range Selector */}
                        <div className="relative" ref={periodRef}>
                            <button
                                type="button"
                                onClick={() => setIsPeriodOpen(!isPeriodOpen)}
                                aria-haspopup="listbox"
                                aria-expanded={isPeriodOpen}
                                className={`${controlBase} ${controlState(period !== "all")} inline-flex items-center gap-1.5 px-2.5`}
                            >
                                <Calendar className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                <span>{periodLabel}</span>
                                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
                            </button>

                            {isPeriodOpen && (
                                <div role="listbox" className="absolute left-0 top-full z-40 mt-1 w-52 rounded-lg border border-slate-200 bg-white p-1 shadow-pop sm:left-auto sm:right-0">
                                    <div className="px-2 py-1.5 text-[11px] font-medium text-slate-400 uppercase tracking-wider">
                                        Date Range
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
                                                        ? "font-semibold text-slate-900 bg-slate-50"
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

                        {/* 2. Searchable Vehicle Selector */}
                        <div className="relative" ref={vehicleRef}>
                            <button
                                type="button"
                                onClick={() => {
                                    if (!isVehicleOpen) setVehicleSearch("")
                                    setIsVehicleOpen(!isVehicleOpen)
                                }}
                                aria-haspopup="listbox"
                                aria-expanded={isVehicleOpen}
                                className={`${controlBase} ${controlState(Boolean(vehicle))} inline-flex items-center gap-1.5 px-2.5 max-w-[190px]`}
                            >
                                <Truck className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                <span className="truncate">
                                    {vehicle || `All Vehicles (${vehiclesList.length > 0 ? vehiclesList.length : "…"})`}
                                </span>
                                {vehicle ? (
                                    <span
                                        role="button"
                                        tabIndex={0}
                                        onClick={(e) => {
                                            e.stopPropagation()
                                            setVehicle("")
                                        }}
                                        onKeyDown={(e) => {
                                            if (e.key === "Enter" || e.key === " ") {
                                                e.stopPropagation()
                                                setVehicle("")
                                            }
                                        }}
                                        className="ml-0.5 rounded-sm p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 cursor-pointer"
                                        title="Clear vehicle filter"
                                    >
                                        <X className="h-3 w-3" />
                                    </span>
                                ) : (
                                    <ChevronDown className="h-3.5 w-3.5 text-slate-400 shrink-0" />
                                )}
                            </button>

                            {isVehicleOpen && (
                                <div role="listbox" className="absolute left-0 top-full z-40 mt-1 w-64 rounded-lg border border-slate-200 bg-white p-1.5 shadow-pop sm:left-auto sm:right-0">
                                    <div className="relative mb-1">
                                        <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                                        <input
                                            ref={vehicleSearchInputRef}
                                            type="text"
                                            value={vehicleSearch}
                                            onChange={(e) => setVehicleSearch(e.target.value)}
                                            placeholder="Search vehicle identifier…"
                                            className="h-7 w-full rounded border border-slate-200 bg-slate-50 pl-7 pr-2 text-xs text-slate-800 placeholder-slate-400 focus:border-brand focus:bg-white focus:outline-none"
                                        />
                                    </div>
                                    <div className="max-h-52 overflow-y-auto divide-y divide-slate-50">
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setVehicle("")
                                                setIsVehicleOpen(false)
                                            }}
                                            role="option"
                                            aria-selected={!vehicle}
                                            className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs transition-colors cursor-pointer ${
                                                !vehicle ? "font-semibold text-slate-900 bg-slate-50" : "text-slate-700 hover:bg-slate-50"
                                            }`}
                                        >
                                            <span>All Vehicles ({vehiclesList.length})</span>
                                            {!vehicle && <Check className="h-3.5 w-3.5 text-brand" />}
                                        </button>
                                        {filteredVehicles.map((v) => (
                                            <button
                                                key={v}
                                                type="button"
                                                onClick={() => {
                                                    setVehicle(v)
                                                    setIsVehicleOpen(false)
                                                }}
                                                role="option"
                                                aria-selected={vehicle === v}
                                                className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs transition-colors cursor-pointer ${
                                                    vehicle === v ? "font-semibold text-slate-900 bg-slate-50" : "text-slate-700 hover:bg-slate-50"
                                                }`}
                                            >
                                                <span className="font-mono text-xs">{v}</span>
                                                {vehicle === v && <Check className="h-3.5 w-3.5 text-brand" />}
                                            </button>
                                        ))}
                                        {filteredVehicles.length === 0 && (
                                            <div className="py-3 text-center text-[11px] text-slate-400">
                                                No vehicles matching &ldquo;{vehicleSearch}&rdquo;
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}
                        </div>

                        {/* 3. Compact Advanced Filters Popover */}
                        <div className="relative" ref={advancedRef}>
                            <button
                                type="button"
                                onClick={() => setIsAdvancedOpen(!isAdvancedOpen)}
                                aria-haspopup="dialog"
                                aria-expanded={isAdvancedOpen}
                                className={`${controlBase} ${controlState(hasAdvancedActive)} inline-flex items-center gap-1.5 px-2.5`}
                                title="Advanced filters (Fuel type, Idle burn rate, Reset)"
                            >
                                <Sliders className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                                <span>Advanced Filters</span>
                                {hasAdvancedActive && (
                                    <span className="h-1.5 w-1.5 rounded-full bg-brand" />
                                )}
                                <ChevronDown className="h-3.5 w-3.5 text-slate-400" />
                            </button>

                            {isAdvancedOpen && (
                                <div
                                    role="dialog"
                                    aria-label="Advanced filters"
                                    className="absolute right-0 top-full z-40 mt-1 w-72 rounded-xl border border-slate-200 bg-white p-3.5 shadow-pop"
                                >
                                    <div className="mb-3 flex items-center justify-between border-b border-slate-100 pb-2">
                                        <span className="text-xs font-semibold text-slate-900">
                                            Advanced Filters
                                        </span>
                                        {hasActiveFilters && (
                                            <button
                                                type="button"
                                                onClick={handleResetAll}
                                                className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-500 hover:text-brand cursor-pointer"
                                            >
                                                <RotateCcw className="h-3 w-3" />
                                                <span>Reset all</span>
                                            </button>
                                        )}
                                    </div>

                                    {/* Fuel Type */}
                                    <div className="mb-3.5">
                                        <label htmlFor="fuel-type-select" className="mb-1 block text-[11px] font-medium text-slate-600">
                                            Fuel Type
                                        </label>
                                        <div className="relative">
                                            <select
                                                id="fuel-type-select"
                                                value={fuelType}
                                                onChange={(e) => setFuelType(e.target.value)}
                                                className="h-8 w-full appearance-none rounded-md border border-slate-200 bg-white pl-2.5 pr-7 text-xs font-medium text-slate-800 cursor-pointer focus:border-brand focus:outline-none"
                                            >
                                                <option value="">All Fuel Types ({fuelTypesList.length > 0 ? fuelTypesList.length : "All"})</option>
                                                {fuelTypesList.map((ft) => (
                                                    <option key={ft} value={ft}>
                                                        {ft}
                                                    </option>
                                                ))}
                                            </select>
                                            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                                        </div>
                                    </div>

                                    {/* Idle Burn Rate */}
                                    {setIdleRate && (
                                        <div className="space-y-2 border-t border-slate-100 pt-3">
                                            <div className="flex items-center justify-between">
                                                <span className="text-[11px] font-medium text-slate-600">
                                                    Idle Burn Rate
                                                </span>
                                                <span className="font-mono text-xs font-bold text-brand">
                                                    {idleRate.toFixed(1)} L/h
                                                </span>
                                            </div>
                                            <p className="text-[10px] leading-relaxed text-slate-400">
                                                Assumption for stationary idle consumption and waste costs.
                                            </p>

                                            {/* Presets */}
                                            <div className="flex items-center justify-between gap-1">
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
                                            <div className="space-y-1 pt-1">
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
                        </div>

                        {/* 4. Reset Filters button (if active) */}
                        {hasActiveFilters && (
                            <button
                                type="button"
                                onClick={handleResetAll}
                                title="Reset all filters to defaults"
                                className={`${controlBase} inline-flex items-center gap-1.5 border-slate-200 bg-white px-2.5 text-slate-500 hover:bg-slate-50 hover:text-slate-800`}
                            >
                                <RotateCcw className="h-3.5 w-3.5 text-slate-400" />
                                <span>Reset</span>
                            </button>
                        )}
                    </div>
                )}

                {/* Import Fuel & Import History Actions */}
                <div className="flex items-center gap-2">
                    {onOpenHistory && (
                        <button
                            type="button"
                            onClick={onOpenHistory}
                            className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                            title="View recent fuel statement imports and audit batches"
                        >
                            <History className="h-3.5 w-3.5 text-slate-500" />
                            <span>Import History</span>
                        </button>
                    )}

                    {onOpenImport && (
                        <button
                            type="button"
                            onClick={canImport ? onOpenImport : undefined}
                            disabled={!canImport}
                            className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-[#33758F] px-3 text-xs font-medium text-white transition-colors hover:bg-[#285d72] cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-[#33758F]"
                            title={canImport ? "Upload new Excel fuel statement" : "Your role does not permit importing fuel statements"}
                        >
                            <UploadCloud className="h-3.5 w-3.5" />
                            <span>Import Fuel</span>
                        </button>
                    )}

                    {actions}
                </div>
            </PageHeader>

            {/* Custom Date Range Modal */}
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
