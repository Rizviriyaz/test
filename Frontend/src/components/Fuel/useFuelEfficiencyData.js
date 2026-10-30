import { useState, useEffect, useRef, useCallback } from "react"
import { getFleetEfficiency, getVehicleEfficiencyList, clearClientCache } from "../../api/fleetApi"
import { fuelVehicleListParams } from "../../utils/fleetRules"
import { TELEMATICS_RETRY_DELAY_MS, shouldRetryTelematics } from "./telemetryFormat"

// Fleet efficiency KPIs, the vehicle leaderboard and the fleet-wide idle fuel ranking for the fuel filters
export default function useFuelEfficiencyData(queryParams) {
    // Configurable Telematics Idle Rate (Litres/Hour)
    const [idleRate, setIdleRate] = useState(2.0)

    // Requests use the debounced rate, so dragging the slider does not fire a request per 0.1 L/h step
    const [idleRateQuery, setIdleRateQuery] = useState(2.0)
    useEffect(() => {
        const t = setTimeout(() => setIdleRateQuery(idleRate), 300)
        return () => clearTimeout(t)
    }, [idleRate])

    // Latest-request guards: a slower earlier response must never overwrite a newer one
    const efficiencyRequestIdRef = useRef(0)
    const rankingsRequestIdRef = useRef(0)
    const idleWastersRequestIdRef = useRef(0)

    // Fleet Efficiency & Overview Metrics
    const [efficiency, setEfficiency] = useState(null)
    const [loadingEfficiency, setLoadingEfficiency] = useState(true)
    const [overviewError, setOverviewError] = useState(null)

    // Fleet-wide idle fuel ranking (independent of the leaderboard's page, search, sort and status)
    const [idleWasters, setIdleWasters] = useState([])
    const [loadingIdleWasters, setLoadingIdleWasters] = useState(true)
    const [idleWastersError, setIdleWastersError] = useState(null)

    // Rankings & Leaderboard State
    const [sortBy, setSortBy] = useState("efficiency")
    const [sortOrder, setSortOrder] = useState("desc")
    const [statusFilter, setStatusFilter] = useState("")
    const [searchQuery, setSearchQuery] = useState("")
    const [page, setPage] = useState(1)
    const [pageSize] = useState(20)
    const [vehiclesData, setVehiclesData] = useState({ data: [], total: 0, page: 1 })
    const [loadingRankings, setLoadingRankings] = useState(true)
    const [rankingsError, setRankingsError] = useState(null)

    // 1. Fetch Fleet Efficiency Metrics
    // `silent`: a background refresh (telematics retry) keeps the figures on screen instead of a loading state
    const loadEfficiencyData = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++efficiencyRequestIdRef.current
        try {
            if (!silent) setLoadingEfficiency(true)
            setOverviewError(null)
            const res = await getFleetEfficiency({
                ...queryParams,
                idle_rate_lph: idleRateQuery,
            })
            if (requestId !== efficiencyRequestIdRef.current) return
            setEfficiency(res)
        } catch (err) {
            if (requestId !== efficiencyRequestIdRef.current) return
            setEfficiency(null)
            setOverviewError(err.message || "Failed to load fleet efficiency metrics.")
        } finally {
            if (requestId === efficiencyRequestIdRef.current) setLoadingEfficiency(false)
        }
    }, [queryParams, idleRateQuery])

    // 2. Fetch Vehicle Rankings
    const loadRankingsData = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++rankingsRequestIdRef.current
        try {
            if (!silent) setLoadingRankings(true)
            setRankingsError(null)
            const res = await getVehicleEfficiencyList({
                page,
                page_size: pageSize,
                sort_by: sortBy,
                sort_order: sortOrder,
                // Global filters first, so the local search box (and status/sort) is never overwritten by them. The
                // global vehicle filter is exact (vehicle_filter, as the KPI cards); the search box stays a substring.
                ...fuelVehicleListParams(queryParams, searchQuery),
                status: statusFilter || undefined,
                idle_rate_lph: idleRateQuery,
            })
            if (requestId !== rankingsRequestIdRef.current) return
            setVehiclesData(res || { data: [], total: 0, page: 1 })
        } catch (err) {
            if (requestId !== rankingsRequestIdRef.current) return
            setVehiclesData({ data: [], total: 0, page: 1 })
            setRankingsError(err.message || "Failed to load vehicle rankings.")
        } finally {
            if (requestId === rankingsRequestIdRef.current) setLoadingRankings(false)
        }
    }, [page, pageSize, sortBy, sortOrder, statusFilter, searchQuery, queryParams, idleRateQuery])

    // 3. Fetch the fleet-wide idle fuel ranking: period and global filters only, never the leaderboard's
    // page, search, sort or status
    const loadIdleWasters = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++idleWastersRequestIdRef.current
        try {
            if (!silent) setLoadingIdleWasters(true)
            setIdleWastersError(null)
            const res = await getVehicleEfficiencyList({
                page: 1,
                page_size: 5,
                sort_by: "estimated_idle_fuel",
                sort_order: "desc",
                // No status filter: vehicles with known idle hours but unknown distance (no_distance_data) carry the
                // same idle estimate as the KPI; unknown estimates are dropped below, never ranked as 0
                idle_rate_lph: idleRateQuery,
                ...fuelVehicleListParams(queryParams),
            })
            if (requestId !== idleWastersRequestIdRef.current) return
            // Without Wialon telematics there is no ranking: say so rather than "no idle fuel"
            if (res?.telematics_status === "unavailable") {
                setIdleWasters([])
                setIdleWastersError(res.data_warnings?.[0] || "Wialon telematics unavailable.")
                return
            }
            // Unknown estimates are not ranked
            setIdleWasters((res?.data || []).filter(
                (v) => v.estimated_idle_fuel_litres !== null && v.estimated_idle_fuel_litres !== undefined
            ))
        } catch (err) {
            if (requestId !== idleWastersRequestIdRef.current) return
            setIdleWasters([])
            setIdleWastersError(err.message || "Failed to load the idle fuel ranking.")
        } finally {
            if (requestId === idleWastersRequestIdRef.current) setLoadingIdleWasters(false)
        }
    }, [queryParams, idleRateQuery])

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadEfficiencyData()
        loadIdleWasters()
    }, [loadEfficiencyData, loadIdleWasters])

    // Wialon reports still running on the server (telematics_pending): ask again, silently and a bounded number of
    // times, so real distance, driving and idle values replace the placeholders. The server runs one report per period
    // and caches it, so a retry joins it instead of starting another. Counted per filter set.
    const telematicsKey = `${new URLSearchParams(queryParams).toString()}|${idleRateQuery}`
    const [telematicsRetry, setTelematicsRetry] = useState({ key: null, count: 0 })
    const telematicsRetries = telematicsRetry.key === telematicsKey ? telematicsRetry.count : 0
    const telematicsPending = !loadingEfficiency && shouldRetryTelematics(efficiency, telematicsRetries)
    useEffect(() => {
        if (!telematicsPending) return undefined
        const timer = setTimeout(() => {
            setTelematicsRetry({ key: telematicsKey, count: telematicsRetries + 1 })
            clearClientCache("/api/fuel/analytics/efficiency")
            loadEfficiencyData({ silent: true })
            loadRankingsData({ silent: true })
            loadIdleWasters({ silent: true })
        }, TELEMATICS_RETRY_DELAY_MS)
        return () => clearTimeout(timer)
    }, [telematicsPending, telematicsKey, telematicsRetries, loadEfficiencyData, loadRankingsData, loadIdleWasters])

    const [pageFilters, setPageFilters] = useState(queryParams)
    if (pageFilters !== queryParams) {
        setPageFilters(queryParams)
        setPage(1)
    }

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- loaders fetch from the API and must show their loading state on every filter change
        loadRankingsData()
    }, [loadRankingsData])

    return {
        idleRate, setIdleRate,
        efficiency, loadingEfficiency, overviewError, loadEfficiencyData,
        idleWasters, loadingIdleWasters, idleWastersError, loadIdleWasters,
        vehiclesData, loadingRankings, rankingsError, loadRankingsData,
        sortBy, setSortBy, sortOrder, setSortOrder, statusFilter, setStatusFilter,
        searchQuery, setSearchQuery, page, setPage, pageSize,
        telematicsPending, telematicsRetries,
    }
}
