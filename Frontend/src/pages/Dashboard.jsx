import { useEffect, useMemo, useState, useRef } from "react"
import { Link } from "react-router-dom"
import {
    Layers,
    Loader2,
    ArrowUpRight,
    CheckCircle2,
} from "lucide-react"

import {
    getVehicles,
    getDashboardMileage,
    getFleetAlertsAnalytics,
    getFleetTripsAnalytics,
    getFleetIdleAnalytics,
} from "../api/fleetApi"

import {
    ResponsiveContainer,
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    PieChart,
    Pie,
    Cell,
    Legend,
} from "recharts"

import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import SectionCard from "../components/Common/SectionCard"
import FilterSelect from "../components/Common/FilterSelect"
import { ErrorBanner, LoadingState } from "../components/Common/StateViews"
import { formatUAETime } from "../utils/datetime"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import {
    AXIS_LINE,
    CATEGORY_TICK,
    GRID_STROKE,
    SERIES_COLOR,
    TICK,
    TOOLTIP_CURSOR,
    TOOLTIP_STYLE,
    categoricalColor,
} from "../components/Common/chartTheme"
import LiveIdleTimer from "../components/Common/LiveIdleTimer"
import { compareKnownDesc, idleSecondsOf, isKnownNumber, sumKnown } from "../utils/knownTotals"

const TIMEFRAME_OPTIONS = [
    { id: "today", label: "Today", unit: "km today" },
    { id: "yesterday", label: "Yesterday", unit: "km yesterday" },
    { id: "week", label: "This Week", unit: "km (this week)" },
    { id: "month", label: "This Month", unit: "km (this month)" },
    { id: "year", label: "This Year", unit: "km (this year)" },
    { id: "all", label: "All Time", unit: "total km" },
]

