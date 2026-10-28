import { useState, useEffect, useMemo, useCallback } from "react"
import { getFuelTypes, getFuelVehicles } from "../api/fleetApi"
import { getUAETodayStr, getUAEDateStr } from "../utils/datetime"
import { FuelFilterContext } from "./fuel-filter-context"
import { lastCompleteMonth, monthBounds, monthLabel } from "../components/Fuel/fuelPeriod"

export function FuelFilterProvider({ children }) {
    // Period selection: 'month' (selectedMonth, the default: the last complete UAE month) or 'custom'.
    // 'today', 'yesterday', '7d', '30d' and 'all' remain supported values.
    const [period, setPeriod] = useState("month")
    const [selectedMonth, setSelectedMonth] = useState(() => lastCompleteMonth(getUAETodayStr()))
    const [customFrom, setCustomFrom] = useState("")
    const [customTo, setCustomTo] = useState("")
    const [vehicle, setVehicle] = useState("")
    const [fuelType, setFuelType] = useState("")

    // Dynamic dropdown lists from backend
    const [vehiclesList, setVehiclesList] = useState([])
    const [fuelTypesList, setFuelTypesList] = useState([])
    const [loadingFilterOptions, setLoadingFilterOptions] = useState(false)

    // Load dynamic filter options from backend
    useEffect(() => {
        let active = true
        async function fetchFilterOptions() {
            try {
                setLoadingFilterOptions(true)
                const [ftRes, vehRes] = await Promise.allSettled([
                    getFuelTypes(),
                    getFuelVehicles({ page: 1, page_size: 500 }),
                ])

                if (!active) return

                if (ftRes.status === "fulfilled" && Array.isArray(ftRes.value?.data)) {
                    setFuelTypesList(ftRes.value.data.map(item => item.fuel_type).filter(Boolean))
                }

                if (vehRes.status === "fulfilled" && Array.isArray(vehRes.value?.data)) {
                    setVehiclesList(vehRes.value.data.map(item => item.vehicle_identifier).filter(Boolean))
                }
            } catch (err) {
                console.error("Failed to load fuel filter options:", err)
            } finally {
                if (active) setLoadingFilterOptions(false)
            }
        }

        fetchFilterOptions()
        return () => { active = false }
    }, [])

    // Calculate effective date_from and date_to based on current period and UAE time
    const { dateFrom, dateTo, periodLabel } = useMemo(() => {
        const todayStr = getUAETodayStr()
        if (period === "month") {
            const { from, to } = monthBounds(selectedMonth)
            return {
                dateFrom: from,
                dateTo: to,
                periodLabel: monthLabel(selectedMonth, true),
            }
        }
        if (period === "today") {
            return {
                dateFrom: todayStr,
                dateTo: todayStr,
                periodLabel: "Today",
            }
        }
        if (period === "yesterday") {
            const yesterdayStr = getUAEDateStr(-1)
            return {
                dateFrom: yesterdayStr,
                dateTo: yesterdayStr,
                periodLabel: "Yesterday",
            }
        }
        if (period === "7d") {
            const past7 = getUAEDateStr(-6)
            return {
                dateFrom: past7,
                dateTo: todayStr,
                periodLabel: "Last 7 Days",
            }
        }
        if (period === "30d") {
            const past30 = getUAEDateStr(-29)
            return {
                dateFrom: past30,
                dateTo: todayStr,
                periodLabel: "Last 30 Days",
            }
        }
        if (period === "custom") {
            const label = (customFrom && customTo) 
                ? `${customFrom} → ${customTo}` 
                : customFrom ? `From ${customFrom}` : customTo ? `Until ${customTo}` : "Custom Range"
            return {
                dateFrom: customFrom,
                dateTo: customTo,
                periodLabel: label,
            }
        }
        // "all" - unbounded
        return {
            dateFrom: "",
            dateTo: "",
            periodLabel: "All Time",
        }
    }, [period, selectedMonth, customFrom, customTo])

    const setMonth = useCallback((month) => {
        setSelectedMonth(month)
        setPeriod("month")
    }, [])

    const applyCustomRange = useCallback((from, to) => {
        setCustomFrom(from || "")
        setCustomTo(to || "")
        setPeriod("custom")
    }, [])

    // Back to the default view: the last complete month, every vehicle and fuel type
    const resetFilters = useCallback(() => {
        setSelectedMonth(lastCompleteMonth(getUAETodayStr()))
        setPeriod("month")
        setCustomFrom("")
        setCustomTo("")
        setVehicle("")
        setFuelType("")
    }, [])

    const queryParams = useMemo(() => {
        const params = {}
        if (dateFrom) params.date_from = dateFrom
        if (dateTo) params.date_to = dateTo
        if (vehicle) params.vehicle_identifier = vehicle
        if (fuelType) params.fuel_type = fuelType
        return params
    }, [dateFrom, dateTo, vehicle, fuelType])

    const value = {
        period,
        setPeriod,
        selectedMonth,
        setMonth,
        customFrom,
        customTo,
        applyCustomRange,
        dateFrom,
        dateTo,
        periodLabel,
        vehicle,
        setVehicle,
        fuelType,
        setFuelType,
        vehiclesList,
        fuelTypesList,
        loadingFilterOptions,
        resetFilters,
        queryParams,
    }

    return (
        <FuelFilterContext.Provider value={value}>
            {children}
        </FuelFilterContext.Provider>
    )
}
