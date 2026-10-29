import { useState, useEffect, useMemo, useCallback } from "react"
import { getFuelVehicles } from "../api/fleetApi"
import { getUAETodayStr, getUAEDateStr } from "../utils/datetime"
import { FuelFilterContext } from "./fuel-filter-context"
import { lastCompleteMonth, monthBounds, monthLabel } from "../components/Fuel/fuelPeriod"

export function FuelFilterProvider({ children }) {
    // Period selection: 'month' (selectedMonth, the default: the last complete UAE month).
    // Supported values: 'month', 'today', 'yesterday', '7d', '30d', 'all'.
    const [period, setPeriod] = useState("month")
    const [selectedMonth, setSelectedMonth] = useState(() => lastCompleteMonth(getUAETodayStr()))
    const [vehicle, setVehicle] = useState("")

    // Dynamic dropdown lists from backend (vehicle list only)
    const [vehiclesList, setVehiclesList] = useState([])
    const [loadingFilterOptions, setLoadingFilterOptions] = useState(false)

    // Load dynamic filter options from backend
    useEffect(() => {
        let active = true
        async function fetchFilterOptions() {
            try {
                setLoadingFilterOptions(true)
                const vehRes = await getFuelVehicles({ page: 1, page_size: 500 })
                if (!active) return
                if (vehRes && Array.isArray(vehRes.data)) {
                    setVehiclesList(vehRes.data.map((item) => item.vehicle_identifier).filter(Boolean))
                }
            } catch (err) {
                console.error("Failed to load fuel filter options:", err)
            } finally {
                if (active) setLoadingFilterOptions(false)
            }
        }
        fetchFilterOptions()
        return () => {
            active = false
        }
    }, [])

    // Calculate effective date_from and date_to based on current period
    const { dateFrom, dateTo, periodLabel } = useMemo(() => {
        const todayStr = getUAETodayStr()
        if (period === "month") {
            const { from, to } = monthBounds(selectedMonth)
            return { dateFrom: from, dateTo: to, periodLabel: monthLabel(selectedMonth, true) }
        }
        if (period === "today") {
            return { dateFrom: todayStr, dateTo: todayStr, periodLabel: "Today" }
        }
        if (period === "yesterday") {
            const yesterdayStr = getUAEDateStr(-1)
            return { dateFrom: yesterdayStr, dateTo: yesterdayStr, periodLabel: "Yesterday" }
        }
        if (period === "7d") {
            const past7 = getUAEDateStr(-6)
            return { dateFrom: past7, dateTo: todayStr, periodLabel: "Last 7 Days" }
        }
        if (period === "30d") {
            const past30 = getUAEDateStr(-29)
            return { dateFrom: past30, dateTo: todayStr, periodLabel: "Last 30 Days" }
        }
        // "all" – unbounded
        return { dateFrom: "", dateTo: "", periodLabel: "All Time" }
    }, [period, selectedMonth])

    const setMonth = useCallback((month) => {
        setSelectedMonth(month)
        setPeriod("month")
    }, [])

    const resetFilters = useCallback(() => {
        setSelectedMonth(lastCompleteMonth(getUAETodayStr()))
        setPeriod("month")
        setVehicle("")
    }, [])

    const queryParams = useMemo(() => {
        const params = {}
        if (dateFrom) params.date_from = dateFrom
        if (dateTo) params.date_to = dateTo
        if (vehicle) params.vehicle_identifier = vehicle
        return params
    }, [dateFrom, dateTo, vehicle])

    const value = {
        period,
        setPeriod,
        selectedMonth,
        setMonth,
        dateFrom,
        dateTo,
        periodLabel,
        vehicle,
        setVehicle,
        vehiclesList,
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
