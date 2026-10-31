import { Suspense, useEffect, useMemo, useState, useRef } from "react"
import { Link } from "react-router-dom"
import {
    Layers,
    ArrowUpRight,
    CheckCircle2,
} from "lucide-react"


import {
    getVehicles,
    getDashboardMileage,
    getFleetTripsAnalytics,
    getFleetIdleAnalytics,
    peekFleetIdleAnalytics,
    peekFleetTripsAnalytics,
    peekVehicles,
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
import FreshnessBadge from "../components/Common/FreshnessBadge"
import SectionCard from "../components/Common/SectionCard"
import FilterSelect from "../components/Common/FilterSelect"
import { ErrorBanner } from "../components/Common/StateViews"
import { formatUAEDateTime, formatUAETime, getUAETodayBounds, getUAETodayStr } from "../utils/datetime"
import { groupNamesOf, idleDurationStatus, inGroup } from "../utils/fleetRules"
import { isTelemetryStale, normalizeVehicleStatus } from "../utils/vehicleStatus"
import {
    EARLIER_RESULT_NOTICE, OFFLINE_RULE_TITLE, STALE_LIVE_FLEET_MESSAGE, activeTodaySummary, forUaeDay, formatKm2,
    liveFleetRead, offlineNote, previousUaeDay, reportNotice, roundKm2, sectionState, todayMileageState,
    yesterdayCellStates,
} from "../utils/fleetNow"
import { LazyVehicleDetailDrawer as VehicleDetailDrawer } from "../components/Vehicles/lazyVehicleDetailDrawer"
import LiveIdleTimer from "../components/Common/LiveIdleTimer"
import {
    CATEGORY_TICK,
    GRID_STROKE,
    TICK,
    TOOLTIP_CURSOR,
    TOOLTIP_STYLE,
} from "../components/Common/chartTheme"
import {
    compareKnownDesc, drivingSecondsByVehicle, formatKm, idleSecondsOf, isKnownNumber, sumKnown, sumKnownInBoth,
    yesterdayLoadOutcome,
} from "../utils/knownTotals"

// A partial or stale "yesterday" report is re-read after this long instead of staying partial until midnight
const YESTERDAY_PARTIAL_RETRY_MS = 180000

const DRIVING_LABEL_TITLE = "Wialon Engine Hours report \"In motion\" time per vehicle (engine on and moving). Not total "
    + "engine hours: Wialon may classify some engine-on time as neither moving nor idling. A vehicle is left out as "
    + "unknown when its \"In motion\" value is missing, unreadable or cannot be attributed."

// Legacy mileage formatter contract: formatter={(val) => [formatKm(val) ?? "Unknown (unreadable)", "Mileage"]}

const BRAND_BLUE = "#33758F"

// Placeholder for a value whose request has not answered yet (never shown as 0 or "—")
function Pending({ className = "h-5 w-12" }) {
    return <span role="status" aria-label="Loading" className={`inline-block animate-pulse rounded bg-slate-100 align-middle motion-reduce:animate-none ${className}`} />
}

// Body of a section whose data is not available yet: a skeleton while pending, a notice once its request failed
function SectionPlaceholder({ state, label }) {
    if (state === "failed") {
        return (
            <div role="alert" className="flex h-[240px] items-center justify-center px-6 text-center text-xs text-slate-500">
                {label} could not be loaded. Retrying automatically.
            </div>
        )
    }
    return (
        <div role="status" aria-label={`Loading ${label}`} className="flex h-[240px] flex-col justify-center gap-3 px-2">
            {[80, 65, 50, 40].map(w => (
                <span key={w} className="h-3 animate-pulse rounded bg-slate-100 motion-reduce:animate-none" style={{ width: `${w}%` }} />
            ))}
        </div>
    )
}


function Dashboard() {
    // ─── Core State ─────────────────────────────────────────────────────────
    // Responses still in the client cache from an earlier visit are shown at once, marked as not refreshed yet
    // (vehiclesFresh / earlier below), while every section refetches (live vehicles up to STALE_LIVE_MS old,
    // reports up to STALE_REPORT_MS)
    const [cached] = useState(() => ({
        vehicles: peekVehicles(),
        trips: peekFleetTripsAnalytics({ timeframe: "today" }),
        idle: peekFleetIdleAnalytics({ timeframe: "today", include_events: false }),
        yesterdayTrips: peekFleetTripsAnalytics({ timeframe: "yesterday" }),
        yesterdayIdle: peekFleetIdleAnalytics({ timeframe: "yesterday", include_events: false }),
    }))
    const [vehicles, setVehicles] = useState(() => cached.vehicles?.data?.vehicles || [])
    // False until /api/vehicles succeeds once: an empty list before then is unavailable data, not a fleet of 0
    const [vehiclesLoaded, setVehiclesLoaded] = useState(() => Boolean(cached.vehicles))
    // False while the vehicles shown are an earlier visit's cached response
    const [vehiclesFresh, setVehiclesFresh] = useState(false)
    const [error, setError] = useState(null)
    // When the shown fleet was read from Wialon (the backend's time, never the browser's clock)
    const [lastUpdated, setLastUpdated] = useState(() => (cached.vehicles ? liveFleetRead(cached.vehicles.data).retrievedAt : null))
    // Freshness of the live fleet shown (backend block) and whether the latest live request failed outright
    const [liveFreshness, setLiveFreshness] = useState(() => cached.vehicles?.data?.freshness ?? null)
    const [liveFailed, setLiveFailed] = useState(false)

    // Global Vehicle Group Filter (Affects all sections on the page)
    const [selectedGroup, setSelectedGroup] = useState("ALL")


    // Today's trips data
    const [tripsData, setTripsData] = useState(() => cached.trips?.data ?? null)

    // Today's verified idle analytics data
    const [idleAnalyticsData, setIdleAnalyticsData] = useState(() => cached.idle?.data ?? null)

    // Today's dedicated mileage data (the latest successful response) and whether the latest refresh failed
    const [todayMileageData, setTodayMileageData] = useState(null)
    const [todayMileageFailed, setTodayMileageFailed] = useState(false)

    // Yesterday's trips, idle, and mileage data
    const [yesterdayTripsData, setYesterdayTripsData] = useState(() => cached.yesterdayTrips?.data ?? null)
    const [yesterdayIdleAnalyticsData, setYesterdayIdleAnalyticsData] = useState(() => cached.yesterdayIdle?.data ?? null)
    const [yesterdayMileageData, setYesterdayMileageData] = useState(null)

    // Whether the latest request of each Dashboard report failed (the last good data, if any, stays shown)
    const [auxFailed, setAuxFailed] = useState({
        trips: false, idle: false, yesterdayTrips: false, yesterdayIdle: false, yesterdayMileage: false,
    })
    // Reports still showing an earlier visit's cached response: labelled until their first request of this visit succeeds
    const [earlier, setEarlier] = useState(() => ({
        trips: Boolean(cached.trips), idle: Boolean(cached.idle),
        yesterdayTrips: Boolean(cached.yesterdayTrips), yesterdayIdle: Boolean(cached.yesterdayIdle),
    }))
    // Runs the report poll now (Retry), replacing its pending timer
    const auxRetryRef = useRef(null)

    const auxSeqRef = useRef(0)
    // UAE day (its 00:00 epoch) whose "yesterday" reports were loaded: a closed day does not change, so its three
    // reports are read once per day instead of on every 30 s poll
    const yesterdayLoadedForRef = useRef(null)
    // Set when the loaded yesterday reports were partial or stale: they are re-read after this time (epoch ms)
    const yesterdayRetryAtRef = useRef(null)


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
                    // A stale read is the backend's last good fleet (Wialon failed): shown with its own read time,
                    // never as live
                    const read = liveFleetRead(data)
                    setVehicles(data.vehicles || [])
                    setVehiclesLoaded(true)
                    setVehiclesFresh(!read.stale)
                    setLastUpdated(read.retrievedAt)
                    setLiveFreshness(data.freshness ?? null)
                    setLiveFailed(false)
                    setError(read.stale
                        ? `${STALE_LIVE_FLEET_MESSAGE} Showing positions read at ${read.retrievedAt ? formatUAETime(read.retrievedAt) : "an earlier time"}`
                        : null)
                }
            } catch (err) {
                if (mounted) {
                    console.error("Failed to load vehicle data:", err)
                    setError("Failed to load vehicle data")
                    setLiveFailed(true)
                }
            } finally {
                isFetching = false
                if (mounted) {
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
                // Records whether the latest request of a report failed; the failure is re-thrown for allSettled
                const tracked = (key, promise) => promise.then(
                    res => {
                        if (isCurrent()) {
                            setAuxFailed(f => (f[key] ? { ...f, [key]: false } : f))
                            setEarlier(e => (e[key] ? { ...e, [key]: false } : e))
                        }
                        return res
                    },
                    err => { if (isCurrent()) setAuxFailed(f => (f[key] ? f : { ...f, [key]: true })); throw err },
                )
                const dayKey = getUAETodayBounds().from_time
                const retryAt = yesterdayRetryAtRef.current
                const loadYesterday = yesterdayLoadedForRef.current !== dayKey || (retryAt !== null && Date.now() >= retryAt)
                const yesterdayRequests = loadYesterday ? [
                    tracked("yesterdayTrips", getFleetTripsAnalytics({ timeframe: "yesterday" })).then(res => { if (isCurrent()) setYesterdayTripsData(res); return res }),
                    tracked("yesterdayIdle", getFleetIdleAnalytics({ timeframe: "yesterday", include_events: false })).then(res => { if (isCurrent()) setYesterdayIdleAnalyticsData(res); return res }),
                    tracked("yesterdayMileage", getDashboardMileage("yesterday")).then(res => { if (isCurrent()) setYesterdayMileageData(res); return res }),
                ] : []
                const todayRequests = [
                    tracked("trips", getFleetTripsAnalytics({ timeframe: "today" })).then(res => { if (isCurrent()) setTripsData(res) }),
                    tracked("idle", getFleetIdleAnalytics({ timeframe: "today", include_events: false })).then(res => { if (isCurrent()) setIdleAnalyticsData(res) }),
                    getDashboardMileage("today").then(
                        res => { if (isCurrent()) { setTodayMileageData(res); setTodayMileageFailed(false) } },
                        err => { if (isCurrent()) setTodayMileageFailed(true); throw err },
                    ),
                ]
                const results = await Promise.allSettled([...todayRequests, ...yesterdayRequests])
                // Marked loaded only when every yesterday report succeeded and none came back partial or stale; a
                // failed one is retried on the next poll, a partial or stale one after YESTERDAY_PARTIAL_RETRY_MS
                if (loadYesterday && isCurrent()) {
                    const outcome = yesterdayLoadOutcome(results, todayRequests.length)
                    if (outcome.loaded) {
                        yesterdayLoadedForRef.current = dayKey
                        yesterdayRetryAtRef.current = outcome.complete ? null : Date.now() + YESTERDAY_PARTIAL_RETRY_MS
                    }
                }
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
        auxRetryRef.current = () => {
            if (!active || isFetching) return
            if (timerId) clearTimeout(timerId)
            fetchAuxiliaryData()
        }
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
        return normalizedVehicles.filter(v => inGroup(v, selectedGroup))
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
    // Stale telemetry: the last known status is counted above and the stale vehicles are noted on their card
    const staleMovingCount = useMemo(() => movingVehicles.filter(isTelemetryStale).length, [movingVehicles])
    const staleIdlingCount = useMemo(() => idlingVehicles.filter(isTelemetryStale).length, [idlingVehicles])

    const movingCount = movingVehicles.length
    const idlingCount = idlingVehicles.length
    const stoppedCount = stoppedVehicles.length
    const offlineCount = offlineVehicles.length

    const pct = (count) => (totalVehiclesCount > 0 ? Math.round((count / totalVehiclesCount) * 100) : 0)

    // Report payloads apply only to their own UAE day. Re-evaluated on every render (the 10 s vehicle poll re-renders):
    // after midnight a payload loaded the day before drops out (its section shows pending) until the new day's arrives,
    // instead of being shown as today's (or yesterday's).
    const todayStr = getUAETodayStr()
    const yesterdayStr = previousUaeDay(todayStr)
    const todayTrips = forUaeDay(tripsData, todayStr)
    const todayIdle = forUaeDay(idleAnalyticsData, todayStr, d => d.timeframe?.from_time)
    const yesterdayTrips = forUaeDay(yesterdayTripsData, yesterdayStr)
    const yesterdayIdle = forUaeDay(yesterdayIdleAnalyticsData, yesterdayStr, d => d.timeframe?.from_time)
    const yesterdayMileage = forUaeDay(yesterdayMileageData, yesterdayStr, d => d.effective_from)

    // Active Today: activity evidence inside today's UAE day; vehicles without known data are counted as unknown
    const activeToday = useMemo(() => activeTodaySummary(filteredVehicles, {
        mileageData: todayMileageData, tripsData, todayStr,
    }), [filteredVehicles, todayMileageData, tripsData, todayStr])
    const activeTodayCount = activeToday.active


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

        // Trips: known counts are summed and the vehicles with an unknown count left out and counted, never 0
        let trips = null
        let tripsUnknownVehicles = 0
        if (tripsDataPayload?.vehicles) {
            const filteredTripVehs = selectedGroup === "ALL"
                ? tripsDataPayload.vehicles
                : tripsDataPayload.vehicles.filter(tv => filteredVehicleIdSet.has(String(tv.vehicle_id)))
            const tripSum = sumKnown(filteredTripVehs, tv => tv.trip_count)
            trips = tripSum.allUnknown ? null : tripSum.total
            tripsUnknownVehicles = tripSum.unknown
        }

        // Driving time (engine on, moving): Wialon Engine Hours "In motion" per unit, as reported. Unknown units are
        // left out and counted, never 0.
        const drivingByVehicle = drivingSecondsByVehicle(
            idleDataPayload, id => selectedGroup === "ALL" || filteredVehicleIdSet.has(id),
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

        // Trips report precision (0.01 km per unit), not whole km, so the total reads like the report
        return {
            distance: distanceAllUnknown ? "—" : formatKm2(distanceKm),
            distanceRaw: distanceAllUnknown ? null : roundKm2(distanceKm),
            distanceUnknownUnits,
            trips: trips === null ? "—" : trips.toLocaleString(),
            tripsRaw: trips,
            tripsUnknownVehicles,
            driving: drivingKnown ? formatHoursMins(drivingMinutes) : "—",
            drivingRaw: drivingKnown ? drivingMinutes : null,
            drivingUnknownVehicles,
            drivingByVehicle,
            idle: idleAllUnknown ? "—" : formatHoursMins(idleMinutes),
            idleRaw: idleAllUnknown ? null : idleMinutes,
            idleUnknownVehicles,
        }
    }

    // Re-evaluated on every render (the 10 s vehicle poll re-renders), so a previous day's result drops out at midnight
    const todayMileage = todayMileageState(todayMileageData, { refreshFailed: todayMileageFailed, todayStr })
    const todayFleetActivity = useMemo(() => {
        return calculateTimeframeActivity(todayMileage.data, todayTrips, todayIdle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [todayMileage.data, todayTrips, todayIdle, selectedGroup, filteredVehicleIdSet])

    const yesterdayFleetActivity = useMemo(() => {
        return calculateTimeframeActivity(yesterdayMileage, yesterdayTrips, yesterdayIdle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [yesterdayMileage, yesterdayTrips, yesterdayIdle, selectedGroup, filteredVehicleIdSet])

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
                partial: todayFleetActivity.tripsUnknownVehicles > 0 ? `${todayFleetActivity.tripsUnknownVehicles} unknown` : null,
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
        if (!todayIdle) return []
        let list = []
        if (Array.isArray(todayIdle.by_vehicle) && todayIdle.by_vehicle.length > 0) {
            const groupMap = new Map()
            const seenVehicles = new Set()
            todayIdle.by_vehicle.forEach(v => {
                const vid = String(v.vehicle_id)
                if (seenVehicles.has(vid)) return
                seenVehicles.add(vid)

                // Group-membership totals: a vehicle in several groups counts in each (like Fleet Now's group filter)
                const names = groupNamesOf(v)
                for (const gname of names.length ? names : ["General Fleet"]) {
                    if (gname.toLowerCase() === "all unit") continue
                    if (!groupMap.has(gname)) {
                        groupMap.set(gname, { name: gname, idleSeconds: 0, vehicleCount: 0, unknownCount: 0 })
                    }
                    const item = groupMap.get(gname)
                    item.vehicleCount += 1
                    const secs = idleSecondsOf(v)
                    // Unknown idle is left out of the bar and counted, never added as 0
                    if (isKnownNumber(secs)) {
                        item.idleSeconds += Number(secs)
                    } else {
                        item.unknownCount += 1
                    }
                }
            })
            list = Array.from(groupMap.values()).map(g => {
                const allUnknown = g.unknownCount === g.vehicleCount
                return {
                    name: g.name,
                    idleHours: allUnknown ? null : Number((g.idleSeconds / 3600).toFixed(1)),
                    idleSeconds: allUnknown ? null : g.idleSeconds,
                    vehicleCount: g.vehicleCount,
                    unknownCount: g.unknownCount,
                }
            })
        } else if (Array.isArray(todayIdle.by_group) && todayIdle.by_group.length > 0) {
            list = todayIdle.by_group
                .filter(g => g.group_name && g.group_name.trim().toLowerCase() !== "all unit")
                .map(g => {
                    const totalSec = isKnownNumber(g.total_idle_seconds) ? Number(g.total_idle_seconds) : null
                    const hours = totalSec !== null ? Number((totalSec / 3600).toFixed(1)) : null
                    return {
                        name: g.group_name,
                        idleHours: hours,
                        idleSeconds: totalSec,
                        vehicleCount: g.vehicle_count || 0,
                        // The backend leaves a group total unknown (null) when any member is unknown
                        unknownCount: totalSec === null ? (g.vehicle_count || 0) : 0,
                    }
                })
        }

        if (selectedGroup !== "ALL") {
            list = list.filter(g => g.name.trim().toLowerCase() === selectedGroup.trim().toLowerCase())
        }

        list.sort((a, b) => compareKnownDesc(a.idleHours, b.idleHours))
        return list
    }, [todayIdle, selectedGroup])

    // ─── 9. Fleet Performance: Vehicles with Highest Idle Time (Bar Chart) ────
    const highestIdleVehicles = useMemo(() => {
        if (!todayIdle?.by_vehicle || !Array.isArray(todayIdle.by_vehicle)) return []
        let list = [...todayIdle.by_vehicle]
        if (selectedGroup !== "ALL") {
            list = list.filter(v => inGroup(v, selectedGroup))
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
    }, [todayIdle, selectedGroup])


    // ─── 10. Fleet Performance: Top Vehicles by Mileage ───────────────────────
    const topMileageVehicles = useMemo(() => {
        let sourceUnits = []
        if (todayMileage.data?.all_units && todayMileage.data.all_units.length > 0) {
            sourceUnits = todayMileage.data.all_units.map(u => ({
                vehicle_id: u.vehicle_id,
                vehicle_name: u.vehicle_name,
                group: u.group,
                groups: u.groups,
                mileage: u.mileage,
            }))
        } else if (todayTrips?.vehicles && todayTrips.vehicles.length > 0) {
            sourceUnits = todayTrips.vehicles.map(tv => ({
                vehicle_id: tv.vehicle_id,
                vehicle_name: tv.vehicle_name,
                group: tv.group,
                groups: [tv.group],
                mileage: tv.gps_mileage_km,
            }))
        }
        if (!sourceUnits || sourceUnits.length === 0) return []

        let list = sourceUnits
        if (selectedGroup !== "ALL") {
            list = list.filter(u => {
                if (filteredVehicleIdSet.has(String(u.vehicle_id))) return true
                return inGroup(u, selectedGroup)
            })
        }

        list = list.filter(u => isKnownNumber(u.mileage) && Number(u.mileage) > 0)
        list.sort((a, b) => compareKnownDesc(a.mileage, b.mileage))

        return list.slice(0, 8).map((u, index) => {
            const distKm = Number(u.mileage)
            const name = u.vehicle_name || `Unit ${u.vehicle_id}`
            return {
                ...u,
                rank: index + 1,
                reportedMileage: distKm,
                formattedMileage: formatKm(distKm) ?? `${distKm.toFixed(1)} km`,
                name,
            }
        })
    }, [todayMileage.data, todayTrips, selectedGroup, filteredVehicleIdSet])

    // Currently Idling: only vehicles whose existing current idle-duration value is > 5 minutes (300s)
    const MIN_CURRENT_IDLE_SECONDS = 300
    const currentlyIdlingVehicles = useMemo(() => {
        return idlingVehicles.filter(v => {
            const status = idleDurationStatus(v)
            if (status === "unknown") return false
            const secs = Number(v.idle_duration)
            return Number.isFinite(secs) && secs > MIN_CURRENT_IDLE_SECONDS
        })
    }, [idlingVehicles])

    // ─── Selected Vehicle Object for Drawer ─────────────────────────────────
    const selectedVehicle = useMemo(() => {
        if (!selectedVehicleId) return null
        return vehicles.find(v => v.vehicle_id === selectedVehicleId || v.id === selectedVehicleId) || null
    }, [vehicles, selectedVehicleId])

    const statusSegments = [
        { key: "MOVING", label: "Moving", count: movingCount, dot: "bg-status-moving", note: staleMovingCount > 0 ? `${staleMovingCount} stale` : null },
        { key: "IDLING", label: "Idling", count: idlingCount, dot: "bg-status-idling", note: staleIdlingCount > 0 ? `${staleIdlingCount} stale` : null },
        { key: "STOPPED", label: "Stopped", count: stoppedCount, dot: "bg-status-stopped", note: null },
        { key: "OFFLINE", label: "Offline", count: offlineCount, dot: "bg-status-offline", note: offlineNote(offlineVehicles), title: OFFLINE_RULE_TITLE },
    ]
    // The Tracking page has no group filter: with a group selected its links open the whole fleet, and say so
    const trackingScopeNote = selectedGroup === "ALL" ? "" : " (Live Tracking shows the whole fleet, not only this group)"
    // Before the first vehicles response: a skeleton while it loads, "—" once it failed (never a fleet of 0)
    const fleetValue = (count) => (vehiclesLoaded ? count : error ? "—" : <Pending />)

    // Per-report section state (pending / failed / ready); Distance falls back to the trips report like its value
    // Day-guarded data only: a previous day's payload is pending, never shown as this day's
    const tripsState = sectionState(todayTrips, auxFailed.trips)
    const idleState = sectionState(todayIdle, auxFailed.idle)
    const distanceState = sectionState(todayMileage.data ?? todayTrips, todayMileageFailed && auxFailed.trips)
    const topMileageState = sectionState(todayMileage.data ?? todayTrips, todayMileageFailed && auxFailed.trips)
    // Each Yesterday cell follows its own report request (pending / failed / earlier-visit label)
    const yesterdayCellById = yesterdayCellStates({ mileage: yesterdayMileage, trips: yesterdayTrips, idle: yesterdayIdle }, {
        failed: { mileage: auxFailed.yesterdayMileage, trips: auxFailed.yesterdayTrips, idle: auxFailed.yesterdayIdle },
        earlier: { trips: earlier.yesterdayTrips, idle: earlier.yesterdayIdle },
    })
    const todayStateById = { distance: distanceState, trips: tripsState, driving: idleState, idle: idleState }
    // Distance and the top-mileage chart show an earlier visit's trips only while today's mileage has not answered
    const distanceEarlier = !todayMileage.data && earlier.trips
    // Subtitle of a chart section still showing an earlier visit's cached response
    const earlierSubtitle = (state, isEarlier) => (state === "ready" && isEarlier ? EARLIER_RESULT_NOTICE.text : undefined)

    const activityRows = [
        {
            label: "Distance",
            labelTitle: todayMileage.data
                ? `Wialon Trips report mileage, summed per unit: ${formatUAEDateTime(todayMileage.data.effective_from)} → `
                    + `${formatUAEDateTime(todayMileage.data.effective_to)} (UAE). A report run over a different window `
                    + "differs; Engine Hours mileage is measured differently and is not used here."
                : "Wialon Trips report mileage, summed per unit, for today (UAE)",
            value: todayFleetActivity.distance,
            state: distanceState,
            notice: todayMileage.notice ?? reportNotice(distanceState, { earlier: distanceEarlier }),
            partial: todayFleetActivity.distanceUnknownUnits > 0 ? `${todayFleetActivity.distanceUnknownUnits} unit(s) unknown` : null,
            partialTitle: "Wialon reported no usable mileage for these units (unreadable, ambiguous name, or not covered by the report group); they are left out of the total",
        },
        {
            label: "Trips",
            value: todayFleetActivity.trips,
            state: tripsState,
            notice: reportNotice(tripsState, { failed: auxFailed.trips, earlier: earlier.trips }),
            partial: todayFleetActivity.tripsUnknownVehicles > 0 ? `${todayFleetActivity.tripsUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "Wialon reported no usable trip count for these vehicles (unreadable, ambiguous name, or not covered by the report group); they are left out of the total",
        },
        {
            label: "Driving time (engine on, moving)",
            labelTitle: DRIVING_LABEL_TITLE,
            value: todayFleetActivity.driving,
            state: idleState,
            notice: reportNotice(idleState, { failed: auxFailed.idle, earlier: earlier.idle }),
            partial: todayFleetActivity.drivingUnknownVehicles > 0 ? `${todayFleetActivity.drivingUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "Wialon \"In motion\" time is missing, unreadable or unattributed for these vehicles; they are left out of the total",
        },
        {
            label: "Idle time",
            value: todayFleetActivity.idle,
            state: idleState,
            notice: reportNotice(idleState, { failed: auxFailed.idle, earlier: earlier.idle }),
            partial: todayFleetActivity.idleUnknownVehicles > 0 ? `${todayFleetActivity.idleUnknownVehicles} vehicle(s) unknown` : null,
            partialTitle: "These vehicles' idle time could not be attributed; they are left out of the total",
        },
    ]

    const idleBarChartHeight = Math.max(220, idleTimeByGroupData.length * 36 + 24)
    const highestIdleChartHeight = Math.max(220, highestIdleVehicles.length * 34 + 24)
    const topMileageChartHeight = Math.max(220, topMileageVehicles.length * 34 + 24)


    return (
        <AppShell
            overlay={selectedVehicle && (
                <Suspense fallback={null}>
                    <VehicleDetailDrawer
                        vehicle={selectedVehicle}
                        onClose={() => setSelectedVehicleId(null)}
                    />
                </Suspense>
            )}
        >
            <PageHeader
                lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
            >
                <FreshnessBadge label="Live" freshness={liveFreshness} error={liveFailed ? "failed" : null} hasData={vehiclesLoaded} loading={!vehiclesLoaded} />
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

            {/* The layout renders at once; each section shows its own skeleton until its request answers */}
            {(
                <div className="space-y-4 sm:space-y-5">
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
                                error ? (
                                    <div className="flex items-center gap-1.5 text-xs text-amber-700 font-medium">
                                        <span className="h-2 w-2 rounded-full bg-amber-500" aria-hidden="true" />
                                        <span>{vehiclesLoaded ? "Update failed, last data shown" : "Unavailable"}</span>
                                    </div>
                                ) : (
                                    <div className="flex items-center gap-1.5 text-xs text-slate-500 font-medium">
                                        <span className={`h-2 w-2 rounded-full ${vehiclesFresh ? "bg-emerald-500" : "bg-slate-300"} animate-pulse`} aria-hidden="true" />
                                        <span title={vehiclesLoaded && !vehiclesFresh ? "Showing the status from your previous visit until the live refresh answers" : undefined}>
                                            {vehiclesFresh ? "Live status" : vehiclesLoaded ? "Earlier status · refreshing" : "Loading live status"}
                                        </span>
                                    </div>
                                )
                            }
                        >
                            <div className="space-y-4">
                                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl bg-slate-50/60 p-3 border border-slate-200/60">
                                    <div>
                                        <span className="text-[11px] font-medium uppercase tracking-wider text-slate-500">Total Fleet</span>
                                        <div className="mt-0.5 flex items-baseline gap-1.5">
                                            <Link
                                                to="/tracking"
                                                title={`View all units in Live Tracking${trackingScopeNote}`}
                                                className="text-2xl font-bold leading-none tracking-tight text-slate-900 tabular-nums hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 rounded-sm"
                                            >
                                                {fleetValue(totalVehiclesCount)}
                                            </Link>
                                            <span className="text-xs text-slate-500">vehicles</span>
                                        </div>
                                    </div>
                                    <dl className="flex items-center gap-5 sm:gap-7">
                                        <div>
                                            <dt
                                                className="text-[11px] font-medium uppercase tracking-wider text-slate-500"
                                                title="Vehicles with Trips report mileage or a trip today (UAE), or live Moving/Idling from a message sent today. A stale last-known status is not counted."
                                            >
                                                Active Today
                                            </dt>
                                            <dd className="mt-0.5 flex items-baseline gap-1.5">
                                                <span className="text-lg font-bold tabular-nums text-slate-900">{fleetValue(activeTodayCount)}</span>
                                                {vehiclesLoaded && (
                                                    <span className="text-xs font-medium tabular-nums text-slate-500">({pct(activeTodayCount)}%)</span>
                                                )}
                                            </dd>
                                            {vehiclesLoaded && !activeToday.complete && (
                                                <span
                                                    className="mt-0.5 block text-[11px] font-medium text-amber-700"
                                                    title="No activity data for these vehicles today yet (report unavailable, failed, stale or unreadable); they are not counted as inactive"
                                                >
                                                    Partial: {activeToday.unknown} unknown
                                                </span>
                                            )}
                                        </div>
                                    </dl>
                                </div>

                                {/* Status Segment Progress Bar */}
                                <div>
                                    <div className="flex h-2 gap-1 overflow-hidden rounded-full bg-slate-100 p-0.5" aria-hidden="true">
                                        {vehiclesLoaded && totalVehiclesCount > 0 && statusSegments.filter(seg => seg.count > 0).map(seg => (
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
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                                    {statusSegments.map(seg => (
                                        <Link
                                            key={seg.key}
                                            to={`/tracking?status=${seg.key}`}
                                            title={`View ${seg.label.toLowerCase()} vehicles${trackingScopeNote}${seg.title ? `. ${seg.title}` : ""}`}
                                            className="group rounded-xl border border-slate-200/70 bg-white p-3 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                                        >
                                            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
                                                <span className={`h-2 w-2 rounded-full ${seg.dot}`} aria-hidden="true" />
                                                <span className="truncate">{seg.label}</span>
                                            </div>
                                            <div className="mt-1.5 flex items-baseline gap-1.5">
                                                <span className={`text-xl font-bold tabular-nums tracking-tight ${vehiclesLoaded && seg.key === "OFFLINE" && seg.count > 0 ? "text-rose-700" : "text-slate-900"}`}>
                                                    {fleetValue(seg.count)}
                                                </span>
                                                {vehiclesLoaded && <span className="text-xs font-medium tabular-nums text-slate-400">{pct(seg.count)}%</span>}
                                            </div>
                                            {vehiclesLoaded && seg.note && <span className="mt-0.5 text-[11px] text-slate-400 block leading-tight truncate" title={seg.note}>{seg.note}</span>}
                                        </Link>
                                    ))}
                                </div>
                            </div>
                        </SectionCard>

                        {/* Today — 50% */}
                        <SectionCard
                            className="flex flex-col h-full justify-between"
                            title="Today"
                            actions={(auxFailed.trips || auxFailed.idle || todayMileageFailed) ? (
                                <button
                                    type="button"
                                    onClick={() => auxRetryRef.current?.()}
                                    className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                >
                                    Retry now
                                </button>
                            ) : null}
                        >
                            <div className="grid flex-1 grid-cols-2 grid-rows-2 gap-3 h-full">
                                {activityRows.map(row => (
                                    <div
                                        key={row.label}
                                        className="flex flex-col justify-between rounded-xl border border-slate-200/70 bg-white p-3.5 sm:p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)] transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_4px_12px_-2px_rgba(15,23,42,0.08)] hover:border-slate-300"
                                    >
                                        <dt className="text-xs font-semibold text-slate-600 leading-snug" title={row.labelTitle}>{row.label}</dt>
                                        <dd className="mt-2 flex flex-col justify-end">
                                            <span className="text-xl sm:text-2xl lg:text-[26px] font-bold tabular-nums text-slate-900 tracking-tight leading-tight">
                                                {row.state === "pending" ? <Pending className="h-7 w-24" /> : row.value}
                                            </span>
                                            {row.notice && (
                                                <span className="mt-1 block truncate text-[11px] text-amber-700 font-medium" title={row.notice.title}>
                                                    {row.notice.text}
                                                </span>
                                            )}
                                            {row.partial && (
                                                <span className="mt-1 block truncate text-[11px] text-amber-700 font-medium" title={row.partialTitle}>
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
                        ROW 2 — OPERATIONAL PERFORMANCE (Two equal-width cards on desktop)
                    ═══════════════════════════════════════════════════════════════ */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                        {/* 1. Currently Idling (> 5 min) */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title={selectedGroup === "ALL" ? "Currently Idling" : `Currently Idling · ${selectedGroup}`}
                            actions={
                                <div className="flex items-center gap-2">
                                    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold tabular-nums text-amber-800">
                                        {currentlyIdlingVehicles.length} idling
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
                            {!vehiclesLoaded ? (
                                <SectionPlaceholder state={error ? "failed" : "pending"} label="Live vehicle status" />
                            ) : currentlyIdlingVehicles.length === 0 ? (
                                <div className="flex h-[240px] flex-col items-center justify-center p-6 text-center">
                                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-50 text-emerald-600 mb-2">
                                        <CheckCircle2 className="h-5 w-5" />
                                    </div>
                                    <h4 className="text-xs font-semibold text-slate-800">No Vehicles Currently Idling</h4>
                                    <p className="mt-1 text-[11px] text-slate-500 max-w-xs leading-relaxed">
                                        No active fleet units currently idling over 5 minutes.
                                    </p>
                                </div>
                            ) : (
                                <div className="max-h-[260px] overflow-y-auto pr-1">
                                    <ul className="divide-y divide-slate-100">
                                        {currentlyIdlingVehicles.map((vehicle) => {
                                            const lastUpdateText = isKnownNumber(vehicle.seconds_since_update)
                                                ? vehicle.seconds_since_update < 60
                                                    ? "Updated just now"
                                                    : `${Math.floor(vehicle.seconds_since_update / 60)}m ago${isTelemetryStale(vehicle) ? " (stale)" : ""}`
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
                                                            <div className="flex items-center justify-end gap-0.5 text-xs font-bold text-amber-800 tabular-nums">
                                                                <LiveIdleTimer
                                                                    startTime={vehicle.idle_start || (vehicle.last_message_timestamp && vehicle.idle_duration ? vehicle.last_message_timestamp - vehicle.idle_duration : null)}
                                                                    baseDuration={vehicle.idle_duration}
                                                                    status={vehicle.normalizedStatus}
                                                                    endTime={isTelemetryStale(vehicle) ? vehicle.last_message_timestamp : null}
                                                                    className="text-xs font-bold text-amber-800"
                                                                />
                                                                {idleDurationStatus(vehicle) === "lower_bound" && <span>+</span>}
                                                            </div>
                                                        </div>
                                                    </button>
                                                </li>
                                            )
                                        })}
                                    </ul>
                                </div>
                            )}
                        </SectionCard>

                        {/* 2. Top Vehicles by Mileage */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title={selectedGroup === "ALL" ? "Top Vehicles by Mileage" : `Top Vehicles by Mileage · ${selectedGroup}`}
                            subtitle={earlierSubtitle(topMileageState, distanceEarlier)}
                            actions={
                                <Link
                                    to="/tracking"
                                    className="inline-flex items-center gap-1 text-xs font-medium text-brand hover:underline underline-offset-2"
                                >
                                    Live tracking
                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                </Link>
                            }
                        >
                            {topMileageState !== "ready" ? (
                                <SectionPlaceholder state={topMileageState} label="Today's mileage" />
                            ) : topMileageVehicles.length === 0 ? (
                                <div className="flex h-[240px] items-center justify-center gap-2 text-xs text-slate-500">
                                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                                    No mileage recorded for vehicles today.
                                </div>
                            ) : (
                                <div className="max-h-[260px] overflow-y-auto pr-1">
                                    <div style={{ height: `${topMileageChartHeight}px` }}>
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart
                                                data={topMileageVehicles}
                                                layout="vertical"
                                                margin={{ top: 4, right: 65, left: 4, bottom: 4 }}
                                            >
                                                <CartesianGrid horizontal={false} stroke={GRID_STROKE} />
                                                <XAxis
                                                    type="number"
                                                    tickLine={false}
                                                    axisLine={false}
                                                    tick={TICK}
                                                    unit=" km"
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
                                                    formatter={(val) => [formatKm(val) ?? "Unknown (unreadable)", "Mileage"]}
                                                    contentStyle={TOOLTIP_STYLE}
                                                />
                                                <Bar
                                                    dataKey="reportedMileage"
                                                    fill={BRAND_BLUE}
                                                    radius={[0, 3, 3, 0]}
                                                    maxBarSize={16}
                                                    onClick={(entry) => {
                                                        if (entry?.vehicle_id) setSelectedVehicleId(entry.vehicle_id)
                                                    }}
                                                    cursor="pointer"
                                                >
                                                    <LabelList
                                                        dataKey="formattedMileage"
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
                    </div>

                    {/* ═══════════════════════════════════════════════════════════════
                        ROW 3 — HISTORICAL & GROUP ANALYTICS (Two equal-width cards on desktop)
                    ═══════════════════════════════════════════════════════════════ */}
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 items-stretch">
                        {/* 1. Today vs. Yesterday */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title="Today vs. Yesterday"
                            subtitle={earlierSubtitle("ready", earlier.trips || earlier.idle)}
                            actions={
                                <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                                    <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" aria-hidden="true" />
                                    Today in progress
                                </span>
                            }
                        >
                            <div className="overflow-x-auto">
                                <table className="w-full text-left border-collapse">
                                    <thead>
                                        <tr className="border-b border-slate-200/80 text-[11px] uppercase tracking-wider text-slate-500">
                                            <th className="pb-2 font-semibold">Metric</th>
                                            <th className="pb-2 text-right font-semibold">
                                                <span>Today</span>
                                                <span className="ml-1 text-[10px] text-amber-600 font-normal lowercase">(so far)</span>
                                            </th>
                                            <th className="pb-2 text-right font-semibold">
                                                <span>Yesterday</span>
                                                <span className="ml-1 text-[10px] text-slate-400 font-normal lowercase">(24h)</span>
                                            </th>
                                            <th className="pb-2 text-right font-semibold">Change</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 text-xs">
                                        {comparisonMetrics.map((m) => (
                                            <tr key={m.id} className="transition-colors hover:bg-slate-50/80">
                                                <td className="py-2.5 font-medium text-slate-800 pr-2">
                                                    <div className="flex flex-col">
                                                        <span className="font-semibold text-slate-900" title={m.labelTitle}>{m.label}</span>
                                                        {m.partial && (
                                                            <span className="text-[10px] text-amber-700 font-medium">
                                                                {m.partial}
                                                            </span>
                                                        )}
                                                    </div>
                                                </td>
                                                <td className="py-2.5 text-right font-bold text-slate-900 tabular-nums px-2">
                                                    {todayStateById[m.id] === "pending" ? <Pending className="h-4 w-14" /> : m.today}
                                                </td>
                                                <td className="py-2.5 text-right font-medium text-slate-600 tabular-nums px-2">
                                                    {yesterdayCellById[m.id].state === "pending" ? <Pending className="h-4 w-14" /> : m.yesterday}
                                                    {yesterdayCellById[m.id].notice && (
                                                        <span className="mt-0.5 block text-[10px] font-medium text-amber-700" title={yesterdayCellById[m.id].notice.title}>
                                                            {yesterdayCellById[m.id].notice.text}
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="py-2.5 text-right tabular-nums pl-2">
                                                    <span
                                                        title={m.change.tooltip}
                                                        className={`inline-flex items-center justify-end rounded px-2 py-0.5 text-[11px] font-semibold ${
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
                        </SectionCard>

                        {/* 2. Idle Time by Fleet Group */}
                        <SectionCard
                            className="flex flex-col h-full"
                            title="Idle Time by Fleet Group"
                            subtitle={earlierSubtitle(idleState, earlier.idle)}
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
                            {idleState !== "ready" ? (
                                <SectionPlaceholder state={idleState} label="Today's idle report" />
                            ) : idleTimeByGroupData.length === 0 ? (
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
                                                        `${val !== null ? `${val} hrs` : "Unknown"} · ${item?.payload?.vehicleCount || 0} unit(s)${item?.payload?.unknownCount > 0 && val !== null ? ` · partial: ${item.payload.unknownCount} unknown` : ""}`,
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
                            {idleTimeByGroupData.some(g => g.unknownCount > 0) && (
                                <p className="mt-2 text-[11px] text-amber-700">
                                    Partial: {idleTimeByGroupData.filter(g => g.unknownCount > 0).map(g => `${g.name} (${g.unknownCount} unknown)`).join(", ")}. Unknown idle time is left out of the bars.
                                </p>
                            )}
                        </SectionCard>
                    </div>

                    {/* ═══════════════════════════════════════════════════════════════
                        ROW 4 — VEHICLES WITH HIGHEST IDLE TIME (Full-width spanning both columns)
                    ═══════════════════════════════════════════════════════════════ */}
                    <div className="grid grid-cols-1 gap-4">
                        <SectionCard
                            className="flex flex-col"
                            title="Vehicles with Highest Idle Time"
                            subtitle={earlierSubtitle(idleState, earlier.idle)}
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
                            {idleState !== "ready" ? (
                                <SectionPlaceholder state={idleState} label="Today's idle report" />
                            ) : highestIdleVehicles.length === 0 ? (
                                <div className="flex h-[240px] items-center justify-center gap-2 text-xs text-slate-500">
                                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                                    No idle time recorded for vehicles today.
                                </div>
                            ) : (
                                <div className="max-h-[280px] overflow-y-auto pr-1">
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
                                                    width={140}
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
                                                    maxBarSize={18}
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
                    </div>

                </div>
            )}
        </AppShell>
    )
}

export default Dashboard
