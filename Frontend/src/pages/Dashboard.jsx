import { useEffect, useMemo, useState, useRef } from "react"
import { Link } from "react-router-dom"
import {
    Layers,
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
    LabelList,
} from "recharts"


import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import SectionCard from "../components/Common/SectionCard"
import FilterSelect from "../components/Common/FilterSelect"
import { ErrorBanner, LoadingState } from "../components/Common/StateViews"
import { formatUAETime } from "../utils/datetime"
import { normalizeVehicleStatus } from "../utils/vehicleStatus"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"
import {
    CATEGORY_TICK,
    GRID_STROKE,
    TICK,
    TOOLTIP_CURSOR,
    TOOLTIP_STYLE,
} from "../components/Common/chartTheme"
import {
    compareKnownDesc, drivingSecondsByVehicle, idleSecondsOf, isKnownNumber, sumKnown, sumKnownInBoth,
} from "../utils/knownTotals"

const DRIVING_LABEL_TITLE = "Wialon Engine Hours report \"In motion\" time per vehicle (engine on and moving). Not total "
    + "engine hours: Wialon may classify some engine-on time as neither moving nor idling. A vehicle is left out as "
    + "unknown when this report's mileage disagrees with the Dashboard mileage by more than max(2 km, 5%)."

// Legacy mileage formatter contract: formatter={(val) => [formatKm(val) ?? "Unknown (unreadable)", "Mileage"]}