function Dashboard() {
    // ─── Core State ─────────────────────────────────────────────────────────
    const [vehicles, setVehicles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [lastUpdated, setLastUpdated] = useState(null)

    // Global Vehicle Group Filter (Affects all sections on the page)
    const [selectedGroup, setSelectedGroup] = useState("ALL")

    // Alerts data
    const [alertsData, setAlertsData] = useState(null)

    // Today's trips data
    const [tripsData, setTripsData] = useState(null)

    // Today's verified idle analytics data
    const [idleAnalyticsData, setIdleAnalyticsData] = useState(null)

    // Today's dedicated mileage data (independent of ranking timeframe)
    const [todayMileageData, setTodayMileageData] = useState(null)
    const auxSeqRef = useRef(0)

    // Mileage ranking state
    const [mileageTimeframe, setMileageTimeframe] = useState("today")
    const [mileageLimit, setMileageLimit] = useState("5")
    const [mileageData, setMileageData] = useState(null)
    const [mileageLoading, setMileageLoading] = useState(false)

    // Chart type selectors
    const [utilizationChartType, setUtilizationChartType] = useState("bar") // "bar" | "horizontal" | "donut"
    const [idleChartType, setIdleChartType] = useState("bar") // "bar" | "horizontal"

    // Vehicle Details Drawer state
    const [selectedVehicleId, setSelectedVehicleId] = useState(null)

    // ─── 1. Load Live Vehicles Telemetry (Periodic Refresh) ─────────────────
    useEffect(() => {
        let mounted = true
        let timerId = null
        let isFetching = false

        const fetchVehiclesPeriodic = async () => {
            if (isFetching || !mounted) return
            // Polling pauses while the tab is hidden and resumes from the visibilitychange listener.
            if (document.hidden) return
            isFetching = true
            try {
                const data = await getVehicles()
                if (mounted) {
                    setVehicles(data.vehicles || [])
                    setLastUpdated(new Date())
                    setError(null)
                }
            } catch (err) {
                if (mounted) {
                    console.error("Failed to load vehicle data:", err)
                    setError("Failed to load vehicle data")
                }
            } finally {
                isFetching = false
                if (mounted) {
                    setLoading(false)
                    timerId = setTimeout(fetchVehiclesPeriodic, 10000)
                }
            }
        }

        fetchVehiclesPeriodic()
        const onVisibilityChange = () => {
            if (document.hidden || !mounted) return
            if (timerId) clearTimeout(timerId)
            fetchVehiclesPeriodic()
        }
        document.addEventListener("visibilitychange", onVisibilityChange)

        return () => {
            document.removeEventListener("visibilitychange", onVisibilityChange)
            mounted = false
            if (timerId) clearTimeout(timerId)
        }
    }, [])

    // ─── 2. Load Alerts, Today Trips, Idle Analytics & Today Mileage ────────
    const mileageTimeframeRef = useRef(mileageTimeframe)
    useEffect(() => {
        mileageTimeframeRef.current = mileageTimeframe
    }, [mileageTimeframe])

    useEffect(() => {
        let active = true
        let isFetching = false
        let timerId = null

        async function fetchAuxiliaryData() {
            if (isFetching || !active) return
            // Polling pauses while the tab is hidden and resumes from the visibilitychange listener.
            if (document.hidden) return
            isFetching = true
            const seq = ++auxSeqRef.current
            try {
                // Each result is applied as soon as it arrives, so a slow cold alerts scan
                // does not hold back the trips, idle and mileage cards; polling still waits for all four.
                const isCurrent = () => active && seq === auxSeqRef.current
                await Promise.allSettled([
                    getFleetAlertsAnalytics().then(res => { if (isCurrent()) setAlertsData(res) }),
                    getFleetTripsAnalytics({ timeframe: "today" }).then(res => { if (isCurrent()) setTripsData(res) }),
                    getFleetIdleAnalytics({ timeframe: "today", include_events: false }).then(res => { if (isCurrent()) setIdleAnalyticsData(res) }),
                    getDashboardMileage("today").then(res => {
                        if (!isCurrent()) return
                        setTodayMileageData(res)
                        if (mileageTimeframeRef.current === "today") {
                            setMileageData(res)
                        }
                    }),
                ])
            } catch (err) {
                console.error("Failed to fetch dashboard auxiliary data:", err)
            } finally {
                isFetching = false
                if (active) {
                    timerId = setTimeout(fetchAuxiliaryData, 30000)
                }
            }
        }
        fetchAuxiliaryData()
        const onVisibilityChange = () => {
            if (document.hidden || !active) return
            if (timerId) clearTimeout(timerId)
            fetchAuxiliaryData()
        }
        document.addEventListener("visibilitychange", onVisibilityChange)
        return () => {
            document.removeEventListener("visibilitychange", onVisibilityChange)
            active = false
            if (timerId) clearTimeout(timerId)
        }
    }, [])

    // ─── 3. Load Mileage Rankings by Timeframe ──────────────────────────────
    useEffect(() => {
        let active = true
        async function fetchMileage() {
            try {
                setMileageLoading(true)
                const data = await getDashboardMileage(mileageTimeframe)
                if (!active) return
                setMileageData(data)
            } catch (err) {
                console.error("Mileage ranking error:", err)
            } finally {
                if (active) setMileageLoading(false)
            }
        }
        fetchMileage()
        return () => { active = false }
    }, [mileageTimeframe])

    // ─── 4. Dynamic Group Discovery from Actual Wialon Data ─────────────────
    const availableGroups = useMemo(() => {
        const set = new Set()
        vehicles.forEach(v => {
            if (v.group && v.group.trim()) set.add(v.group.trim())
            if (Array.isArray(v.groups)) {
                v.groups.forEach(g => { if (g && g.trim()) set.add(g.trim()) })
            }
        })
        if (mileageData?.all_units) {
            mileageData.all_units.forEach(u => {
                if (u.group && u.group.trim()) set.add(u.group.trim())
            })
        }
        return Array.from(set).sort()
    }, [vehicles, mileageData])

    // ─── 5. Normalize Vehicles ──────────────────────────────────────────────
    const normalizedVehicles = useMemo(() => {
        return vehicles.map(vehicle => {
            const rawStatus = String(vehicle.status || "").trim().toUpperCase()
            const isOffline = vehicle.connection_status === "OFFLINE" ||
                Boolean(vehicle.is_offline) ||
                rawStatus === "OFFLINE"
            let finalStatus = "STOPPED"
            if (isOffline) {
                finalStatus = "OFFLINE"
            } else if (rawStatus === "MOVING") {
                finalStatus = "MOVING"
            } else if (rawStatus === "IDLING") {
                finalStatus = "IDLING"
            }
            return {
                ...vehicle,
                normalizedStatus: finalStatus,
                isOffline,
            }
        })
    }, [vehicles])

    // ─── 6. Global Group Filtering (Applied Page-wide) ──────────────────────
    const filteredVehicles = useMemo(() => {
        if (selectedGroup === "ALL") return normalizedVehicles
        return normalizedVehicles.filter(v => {
            const primaryMatch = (v.group || "").trim().toLowerCase() === selectedGroup.trim().toLowerCase()
            const secondaryMatch = Array.isArray(v.groups) && v.groups.some(g => (g || "").trim().toLowerCase() === selectedGroup.trim().toLowerCase())
            return primaryMatch || secondaryMatch
        })
    }, [normalizedVehicles, selectedGroup])

    const filteredVehicleIdSet = useMemo(() => {
        return new Set(filteredVehicles.map(v => String(v.vehicle_id)))
    }, [filteredVehicles])

    // ─── 7. KPI Calculations (Filtered by Global Group) ─────────────────────
    const totalVehiclesCount = filteredVehicles.length
    const movingVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "MOVING"), [filteredVehicles])
    const idlingVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "IDLING"), [filteredVehicles])
    const stoppedVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "STOPPED"), [filteredVehicles])
    const offlineVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "OFFLINE"), [filteredVehicles])

    const movingCount = movingVehicles.length
    const idlingCount = idlingVehicles.length
    const stoppedCount = stoppedVehicles.length
    const offlineCount = offlineVehicles.length

    // Active Today Calculation (Scaped strictly to Today)
    const activeTodayCount = useMemo(() => {
        const activeIds = new Set()
        // 1. Currently moving or idling
        filteredVehicles.forEach(v => {
            if (v.normalizedStatus === "MOVING" || v.normalizedStatus === "IDLING") {
                activeIds.add(v.vehicle_id)
            }
        })
        // 2. Vehicles with distance today from todayMileageData
        const sourceMileage = todayMileageData?.all_units || (mileageTimeframe === "today" ? mileageData?.all_units : null)
        if (sourceMileage) {
            sourceMileage.forEach(u => {
                // Unknown (null) mileage is not evidence of activity
                if (isKnownNumber(u.mileage) && u.mileage > 0) {
                    const vidStr = String(u.vehicle_id)
                    if (filteredVehicleIdSet.has(vidStr)) {
                        activeIds.add(u.vehicle_id)
                    } else {
                        const matched = filteredVehicles.find(v => v.vehicle_name === u.vehicle_name)
                        if (matched) activeIds.add(matched.vehicle_id)
                    }
                }
            })
        }
        // 3. Vehicles from today's trips
        if (tripsData?.vehicles) {
            tripsData.vehicles.forEach(tv => {
                if (Number(tv.trip_count || 0) > 0 || Number(tv.gps_mileage_km || 0) > 0) {
                    const vidStr = String(tv.vehicle_id)
                    if (filteredVehicleIdSet.has(vidStr)) activeIds.add(tv.vehicle_id)
                }
            })
        }
        return activeIds.size
    }, [filteredVehicles, filteredVehicleIdSet, todayMileageData, mileageData, mileageTimeframe, tripsData])

    // Filtered Alerts Count (Accurate fleet-wide counts supporting secondary groups)
    const alertsCount = useMemo(() => {
        if (!alertsData) return 0
        if (selectedGroup === "ALL") return alertsData.total_alerts || alertsData.alerts?.length || 0
        if (alertsData.by_vehicle && filteredVehicles.length > 0) {
            return filteredVehicles.reduce((sum, v) => sum + (alertsData.by_vehicle[String(v.vehicle_id)] || 0), 0)
        }
        if (alertsData.by_group) {
            const grpKey = Object.keys(alertsData.by_group).find(k => k.trim().toLowerCase() === selectedGroup.trim().toLowerCase())
            if (grpKey) return alertsData.by_group[grpKey]
        }
        if (!alertsData.alerts) return 0
        return alertsData.alerts.filter(a => {
            const grp = (a.group || "").trim().toLowerCase()
            return grp === selectedGroup.trim().toLowerCase()
        }).length
    }, [alertsData, selectedGroup, filteredVehicles])

    // ─── 8. Today's Fleet Activity Metrics (Scaped to Today and filtered by Global Group) ───────
    const todayFleetActivity = useMemo(() => {
        // Distance strictly for today
        // Unknown (null) unit mileage is left out of the total and marked, never counted as 0 km
        let distanceKm = 0
        let distanceUnknownUnits = 0
        let distanceAllUnknown = false
        const sourceMileage = todayMileageData?.all_units || (mileageTimeframe === "today" ? mileageData?.all_units : null)
        if (sourceMileage) {
            const filteredUnits = selectedGroup === "ALL"
                ? sourceMileage
                : sourceMileage.filter(u => filteredVehicleIdSet.has(String(u.vehicle_id)))
            const mileage = sumKnown(filteredUnits, u => u.mileage)
            distanceKm = mileage.total
            distanceUnknownUnits = mileage.unknown
            distanceAllUnknown = mileage.allUnknown
        } else if (tripsData?.vehicles) {
            const filteredTripVehs = selectedGroup === "ALL"
                ? tripsData.vehicles
                : tripsData.vehicles.filter(tv => filteredVehicleIdSet.has(String(tv.vehicle_id)))
            const mileage = sumKnown(filteredTripVehs, tv => tv.gps_mileage_km)
            distanceKm = mileage.total
            distanceUnknownUnits = mileage.unknown
            distanceAllUnknown = mileage.allUnknown
        }

        // Trips count: only real Wialon trip counts. Unknown (null) for any vehicle -> "—", never an active-vehicle count
        let trips = null
        if (tripsData?.vehicles) {
            const filteredTripVehs = selectedGroup === "ALL"
                ? tripsData.vehicles
                : tripsData.vehicles.filter(tv => filteredVehicleIdSet.has(String(tv.vehicle_id)))
            const tripCountsKnown = filteredTripVehs.every(tv => typeof tv.trip_count === "number")
            trips = tripCountsKnown ? filteredTripVehs.reduce((sum, tv) => sum + tv.trip_count, 0) : null
        }

        // Driving time from Wialon trip durations, or Wialon distance ÷ Wialon average speed.
        // A vehicle with neither is unknown and left out (never estimated from an assumed speed).
        let drivingMinutes = 0
        let drivingKnown = false
        let drivingUnknownVehicles = 0

        if (tripsData?.vehicles) {
            tripsData.vehicles.forEach(tv => {
                if (selectedGroup === "ALL" || filteredVehicleIdSet.has(String(tv.vehicle_id))) {
                    if (Array.isArray(tv.trips) && tv.trips.length > 0) {
                        const tripMinutes = sumKnown(tv.trips, t => t.duration_minutes)
                        drivingMinutes += tripMinutes.total
                        if (tripMinutes.partial) drivingUnknownVehicles += 1
                        if (tripMinutes.known > 0) drivingKnown = true
                    } else if (tv.gps_mileage_km === 0) {
                        // No distance recorded: no driving time
                        drivingKnown = true
                    } else if (isKnownNumber(tv.gps_mileage_km) && isKnownNumber(tv.avg_speed_kmh) && Number(tv.avg_speed_kmh) > 0) {
                        drivingMinutes += Math.round((Number(tv.gps_mileage_km) / Number(tv.avg_speed_kmh)) * 60)
                        drivingKnown = true
                    } else {
                        drivingUnknownVehicles += 1
                    }
                }
            })
        }

        // Idle time from verified historical idle data for today (correct field names)
        // Unknown (null) vehicle idle is left out of the total and marked, never counted as 0
        let idleMinutes = 0
        let idleUnknownVehicles = 0
        let idleAllUnknown = false
        if (idleAnalyticsData?.by_vehicle) {
            const matchingVehicles = selectedGroup === "ALL"
                ? idleAnalyticsData.by_vehicle
                : idleAnalyticsData.by_vehicle.filter(v => filteredVehicleIdSet.has(String(v.vehicle_id)))
            const idle = sumKnown(matchingVehicles, idleSecondsOf)
            idleMinutes = Math.round(idle.total / 60)
            idleUnknownVehicles = idle.unknown
            idleAllUnknown = idle.allUnknown
        } else if (idleAnalyticsData?.summary?.total_idle_seconds !== undefined && selectedGroup === "ALL") {
            const summaryIdle = idleAnalyticsData.summary.total_idle_seconds
            idleAllUnknown = !isKnownNumber(summaryIdle)
            idleMinutes = idleAllUnknown ? 0 : Math.round(Number(summaryIdle) / 60)
        }

        const formatHoursMins = (totalMins) => {
            if (!totalMins || totalMins <= 0) return "0h 0m"
            const h = Math.floor(totalMins / 60)
            const m = Math.round(totalMins % 60)
            if (h === 0) return `${m}m`
            return `${h}h ${m}m`
        }

        return {
            distance: distanceAllUnknown ? "—" : `${Math.round(distanceKm).toLocaleString()} km`,
            distanceUnknownUnits,
            trips: trips === null ? "—" : trips.toLocaleString(),
            driving: drivingKnown ? formatHoursMins(drivingMinutes) : "—",
            drivingUnknownVehicles,
            idle: idleAllUnknown ? "—" : formatHoursMins(idleMinutes),
            idleUnknownVehicles,
        }
    }, [todayMileageData, mileageData, mileageTimeframe, tripsData, idleAnalyticsData, selectedGroup, filteredVehicleIdSet])

    // ─── 9. Idle Vehicles Requiring Attention (Active continuous idle >= 5 mins / 300s) ──────
    const attentionVehicles = useMemo(() => {
        if (!vehicles || vehicles.length === 0) return []

        return vehicles
            .filter(v => {
                const isIdling = v.status === "IDLING"
                const dur = Number(v.idle_duration || 0)
                const passesThreshold = dur >= 300
                const vidStr = String(v.vehicle_id || v.id)
                const passesGroup = selectedGroup === "ALL" || (v.group || "General Fleet") === selectedGroup || filteredVehicleIdSet.has(vidStr)
                return isIdling && passesThreshold && passesGroup
            })
            .map(v => {
                const vid = v.vehicle_id || v.id
                return {
                    vehicle_id: vid,
                    vehicle_name: v.vehicle_name || `Vehicle #${vid}`,
                    registration_plate: v.registration_plate || v.plate || null,
                    group: v.group || "General Fleet",
                    status: v.status,
                    idle_duration: Number(v.idle_duration || 0),
                    idle_start: v.idle_start || null,
                }
            })
            .sort((a, b) => b.idle_duration - a.idle_duration)
            .slice(0, 5)
    }, [vehicles, selectedGroup, filteredVehicleIdSet])

    // ─── 10. Top Vehicles by Mileage (Filtered by Group & Timeframe) ────────
    const mileageUnitsSource = useMemo(() => {
        if (mileageData?.all_units && Array.isArray(mileageData.all_units) && mileageData.all_units.length > 0) {
            return mileageData.all_units
        }
        if (mileageData?.top_units && Array.isArray(mileageData.top_units) && mileageData.top_units.length > 0) {
            return mileageData.top_units
        }
        return []
    }, [mileageData])

    const filteredMileageList = useMemo(() => {
        let units = [...mileageUnitsSource]
        if (selectedGroup !== "ALL") {
            units = units.filter(u => {
                const g = (u.group || "").trim().toLowerCase()
                const sel = selectedGroup.trim().toLowerCase()
                return g === sel || (Array.isArray(u.groups) && u.groups.some(x => (x || "").trim().toLowerCase() === sel))
            })
        }
        // Unknown (null) mileage sorts last and has no bar, instead of showing as 0 km
        units.sort((a, b) => compareKnownDesc(a.mileage, b.mileage))
        if (mileageLimit !== "ALL") {
            units = units.slice(0, Number(mileageLimit))
        }
        return units.map(u => ({
            vehicle_id: u.vehicle_id,
            vehicle: u.vehicle_name || `Vehicle #${u.vehicle_id}`,
            mileage: isKnownNumber(u.mileage) ? Math.round(Number(u.mileage)) : null,
            group: u.group || "General Fleet",
        }))
    }, [mileageUnitsSource, selectedGroup, mileageLimit])

    // ─── 11. Fleet by Group Summary Breakdown ───────────────────────────────
    const fleetByGroupData = useMemo(() => {
        const groupsToProcess = availableGroups.length > 0 ? availableGroups : ["General Fleet"]
        return groupsToProcess.map(grpName => {
            const grpVehicles = normalizedVehicles.filter(v => (v.group || "").trim().toLowerCase() === grpName.trim().toLowerCase())
            const count = grpVehicles.length
            const moving = grpVehicles.filter(v => v.normalizedStatus === "MOVING").length
            const idling = grpVehicles.filter(v => v.normalizedStatus === "IDLING").length
            const stopped = grpVehicles.filter(v => v.normalizedStatus === "STOPPED").length
            const offline = grpVehicles.filter(v => v.normalizedStatus === "OFFLINE").length
            return {
                name: grpName,
                count,
                moving,
                idling,
                stopped,
                offline,
                percent: normalizedVehicles.length > 0 ? Math.round((count / normalizedVehicles.length) * 100) : 0,
            }
        }).filter(item => item.count > 0).sort((a, b) => b.count - a.count)
    }, [availableGroups, normalizedVehicles])

    // ─── 12. Fleet Utilization by Category Data ─────────────────────────────
    const utilizationChartData = useMemo(() => {
        if (selectedGroup === "ALL") {
            const groupsToProcess = availableGroups.length > 0 ? availableGroups : ["General Fleet"]
            return groupsToProcess.map(grpName => {
                const grpVehs = normalizedVehicles.filter(v => (v.group || "").trim().toLowerCase() === grpName.trim().toLowerCase())
                const total = grpVehs.length
                const active = grpVehs.filter(v => v.normalizedStatus === "MOVING" || v.normalizedStatus === "IDLING").length
                const pct = total > 0 ? Math.round((active / total) * 100) : 0
                return {
                    name: grpName,
                    value: pct,
                    totalVehicles: total,
                    activeVehicles: active,
                }
            }).filter(item => item.totalVehicles > 0)
        } else {
            return filteredVehicles.map(v => {
                const isActive = v.normalizedStatus === "MOVING" || v.normalizedStatus === "IDLING"
                return {
                    name: v.vehicle_name,
                    value: isActive ? 100 : 0,
                    status: v.normalizedStatus,
                    vehicle_id: v.vehicle_id,
                }
            }).slice(0, 10)
        }
    }, [selectedGroup, availableGroups, normalizedVehicles, filteredVehicles])

    // ─── 13. Idle Duration by Category Data (Historical verified idle with single-counting rule) ───
    const idleDurationChartData = useMemo(() => {
        if (selectedGroup === "ALL") {
            const groupsToProcess = availableGroups.length > 0 ? availableGroups : ["General Fleet"]
            return groupsToProcess.map(grpName => {
                const groupKey = grpName.trim().toLowerCase()
                // Known idle is summed; vehicles with unknown idle are counted and flagged, never added as 0
                let idle = { total: 0, unknown: 0, allUnknown: false }
                if (idleAnalyticsData?.by_vehicle) {
                    // Single-counting rule: each vehicle is strictly categorized under its primary group in by_vehicle
                    const grpVehs = idleAnalyticsData.by_vehicle.filter(v =>
                        (v.group_name || "").trim().toLowerCase() === groupKey
                    )
                    idle = sumKnown(grpVehs, idleSecondsOf)
                } else if (idleAnalyticsData?.by_group) {
                    const grp = idleAnalyticsData.by_group.find(g => (g.group_name || "").trim().toLowerCase() === groupKey)
                    if (grp) {
                        idle = sumKnown([grp], g => (Object.prototype.hasOwnProperty.call(g, "total_idle_seconds")
                            ? g.total_idle_seconds
                            : (isKnownNumber(g.total_idle_hours) ? g.total_idle_hours * 3600 : null)))
                    }
                }
                return {
                    name: grpName,
                    value: idle.allUnknown ? null : Math.round(idle.total / 60),
                    unknownCount: idle.unknown,
                }
            })
        } else {
            if (idleAnalyticsData?.by_vehicle) {
                return idleAnalyticsData.by_vehicle
                    .filter(v => filteredVehicleIdSet.has(String(v.vehicle_id)))
                    .map(v => {
                        const secs = idleSecondsOf(v)
                        const known = isKnownNumber(secs)
                        return {
                            name: v.vehicle_name,
                            value: known ? Math.round(Number(secs) / 60) : null,
                            status: !known ? "UNKNOWN" : Number(secs) > 0 ? "IDLED TODAY" : "NO IDLE",
                            unknownCount: known ? 0 : 1,
                            vehicle_id: v.vehicle_id,
                        }
                    })
                    .sort((a, b) => compareKnownDesc(a.value, b.value))
                    .slice(0, 10)
            }
            return []
        }
    }, [selectedGroup, availableGroups, idleAnalyticsData, filteredVehicleIdSet])

    const formatIdleTooltip = (val, _name, item) => {
        const unknownCount = item?.payload?.unknownCount || 0
        if (!isKnownNumber(val)) return ["Unknown", "Idle Duration"]
        return [unknownCount > 0 ? `${val} min (partial: ${unknownCount} vehicle(s) unknown)` : `${val} min`, "Idle Duration"]
    }

    // ─── Selected Vehicle Object for Drawer ─────────────────────────────────
    const selectedVehicle = useMemo(() => {
        if (!selectedVehicleId) return null
        return vehicles.find(v => v.vehicle_id === selectedVehicleId || v.id === selectedVehicleId) || null
    }, [vehicles, selectedVehicleId])

    const pct = (count) => (totalVehiclesCount > 0 ? Math.round((count / totalVehiclesCount) * 100) : 0)

    const statusSegments = [
        { key: "MOVING", label: "Moving", count: movingCount, dot: "bg-status-moving", note: "in motion" },
        { key: "IDLING", label: "Idling", count: idlingCount, dot: "bg-status-idling", note: "engine on" },
        { key: "STOPPED", label: "Stopped", count: stoppedCount, dot: "bg-status-stopped", note: "parked" },
        { key: "OFFLINE", label: "Offline", count: offlineCount, dot: "bg-status-offline", note: "no signal" },
    ]

    const activityRows = [
        {
            label: "Distance",
            value: todayFleetActivity.distance,
            partial: todayFleetActivity.distanceUnknownUnits > 0 ? `${todayFleetActivity.distanceUnknownUnits} unit(s) unknown` : null,
            partialTitle: "Wialon could not read these units' mileage; they are left out of the total",
        },
        { label: "Trips", value: todayFleetActivity.trips, partial: null },
        {
            label: "Driving time",
            value: todayFleetActivity.driving,
            partial: todayFleetActivity.drivingUnknownVehicles > 0 ? `${todayFleetActivity.drivingUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "Wialon reported no usable distance, speed or trip duration for these vehicles; they are left out of the total",
        },
        {
            label: "Idle time",
            value: todayFleetActivity.idle,
            partial: todayFleetActivity.idleUnknownVehicles > 0 ? `${todayFleetActivity.idleUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "These vehicles' idle time could not be attributed; they are left out of the total",
        },
    ]

    // One row per vehicle keeps long "Show All" rankings readable instead of squeezing bars
    const mileageChartHeight = Math.max(220, filteredMileageList.length * 28 + 20)
    const emptyChart = (text) => (
        <div className="flex h-full items-center justify-center text-xs text-slate-400">{text}</div>
    )

    return (
        <AppShell
            overlay={selectedVehicle && (
                <VehicleDetailDrawer
                    vehicle={selectedVehicle}
                    onClose={() => setSelectedVehicleId(null)}
                />
            )}
        >
            <PageHeader
                title="Fleet Overview"
                subtitle="Live fleet status and today's activity"
                lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
            >
                <FilterSelect
                    icon={Layers}
                    label="Group"
                    value={selectedGroup}
                    onChange={setSelectedGroup}
                    ariaLabel="Filter fleet by group"
                    active={selectedGroup !== "ALL"}
                    selectClassName="max-w-[160px]"
                >
                    <option value="ALL">All Groups ({availableGroups.length})</option>
                    {availableGroups.map(grp => (
                        <option key={grp} value={grp}>{grp}</option>
                    ))}
                </FilterSelect>
            </PageHeader>

            {loading ? (
                <LoadingState label="Loading live fleet telemetry…" />
            ) : (
                <div className="space-y-4">
                    {error && <ErrorBanner message={`${error}. Retrying automatically.`} />}

                    {/* ─── FLEET NOW + TODAY ─────────────────────────────────── */}
                    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
                        <section aria-label="Fleet status" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
                            <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4 px-4 pt-4">
                                <div>
                                    <p className="text-xs font-medium text-slate-500">
                                        {selectedGroup === "ALL" ? "Fleet now" : selectedGroup}
                                    </p>
                                    <p className="mt-1 flex items-baseline gap-2">
                                        <Link
                                            to="/tracking"
                                            title="View all units in Live Tracking"
                                            className="text-[34px] font-semibold leading-none tracking-tight text-slate-900 tabular-nums hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 rounded-sm"
                                        >
                                            {totalVehiclesCount}
                                        </Link>
                                        <span className="text-[13px] text-slate-500">vehicles</span>
                                    </p>
                                </div>
                                <dl className="flex gap-8">
                                    <div>
                                        <dt className="text-xs text-slate-500">Active today</dt>
                                        <dd className="mt-0.5 flex items-baseline gap-1.5">
                                            <span className="text-[17px] font-semibold tabular-nums text-slate-900">{activeTodayCount}</span>
                                            <span className="text-xs tabular-nums text-slate-500">{pct(activeTodayCount)}% utilized</span>
                                        </dd>
                                    </div>
                                    <div>
                                        <dt className="text-xs text-slate-500">Alerts</dt>
                                        <dd className="mt-0.5">
                                            <Link
                                                to="/alerts"
                                                title="View fleet alerts"
                                                className={`group inline-flex items-baseline gap-1.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${alertsCount > 0 ? "text-rose-700" : "text-slate-900"}`}
                                            >
                                                <span className="text-[17px] font-semibold tabular-nums">{alertsCount}</span>
                                                <span className="inline-flex items-center gap-0.5 text-xs text-slate-500 group-hover:text-brand">
                                                    {alertsCount > 0 ? "review" : "none open"}
                                                    <ArrowUpRight className="h-3 w-3" />
                                                </span>
                                            </Link>
                                        </dd>
                                    </div>
                                </dl>
                            </div>

                            <div className="px-4 pt-4">
                                <div className="flex h-2 gap-0.5 overflow-hidden rounded-sm bg-slate-100" aria-hidden="true">
                                    {totalVehiclesCount > 0 && statusSegments.filter(seg => seg.count > 0).map(seg => (
                                        <div
                                            key={seg.key}
                                            className={`${seg.dot} transition-[width] duration-300 motion-reduce:transition-none`}
                                            style={{ width: `${(seg.count / totalVehiclesCount) * 100}%` }}
                                            title={`${seg.label}: ${seg.count}`}
                                        />
                                    ))}
                                </div>
                            </div>

                            <div className="mt-4 grid grid-cols-2 gap-px border-t border-slate-200 bg-slate-200 sm:grid-cols-4">
                                {statusSegments.map(seg => (
                                    <Link
                                        key={seg.key}
                                        to={`/tracking?status=${seg.key}`}
                                        title={`View ${seg.label.toLowerCase()} vehicles`}
                                        className="group bg-white px-4 py-3 transition-colors hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
                                    >
                                        <span className="flex items-center gap-1.5 text-xs font-medium text-slate-500">
                                            <span className={`h-2 w-2 rounded-[2px] ${seg.dot}`} aria-hidden="true" />
                                            {seg.label}
                                        </span>
                                        <span className="mt-1 flex items-baseline gap-1.5">
                                            <span className={`text-xl font-semibold tabular-nums ${seg.key === "OFFLINE" && seg.count > 0 ? "text-rose-700" : "text-slate-900"}`}>
                                                {seg.count}
                                            </span>
                                            <span className="text-xs tabular-nums text-slate-500">{pct(seg.count)}% {seg.note}</span>
                                        </span>
                                    </Link>
                                ))}
                            </div>
                        </section>

                        <SectionCard
                            title="Today"
                            actions={<span className="text-xs text-slate-500">UAE 00:00 → now</span>}
                        >
                            <dl className="-my-1 divide-y divide-slate-100">
                                {activityRows.map(row => (
                                    <div key={row.label} className="flex items-baseline justify-between gap-3 py-2">
                                        <dt className="text-[13px] text-slate-600">{row.label}</dt>
                                        <dd className="min-w-0 text-right">
                                            <span className="text-[15px] font-semibold tabular-nums text-slate-900">{row.value}</span>
                                            {row.partial && (
                                                <span className="block truncate text-[11px] text-amber-700" title={row.partialTitle}>
                                                    Partial: {row.partial}
                                                </span>
                                            )}
                                        </dd>
                                    </div>
                                ))}
                            </dl>
                        </SectionCard>
                    </div>

                    {/* ─── IDLE VEHICLES & TOP MILEAGE ───────────────────────── */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        <SectionCard
                            flush
                            title="Idling over 5 minutes"
                            subtitle="Engine on, stationary, longest first"
                            actions={
                                <Link
                                    to="/idle-analytics"
                                    className="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline underline-offset-2"
                                >
                                    Idle analytics
                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                </Link>
                            }
                        >
                            {attentionVehicles.length === 0 ? (
                                <p className="flex items-center gap-2 px-4 py-6 text-xs text-slate-500">
                                    <CheckCircle2 className="h-4 w-4 shrink-0 text-status-moving" />
                                    No vehicle has been idling for more than 5 minutes.
                                </p>
                            ) : (
                                <ul className="divide-y divide-slate-100">
                                    {attentionVehicles.map(vehicle => (
                                        <li key={vehicle.vehicle_id}>
                                            <button
                                                type="button"
                                                onClick={() => setSelectedVehicleId(vehicle.vehicle_id)}
                                                className="flex w-full items-center justify-between gap-4 px-4 py-2.5 text-left transition-colors hover:bg-slate-50 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40"
                                            >
                                                <span className="min-w-0">
                                                    <span className="block truncate font-mono text-[13px] font-semibold text-slate-900">
                                                        {vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name}
                                                    </span>
                                                    <span className="block truncate text-xs text-slate-500">{vehicle.group || "General Fleet"}</span>
                                                </span>
                                                <span className="shrink-0 text-right">
                                                    <LiveIdleTimer
                                                        startTime={vehicle.idle_start}
                                                        baseDuration={vehicle.idle_duration}
                                                        status={vehicle.status}
                                                        className="block text-[13px] text-amber-800 tabular-nums"
                                                    />
                                                    <span className="block text-[11px] text-slate-500">
                                                        {vehicle.idle_start ? `since ${formatUAETime(vehicle.idle_start)}` : "Start time unavailable"}
                                                    </span>
                                                </span>
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </SectionCard>

                        <SectionCard
                            title="Top vehicles by mileage"
                            actions={
                                <>
                                    {mileageLoading && (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-400 motion-reduce:animate-none" aria-label="Updating" />
                                    )}
                                    <FilterSelect
                                        value={mileageTimeframe}
                                        onChange={setMileageTimeframe}
                                        ariaLabel="Select mileage timeframe"
                                    >
                                        {TIMEFRAME_OPTIONS.map(tf => (
                                            <option key={tf.id} value={tf.id}>{tf.label}</option>
                                        ))}
                                    </FilterSelect>
                                    <FilterSelect
                                        value={mileageLimit}
                                        onChange={setMileageLimit}
                                        ariaLabel="Select mileage limit"
                                    >
                                        <option value="5">Top 5</option>
                                        <option value="10">Top 10</option>
                                        <option value="15">Top 15</option>
                                        <option value="ALL">Show All</option>
                                    </FilterSelect>
                                </>
                            }
                        >
                            {filteredMileageList.length === 0 ? (
                                <div className="h-[220px]">{emptyChart("No mileage data for this selection.")}</div>
                            ) : (
                                <div className="max-h-[360px] overflow-y-auto">
                                    <div style={{ height: `${mileageChartHeight}px` }}>
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart
                                                data={filteredMileageList}
                                                layout="vertical"
                                                margin={{ top: 4, right: 24, left: 4, bottom: 4 }}
                                            >
                                                <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
                                                <XAxis type="number" tickLine={false} axisLine={false} tick={TICK} />
                                                <YAxis
                                                    type="category"
                                                    dataKey="vehicle"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    width={96}
                                                    interval={0}
                                                    tick={CATEGORY_TICK}
                                                />
                                                <Tooltip
                                                    cursor={TOOLTIP_CURSOR}
                                                    formatter={(val) => [isKnownNumber(val) ? `${Number(val).toLocaleString()} km` : "Unknown (unreadable)", "Mileage"]}
                                                    contentStyle={TOOLTIP_STYLE}
                                                />
                                                <Bar
                                                    dataKey="mileage"
                                                    fill={SERIES_COLOR}
                                                    radius={[0, 3, 3, 0]}
                                                    maxBarSize={14}
                                                    onClick={(entry) => {
                                                        if (entry?.vehicle_id) setSelectedVehicleId(entry.vehicle_id)
                                                    }}
                                                    cursor="pointer"
                                                />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            )}
                        </SectionCard>
                    </div>

                    {/* ─── FLEET BY GROUP & UTILIZATION ──────────────────────── */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        <SectionCard
                            flush
                            title="Fleet by group"
                            actions={
                                <span className="max-w-[180px] truncate text-xs text-slate-500">
                                    {selectedGroup === "ALL" ? `${fleetByGroupData.length} groups` : selectedGroup}
                                </span>
                            }
                        >
                            {selectedGroup === "ALL" ? (
                                fleetByGroupData.length === 0 ? (
                                    <div className="h-[160px]">{emptyChart("No group data available.")}</div>
                                ) : (
                                    <div className="max-h-[300px] overflow-y-auto">
                                        <table className="w-full text-xs">
                                            <thead className="sticky top-0 bg-white">
                                                <tr className="border-b border-slate-200 text-left text-[11px] font-medium text-slate-500">
                                                    <th scope="col" className="px-4 py-2 font-medium">Group</th>
                                                    <th scope="col" className="px-2 py-2 text-right font-medium">Units</th>
                                                    <th scope="col" className="hidden px-2 py-2 font-medium sm:table-cell">Status mix</th>
                                                    <th scope="col" className="px-4 py-2 text-right font-medium">Share</th>
                                                </tr>
                                            </thead>
                                            <tbody className="divide-y divide-slate-100">
                                                {fleetByGroupData.map(grp => (
                                                    <tr key={grp.name}>
                                                        <td className="max-w-[180px] truncate px-4 py-2 font-medium text-slate-800">{grp.name}</td>
                                                        <td className="px-2 py-2 text-right font-semibold tabular-nums text-slate-900">{grp.count}</td>
                                                        <td className="hidden px-2 py-2 sm:table-cell">
                                                            <div
                                                                className="flex h-1.5 w-32 gap-px overflow-hidden rounded-sm bg-slate-100"
                                                                title={`Moving ${grp.moving} · Idling ${grp.idling} · Stopped ${grp.stopped} · Offline ${grp.offline}`}
                                                            >
                                                                {[["moving", "bg-status-moving"], ["idling", "bg-status-idling"], ["stopped", "bg-status-stopped"], ["offline", "bg-status-offline"]]
                                                                    .filter(([k]) => grp[k] > 0)
                                                                    .map(([k, cls]) => (
                                                                        <span key={k} className={cls} style={{ width: `${(grp[k] / grp.count) * 100}%` }} />
                                                                    ))}
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-2 text-right tabular-nums text-slate-500">{grp.percent}%</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )
                            ) : (
                                <dl className="divide-y divide-slate-100">
                                    {statusSegments.map(seg => (
                                        <div key={seg.key} className="flex items-center justify-between px-4 py-2.5">
                                            <dt className="flex items-center gap-1.5 text-[13px] text-slate-600">
                                                <span className={`h-2 w-2 rounded-[2px] ${seg.dot}`} aria-hidden="true" />
                                                {seg.label}
                                            </dt>
                                            <dd className="text-[15px] font-semibold tabular-nums text-slate-900">{seg.count}</dd>
                                        </div>
                                    ))}
                                </dl>
                            )}
                        </SectionCard>

                        <SectionCard
                            title="Utilization by group"
                            subtitle={selectedGroup === "ALL" ? "Share of units moving or idling" : `Units moving or idling in ${selectedGroup}`}
                            actions={
                                <FilterSelect
                                    value={utilizationChartType}
                                    onChange={setUtilizationChartType}
                                    ariaLabel="Select utilization chart type"
                                >
                                    <option value="bar">Bar</option>
                                    <option value="horizontal">Horizontal Bar</option>
                                    <option value="donut">Donut</option>
                                </FilterSelect>
                            }
                        >
                            <div className="h-[220px]">
                                {utilizationChartData.length === 0 ? (
                                    emptyChart("No utilization data available.")
                                ) : utilizationChartType === "bar" ? (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={utilizationChartData} margin={{ top: 8, right: 8, left: -20, bottom: 16 }}>
                                            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
                                            <XAxis dataKey="name" tickLine={false} axisLine={{ stroke: AXIS_LINE }} tick={CATEGORY_TICK} />
                                            <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={TICK} />
                                            <Tooltip cursor={TOOLTIP_CURSOR} formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={TOOLTIP_STYLE} />
                                            <Bar dataKey="value" fill={SERIES_COLOR} radius={[3, 3, 0, 0]} maxBarSize={36} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : utilizationChartType === "horizontal" ? (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={utilizationChartData} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 4 }}>
                                            <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
                                            <XAxis type="number" domain={[0, 100]} tickLine={false} axisLine={false} tick={TICK} />
                                            <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={90} tick={CATEGORY_TICK} />
                                            <Tooltip cursor={TOOLTIP_CURSOR} formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={TOOLTIP_STYLE} />
                                            <Bar dataKey="value" fill={SERIES_COLOR} radius={[0, 3, 3, 0]} maxBarSize={16} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <PieChart>
                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={TOOLTIP_STYLE} />
                                            <Pie
                                                data={utilizationChartData}
                                                dataKey="value"
                                                nameKey="name"
                                                cx="50%"
                                                cy="45%"
                                                innerRadius={46}
                                                outerRadius={72}
                                                paddingAngle={1}
                                                stroke="#fff"
                                                strokeWidth={2}
                                            >
                                                {utilizationChartData.map((entry, index) => (
                                                    <Cell key={`util-cell-${index}`} fill={categoricalColor(index)} />
                                                ))}
                                            </Pie>
                                            <Legend iconType="square" iconSize={8} wrapperStyle={{ fontSize: 11, color: "#475569" }} />
                                        </PieChart>
                                    </ResponsiveContainer>
                                )}
                            </div>
                        </SectionCard>
                    </div>

                    {/* ─── IDLE DURATION BY GROUP ────────────────────────────── */}
                    <SectionCard
                        title="Idle time today"
                        subtitle={selectedGroup === "ALL" ? "Idle minutes by fleet group" : `Idle minutes per vehicle in ${selectedGroup}`}
                        actions={
                            <FilterSelect
                                value={idleChartType}
                                onChange={setIdleChartType}
                                ariaLabel="Select idle duration chart type"
                            >
                                <option value="bar">Bar</option>
                                <option value="horizontal">Horizontal Bar</option>
                            </FilterSelect>
                        }
                    >
                        <div className="h-[240px]">
                            {idleDurationChartData.length === 0 ? (
                                emptyChart("No idle time recorded for this selection.")
                            ) : idleChartType === "horizontal" ? (
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={idleDurationChartData} layout="vertical" margin={{ top: 4, right: 20, left: 8, bottom: 4 }}>
                                        <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
                                        <XAxis type="number" tickLine={false} axisLine={false} tick={TICK} />
                                        <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={100} tick={CATEGORY_TICK} />
                                        <Tooltip cursor={TOOLTIP_CURSOR} formatter={formatIdleTooltip} contentStyle={TOOLTIP_STYLE} />
                                        <Bar dataKey="value" fill={SERIES_COLOR} radius={[0, 3, 3, 0]} maxBarSize={16} />
                                    </BarChart>
                                </ResponsiveContainer>
                            ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={idleDurationChartData} margin={{ top: 8, right: 8, left: -20, bottom: 16 }}>
                                        <CartesianGrid vertical={false} stroke={GRID_STROKE} />
                                        <XAxis dataKey="name" tickLine={false} axisLine={{ stroke: AXIS_LINE }} tick={CATEGORY_TICK} />
                                        <YAxis tickLine={false} axisLine={false} tick={TICK} />
                                        <Tooltip cursor={TOOLTIP_CURSOR} formatter={formatIdleTooltip} contentStyle={TOOLTIP_STYLE} />
                                        <Bar dataKey="value" fill={SERIES_COLOR} radius={[3, 3, 0, 0]} maxBarSize={36} />
                                    </BarChart>
                                </ResponsiveContainer>
                            )}
                        </div>
                    </SectionCard>
                </div>
            )}
        </AppShell>
    )
}

export default Dashboard
