import { useState, useMemo, useCallback, useRef } from "react"
import { getFuelLatestMonth } from "../api/fleetApi"
import { getUAETodayStr, getUAEDateStr } from "../utils/datetime"
import { FuelFilterContext } from "./fuel-filter-context"
import { lastCompleteMonth, monthBounds, monthLabel, validLatestMonth } from "../components/Fuel/fuelPeriod"

export function FuelFilterProvider({ children }) {
    // Period selection: 'month' (selectedMonth, the default: the last complete UAE month).
    // Supported values: 'month', 'today', 'yesterday', '7d', '30d', 'all'.
    // The selected month changes only through setMonth (the user's month navigation or the jump button) and
    // resetFilters: never when a request finishes.
    const [period, setPeriod] = useState("month")
    const [selectedMonth, setSelectedMonth] = useState(() => lastCompleteMonth(getUAETodayStr()))

    // Latest month with matched monthly-summary fuel data, looked up in the background for the "Jump to latest month
    // with data" button (/api/fuel/analytics/latest-month: summary identifiers and months only, no dated rows or Wialon
    // reports). It only records the month (status: idle | loading | found | none | error); it never changes
    // selectedMonth. Looked up once per session, or again after an import (force).
    const [latestMonth, setLatestMonth] = useState({ status: "idle", month: null })
    const latestLookupRef = useRef(0)
    const lookupLatestMonth = useCallback(({ force = false } = {}) => {
        if (latestLookupRef.current && !force) return
        const lookup = ++latestLookupRef.current
        setLatestMonth((prev) => ({ status: "loading", month: prev.month }))
        getFuelLatestMonth()
            .then((res) => {
                if (lookup !== latestLookupRef.current) return
                const month = validLatestMonth(res?.month, getUAETodayStr())
                setLatestMonth({ status: month ? "found" : "none", month })
            })
            .catch(() => {
                if (lookup === latestLookupRef.current) setLatestMonth({ status: "error", month: null })
            })
    }, [])
    // Exact vehicle filter: no page control sets it any more (the month is the only page filter); kept for callers
    // that clear it (Data Coverage) and the drill-down
    const [vehicle, setVehicle] = useState("")

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
        latestMonth,
        lookupLatestMonth,
        dateFrom,
        dateTo,
        periodLabel,
        vehicle,
        setVehicle,
        resetFilters,
        queryParams,
    }

    return (
        <FuelFilterContext.Provider value={value}>
            {children}
        </FuelFilterContext.Provider>
    )
}
