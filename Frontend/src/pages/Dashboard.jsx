import { useEffect, useMemo, useState, useRef } from "react"
import { Link } from "react-router-dom"
import {
    Activity,
    Layers,
    Loader2,
    Route,
    ArrowUpRight,
    Filter,
    Clock,
    AlertTriangle,
    CheckCircle2,
    BarChart3,
    TrendingUp,
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

import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { formatUAETime } from "../utils/datetime"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import KpiCard from "../components/Common/KpiCard"
import LiveIdleTimer from "../components/Common/LiveIdleTimer"
import { compareKnownDesc, idleSecondsOf, isKnownNumber, sumKnown } from "../utils/knownTotals"

// Curated enterprise palette starting with ATS brand blue
const CHART_COLORS = [
    "#33758F",
    "#0284c7",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#db2777",
    "#475569",
    "#ea580c",
]

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

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-white text-slate-900 font-sans antialiased">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-8 sm:py-8">
                        {/* ─── 1. PAGE HEADER ────────────────────────────────────────── */}
                        <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-slate-200 pb-4">
                            <div>
                                <h1 className="text-xl sm:text-2xl font-black tracking-tight text-slate-900 uppercase">
                                    Fleet Overview
                                </h1>
                                <p className="text-xs text-slate-500 font-medium mt-0.5">
                                    Real-time fleet operational command and asset telemetry
                                </p>
                            </div>

                            <div className="flex flex-wrap items-center gap-2.5">
                                {/* Global Vehicle Group Filter */}
                                <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 shadow-2xs">
                                    <Layers className="h-3.5 w-3.5 text-[#33758F]" />
                                    <span className="text-[11px] font-bold text-slate-400 uppercase">Group:</span>
                                    <select
                                        value={selectedGroup}
                                        onChange={(e) => setSelectedGroup(e.target.value)}
                                        className="bg-transparent text-xs font-bold text-slate-800 outline-none cursor-pointer pr-1"
                                        aria-label="Filter fleet by group"
                                    >
                                        <option value="ALL">All Groups ({availableGroups.length})</option>
                                        {availableGroups.map(grp => (
                                            <option key={grp} value={grp}>{grp}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* Last Updated Indicator with Live Pulse */}
                                {lastUpdated && (
                                    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-2xs">
                                        <span className="relative flex h-2 w-2">
                                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                                        </span>
                                        <span className="text-[11px] text-slate-500 font-semibold">
                                            {formatUAETime(lastUpdated)}
                                        </span>
                                    </div>
                                )}
                            </div>
                        </header>

                        {/* Loading State */}
                        {loading && (
                            <div className="rounded-xl border border-slate-200 bg-white p-12 text-center shadow-xs">
                                <Loader2 className="mx-auto h-7 w-7 animate-spin text-[#33758F]" />
                                <p className="mt-3 text-xs font-bold text-slate-800 tracking-wide uppercase">
                                    Synchronizing live fleet telemetry…
                                </p>
                            </div>
                        )}

                        {/* Error State */}
                        {error && !loading && (
                            <div className="mb-6 rounded-xl border border-red-200 bg-red-50 p-4 text-xs font-semibold text-red-700">
                                {error}
                            </div>
                        )}

                        {!loading && (
                            <div className="space-y-6">
                                {/* ─── 2. KPI CARDS SECTION (ENTERPRISE WHITE CARDS) ───── */}
                                <section aria-label="Key Performance Indicators">
                                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7 sm:gap-4">
                                        {/* 1. TOTAL VEHICLES */}
                                        <KpiCard
                                            label="Total Vehicles"
                                            value={totalVehiclesCount}
                                            sub={selectedGroup === "ALL" ? "All registered units" : "Filtered group units"}
                                            to="/tracking"
                                            title="View all units in Live Tracking"
                                        />

                                        {/* 2. MOVING */}
                                        <KpiCard
                                            label="Moving"
                                            value={movingCount}
                                            valueColor="text-emerald-600"
                                            sub={`${totalVehiclesCount > 0 ? Math.round((movingCount / totalVehiclesCount) * 100) : 0}% in motion`}
                                            to="/tracking?status=MOVING"
                                            title="View moving vehicles"
                                        />

                                        {/* 3. IDLING */}
                                        <KpiCard
                                            label="Idling"
                                            value={idlingCount}
                                            valueColor="text-amber-600"
                                            sub={`${totalVehiclesCount > 0 ? Math.round((idlingCount / totalVehiclesCount) * 100) : 0}% engine on`}
                                            to="/tracking?status=IDLING"
                                            title="View idling vehicles"
                                        />

                                        {/* 4. STOPPED */}
                                        <KpiCard
                                            label="Stopped"
                                            value={stoppedCount}
                                            valueColor="text-slate-700"
                                            sub={`${totalVehiclesCount > 0 ? Math.round((stoppedCount / totalVehiclesCount) * 100) : 0}% parked`}
                                            to="/tracking?status=STOPPED"
                                            title="View stopped vehicles"
                                        />

                                        {/* 5. OFFLINE */}
                                        <KpiCard
                                            label="Offline"
                                            value={offlineCount}
                                            valueColor={offlineCount > 0 ? "text-rose-600" : "text-slate-700"}
                                            sub={`${totalVehiclesCount > 0 ? Math.round((offlineCount / totalVehiclesCount) * 100) : 0}% no signal`}
                                            to="/tracking?status=OFFLINE"
                                            title="View offline vehicles"
                                        />

                                        {/* 6. ACTIVE TODAY */}
                                        <KpiCard
                                            label="Active Today"
                                            value={activeTodayCount}
                                            valueColor="text-[#33758F]"
                                            sub={`${totalVehiclesCount > 0 ? Math.round((activeTodayCount / totalVehiclesCount) * 100) : 0}% utilized`}
                                            to="/tracking"
                                            title="View active vehicles today"
                                        />

                                        {/* 7. ALERTS */}
                                        <KpiCard
                                            label="Alerts"
                                            value={alertsCount}
                                            valueColor={alertsCount > 0 ? "text-rose-600" : "text-slate-700"}
                                            sub={alertsCount > 0 ? "Attention required" : "No active issues"}
                                            to="/alerts"
                                            badge={alertsCount > 0 ? "Action" : undefined}
                                            badgeCls="bg-rose-50 text-rose-700 border-rose-200"
                                            title="View fleet alerts"
                                        />
                                    </div>
                                </section>

                                {/* ─── 3. FLEET STATUS / DISTRIBUTION & TODAY'S ACTIVITY ────── */}
                                <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                                    {/* FLEET STATUS & DISTRIBUTION */}
                                    <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between">
                                        <div>
                                            <div className="flex items-center justify-between mb-4">
                                                <div>
                                                    <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                        <Activity className="h-4 w-4 text-[#33758F]" />
                                                        Fleet Status & Live Distribution
                                                    </h2>
                                                    <p className="text-[11px] text-slate-400 font-medium">
                                                        Real-time operational distribution across all monitored units
                                                    </p>
                                                </div>
                                                <span className="text-xs font-bold text-slate-600 bg-slate-100 px-2 py-0.5 rounded-md">
                                                    {totalVehiclesCount} Units
                                                </span>
                                            </div>

                                            {/* Segmented Distribution Progress Bar */}
                                            <div className="flex h-3 overflow-hidden rounded-full bg-slate-100 border border-slate-200/60">
                                                {totalVehiclesCount > 0 ? (
                                                    <>
                                                        <div
                                                            className="bg-emerald-500 transition-all"
                                                            style={{ width: `${(movingCount / totalVehiclesCount) * 100}%` }}
                                                            title={`Moving: ${movingCount}`}
                                                        />
                                                        <div
                                                            className="bg-amber-500 transition-all"
                                                            style={{ width: `${(idlingCount / totalVehiclesCount) * 100}%` }}
                                                            title={`Idling: ${idlingCount}`}
                                                        />
                                                        <div
                                                            className="bg-slate-400 transition-all"
                                                            style={{ width: `${(stoppedCount / totalVehiclesCount) * 100}%` }}
                                                            title={`Stopped: ${stoppedCount}`}
                                                        />
                                                        <div
                                                            className="bg-rose-500 transition-all"
                                                            style={{ width: `${(offlineCount / totalVehiclesCount) * 100}%` }}
                                                            title={`Offline: ${offlineCount}`}
                                                        />
                                                    </>
                                                ) : (
                                                    <div className="w-full bg-slate-200" />
                                                )}
                                            </div>

                                            {/* Status Count Boxes */}
                                            <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                                                <div className="rounded-lg border border-emerald-100 bg-emerald-50/40 p-3 text-center">
                                                    <span className="text-[10px] font-bold text-emerald-800 uppercase tracking-wider block">Moving</span>
                                                    <p className="mt-0.5 text-xl font-black text-emerald-700">{movingCount}</p>
                                                    <span className="text-[10px] font-semibold text-emerald-600">
                                                        {totalVehiclesCount > 0 ? Math.round((movingCount / totalVehiclesCount) * 100) : 0}%
                                                    </span>
                                                </div>

                                                <div className="rounded-lg border border-amber-100 bg-amber-50/40 p-3 text-center">
                                                    <span className="text-[10px] font-bold text-amber-800 uppercase tracking-wider block">Idling</span>
                                                    <p className="mt-0.5 text-xl font-black text-amber-600">{idlingCount}</p>
                                                    <span className="text-[10px] font-semibold text-amber-600">
                                                        {totalVehiclesCount > 0 ? Math.round((idlingCount / totalVehiclesCount) * 100) : 0}%
                                                    </span>
                                                </div>

                                                <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-center">
                                                    <span className="text-[10px] font-bold text-slate-700 uppercase tracking-wider block">Stopped</span>
                                                    <p className="mt-0.5 text-xl font-black text-slate-700">{stoppedCount}</p>
                                                    <span className="text-[10px] font-semibold text-slate-500">
                                                        {totalVehiclesCount > 0 ? Math.round((stoppedCount / totalVehiclesCount) * 100) : 0}%
                                                    </span>
                                                </div>

                                                <div className="rounded-lg border border-rose-100 bg-rose-50/40 p-3 text-center">
                                                    <span className="text-[10px] font-bold text-rose-800 uppercase tracking-wider block">Offline</span>
                                                    <p className="mt-0.5 text-xl font-black text-rose-700">{offlineCount}</p>
                                                    <span className="text-[10px] font-semibold text-rose-600">
                                                        {totalVehiclesCount > 0 ? Math.round((offlineCount / totalVehiclesCount) * 100) : 0}%
                                                    </span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    {/* TODAY'S FLEET ACTIVITY */}
                                    <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between">
                                        <div>
                                            <div className="flex items-center justify-between mb-4">
                                                <div>
                                                    <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                        <Route className="h-4 w-4 text-[#33758F]" />
                                                        Today's Fleet Activity
                                                    </h2>
                                                    <p className="text-[11px] text-slate-400 font-medium">
                                                        Operational performance metrics recorded today
                                                    </p>
                                                </div>
                                                <span className="text-[11px] font-bold text-slate-500 bg-slate-100 px-2 py-0.5 rounded-md">
                                                    UAE (00:00 → Now)
                                                </span>
                                            </div>

                                            {/* 4 Activity Metric Cards */}
                                            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 text-center">
                                                <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-2xs">
                                                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider block">Distance</span>
                                                    <p className="mt-0.5 text-lg sm:text-xl font-black text-slate-900">
                                                        {todayFleetActivity.distance}
                                                    </p>
                                                    {todayFleetActivity.distanceUnknownUnits > 0 ? (
                                                        <span className="text-[10px] font-medium text-amber-700" title="Wialon could not read these units' mileage; they are left out of the total">
                                                            Partial: {todayFleetActivity.distanceUnknownUnits} unit(s) unknown
                                                        </span>
                                                    ) : (
                                                        <span className="text-[10px] font-medium text-slate-400">Total covered</span>
                                                    )}
                                                </div>

                                                <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-2xs">
                                                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider block">Trips</span>
                                                    <p className="mt-0.5 text-lg sm:text-xl font-black text-[#33758F]">
                                                        {todayFleetActivity.trips}
                                                    </p>
                                                    <span className="text-[10px] font-medium text-slate-400">Recorded trips</span>
                                                </div>

                                                <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-2xs">
                                                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider block">Driving</span>
                                                    <p className="mt-0.5 text-lg sm:text-xl font-black text-emerald-700">
                                                        {todayFleetActivity.driving}
                                                    </p>
                                                    {todayFleetActivity.drivingUnknownVehicles > 0 ? (
                                                        <span className="text-[10px] font-medium text-amber-700" title="Wialon reported no usable distance, speed or trip duration for these vehicles; they are left out of the total">
                                                            Partial: {todayFleetActivity.drivingUnknownVehicles} vehicle(s) unknown
                                                        </span>
                                                    ) : (
                                                        <span className="text-[10px] font-medium text-slate-400">Active motion</span>
                                                    )}
                                                </div>

                                                <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-2xs">
                                                    <span className="text-[10px] font-bold text-slate-500 uppercase tracking-wider block">Idle</span>
                                                    <p className="mt-0.5 text-lg sm:text-xl font-black text-amber-600">
                                                        {todayFleetActivity.idle}
                                                    </p>
                                                    {todayFleetActivity.idleUnknownVehicles > 0 ? (
                                                        <span className="text-[10px] font-medium text-amber-700" title="These vehicles' idle time could not be attributed; they are left out of the total">
                                                            Partial: {todayFleetActivity.idleUnknownVehicles} vehicle(s) unknown
                                                        </span>
                                                    ) : (
                                                        <span className="text-[10px] font-medium text-slate-400">Engine running</span>
                                                    )}
                                                </div>
                                            </div>
                                        </div>

                                        <div className="mt-4 pt-3 border-t border-slate-100 flex items-center justify-end">
                                            <Link
                                                to="/trips"
                                                className="inline-flex items-center gap-1 text-xs font-bold text-[#33758F] hover:underline"
                                            >
                                                View Trips & Routes
                                                <ArrowUpRight className="h-3.5 w-3.5" />
                                            </Link>
                                        </div>
                                    </div>
                                </section>

                                {/* ─── 4. IDLE VEHICLES & TOP MILEAGE RANKING ───────────────── */}
                                <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                                    {/* IDLE VEHICLES REQUIRING ATTENTION */}
                                    <div className="rounded-xl border border-slate-200/80 bg-white p-5 shadow-xs flex flex-col justify-between">
                                        <div>
                                            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between mb-4">
                                                <div>
                                                    <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                        <AlertTriangle className="h-4 w-4 text-amber-600" />
                                                        Idle Vehicles Requiring Attention
                                                    </h2>
                                                    <p className="text-[11px] text-slate-500 font-medium">
                                                vehicles with stable telemetry idling continuously for more than 5 minutes
                                                    </p>
                                                </div>

                                                <Link
                                                    to="/idle-analytics"
                                                    className="inline-flex items-center gap-1 text-xs font-bold text-amber-700 hover:underline shrink-0"
                                                >
                                                    View all →
                                                </Link>
                                            </div>

                                            {attentionVehicles.length === 0 ? (
                                                <div className="flex flex-col items-center justify-center rounded-lg bg-emerald-50/40 py-8 text-center border border-emerald-100">
                                                    <CheckCircle2 className="h-6 w-6 text-emerald-600" />
                                                    <p className="mt-2 text-xs font-bold text-emerald-900">
                                                        All Vehicles Operating Efficiently
                                                    </p>
                                                    <p className="mt-0.5 text-[11px] text-emerald-700">
                                                        No continuous idling exceeding 5 minutes detected
                                                    </p>
                                                </div>
                                            ) : (
                                                <div className="space-y-2">
                                                    {attentionVehicles.map(vehicle => {
                                                        return (
                                                            <button
                                                                key={vehicle.vehicle_id}
                                                                type="button"
                                                                onClick={() => setSelectedVehicleId(vehicle.vehicle_id)}
                                                                className="w-full flex items-center justify-between rounded-lg border border-amber-200/80 bg-amber-50/40 p-3 text-left transition hover:bg-amber-100/60 hover:border-amber-300 cursor-pointer group"
                                                            >
                                                                <div className="min-w-0 pr-3">
                                                                    <div className="flex items-center gap-2">
                                                                        <span className="font-mono text-xs font-bold text-slate-900 truncate">
                                                                            {vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name}
                                                                        </span>
                                                                        <span className="rounded bg-amber-200/80 px-1.5 py-0.5 text-[9px] font-bold text-amber-900 shrink-0">
                                                                            {vehicle.group || "General Fleet"}
                                                                        </span>
                                                                    </div>
                                                                    <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] font-semibold text-amber-900">
                                                                        <span className="inline-flex items-center gap-1">
                                                                            <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
                                                                            <LiveIdleTimer
                                                                                startTime={vehicle.idle_start}
                                                                                baseDuration={vehicle.idle_duration}
                                                                                status={vehicle.status}
                                                                                className="text-amber-950 font-bold"
                                                                            />
                                                                        </span>
                                                                        <span className="text-amber-400">·</span>
                                                                        <span className="text-amber-800 text-[10px]">
                                                                            {vehicle.idle_start ? `Idling since ${formatUAETime(vehicle.idle_start)}` : "Start time unavailable"}
                                                                        </span>
                                                                    </div>
                                                                </div>

                                                                <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-amber-600 text-white group-hover:bg-amber-700 transition">
                                                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                                                </div>
                                                            </button>
                                                        )
                                                    })}
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* TOP VEHICLES BY MILEAGE */}
                                    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs flex flex-col justify-between">
                                        <div>
                                            <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                                                <div className="flex items-center gap-2">
                                                    <TrendingUp className="h-4 w-4 text-[#33758F]" />
                                                    <h2 className="text-sm font-bold text-slate-900">
                                                        Top Vehicles by Mileage
                                                    </h2>
                                                    {mileageLoading && (
                                                        <span className="flex items-center gap-1 text-[10px] font-semibold text-sky-600 bg-sky-50 px-2 py-0.5 rounded-full border border-sky-200 animate-pulse">
                                                            <Loader2 className="h-3 w-3 animate-spin" /> Updating
                                                        </span>
                                                    )}
                                                </div>

                                                {/* Dropdown Filters */}
                                                <div className="flex items-center gap-2">
                                                    {/* Timeframe Dropdown */}
                                                    <div className="flex items-center rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs">
                                                        <select
                                                            value={mileageTimeframe}
                                                            onChange={(e) => setMileageTimeframe(e.target.value)}
                                                            className="bg-transparent font-bold text-slate-800 outline-none cursor-pointer text-xs"
                                                            aria-label="Select mileage timeframe"
                                                        >
                                                            {TIMEFRAME_OPTIONS.map(tf => (
                                                                <option key={tf.id} value={tf.id}>{tf.label}</option>
                                                            ))}
                                                        </select>
                                                    </div>

                                                    {/* Limit Dropdown */}
                                                    <div className="flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs">
                                                        <Filter className="h-3 w-3 text-slate-400" />
                                                        <select
                                                            value={mileageLimit}
                                                            onChange={(e) => setMileageLimit(e.target.value)}
                                                            className="bg-transparent font-bold text-slate-800 outline-none cursor-pointer text-xs"
                                                            aria-label="Select mileage limit"
                                                        >
                                                            <option value="5">Top 5</option>
                                                            <option value="10">Top 10</option>
                                                            <option value="15">Top 15</option>
                                                            <option value="ALL">Show All</option>
                                                        </select>
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="h-[220px]">
                                                {filteredMileageList.length === 0 ? (
                                                    <div className="flex h-full items-center justify-center text-xs text-slate-400">
                                                        No vehicle mileage data available for selected filter.
                                                    </div>
                                                ) : (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <BarChart
                                                            data={filteredMileageList}
                                                            layout="vertical"
                                                            margin={{ top: 5, right: 30, left: 10, bottom: 5 }}
                                                        >
                                                            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                                                            <XAxis type="number" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                                                            <YAxis
                                                                type="category"
                                                                dataKey="vehicle"
                                                                tickLine={false}
                                                                axisLine={false}
                                                                width={90}
                                                                tick={{ fontSize: 11, fill: "#0f172a", fontWeight: 700 }}
                                                            />
                                                            <Tooltip
                                                                formatter={(val) => [isKnownNumber(val) ? `${Number(val).toLocaleString()} km` : "Unknown (unreadable)", "Mileage"]}
                                                                contentStyle={{ borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }}
                                                            />
                                                            <Bar
                                                                dataKey="mileage"
                                                                fill="#33758F"
                                                                radius={[0, 4, 4, 0]}
                                                                onClick={(entry) => {
                                                                    if (entry?.vehicle_id) setSelectedVehicleId(entry.vehicle_id)
                                                                }}
                                                                cursor="pointer"
                                                            />
                                                        </BarChart>
                                                    </ResponsiveContainer>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </section>

                                {/* ─── 5. FLEET BY GROUP & ANALYTICS CHARTS ─────────────────── */}
                                <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                                    {/* FLEET BY GROUP */}
                                    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs flex flex-col justify-between">
                                        <div>
                                            <div className="flex items-center justify-between mb-4">
                                                <div>
                                                    <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                        <Layers className="h-4 w-4 text-[#33758F]" />
                                                        Fleet by Group
                                                    </h2>
                                                    <p className="text-[11px] text-slate-400 font-medium">
                                                        Distribution across vehicle operational divisions
                                                    </p>
                                                </div>
                                                <span className="text-xs font-bold text-slate-600 bg-slate-100 px-2 py-0.5 rounded-md">
                                                    {selectedGroup === "ALL" ? `${fleetByGroupData.length} Groups` : selectedGroup}
                                                </span>
                                            </div>

                                            {selectedGroup === "ALL" ? (
                                                <div className="space-y-3">
                                                    {fleetByGroupData.map(grp => (
                                                        <div key={grp.name} className="space-y-1">
                                                            <div className="flex items-center justify-between text-xs">
                                                                <span className="font-bold text-slate-800">{grp.name}</span>
                                                                <div className="flex items-center gap-2">
                                                                    <span className="text-[11px] font-semibold text-slate-400">
                                                                        {grp.percent}%
                                                                    </span>
                                                                    <span className="font-mono font-bold text-slate-900">
                                                                        {grp.count} units
                                                                    </span>
                                                                </div>
                                                            </div>
                                                            <div className="flex h-2 overflow-hidden rounded-full bg-slate-100">
                                                                <div
                                                                    className="bg-[#33758F] rounded-full transition-all"
                                                                    style={{ width: `${grp.percent}%` }}
                                                                />
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="space-y-3">
                                                    <div className="rounded-lg border border-slate-100 bg-slate-50/60 p-3.5">
                                                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Filtered Category</p>
                                                        <p className="mt-0.5 text-base font-black text-slate-900">{selectedGroup}</p>
                                                        <p className="text-[11px] text-slate-500 mt-1">
                                                            Currently monitoring {totalVehiclesCount} units in this division.
                                                        </p>
                                                    </div>

                                                    <div className="grid grid-cols-2 gap-2 text-xs">
                                                        <div className="rounded-md bg-emerald-50/60 p-2.5 border border-emerald-100">
                                                            <span className="text-emerald-800 text-[11px] font-semibold block">In Motion</span>
                                                            <span className="text-base font-black text-emerald-700">{movingCount}</span>
                                                        </div>
                                                        <div className="rounded-md bg-amber-50/60 p-2.5 border border-amber-100">
                                                            <span className="text-amber-800 text-[11px] font-semibold block">Idling</span>
                                                            <span className="text-base font-black text-amber-700">{idlingCount}</span>
                                                        </div>
                                                        <div className="rounded-md bg-slate-100 p-2.5 border border-slate-200">
                                                            <span className="text-slate-700 text-[11px] font-semibold block">Stopped</span>
                                                            <span className="text-base font-black text-slate-800">{stoppedCount}</span>
                                                        </div>
                                                        <div className="rounded-md bg-rose-50/60 p-2.5 border border-rose-100">
                                                            <span className="text-rose-800 text-[11px] font-semibold block">Offline</span>
                                                            <span className="text-base font-black text-rose-700">{offlineCount}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </div>

                                    {/* FLEET UTILIZATION BY CATEGORY */}
                                    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs flex flex-col justify-between">
                                        <div>
                                            <div className="flex items-center justify-between mb-4">
                                                <div>
                                                    <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                        <BarChart3 className="h-4 w-4 text-[#33758F]" />
                                                        Fleet Utilization by Category
                                                    </h2>
                                                    <p className="text-[11px] text-slate-400 font-medium">
                                                        {selectedGroup === "ALL" ? "Active units percentage by group" : `Utilization for ${selectedGroup}`}
                                                    </p>
                                                </div>

                                                {/* Chart Type Selector */}
                                                <div className="flex items-center rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs">
                                                    <select
                                                        value={utilizationChartType}
                                                        onChange={(e) => setUtilizationChartType(e.target.value)}
                                                        className="bg-transparent font-bold text-slate-800 outline-none cursor-pointer text-xs"
                                                        aria-label="Select utilization chart type"
                                                    >
                                                        <option value="bar">Bar</option>
                                                        <option value="horizontal">Horizontal Bar</option>
                                                        <option value="donut">Donut</option>
                                                    </select>
                                                </div>
                                            </div>

                                            <div className="h-[220px]">
                                                {utilizationChartData.length === 0 ? (
                                                    <div className="flex h-full items-center justify-center text-xs text-slate-400">
                                                        No utilization data available.
                                                    </div>
                                                ) : utilizationChartType === "bar" ? (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <BarChart data={utilizationChartData} margin={{ top: 10, right: 10, left: -20, bottom: 20 }}>
                                                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                                            <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 700 }} />
                                                            <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={{ borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }} />
                                                            <Bar dataKey="value" fill="#33758F" radius={[4, 4, 0, 0]} />
                                                        </BarChart>
                                                    </ResponsiveContainer>
                                                ) : utilizationChartType === "horizontal" ? (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <BarChart data={utilizationChartData} layout="vertical" margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                                                            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                                                            <XAxis type="number" domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                                            <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={90} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 700 }} />
                                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={{ borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }} />
                                                            <Bar dataKey="value" fill="#33758F" radius={[0, 4, 4, 0]} />
                                                        </BarChart>
                                                    </ResponsiveContainer>
                                                ) : (
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <PieChart>
                                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={{ borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }} />
                                                            <Pie
                                                                data={utilizationChartData}
                                                                dataKey="value"
                                                                nameKey="name"
                                                                cx="50%"
                                                                cy="50%"
                                                                innerRadius={40}
                                                                outerRadius={75}
                                                                paddingAngle={3}
                                                            >
                                                                {utilizationChartData.map((entry, index) => (
                                                                    <Cell key={`util-cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                                                                ))}
                                                            </Pie>
                                                            <Legend wrapperStyle={{ fontSize: "10px" }} />
                                                        </PieChart>
                                                    </ResponsiveContainer>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </section>

                                {/* ─── 6. IDLE DURATION BY CATEGORY ─────────────────────────── */}
                                <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs">
                                    <div className="flex items-center justify-between mb-4">
                                        <div>
                                            <h2 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                <Clock className="h-4 w-4 text-[#33758F]" />
                                                Idle Duration by Category
                                            </h2>
                                            <p className="text-[11px] text-slate-400 font-medium">
                                                {selectedGroup === "ALL" ? "Current continuous idle minutes by fleet group" : `Current continuous idle minutes for ${selectedGroup}`}
                                            </p>
                                        </div>

                                        {/* Chart Type Selector (Bar and Horizontal Bar ONLY) */}
                                        <div className="flex items-center rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs">
                                            <select
                                                value={idleChartType}
                                                onChange={(e) => setIdleChartType(e.target.value)}
                                                className="bg-transparent font-bold text-slate-800 outline-none cursor-pointer text-xs"
                                                aria-label="Select idle duration chart type"
                                            >
                                                <option value="bar">Bar</option>
                                                <option value="horizontal">Horizontal Bar</option>
                                            </select>
                                        </div>
                                    </div>

                                    <div className="h-[240px]">
                                        {idleDurationChartData.length === 0 ? (
                                            <div className="flex h-full items-center justify-center text-xs text-slate-400">
                                                No idle duration recorded for this selection.
                                            </div>
                                        ) : idleChartType === "horizontal" ? (
                                            <ResponsiveContainer width="100%" height="100%">
                                                <BarChart data={idleDurationChartData} layout="vertical" margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                                                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                                                    <XAxis type="number" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                                    <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={100} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 700 }} />
                                                    <Tooltip formatter={formatIdleTooltip} contentStyle={{ borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }} />
                                                    <Bar dataKey="value" fill="#f97316" radius={[0, 4, 4, 0]} />
                                                </BarChart>
                                            </ResponsiveContainer>
                                        ) : (
                                            <ResponsiveContainer width="100%" height="100%">
                                                <BarChart data={idleDurationChartData} margin={{ top: 10, right: 10, left: -20, bottom: 20 }}>
                                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                                    <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 700 }} />
                                                    <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                                    <Tooltip formatter={formatIdleTooltip} contentStyle={{ borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }} />
                                                    <Bar dataKey="value" fill="#f97316" radius={[4, 4, 0, 0]} />
                                                </BarChart>
                                            </ResponsiveContainer>
                                        )}
                                    </div>
                                </section>
                            </div>
                        )}
                    </main>
                </div>

                {/* ─── 7. VEHICLE DETAILS DRAWER ──────────────────────────────── */}
                {selectedVehicle && (
                    <VehicleDetailDrawer
                        vehicle={selectedVehicle}
                        onClose={() => setSelectedVehicleId(null)}
                    />
                )}
            </div>
        </SidebarProvider>
    )
}

export default Dashboard
