import { useEffect, useMemo, useState, useRef } from "react"
import { Link } from "react-router-dom"
import {
    Activity,
    Layers,
    LayoutDashboard,
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

import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import SectionCard from "../components/Common/SectionCard"
import FilterSelect from "../components/Common/FilterSelect"
import { ErrorBanner, LoadingState } from "../components/Common/StateViews"
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

    const pct = (count) => (totalVehiclesCount > 0 ? Math.round((count / totalVehiclesCount) * 100) : 0)

    const statusSegments = [
        { key: "MOVING", label: "Moving", count: movingCount, bar: "bg-emerald-500", text: "text-emerald-700" },
        { key: "IDLING", label: "Idling", count: idlingCount, bar: "bg-amber-500", text: "text-amber-700" },
        { key: "STOPPED", label: "Stopped", count: stoppedCount, bar: "bg-slate-400", text: "text-slate-700" },
        { key: "OFFLINE", label: "Offline", count: offlineCount, bar: "bg-rose-500", text: "text-rose-700" },
    ]

    const activityTiles = [
        {
            label: "Distance",
            value: todayFleetActivity.distance,
            valueCls: "text-slate-900",
            partial: todayFleetActivity.distanceUnknownUnits > 0 ? `${todayFleetActivity.distanceUnknownUnits} unit(s) unknown` : null,
            partialTitle: "Wialon could not read these units' mileage; they are left out of the total",
        },
        { label: "Trips", value: todayFleetActivity.trips, valueCls: "text-[#33758F]", partial: null },
        {
            label: "Driving",
            value: todayFleetActivity.driving,
            valueCls: "text-emerald-700",
            partial: todayFleetActivity.drivingUnknownVehicles > 0 ? `${todayFleetActivity.drivingUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "Wialon reported no usable distance, speed or trip duration for these vehicles; they are left out of the total",
        },
        {
            label: "Idle",
            value: todayFleetActivity.idle,
            valueCls: "text-amber-600",
            partial: todayFleetActivity.idleUnknownVehicles > 0 ? `${todayFleetActivity.idleUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "These vehicles' idle time could not be attributed; they are left out of the total",
        },
    ]

    const tooltipStyle = { borderRadius: "8px", border: "1px solid #E2E8F0", fontSize: "12px" }
    // One row per vehicle keeps long "Show All" rankings readable instead of squeezing bars
    const mileageChartHeight = Math.max(220, filteredMileageList.length * 28 + 20)

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
                icon={LayoutDashboard}
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
                <div className="space-y-5">
                    {error && <ErrorBanner message={`${error}. Retrying automatically.`} />}

                    {/* ─── KPI CARDS ─────────────────────────────────────────── */}
                    <section aria-label="Key Performance Indicators" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
                        <KpiCard
                            label="Total Vehicles"
                            value={totalVehiclesCount}
                            sub={selectedGroup === "ALL" ? "All registered units" : selectedGroup}
                            to="/tracking"
                            title="View all units in Live Tracking"
                        />
                        <KpiCard
                            label="Moving"
                            value={movingCount}
                            valueColor="text-emerald-600"
                            sub={`${pct(movingCount)}% in motion`}
                            to="/tracking?status=MOVING"
                            title="View moving vehicles"
                        />
                        <KpiCard
                            label="Idling"
                            value={idlingCount}
                            valueColor="text-amber-600"
                            sub={`${pct(idlingCount)}% engine on`}
                            to="/tracking?status=IDLING"
                            title="View idling vehicles"
                        />
                        <KpiCard
                            label="Stopped"
                            value={stoppedCount}
                            valueColor="text-slate-700"
                            sub={`${pct(stoppedCount)}% parked`}
                            to="/tracking?status=STOPPED"
                            title="View stopped vehicles"
                        />
                        <KpiCard
                            label="Offline"
                            value={offlineCount}
                            valueColor={offlineCount > 0 ? "text-rose-600" : "text-slate-700"}
                            sub={`${pct(offlineCount)}% no signal`}
                            to="/tracking?status=OFFLINE"
                            title="View offline vehicles"
                        />
                        <KpiCard
                            label="Active Today"
                            value={activeTodayCount}
                            valueColor="text-[#33758F]"
                            sub={`${pct(activeTodayCount)}% utilized`}
                            to="/tracking"
                            title="View active vehicles today"
                        />
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
                    </section>

                    {/* ─── FLEET STATUS & TODAY'S ACTIVITY ───────────────────── */}
                    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                        <SectionCard
                            title="Fleet Status Distribution"
                            icon={Activity}
                            actions={
                                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-slate-600">
                                    {totalVehiclesCount} units
                                </span>
                            }
                        >
                            <div className="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
                                {totalVehiclesCount > 0 && statusSegments.map(seg => (
                                    <div
                                        key={seg.key}
                                        className={`${seg.bar} transition-all`}
                                        style={{ width: `${(seg.count / totalVehiclesCount) * 100}%` }}
                                        title={`${seg.label}: ${seg.count}`}
                                    />
                                ))}
                            </div>

                            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                                {statusSegments.map(seg => (
                                    <div key={seg.key} className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5">
                                        <div className="flex items-center gap-1.5">
                                            <span className={`h-2 w-2 rounded-full ${seg.bar}`} />
                                            <span className="text-[11px] font-semibold text-slate-500">{seg.label}</span>
                                        </div>
                                        <p className="mt-1 flex items-baseline gap-1.5">
                                            <span className={`text-lg font-bold tabular-nums ${seg.text}`}>{seg.count}</span>
                                            <span className="text-[11px] font-medium tabular-nums text-slate-400">{pct(seg.count)}%</span>
                                        </p>
                                    </div>
                                ))}
                            </div>
                        </SectionCard>

                        <SectionCard
                            title="Today's Activity"
                            icon={Route}
                            actions={
                                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-500">
                                    UAE 00:00 → now
                                </span>
                            }
                        >
                            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                                {activityTiles.map(tile => (
                                    <div key={tile.label} className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5">
                                        <span className="text-[11px] font-semibold text-slate-500">{tile.label}</span>
                                        <p className={`mt-1 truncate text-lg font-bold tabular-nums ${tile.valueCls}`}>
                                            {tile.value}
                                        </p>
                                        {tile.partial && (
                                            <span className="block truncate text-[10px] font-medium text-amber-700" title={tile.partialTitle}>
                                                Partial: {tile.partial}
                                            </span>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </SectionCard>
                    </div>

                    {/* ─── IDLE VEHICLES & TOP MILEAGE ───────────────────────── */}
                    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                        <SectionCard
                            title="Idling Over 5 Minutes"
                            icon={AlertTriangle}
                            iconClassName="text-amber-600"
                            actions={
                                <Link
                                    to="/idle-analytics"
                                    className="inline-flex items-center gap-1 text-xs font-semibold text-[#33758F] hover:underline"
                                >
                                    Idle analytics
                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                </Link>
                            }
                        >
                            {attentionVehicles.length === 0 ? (
                                <div className="flex flex-col items-center justify-center rounded-lg border border-emerald-100 bg-emerald-50/40 py-8 text-center">
                                    <CheckCircle2 className="h-6 w-6 text-emerald-600" />
                                    <p className="mt-2 text-xs font-semibold text-emerald-900">
                                        No vehicle has been idling for more than 5 minutes
                                    </p>
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {attentionVehicles.map(vehicle => (
                                        <button
                                            key={vehicle.vehicle_id}
                                            type="button"
                                            onClick={() => setSelectedVehicleId(vehicle.vehicle_id)}
                                            className="group flex w-full items-center justify-between gap-3 rounded-lg border border-amber-200/80 bg-amber-50/40 px-3 py-2.5 text-left transition hover:border-amber-300 hover:bg-amber-100/60 cursor-pointer"
                                        >
                                            <div className="min-w-0">
                                                <div className="flex items-center gap-2">
                                                    <span className="truncate font-mono text-xs font-bold text-slate-900">
                                                        {vehicle.registration_plate || vehicle.plate || vehicle.vehicle_name}
                                                    </span>
                                                    <span className="shrink-0 rounded bg-amber-200/80 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900">
                                                        {vehicle.group || "General Fleet"}
                                                    </span>
                                                </div>
                                                <div className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] font-medium text-amber-900">
                                                    <span className="inline-flex items-center gap-1">
                                                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500 motion-reduce:animate-none" />
                                                        <LiveIdleTimer
                                                            startTime={vehicle.idle_start}
                                                            baseDuration={vehicle.idle_duration}
                                                            status={vehicle.status}
                                                            className="font-bold text-amber-950"
                                                        />
                                                    </span>
                                                    <span className="text-amber-400">·</span>
                                                    <span className="text-[10px] text-amber-800">
                                                        {vehicle.idle_start ? `since ${formatUAETime(vehicle.idle_start)}` : "Start time unavailable"}
                                                    </span>
                                                </div>
                                            </div>
                                            <ArrowUpRight className="h-4 w-4 shrink-0 text-amber-700 transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
                                        </button>
                                    ))}
                                </div>
                            )}
                        </SectionCard>

                        <SectionCard
                            title="Top Vehicles by Mileage"
                            icon={TrendingUp}
                            actions={
                                <>
                                    {mileageLoading && (
                                        <Loader2 className="h-3.5 w-3.5 animate-spin text-[#33758F]" aria-label="Updating" />
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
                                        icon={Filter}
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
                                <div className="flex h-[220px] items-center justify-center text-xs text-slate-400">
                                    No mileage data for this selection.
                                </div>
                            ) : (
                                <div className="max-h-[360px] overflow-y-auto">
                                    <div style={{ height: `${mileageChartHeight}px` }}>
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart
                                                data={filteredMileageList}
                                                layout="vertical"
                                                margin={{ top: 5, right: 24, left: 4, bottom: 5 }}
                                            >
                                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                                                <XAxis type="number" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#94a3b8" }} />
                                                <YAxis
                                                    type="category"
                                                    dataKey="vehicle"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    width={96}
                                                    interval={0}
                                                    tick={{ fontSize: 11, fill: "#0f172a", fontWeight: 600 }}
                                                />
                                                <Tooltip
                                                    formatter={(val) => [isKnownNumber(val) ? `${Number(val).toLocaleString()} km` : "Unknown (unreadable)", "Mileage"]}
                                                    contentStyle={tooltipStyle}
                                                />
                                                <Bar
                                                    dataKey="mileage"
                                                    fill="#33758F"
                                                    radius={[0, 4, 4, 0]}
                                                    maxBarSize={18}
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
                    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                        <SectionCard
                            title="Fleet by Group"
                            icon={Layers}
                            actions={
                                <span className="max-w-[180px] truncate rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">
                                    {selectedGroup === "ALL" ? `${fleetByGroupData.length} groups` : selectedGroup}
                                </span>
                            }
                        >
                            {selectedGroup === "ALL" ? (
                                fleetByGroupData.length === 0 ? (
                                    <div className="flex h-[160px] items-center justify-center text-xs text-slate-400">
                                        No group data available.
                                    </div>
                                ) : (
                                    <div className="max-h-[260px] space-y-3 overflow-y-auto pr-1">
                                        {fleetByGroupData.map(grp => (
                                            <div key={grp.name} className="space-y-1">
                                                <div className="flex items-center justify-between gap-2 text-xs">
                                                    <span className="truncate font-semibold text-slate-800">{grp.name}</span>
                                                    <span className="shrink-0 tabular-nums">
                                                        <span className="mr-2 text-[11px] font-medium text-slate-400">{grp.percent}%</span>
                                                        <span className="font-bold text-slate-900">{grp.count} units</span>
                                                    </span>
                                                </div>
                                                <div className="flex h-1.5 overflow-hidden rounded-full bg-slate-100">
                                                    <div
                                                        className="rounded-full bg-[#33758F] transition-all"
                                                        style={{ width: `${grp.percent}%` }}
                                                    />
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )
                            ) : (
                                <div className="grid grid-cols-2 gap-2">
                                    {statusSegments.map(seg => (
                                        <div key={seg.key} className="rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5">
                                            <span className="text-[11px] font-semibold text-slate-500">{seg.label}</span>
                                            <p className={`text-lg font-bold tabular-nums ${seg.text}`}>{seg.count}</p>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </SectionCard>

                        <SectionCard
                            title="Utilization by Group"
                            icon={BarChart3}
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
                                    <div className="flex h-full items-center justify-center text-xs text-slate-400">
                                        No utilization data available.
                                    </div>
                                ) : utilizationChartType === "bar" ? (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={utilizationChartData} margin={{ top: 10, right: 10, left: -20, bottom: 20 }}>
                                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                            <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 600 }} />
                                            <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={tooltipStyle} />
                                            <Bar dataKey="value" fill="#33758F" radius={[4, 4, 0, 0]} maxBarSize={48} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : utilizationChartType === "horizontal" ? (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <BarChart data={utilizationChartData} layout="vertical" margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                                            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                                            <XAxis type="number" domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                            <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={90} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 600 }} />
                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={tooltipStyle} />
                                            <Bar dataKey="value" fill="#33758F" radius={[0, 4, 4, 0]} maxBarSize={22} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                ) : (
                                    <ResponsiveContainer width="100%" height="100%">
                                        <PieChart>
                                            <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={tooltipStyle} />
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
                        </SectionCard>
                    </div>

                    {/* ─── IDLE DURATION BY GROUP ────────────────────────────── */}
                    <SectionCard
                        title="Idle Time Today"
                        icon={Clock}
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
                                <div className="flex h-full items-center justify-center text-xs text-slate-400">
                                    No idle time recorded for this selection.
                                </div>
                            ) : idleChartType === "horizontal" ? (
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={idleDurationChartData} layout="vertical" margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
                                        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
                                        <XAxis type="number" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                        <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} width={100} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 600 }} />
                                        <Tooltip formatter={formatIdleTooltip} contentStyle={tooltipStyle} />
                                        <Bar dataKey="value" fill="#f97316" radius={[0, 4, 4, 0]} maxBarSize={22} />
                                    </BarChart>
                                </ResponsiveContainer>
                            ) : (
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={idleDurationChartData} margin={{ top: 10, right: 10, left: -20, bottom: 20 }}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
                                        <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#0f172a", fontWeight: 600 }} />
                                        <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 10, fill: "#64748b" }} />
                                        <Tooltip formatter={formatIdleTooltip} contentStyle={tooltipStyle} />
                                        <Bar dataKey="value" fill="#f97316" radius={[4, 4, 0, 0]} maxBarSize={48} />
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