const BRAND_BLUE = "#33758F"


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

    // Today's dedicated mileage data
    const [todayMileageData, setTodayMileageData] = useState(null)

    // Yesterday's trips, idle, and mileage data
    const [yesterdayTripsData, setYesterdayTripsData] = useState(null)
    const [yesterdayIdleAnalyticsData, setYesterdayIdleAnalyticsData] = useState(null)
    const [yesterdayMileageData, setYesterdayMileageData] = useState(null)

    const auxSeqRef = useRef(0)


    // Vehicle Details Drawer state
    const [selectedVehicleId, setSelectedVehicleId] = useState(null)

    // ─── 1. Load Live Vehicles Telemetry (Periodic Refresh) ─────────────────
    useEffect(() => {
        let mounted = true
        let timerId = null
        let isFetching = false

        const fetchVehiclesPeriodic = async () => {
            if (isFetching || !mounted) return
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
    useEffect(() => {
        let active = true
        let isFetching = false
        let timerId = null

        async function fetchAuxiliaryData() {
            if (isFetching || !active) return
            if (document.hidden) return
            isFetching = true
            const seq = ++auxSeqRef.current
            try {
                const isCurrent = () => active && seq === auxSeqRef.current
                await Promise.allSettled([
                    getFleetAlertsAnalytics().then(res => { if (isCurrent()) setAlertsData(res) }),
                    getFleetTripsAnalytics({ timeframe: "today" }).then(res => { if (isCurrent()) setTripsData(res) }),
                    getFleetIdleAnalytics({ timeframe: "today", include_events: false }).then(res => { if (isCurrent()) setIdleAnalyticsData(res) }),
                    getDashboardMileage("today").then(res => { if (isCurrent()) setTodayMileageData(res) }),
                    getFleetTripsAnalytics({ timeframe: "yesterday" }).then(res => { if (isCurrent()) setYesterdayTripsData(res) }),
                    getFleetIdleAnalytics({ timeframe: "yesterday", include_events: false }).then(res => { if (isCurrent()) setYesterdayIdleAnalyticsData(res) }),
                    getDashboardMileage("yesterday").then(res => { if (isCurrent()) setYesterdayMileageData(res) }),
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

    // ─── 3. Dynamic Group Discovery from Actual Wialon Data ─────────────────
    const availableGroups = useMemo(() => {
        const set = new Set()
        vehicles.forEach(v => {
            if (v.group && v.group.trim()) set.add(v.group.trim())
            if (Array.isArray(v.groups)) {
                v.groups.forEach(g => { if (g && g.trim()) set.add(g.trim()) })
            }
        })
        if (todayMileageData?.all_units) {
            todayMileageData.all_units.forEach(u => {
                if (u.group && u.group.trim()) set.add(u.group.trim())
            })
        }
        if (yesterdayMileageData?.all_units) {
            yesterdayMileageData.all_units.forEach(u => {
                if (u.group && u.group.trim()) set.add(u.group.trim())
            })
        }
        return Array.from(set).sort()
    }, [vehicles, todayMileageData, yesterdayMileageData])


    // ─── 4. Normalize Vehicles ──────────────────────────────────────────────
    const normalizedVehicles = useMemo(() => {
        return vehicles.map(vehicle => {
            const finalStatus = normalizeVehicleStatus(vehicle)
            return {
                ...vehicle,
                normalizedStatus: finalStatus,
                isOffline: finalStatus === "OFFLINE",
            }
        })
    }, [vehicles])

    // ─── 5. Global Group Filtering ──────────────────────────────────────────
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

    // ─── 6. Live Status KPI Counts ──────────────────────────────────────────
    const totalVehiclesCount = filteredVehicles.length
    const movingVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "MOVING"), [filteredVehicles])
    const idlingVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "IDLING"), [filteredVehicles])
    const stoppedVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "STOPPED"), [filteredVehicles])
    const offlineVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "OFFLINE"), [filteredVehicles])
    // Stale telemetry: not counted as live Moving/Idling/Stopped
    const delayedVehicles = useMemo(() => filteredVehicles.filter(v => v.normalizedStatus === "DELAYED"), [filteredVehicles])

    const movingCount = movingVehicles.length
    const idlingCount = idlingVehicles.length
    const stoppedCount = stoppedVehicles.length
    const offlineCount = offlineVehicles.length
    const delayedCount = delayedVehicles.length

    const pct = (count) => (totalVehiclesCount > 0 ? Math.round((count / totalVehiclesCount) * 100) : 0)

    // Active Today
    const activeTodayCount = useMemo(() => {
        const activeIds = new Set()
        filteredVehicles.forEach(v => {
            if (v.normalizedStatus === "MOVING" || v.normalizedStatus === "IDLING") {
                activeIds.add(v.vehicle_id)
            }
        })
        const sourceMileage = todayMileageData?.all_units
        if (sourceMileage) {
            sourceMileage.forEach(u => {
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
        if (tripsData?.vehicles) {
            tripsData.vehicles.forEach(tv => {
                if (Number(tv.trip_count || 0) > 0 || Number(tv.gps_mileage_km || 0) > 0) {
                    const vidStr = String(tv.vehicle_id)
                    if (filteredVehicleIdSet.has(vidStr)) activeIds.add(tv.vehicle_id)
                }
            })
        }
        return activeIds.size
    }, [filteredVehicles, filteredVehicleIdSet, todayMileageData, tripsData])

    // Filtered Alerts Count
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

    // ─── 7. Fleet Activity Metrics Calculation (Reusable for Today & Yesterday)
    const formatHoursMins = (totalMins) => {
        if (!totalMins || totalMins <= 0) return "0h 0m"
        const h = Math.floor(totalMins / 60)
        const m = Math.round(totalMins % 60)
        if (h === 0) return `${m}m`
        return `${h}h ${m}m`
    }

    const calculateTimeframeActivity = (mileageData, tripsDataPayload, idleDataPayload) => {
        let distanceKm = 0
        let distanceUnknownUnits = 0
        let distanceAllUnknown
        const sourceMileage = mileageData?.all_units
        if (sourceMileage) {
            const filteredUnits = selectedGroup === "ALL"
                ? sourceMileage
                : sourceMileage.filter(u => filteredVehicleIdSet.has(String(u.vehicle_id)))
            const mileage = sumKnown(filteredUnits, u => u.mileage)
            distanceKm = mileage.total
            distanceUnknownUnits = mileage.unknown
            distanceAllUnknown = mileage.allUnknown
        } else if (tripsDataPayload?.vehicles) {
            const filteredTripVehs = selectedGroup === "ALL"
                ? tripsDataPayload.vehicles
                : tripsDataPayload.vehicles.filter(tv => filteredVehicleIdSet.has(String(tv.vehicle_id)))
            const mileage = sumKnown(filteredTripVehs, tv => tv.gps_mileage_km)
            distanceKm = mileage.total
            distanceUnknownUnits = mileage.unknown
            distanceAllUnknown = mileage.allUnknown
        } else {
            distanceAllUnknown = true
        }

        let trips = null
        if (tripsDataPayload?.vehicles) {
            const filteredTripVehs = selectedGroup === "ALL"
                ? tripsDataPayload.vehicles
                : tripsDataPayload.vehicles.filter(tv => filteredVehicleIdSet.has(String(tv.vehicle_id)))
            const tripCountsKnown = filteredTripVehs.every(tv => typeof tv.trip_count === "number")
            trips = tripCountsKnown ? filteredTripVehs.reduce((sum, tv) => sum + tv.trip_count, 0) : null
        }

        // Driving time (engine on, moving): Wialon Engine Hours "In motion", trusted per unit only when that report's
        // mileage agrees with this period's Dashboard mileage. Unknown units are left out and counted, never 0.
        const drivingByVehicle = drivingSecondsByVehicle(
            idleDataPayload, mileageData, id => selectedGroup === "ALL" || filteredVehicleIdSet.has(id),
        )
        const drivingSum = sumKnown(Array.from(drivingByVehicle.values()), seconds => seconds)
        const drivingKnown = Array.isArray(idleDataPayload?.by_vehicle) && !drivingSum.allUnknown
        const drivingMinutes = Math.round(drivingSum.total / 60)
        const drivingUnknownVehicles = drivingSum.unknown

        let idleMinutes = 0
        let idleUnknownVehicles = 0
        let idleAllUnknown
        if (idleDataPayload?.by_vehicle) {
            const matchingVehicles = selectedGroup === "ALL"
                ? idleDataPayload.by_vehicle
                : idleDataPayload.by_vehicle.filter(v => filteredVehicleIdSet.has(String(v.vehicle_id)))
            const idle = sumKnown(matchingVehicles, idleSecondsOf)
            idleMinutes = Math.round(idle.total / 60)
            idleUnknownVehicles = idle.unknown
            idleAllUnknown = idle.allUnknown
        } else if (idleDataPayload?.summary?.total_idle_seconds !== undefined && selectedGroup === "ALL") {
            const summaryIdle = idleDataPayload.summary.total_idle_seconds
            idleAllUnknown = !isKnownNumber(summaryIdle)
            idleMinutes = idleAllUnknown ? 0 : Math.round(Number(summaryIdle) / 60)
        } else {
            idleAllUnknown = true
        }

        return {
            distance: distanceAllUnknown ? "—" : `${Math.round(distanceKm).toLocaleString()} km`,
            distanceRaw: distanceAllUnknown ? null : Math.round(distanceKm),
            distanceUnknownUnits,
            trips: trips === null ? "—" : trips.toLocaleString(),
            tripsRaw: trips,
            driving: drivingKnown ? formatHoursMins(drivingMinutes) : "—",
            drivingRaw: drivingKnown ? drivingMinutes : null,
            drivingUnknownVehicles,
            drivingByVehicle,
            idle: idleAllUnknown ? "—" : formatHoursMins(idleMinutes),
            idleRaw: idleAllUnknown ? null : idleMinutes,
            idleUnknownVehicles,
        }
    }

    const todayFleetActivity = useMemo(() => {
        return calculateTimeframeActivity(todayMileageData, tripsData, idleAnalyticsData)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [todayMileageData, tripsData, idleAnalyticsData, selectedGroup, filteredVehicleIdSet])

    const yesterdayFleetActivity = useMemo(() => {
        return calculateTimeframeActivity(yesterdayMileageData, yesterdayTripsData, yesterdayIdleAnalyticsData)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [yesterdayMileageData, yesterdayTripsData, yesterdayIdleAnalyticsData, selectedGroup, filteredVehicleIdSet])

    // Percentage change helper
    const calculateComparisonChange = (todayVal, yesterdayVal, isLowerBetter = false) => {
        if (!isKnownNumber(todayVal) || !isKnownNumber(yesterdayVal)) {
            return {
                display: "—",
                type: "unavailable",
                tooltip: "Metric is unavailable for comparison",
            }
        }

        const t = Number(todayVal)
        const y = Number(yesterdayVal)

        if (y === 0) {
            if (t === 0) {
                return {
                    display: "0.0%",
                    type: "neutral",
                    tooltip: "No activity recorded in either period (both 0)",
                }
            }
            return {
                display: "New",
                type: "info",
                tooltip: "No activity recorded yesterday (zero baseline)",
            }
        }

        const pct = ((t - y) / y) * 100
        const sign = pct > 0 ? "+" : ""
        const display = `${sign}${pct.toFixed(1)}%`

        let type
        if (Math.abs(pct) < 0.05) {
            type = "neutral"
        } else if (isLowerBetter) {
            type = pct < 0 ? "positive" : "negative"
        } else {
            type = pct > 0 ? "positive" : "neutral"
        }


        return {
            display,
            pct,
            type,
            tooltip: `Yesterday: ${y.toLocaleString()} · Today: ${t.toLocaleString()}`,
        }
    }

    const comparisonMetrics = useMemo(() => {
        const distChange = calculateComparisonChange(todayFleetActivity.distanceRaw, yesterdayFleetActivity.distanceRaw, false)
        const tripsChange = calculateComparisonChange(todayFleetActivity.tripsRaw, yesterdayFleetActivity.tripsRaw, false)
        // Driving time is compared over the vehicles known on both days, so an unknown vehicle on either day does
        // not read as a change
        const drivingBoth = sumKnownInBoth(todayFleetActivity.drivingByVehicle, yesterdayFleetActivity.drivingByVehicle)
        const drivingChange = todayFleetActivity.drivingRaw === null || yesterdayFleetActivity.drivingRaw === null || drivingBoth.units === 0
            ? calculateComparisonChange(null, null, false)
            : calculateComparisonChange(Math.round(drivingBoth.a / 60), Math.round(drivingBoth.b / 60), false)
        if (drivingBoth.units > 0 && (todayFleetActivity.drivingUnknownVehicles > 0 || yesterdayFleetActivity.drivingUnknownVehicles > 0)) {
            drivingChange.tooltip = `${drivingChange.tooltip} min, over ${drivingBoth.units} vehicle(s) known on both days`
        }
        const idleChange = calculateComparisonChange(todayFleetActivity.idleRaw, yesterdayFleetActivity.idleRaw, true)

        return [
            {
                id: "distance",
                label: "Distance",
                unit: "km",
                today: todayFleetActivity.distance,
                yesterday: yesterdayFleetActivity.distance,
                change: distChange,
                partial: todayFleetActivity.distanceUnknownUnits > 0 ? `${todayFleetActivity.distanceUnknownUnits} unknown` : null,
            },
            {
                id: "trips",
                label: "Trips",
                unit: "trips",
                today: todayFleetActivity.trips,
                yesterday: yesterdayFleetActivity.trips,
                change: tripsChange,
                partial: null,
            },
            {
                id: "driving",
                label: "Driving time (engine on, moving)",
                labelTitle: DRIVING_LABEL_TITLE,
                unit: "hrs/mins",
                today: todayFleetActivity.driving,
                yesterday: yesterdayFleetActivity.driving,
                change: drivingChange,
                partial: todayFleetActivity.drivingUnknownVehicles > 0 ? `${todayFleetActivity.drivingUnknownVehicles} unknown` : null,
            },
            {
                id: "idle",
                label: "Idle Time",
                unit: "hrs/mins",
                today: todayFleetActivity.idle,
                yesterday: yesterdayFleetActivity.idle,
                change: idleChange,
                partial: todayFleetActivity.idleUnknownVehicles > 0 ? `${todayFleetActivity.idleUnknownVehicles} unknown` : null,
            },
        ]
    }, [todayFleetActivity, yesterdayFleetActivity])

    // ─── 8. Operational Insights: Idle Time by Fleet Group (No Double Counting) ───
    const idleTimeByGroupData = useMemo(() => {
        if (!idleAnalyticsData) return []
        let list = []
        if (Array.isArray(idleAnalyticsData.by_vehicle) && idleAnalyticsData.by_vehicle.length > 0) {
            const groupMap = new Map()
            const seenVehicles = new Set()
            idleAnalyticsData.by_vehicle.forEach(v => {
                const vid = String(v.vehicle_id)
                if (seenVehicles.has(vid)) return
                seenVehicles.add(vid)

                const gname = (v.group_name || v.group || "General Fleet").trim()
                if (gname.toLowerCase() === "all unit") return

                if (!groupMap.has(gname)) {
                    groupMap.set(gname, { name: gname, idleSeconds: 0, vehicleCount: 0 })
                }
                const item = groupMap.get(gname)
                item.vehicleCount += 1
                const secs = idleSecondsOf(v)
                if (isKnownNumber(secs)) {
                    item.idleSeconds += Number(secs)
                }
            })
            list = Array.from(groupMap.values()).map(g => ({
                name: g.name,
                idleHours: Number((g.idleSeconds / 3600).toFixed(1)),
                idleSeconds: g.idleSeconds,
                vehicleCount: g.vehicleCount,
            }))
        } else if (Array.isArray(idleAnalyticsData.by_group) && idleAnalyticsData.by_group.length > 0) {
            list = idleAnalyticsData.by_group
                .filter(g => g.group_name && g.group_name.trim().toLowerCase() !== "all unit")
                .map(g => {
                    const totalSec = isKnownNumber(g.total_idle_seconds) ? Number(g.total_idle_seconds) : null
                    const hours = totalSec !== null ? Number((totalSec / 3600).toFixed(1)) : null
                    return {
                        name: g.group_name,
                        idleHours: hours,
                        idleSeconds: totalSec,
                        vehicleCount: g.vehicle_count || 0,
                    }
                })
        }

        if (selectedGroup !== "ALL") {
            list = list.filter(g => g.name.trim().toLowerCase() === selectedGroup.trim().toLowerCase())
        }

        list.sort((a, b) => compareKnownDesc(a.idleHours, b.idleHours))
        return list
    }, [idleAnalyticsData, selectedGroup])

    // ─── 9. Fleet Performance: Vehicles with Highest Idle Time (Bar Chart) ────
    const highestIdleVehicles = useMemo(() => {
        if (!idleAnalyticsData?.by_vehicle || !Array.isArray(idleAnalyticsData.by_vehicle)) return []
        let list = [...idleAnalyticsData.by_vehicle]
        if (selectedGroup !== "ALL") {
            const sel = selectedGroup.trim().toLowerCase()
            list = list.filter(v => (v.group_name || "").trim().toLowerCase() === sel)
        }
        list = list.filter(v => isKnownNumber(v.total_idle_seconds) && Number(v.total_idle_seconds) > 0)
        list.sort((a, b) => compareKnownDesc(a.total_idle_seconds, b.total_idle_seconds))
        return list.slice(0, 8).map(v => {
            const secs = Number(v.total_idle_seconds)
            const hours = Number((secs / 3600).toFixed(1))
            const mins = Math.round(secs / 60)
            return {
                ...v,
                idleHours: hours,
                idleMinutes: mins,
                idleFormatted: formatHoursMins(mins),
                name: v.vehicle_name || `Unit ${v.vehicle_id}`,
            }
        })
    }, [idleAnalyticsData, selectedGroup])


    // ─── Selected Vehicle Object for Drawer ─────────────────────────────────
    const selectedVehicle = useMemo(() => {
        if (!selectedVehicleId) return null
        return vehicles.find(v => v.vehicle_id === selectedVehicleId || v.id === selectedVehicleId) || null
    }, [vehicles, selectedVehicleId])

    const statusSegments = [
        { key: "MOVING", label: "Moving", count: movingCount, dot: "bg-status-moving", note: "in motion" },
        { key: "IDLING", label: "Idling", count: idlingCount, dot: "bg-status-idling", note: "engine on" },
        { key: "STOPPED", label: "Stopped", count: stoppedCount, dot: "bg-status-stopped", note: "parked" },
        { key: "DELAYED", label: "Delayed", count: delayedCount, dot: "bg-status-delayed", note: "moving/idling, stale > 10 min" },
        { key: "OFFLINE", label: "Offline", count: offlineCount, dot: "bg-status-offline", note: "no signal" },
    ]

    const activityRows = [
        {
            label: "Distance",
            value: todayFleetActivity.distance,
            partial: todayFleetActivity.distanceUnknownUnits > 0 ? `${todayFleetActivity.distanceUnknownUnits} unit(s) unknown` : null,
            partialTitle: "Wialon reported no usable mileage for these units (unreadable, ambiguous name, or not covered by the report group); they are left out of the total",
        },
        { label: "Trips", value: todayFleetActivity.trips, partial: null },
        {
            label: "Driving time (engine on, moving)",
            labelTitle: DRIVING_LABEL_TITLE,
            value: todayFleetActivity.driving,
            partial: todayFleetActivity.drivingUnknownVehicles > 0 ? `${todayFleetActivity.drivingUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "Wialon \"In motion\" time is unreadable or unattributed for these vehicles, or their Engine Hours mileage disagrees with the Dashboard mileage; they are left out of the total",
        },
        {
            label: "Idle time",
            value: todayFleetActivity.idle,
            partial: todayFleetActivity.idleUnknownVehicles > 0 ? `${todayFleetActivity.idleUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "These vehicles' idle time could not be attributed; they are left out of the total",
        },
    ]

    const idleBarChartHeight = Math.max(220, idleTimeByGroupData.length * 36 + 24)
    const highestIdleChartHeight = Math.max(220, highestIdleVehicles.length * 34 + 24)


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
                <div className="space-y-5">
                    {error && <ErrorBanner message={`${error}. Retrying automatically.`} />}

                    {/* ═══════════════════════════════════════════════════════════════
                        01 — LIVE OVERVIEW (50/50 Desktop Layout)
                    ═══════════════════════════════════════════════════════════════ */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                        {/* Fleet Now — 50% */}
                        <SectionCard
                            className="flex flex-col h-full justify-between"
                            title={selectedGroup === "ALL" ? "Fleet Now" : `Fleet Now · ${selectedGroup}`}
                            actions={
                                <div className="flex items-center gap-1.5 text-xs text-slate-500 font-medium">
                                    <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" aria-hidden="true" />
                                    <span>Live status</span>
                                </div>
                            }
                        >
                            <div className="space-y-4">
                                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl bg-slate-50/60 p-3 border border-slate-200/60">
                                    <div>
                                        <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Total Fleet</span>
                                        <div className="mt-0.5 flex items-baseline gap-1.5">
                                            <Link
                                                to="/tracking"
                                                title="View all units in Live Tracking"
                                                className="text-2xl font-bold leading-none tracking-tight text-slate-900 tabular-nums hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 rounded-sm"
                                            >
                                                {totalVehiclesCount}
                                            </Link>
                                            <span className="text-xs text-slate-500">vehicles</span>
                                        </div>
                                    </div>
                                    <dl className="flex items-center gap-5 sm:gap-7">
                                        <div>
                                            <dt className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Active Today</dt>
                                            <dd className="mt-0.5 flex items-baseline gap-1.5">
                                                <span className="text-lg font-bold tabular-nums text-slate-900">{activeTodayCount}</span>
                                                <span className="text-xs font-medium tabular-nums text-slate-500">({pct(activeTodayCount)}%)</span>
                                            </dd>
                                        </div>
                                        <div>
                                            <dt className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Alerts</dt>
                                            <dd className="mt-0.5">
                                                <Link
                                                    to="/alerts"
                                                    title="View fleet alerts"
                                                    className={`group inline-flex items-baseline gap-1.5 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${alertsCount > 0 ? "text-rose-700" : "text-slate-900"}`}
                                                >
                                                    <span className="text-lg font-bold tabular-nums">{alertsCount}</span>
                                                    <span className="inline-flex items-center gap-0.5 text-xs text-slate-500 group-hover:text-brand font-medium">
                                                        {alertsCount > 0 ? "review" : "none open"}
                                                        <ArrowUpRight className="h-3 w-3" />
                                                    </span>
                                                </Link>
                                            </dd>
                                        </div>
                                    </dl>
                                </div>

                                {/* Status Segment Progress Bar */}
                                <div>
                                    <div className="flex h-2 gap-1 overflow-hidden rounded-full bg-slate-100 p-0.5" aria-hidden="true">
                                        {totalVehiclesCount > 0 && statusSegments.filter(seg => seg.count > 0).map(seg => (
                                            <div
                                                key={seg.key}
                                                className={`${seg.dot} rounded-full transition-[width] duration-300 motion-reduce:transition-none`}
                                                style={{ width: `${(seg.count / totalVehiclesCount) * 100}%` }}
                                                title={`${seg.label}: ${seg.count}`}
                                            />
                                        ))}
                                    </div>
                                </div>

                                {/* Status Cards Grid */}
                                <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-5 gap-2.5">
                                    {statusSegments.map(seg => (
                                        <Link
                                            key={seg.key}
                                            to={`/tracking?status=${seg.key}`}
                                            title={`View ${seg.label.toLowerCase()} vehicles`}
                                            className="group rounded-xl border border-slate-200/70 bg-white p-3 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                        >
                                            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
                                                <span className={`h-2 w-2 rounded-full ${seg.dot}`} aria-hidden="true" />
                                                <span className="truncate">{seg.label}</span>
                                            </div>
                                            <div className="mt-1.5 flex items-baseline gap-1.5">
                                                <span className={`text-xl font-bold tabular-nums tracking-tight ${seg.key === "OFFLINE" && seg.count > 0 ? "text-rose-700" : "text-slate-900"}`}>
                                                    {seg.count}
                                                </span>
                                                <span className="text-xs font-medium tabular-nums text-slate-400">{pct(seg.count)}%</span>
                                            </div>
                                            <span className="mt-0.5 text-[11px] text-slate-400 block leading-tight truncate">{seg.note}</span>
                                        </Link>
                                    ))}
                                </div>
                            </div>
                        </SectionCard>

                        {/* Today — 50% */}
                        <SectionCard
                            className="flex flex-col h-full justify-between"
                            title="Today"
                            actions={<span className="text-xs text-slate-500 font-medium">UAE 00:00 → now</span>}
                        >
                            <div className="grid flex-1 grid-cols-2 grid-rows-2 gap-2.5 h-full">
                                {activityRows.map(row => (
                                    <div
                                        key={row.label}
                                        className="flex flex-col justify-center rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300"
                                    >
                                        <dt className="text-xs font-medium text-slate-500" title={row.labelTitle}>{row.label}</dt>
                                        <dd className="mt-1">
                                            <span className="text-2xl font-bold tabular-nums text-slate-900 tracking-tight whitespace-nowrap">
                                                {row.value}
                                            </span>
                                            {row.partial && (
                                                <span className="mt-0.5 block truncate text-[11px] text-amber-700 font-medium" title={row.partialTitle}>
                                                    Partial: {row.partial}
                                                </span>
                                            )}
                                        </dd>
                                    </div>
                                ))}
                            </div>
                        </SectionCard>
                    </div>

                    {/* ═══════════════════════════════════════════════════════════════
                        ROW 2 — FLEET PERFORMANCE (Two equal-width cards on desktop)
                    ═══════════════════════════════════════════════════════════════ */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                        {/* 1. Vehicles with Highest Idle Time */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title="Vehicles with Highest Idle Time"
                            subtitle="Top accumulated engine idle today (Wialon)"
                            actions={
                                <Link
                                    to="/idle-analytics"
                                    className="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline underline-offset-2"
                                >
                                    Full ranking
                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                </Link>
                            }
                        >
                            {highestIdleVehicles.length === 0 ? (
                                <div className="flex h-[240px] items-center justify-center gap-2 text-xs text-slate-500">
                                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                                    No idle time recorded for vehicles today.
                                </div>
                            ) : (
                                <div className="max-h-[260px] overflow-y-auto pr-1">
                                    <div style={{ height: `${highestIdleChartHeight}px` }}>
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart
                                                data={highestIdleVehicles}
                                                layout="vertical"
                                                margin={{ top: 4, right: 60, left: 4, bottom: 4 }}
                                            >
                                                <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
                                                <XAxis
                                                    type="number"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    tick={TICK}
                                                    unit=" h"
                                                />
                                                <YAxis
                                                    type="category"
                                                    dataKey="name"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    width={110}
                                                    interval={0}
                                                    tick={CATEGORY_TICK}
                                                />
                                                <Tooltip
                                                    cursor={TOOLTIP_CURSOR}
                                                    formatter={(val, _name, item) => [
                                                        `${item?.payload?.idleFormatted} (${val} hrs) · ${item?.payload?.group_name || "General Fleet"}`,
                                                        "Accumulated Idle",
                                                    ]}
                                                    contentStyle={TOOLTIP_STYLE}
                                                />
                                                <Bar
                                                    dataKey="idleHours"
                                                    fill={BRAND_BLUE}
                                                    radius={[0, 3, 3, 0]}
                                                    maxBarSize={16}
                                                    onClick={(entry) => {
                                                        if (entry?.vehicle_id) setSelectedVehicleId(entry.vehicle_id)
                                                    }}
                                                    cursor="pointer"
                                                >
                                                    <LabelList
                                                        dataKey="idleFormatted"
                                                        position="right"
                                                        style={{ fontSize: 11, fill: "#475569", fontWeight: 600 }}
                                                    />
                                                </Bar>
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            )}
                        </SectionCard>

                        {/* 2. Currently Idling */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title={selectedGroup === "ALL" ? "Currently Idling" : `Currently Idling · ${selectedGroup}`}
                            subtitle="Live vehicles with ignition on and zero speed"
                            actions={
                                <div className="flex items-center gap-2">
                                    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-amber-800">
                                        {idlingCount} idling
                                    </span>
                                    <Link
                                        to="/tracking?status=IDLING"
                                        className="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline underline-offset-2"
                                    >
                                        Live tracking
                                        <ArrowUpRight className="h-3.5 w-3.5" />
                                    </Link>
                                </div>
                            }
                        >
                            {idlingCount === 0 ? (
                                <div className="flex h-[240px] flex-col items-center justify-center p-6 text-center">
                                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 mb-2">
                                        <CheckCircle2 className="h-5 w-5" />
                                    </div>
                                    <h4 className="text-xs font-semibold text-slate-800">No Vehicles Currently Idling</h4>
                                    <p className="mt-1 text-[11px] text-slate-500 max-w-xs leading-relaxed">
                                        All active fleet units are currently moving in transit or parked with engines turned off.
                                    </p>
                                </div>
                            ) : (
                                <div className="max-h-[260px] overflow-y-auto pr-1">
                                    <ul className="divide-y divide-slate-100">
                                        {idlingVehicles.map((vehicle) => {
                                            const idleSecs = vehicle.idle_duration
                                            const idleDisplay = vehicle.idle_duration_formatted ||
                                                (isKnownNumber(idleSecs) && idleSecs > 0 ? formatHoursMins(Math.round(idleSecs / 60)) : "Just started")

                                            const lastUpdateText = isKnownNumber(vehicle.seconds_since_update)
                                                ? vehicle.seconds_since_update < 60
                                                    ? "Updated just now"
                                                    : `${Math.floor(vehicle.seconds_since_update / 60)}m ago`
                                                : null

                                            return (
                                                <li key={vehicle.vehicle_id}>
                                                    <button
                                                        type="button"
                                                        onClick={() => setSelectedVehicleId(vehicle.vehicle_id)}
                                                        className="group flex w-full items-center justify-between gap-3 p-2.5 rounded-xl transition-all duration-150 hover:-translate-y-0.5 hover:bg-slate-50/80 hover:shadow-[0_2px_4px_-1px_rgba(15,23,42,0.06)] cursor-pointer text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                                        title={`View details for ${vehicle.vehicle_name}`}
                                                    >
                                                        <div className="min-w-0 flex-1">
                                                            <div className="flex items-center gap-2">
                                                                <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse shrink-0" aria-hidden="true" />
                                                                <span className="font-semibold text-xs text-slate-900 group-hover:text-brand truncate">
                                                                    {vehicle.vehicle_name}
                                                                </span>
                                                                {vehicle.registration_plate && (
                                                                    <span className="text-[10px] text-slate-400 font-mono">
                                                                        {vehicle.registration_plate}
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
                                                                <span className="truncate">{vehicle.group || "General Fleet"}</span>
                                                                {lastUpdateText && (
                                                                    <>
                                                                        <span className="text-slate-300">·</span>
                                                                        <span className="text-slate-400">{lastUpdateText}</span>
                                                                    </>
                                                                )}
                                                            </div>
                                                        </div>

                                                        <div className="shrink-0 text-right">
                                                            <span className="block text-xs font-bold text-amber-800 tabular-nums">
                                                                {idleDisplay}
                                                            </span>
                                                            <span className="block text-[10px] text-slate-400">
                                                                current idle
                                                            </span>
                                                        </div>
                                                    </button>
                                                </li>
                                            )
                                        })}
                                    </ul>
                                </div>
                            )}
                        </SectionCard>
                    </div>

                    {/* ═══════════════════════════════════════════════════════════════
                        ROW 3 — OPERATIONAL INSIGHTS (Two equal-width cards on desktop)
                    ═══════════════════════════════════════════════════════════════ */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                        {/* 1. Idle Time by Fleet Group */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title="Idle Time by Fleet Group"
                            subtitle="Total idle hours across Wialon fleet groups"
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
                            {idleTimeByGroupData.length === 0 ? (
                                <div className="flex h-[240px] items-center justify-center text-xs text-slate-400">
                                    No idle time recorded for this selection today.
                                </div>
                            ) : (
                                <div className="max-h-[260px] overflow-y-auto pr-1">
                                    <div style={{ height: `${idleBarChartHeight}px` }}>
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart
                                                data={idleTimeByGroupData}
                                                layout="vertical"
                                                margin={{ top: 4, right: 24, left: 4, bottom: 4 }}
                                            >
                                                <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
                                                <XAxis
                                                    type="number"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    tick={TICK}
                                                    unit=" h"
                                                />
                                                <YAxis
                                                    type="category"
                                                    dataKey="name"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    width={150}
                                                    interval={0}
                                                    tick={CATEGORY_TICK}
                                                />
                                                <Tooltip
                                                    cursor={TOOLTIP_CURSOR}
                                                    formatter={(val, _name, item) => [
                                                        `${val !== null ? `${val} hrs` : "Unknown"} · ${item?.payload?.vehicleCount || 0} unit(s)`,
                                                        "Idle Duration",
                                                    ]}
                                                    contentStyle={TOOLTIP_STYLE}
                                                />
                                                <Bar
                                                    dataKey="idleHours"
                                                    fill={BRAND_BLUE}
                                                    radius={[0, 3, 3, 0]}
                                                    maxBarSize={16}
                                                />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            )}
                        </SectionCard>

                        {/* 2. Today vs. Yesterday */}
                        <SectionCard
                            className="flex flex-col h-full justify-between"
                            title="Today vs. Yesterday"
                            subtitle="Side-by-side activity and volume comparison"
                            actions={
                                <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                                    <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" aria-hidden="true" />
                                    Today in progress
                                </span>
                            }
                        >
                            <div className="flex flex-col justify-between h-full space-y-3">
                                <div className="overflow-x-auto">
                                    <table className="w-full text-left border-collapse">
                                        <thead>
                                            <tr className="border-b border-slate-100 text-[11px] uppercase tracking-wider text-slate-400">
                                                <th className="pb-2 font-medium">Metric</th>
                                                <th className="pb-2 text-right font-medium">
                                                    <span className="inline-flex items-center gap-1 justify-end">
                                                        Today
                                                        <span className="text-[10px] text-amber-600 lowercase font-normal">(so far)</span>
                                                    </span>
                                                </th>
                                                <th className="pb-2 text-right font-medium">
                                                    <span className="inline-flex items-center gap-1 justify-end">
                                                        Yesterday
                                                        <span className="text-[10px] text-slate-400 lowercase font-normal">(24h)</span>
                                                    </span>
                                                </th>
                                                <th className="pb-2 text-right font-medium">Change</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100 text-xs">
                                            {comparisonMetrics.map((m) => (
                                                <tr key={m.id} className="transition-colors hover:bg-slate-50/60">
                                                    <td className="py-2.5 font-medium text-slate-700">
                                                        <div>
                                                            <span className="text-slate-900 font-semibold" title={m.labelTitle}>{m.label}</span>
                                                            {m.partial && (
                                                                <span className="block text-[10px] text-amber-700">
                                                                    {m.partial}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </td>
                                                    <td className="py-2.5 text-right font-bold text-slate-900 tabular-nums">
                                                        {m.today}
                                                    </td>
                                                    <td className="py-2.5 text-right font-semibold text-slate-600 tabular-nums">
                                                        {m.yesterday}
                                                    </td>
                                                    <td className="py-2.5 text-right tabular-nums">
                                                        <span
                                                            title={m.change.tooltip}
                                                            className={`inline-flex items-center justify-end rounded px-1.5 py-0.5 text-[11px] font-semibold ${
                                                                m.change.type === "positive"
                                                                    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                                                                    : m.change.type === "negative"
                                                                    ? "bg-amber-50 text-amber-700 border border-amber-200"
                                                                    : m.change.type === "info"
                                                                    ? "bg-sky-50 text-sky-700 border border-sky-200"
                                                                    : "bg-slate-100 text-slate-600 border border-slate-200"
                                                            }`}
                                                        >
                                                            {m.change.display}
                                                        </span>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>

                                <div className="rounded-xl bg-slate-50/60 p-2.5 text-[11px] leading-relaxed text-slate-500 border border-slate-200/60">
                                    <span>
                                        <strong>Note:</strong> Today reflects UAE 00:00 → current time (in progress). Yesterday reflects the full 24h UAE calendar day (00:00–23:59:59).
                                    </span>
                                </div>
                            </div>
                        </SectionCard>
                    </div>

                </div>
            )}
        </AppShell>
    )
}

export default Dashboard
