import { useState, useRef, useEffect, useMemo } from "react"
import {
    ChevronDown,
    Check,
    X,
    RotateCcw,
    Sliders,
    Search,
    Truck,
    UploadCloud,
} from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"
import { useAuth } from "../../context/useAuth"

// Roles the backend accepts for fuel import (FUEL_IMPORT_ROLES in Backend/app/fuel/routes.py); it stays the authority
const FUEL_IMPORT_ROLES = new Set(["admin", "manager", "fleet_manager"])
import PageHeader from "../Common/PageHeader"
import MonthNavigator from "./MonthNavigator"
import { lastCompleteMonth } from "./fuelPeriod"
import { getUAETodayStr } from "../../utils/datetime"

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
    title,
    subtitle,
    showFilters = true,
    actions,
    idleRate = 2.0,
    setIdleRate,
    onOpenImport,
}) {
    const { profile } = useAuth()
    const canImport = FUEL_IMPORT_ROLES.has(profile?.role)
    const {
        period,
        selectedMonth,
        setMonth,
        vehicle,
        setVehicle,
        vehiclesList,
        resetFilters,
    } = useFuelFilters()

    const [isVehicleOpen, setIsVehicleOpen] = useState(false)
    const [vehicleSearch, setVehicleSearch] = useState("")
    const [isAdvancedOpen, setIsAdvancedOpen] = useState(false)

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

    // Escape closes open dropdowns
    useEffect(() => {
        if (!isVehicleOpen && !isAdvancedOpen) return undefined
        function handleKey(e) {
            if (e.key !== "Escape") return
            setIsVehicleOpen(false)
            setIsAdvancedOpen(false)
        }
        document.addEventListener("keydown", handleKey)
        return () => document.removeEventListener("keydown", handleKey)
    }, [isVehicleOpen, isAdvancedOpen])

    const hasAdvancedActive = Boolean(idleRate && idleRate !== 2.0)
    const isDefaultPeriod = period === "month" && selectedMonth === lastCompleteMonth(getUAETodayStr())
    const hasActiveFilters = Boolean(vehicle || !isDefaultPeriod || (idleRate && idleRate !== 2.0))

    const handleResetAll = () => {
        resetFilters()
        if (setIdleRate) setIdleRate(2.0)
    }

    return (
        <PageHeader
            title={title}
            subtitle={subtitle}
            actions={
                (onOpenImport || actions) ? (
                    <div className="flex items-center gap-2">
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
                ) : null
            }
        >
            {showFilters && (
                <div className="flex flex-wrap items-center gap-2">
                    {/* 1. Month navigation */}
                    <MonthNavigator
                        month={selectedMonth}
                        onChange={setMonth}
                    />

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
                                {vehicle || `All Vehicles (${vehiclesList.length > 0 ? vehiclesList.length : "…" })`}
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

                    {/* 3. Compact Advanced Filters Popover (Idle burn rate settings) */}
                    <div className="relative" ref={advancedRef}>
                        <button
                            type="button"
                            onClick={() => setIsAdvancedOpen(!isAdvancedOpen)}
                            aria-haspopup="dialog"
                            aria-expanded={isAdvancedOpen}
                            className={`${controlBase} ${controlState(hasAdvancedActive)} inline-flex items-center gap-1.5 px-2.5`}
                            title="Idle burn rate settings"
                        >
                            <Sliders className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                            <span>Advanced</span>
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
                                        Advanced
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

                                {/* Idle Burn Rate */}
                                {setIdleRate && (
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <span className="text-[11px] font-medium text-slate-600">
                                                Idle fuel rate (estimate)
                                            </span>
                                            <span className="font-mono text-xs font-bold text-brand">
                                                {idleRate.toFixed(1)} L/h
                                            </span>
                                        </div>

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
        </PageHeader>
    )
}
