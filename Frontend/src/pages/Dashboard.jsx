import { useEffect, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import {
    Truck,
    CirclePlay,
    Clock3,
    Square,
    AlertTriangle,
    Activity,
    Wifi,
    ArrowUpRight,
    Filter,
    Layers,
    Loader2,
} from "lucide-react"

import {
    getVehicles,
    getDashboardMileage,
} from "../api/fleetApi"

import {
    ResponsiveContainer,
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
} from "recharts"

import FleetMap from "../components/FleetMap"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { formatUAETime } from "../utils/datetime"
import VehicleDetailDrawer from "../components/Vehicles/VehicleDetailDrawer"


function Dashboard() {
    const [vehicles, setVehicles] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [lastUpdated, setLastUpdated] = useState(null)

    // Filters for Top Vehicles by Mileage
    const [selectedMileageGroup, setSelectedMileageGroup] = useState("ALL")
    const [mileageLimit, setMileageLimit] = useState("5")
    const [mileageTimeframe, setMileageTimeframe] = useState("today")
    const [mileageData, setMileageData] = useState(null)
    const [mileageLoading, setMileageLoading] = useState(true)
    const [selectedVehicleId, setSelectedVehicleId] = useState(null)

    /*
        Load fleet data
    */
    useEffect(() => {
        let mounted = true

        const loadVehicles = async () => {
            try {
                const data = await getVehicles()
                if (!mounted) return
                setVehicles(data.vehicles || [])
                setLastUpdated(new Date())
                setError("")
            } catch (err) {
                if (!mounted) return
                console.error(err)
                setError("Failed to load vehicle data")
            } finally {
                if (mounted) setLoading(false)
            }
        }

        loadVehicles()
        const interval = setInterval(loadVehicles, 10000)

        return () => {
            mounted = false
            clearInterval(interval)
        }
    }, [])

    /*
        Load Top Mileage rankings by timeframe
    */
    useEffect(() => {
        let mounted = true
        const loadMileage = async () => {
            try {
                setMileageLoading(true)
                const data = await getDashboardMileage(mileageTimeframe)
                if (!mounted) return
                setMileageData(data)
            } catch (err) {
                console.error("Mileage ranking error:", err)
            } finally {
                if (mounted) setMileageLoading(false)
            }
        }
        loadMileage()
        return () => { mounted = false }
    }, [mileageTimeframe])

    /*
        Fleet calculations
    */
    const totalVehicles = vehicles.length

    const normalizedVehicles = useMemo(() => {
        return vehicles.map((vehicle) => ({
            ...vehicle,
            normalizedStatus: String(vehicle.status || "").trim().toUpperCase(),
            isOffline: vehicle.connection_status === "OFFLINE" || Boolean(vehicle.is_offline) || String(vehicle.status || "").trim().toUpperCase() === "OFFLINE",
        }))
    }, [vehicles])

    const movingVehicles = useMemo(() => normalizedVehicles.filter(v => v.normalizedStatus === "MOVING"), [normalizedVehicles])
    const idlingVehicles = useMemo(() => normalizedVehicles.filter(v => v.normalizedStatus === "IDLING"), [normalizedVehicles])
    const stoppedVehicles = useMemo(() => normalizedVehicles.filter(v => v.normalizedStatus === "STOPPED"), [normalizedVehicles])
    const offlineVehicles = useMemo(() => normalizedVehicles.filter(v => v.isOffline), [normalizedVehicles])

    const movingCount = movingVehicles.length
    const idlingCount = idlingVehicles.length
    const stoppedCount = stoppedVehicles.length
    const offlineCount = offlineVehicles.length

    const fleetUtilization = totalVehicles > 0
        ? Math.round(((movingCount + idlingCount) / totalVehicles) * 100)
        : 0

    const averageSpeed = movingCount > 0
        ? Math.round(movingVehicles.reduce((sum, v) => sum + Number(v.speed || 0), 0) / movingCount)
        : 0

    /*
        Vehicles requiring attention (Idling > 5 mins)
    */
    const attentionVehicles = useMemo(() => {
        return normalizedVehicles
            .filter((vehicle) => vehicle.normalizedStatus === "IDLING")
            .filter((vehicle) => Number(vehicle.idle_duration || 0) > 300)
            .sort((a, b) => Number(b.idle_duration || 0) - Number(a.idle_duration || 0))
    }, [normalizedVehicles])

    /*
        Map vehicles with valid coordinates
    */
    const mapVehicles = useMemo(() => {
        return vehicles.filter(v => v.latitude !== null && v.longitude !== null)
    }, [vehicles])

    const selectedVehicle = useMemo(() => {
        return vehicles.find(v => v.vehicle_id === selectedVehicleId) || null
    }, [vehicles, selectedVehicleId])

    /*
        Top Vehicles by Mileage (Dynamic with Timeframe and Category Filters)
    */
    const timeframeConfig = {
        today: {
            label: "Today",
            subtitle: "Trip distance covered today in UAE (00:00:00 Asia/Dubai -> Now)",
            unit: "km today"
        },
        yesterday: {
            label: "Yesterday",
            subtitle: "Total trip distance covered yesterday in UAE (Full calendar day)",
            unit: "km yesterday"
        },
        week: {
            label: "This Week",
            subtitle: "Cumulative distance covered this week (UAE Monday -> Now)",
            unit: "km (this week)"
        },
        month: {
            label: "This Month",
            subtitle: "Distance covered this month (UAE 1st -> Now)",
            unit: "km (this month)"
        },
        year: {
            label: "This Year",
            subtitle: "Annual fleet mileage breakdown (UAE Jan 1 -> Now)",
            unit: "km (this year)"
        },
        all: {
            label: "All Time",
            subtitle: "Lifetime cumulative odometer rankings",
            unit: "total km"
        }
    }

    const mileageUnitsSource = useMemo(() => {
        if (mileageData?.all_units && Array.isArray(mileageData.all_units) && mileageData.all_units.length > 0) {
            return mileageData.all_units
        }
        if (mileageData?.top_units && Array.isArray(mileageData.top_units) && mileageData.top_units.length > 0) {
            return mileageData.top_units
        }
        return []
    }, [mileageData])

    const availableGroups = useMemo(() => {
        const set = new Set()
        mileageUnitsSource.forEach(u => {
            if (u.group) set.add(u.group)
        })
        vehicles.forEach(v => {
            if (v.group) set.add(v.group)
        })
        return Array.from(set)
    }, [mileageUnitsSource, vehicles])

    const filteredMileageData = useMemo(() => {
        let units = [...mileageUnitsSource]
        if (selectedMileageGroup !== "ALL") {
            units = units.filter(u => u.group === selectedMileageGroup || (u.groups && u.groups.includes(selectedMileageGroup)))
        }
        if (mileageLimit !== "ALL") {
            units = units.slice(0, Number(mileageLimit))
        }
        return units.map(u => ({
            vehicle: u.vehicle_name,
            mileage: u.mileage,
            group: u.group || "General Fleet"
        }))
    }, [mileageUnitsSource, selectedMileageGroup, mileageLimit])

    /*
        Category / Wialon Group Metrics for Bar Charts
    */
    const categoryMetrics = useMemo(() => {
        const groupsToProcess = availableGroups.length > 0 ? availableGroups : ["General Fleet"]

        return groupsToProcess.map(groupName => {
            const groupVehicles = normalizedVehicles.filter(v => (v.group || "General Fleet") === groupName)
            const total = groupVehicles.length
            const moving = groupVehicles.filter(v => v.normalizedStatus === "MOVING").length
            const idling = groupVehicles.filter(v => v.normalizedStatus === "IDLING").length
            const stopped = groupVehicles.filter(v => v.normalizedStatus === "STOPPED").length

            const active = moving + idling
            const utilizationPercent = total > 0 ? Math.round((active / total) * 100) : 0
            const totalIdleSecs = groupVehicles.reduce((sum, v) => sum + Number(v.idle_duration || 0), 0)

            return {
                category: groupName,
                totalVehicles: total,
                movingCount: moving,
                idlingCount: idling,
                stoppedCount: stopped,
                utilizationPercent: utilizationPercent,
                totalIdleMinutes: Math.round(totalIdleSecs / 60)
            }
        }).filter(item => item.totalVehicles > 0)
    }, [availableGroups, normalizedVehicles])

    return (
        <SidebarProvider>
        <div className="flex h-screen overflow-hidden bg-[#F8FAFC] text-slate-900">
            <Sidebar />

            <div className="flex min-w-0 flex-1 flex-col">


                <main className="flex-1 overflow-y-auto px-8 py-8">
                    {/* Header */}
                    <section className="mb-8">
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                            <div>
                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                    Fleet Overview
                                </p>

                                <h1 className="mt-1 text-2xl font-black tracking-tight text-slate-900">
                                    Operational Command Dashboard
                                </h1>

                                <p className="mt-1 text-xs text-slate-500">
                                    Live telemetry, attention management, and utilization metrics
                                </p>
                            </div>

                            <div className="flex items-center gap-3">
                                {lastUpdated && (
                                    <span className="rounded-full bg-white border border-slate-200 px-3 py-1 text-xs font-semibold text-slate-600 shadow-2xs">
                                        Last Updated {formatUAETime(lastUpdated)}
                                    </span>
                                )}
                            </div>
                        </div>
                    </section>

                    {loading && (
                        <div className="rounded-2xl border border-slate-200 bg-white p-12 text-center shadow-xs">
                            <Clock3 className="mx-auto h-8 w-8 animate-pulse text-[#1C5E7A]" />
                            <p className="mt-3 text-sm font-bold text-slate-800">
                                Loading live fleet data...
                            </p>
                        </div>
                    )}

                    {error && (
                        <div className="rounded-2xl border border-red-200 bg-red-50 p-6 text-sm font-semibold text-red-700">
                            Failed to load fleet data: {error}
                        </div>
                    )}

                    {!loading && !error && (
                        <div className="space-y-8">
                            {/* Primary Summary KPIs */}
                            <section>
                                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
                                    {/* Total */}
                                    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                                    Total Vehicles
                                                </p>
                                                <p className="mt-2 text-3xl font-black text-slate-900">
                                                    {totalVehicles}
                                                </p>
                                            </div>
                                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-100 border border-slate-200">
                                                <Truck className="h-5 w-5 text-[#1C5E7A]" />
                                            </div>
                                        </div>
                                        <p className="mt-3 text-xs font-medium text-slate-500">
                                            Live registered fleet units
                                        </p>
                                    </div>

                                    {/* Moving */}
                                    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                                    Moving
                                                </p>
                                                <p className="mt-2 text-3xl font-black text-emerald-600">
                                                    {movingCount}
                                                </p>
                                            </div>
                                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50 border border-emerald-100">
                                                <CirclePlay className="h-5 w-5 text-emerald-600" />
                                            </div>
                                        </div>
                                        <p className="mt-3 text-xs font-medium text-slate-500">
                                            Speed &gt; 0 km/h
                                        </p>
                                    </div>

                                    {/* Idling */}
                                    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                                    Idling
                                                </p>
                                                <p className="mt-2 text-3xl font-black text-orange-600">
                                                    {idlingCount}
                                                </p>
                                            </div>
                                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-orange-50 border border-orange-100">
                                                <Clock3 className="h-5 w-5 text-orange-600" />
                                            </div>
                                        </div>
                                        <p className="mt-3 text-xs font-medium text-slate-500">
                                            Ignition ON, Speed = 0
                                        </p>
                                    </div>

                                    {/* Stopped */}
                                    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs">
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                                    Stopped
                                                </p>
                                                <p className="mt-2 text-3xl font-black text-slate-600">
                                                    {stoppedCount}
                                                </p>
                                            </div>
                                            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-100 border border-slate-200">
                                                <Square className="h-5 w-5 text-slate-500" />
                                            </div>
                                        </div>
                                        <p className="mt-3 text-xs font-medium text-slate-500">
                                            Ignition OFF, Speed = 0
                                        </p>
                                    </div>

                                    {/* Offline */}
                                    <div className="rounded-2xl border border-red-200 bg-white p-5 shadow-xs">
                                        <div className="flex items-start justify-between">
                                            <div>
                                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">Offline</p>
                                                <p className="mt-2 text-3xl font-black text-red-600">{offlineCount}</p>
                                            </div>
                                            <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-red-100 bg-red-50">
                                                <Wifi className="h-5 w-5 text-red-600" />
                                            </div>
                                        </div>
                                        <p className="mt-3 text-xs font-medium text-slate-500">No Wialon message within configured threshold</p>
                                    </div>

                                </div>
                            </section>

                            {/* Status Distribution KPI & Vehicles Requiring Attention */}
                            <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                                {/* Fleet Status Distribution KPI */}
                                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs flex flex-col justify-between">
                                    <div>
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-2">
                                                <Activity className="h-5 w-5 text-[#1C5E7A]" />
                                                <h2 className="text-base font-bold text-slate-900">
                                                    Fleet Status Distribution
                                                </h2>
                                            </div>
                                            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-700">
                                                Operational KPI
                                            </span>
                                        </div>
                                        <p className="mt-1 text-xs text-slate-500">
                                             Real-time fleet counts by Wialon connection and activity status
                                        </p>

                                        {/* Progress Bar */}
                                        <div className="mt-6">
                                            <div className="flex h-4 overflow-hidden rounded-full bg-slate-100 p-0.5 border border-slate-200">
                                                {totalVehicles > 0 && (
                                                    <>
                                                        <div
                                                            className="bg-emerald-500 transition-all rounded-l-full"
                                                            style={{ width: `${(movingCount / totalVehicles) * 100}%` }}
                                                            title={`Moving: ${movingCount}`}
                                                        />
                                                        <div
                                                            className="bg-orange-500 transition-all"
                                                            style={{ width: `${(idlingCount / totalVehicles) * 100}%` }}
                                                            title={`Idling: ${idlingCount}`}
                                                        />
                                                        <div
                                                            className="bg-slate-400 transition-all rounded-r-full"
                                                            style={{ width: `${(stoppedCount / totalVehicles) * 100}%` }}
                                                            title={`Stopped: ${stoppedCount}`}
                                                        />
                                                    </>
                                                )}
                                            </div>
                                        </div>

                                        {/* Metrics Grid */}
                                        <div className="mt-6 grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
                                            <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3">
                                                <span className="text-[11px] font-bold text-emerald-800">Moving</span>
                                                <p className="mt-1 text-xl font-black text-emerald-700">{movingCount}</p>
                                                <p className="text-[10px] text-emerald-600 font-semibold">
                                                    {totalVehicles > 0 ? Math.round((movingCount / totalVehicles) * 100) : 0}%
                                                </p>
                                            </div>

                                            <div className="rounded-xl border border-orange-200 bg-orange-50/60 p-3">
                                                <span className="text-[11px] font-bold text-orange-800">Idling</span>
                                                <p className="mt-1 text-xl font-black text-orange-600">{idlingCount}</p>
                                                <p className="text-[10px] text-orange-600 font-semibold">
                                                    {totalVehicles > 0 ? Math.round((idlingCount / totalVehicles) * 100) : 0}%
                                                </p>
                                            </div>

                                            <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                                                <span className="text-[11px] font-bold text-slate-700">Stopped</span>
                                                <p className="mt-1 text-xl font-black text-slate-700">{stoppedCount}</p>
                                                <p className="text-[10px] text-slate-500 font-semibold">
                                                    {totalVehicles > 0 ? Math.round((stoppedCount / totalVehicles) * 100) : 0}%
                                                </p>
                                            </div>
                                            <div className="rounded-xl border border-red-200 bg-red-50/60 p-3">
                                                <span className="text-[11px] font-bold text-red-800">Offline</span>
                                                <p className="mt-1 text-xl font-black text-red-700">{offlineCount}</p>
                                                <p className="text-[10px] font-semibold text-red-600">
                                                    {totalVehicles > 0 ? Math.round((offlineCount / totalVehicles) * 100) : 0}%
                                                </p>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="mt-4 pt-3 border-t border-slate-100 flex justify-between items-center text-xs text-slate-500">
                                        <span>Fleet Utilization Rate: <strong className="text-slate-900">{fleetUtilization}%</strong></span>
                                        <span>Average Speed: <strong className="text-slate-900">{averageSpeed} km/h</strong></span>
                                    </div>
                                </div>

                                {/* Idle Vehicles / Attention Section */}
                                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs flex flex-col justify-between">
                                    <div>
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-2">
                                                <AlertTriangle className="h-5 w-5 text-amber-600" />
                                                <h2 className="text-base font-bold text-slate-900">
                                                    Idle Vehicles Requiring Attention
                                                </h2>
                                            </div>
                                            <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${
                                                attentionVehicles.length > 0
                                                    ? "bg-amber-100 text-amber-800 ring-1 ring-amber-300"
                                                    : "bg-emerald-100 text-emerald-800"
                                            }`}>
                                                {attentionVehicles.length} Flagged
                                            </span>
                                        </div>
                                        <p className="mt-1 text-xs text-slate-500">
                                            High-priority vehicles idling continuously for more than 5 minutes
                                        </p>

                                        <div className="mt-4 space-y-2.5 max-h-[200px] overflow-y-auto pr-1">
                                            {attentionVehicles.length === 0 ? (
                                                <div className="flex flex-col items-center justify-center rounded-xl bg-emerald-50/60 py-8 text-center border border-emerald-200">
                                                    <CirclePlay className="h-7 w-7 text-emerald-600" />
                                                    <p className="mt-2 text-xs font-bold text-emerald-800">
                                                        All Vehicles Operating Normally
                                                    </p>
                                                    <p className="mt-0.5 text-[11px] text-emerald-700">
                                                        No critical prolonged idling detected across the fleet
                                                    </p>
                                                </div>
                                            ) : (
                                                attentionVehicles.map(vehicle => (
                                                    <div
                                                        key={vehicle.vehicle_id}
                                                        className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50/60 px-4 py-3 shadow-2xs"
                                                    >
                                                        <div>
                                                            <div className="flex items-center gap-2">
                                                                <p className="text-xs font-bold text-slate-900">
                                                                    {vehicle.vehicle_name}
                                                                </p>
                                                                <span className="rounded bg-amber-200/80 px-1.5 py-0.5 text-[10px] font-semibold text-amber-900">
                                                                    {vehicle.group || "General Fleet"}
                                                                </span>
                                                            </div>
                                                            <p className="mt-1 text-[11px] font-semibold text-amber-800">
                                                                Idling for {vehicle.idle_duration_formatted}
                                                                {vehicle.idle_start ? ` (since ${formatUAETime(vehicle.idle_start)})` : ""}
                                                            </p>
                                                        </div>

                                                        <a
                                                            href="/idle-analytics"
                                                            className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-600 text-white transition hover:bg-amber-700"
                                                            title="Inspect idle details"
                                                        >
                                                            <ArrowUpRight className="h-4 w-4" />
                                                        </a>
                                                    </div>
                                                ))
                                            )}
                                        </div>
                                    </div>

                                    <div className="mt-4 pt-3 border-t border-slate-100 flex justify-end">
                                        <Link
                                            to="/idle-analytics"
                                            className="inline-flex items-center gap-1 text-xs font-bold text-amber-700 hover:underline"
                                        >
                                            Open Full Idle Analytics
                                            <ArrowUpRight className="h-3.5 w-3.5" />
                                        </Link>
                                    </div>
                                </div>
                            </section>

                            {/* Top Vehicles by Mileage */}
                            <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
                                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between mb-6">
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <h2 className="text-lg font-bold text-slate-900">
                                                Top Vehicles by Mileage
                                            </h2>
                                            {mileageLoading && (
                                                <span className="flex items-center gap-1 text-[11px] font-semibold text-sky-600 bg-sky-50 px-2 py-0.5 rounded-full border border-sky-200 animate-pulse">
                                                    <Loader2 className="h-3 w-3 animate-spin" /> Updating...
                                                </span>
                                            )}
                                        </div>
                                        <p className="mt-0.5 text-xs text-slate-500">
                                            {timeframeConfig[mileageTimeframe]?.subtitle || "Dynamic mileage rankings"}
                                        </p>
                                    </div>

                                    {/* Filters Toolbar */}
                                    <div className="flex flex-wrap items-center gap-3">
                                        {/* Timeframe Filter Pills */}
                                        <div className="flex items-center rounded-xl border border-slate-200 bg-slate-50 p-1 shadow-2xs">
                                            {[
                                                { id: "today", label: "Today" },
                                                { id: "yesterday", label: "Yesterday" },
                                                { id: "week", label: "This Week" },
                                                { id: "month", label: "This Month" },
                                                { id: "year", label: "This Year" },
                                                { id: "all", label: "All Time" },
                                            ].map((tf) => {
                                                const isActive = mileageTimeframe === tf.id
                                                return (
                                                    <button
                                                        key={tf.id}
                                                        type="button"
                                                        onClick={() => setMileageTimeframe(tf.id)}
                                                        className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition-all ${
                                                            isActive
                                                                ? "bg-[#1C5E7A] text-white shadow-xs"
                                                                : "text-slate-600 hover:text-slate-900 hover:bg-slate-200/60"
                                                        }`}
                                                    >
                                                        {tf.label}
                                                    </button>
                                                )
                                            })}
                                        </div>

                                        {/* Category Filter */}
                                        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 shadow-2xs">
                                            <Layers className="h-3.5 w-3.5 text-slate-500" />
                                            <select
                                                value={selectedMileageGroup}
                                                onChange={(e) => setSelectedMileageGroup(e.target.value)}
                                                className="bg-transparent text-xs font-bold text-slate-800 outline-none cursor-pointer"
                                            >
                                                <option value="ALL">All Fleet Categories</option>
                                                {availableGroups.map(group => (
                                                    <option key={group} value={group}>
                                                        {group}
                                                    </option>
                                                ))}
                                            </select>
                                        </div>

                                        {/* Limit Filter */}
                                        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 shadow-2xs">
                                            <Filter className="h-3.5 w-3.5 text-slate-500" />
                                            <select
                                                value={mileageLimit}
                                                onChange={(e) => setMileageLimit(e.target.value)}
                                                className="bg-transparent text-xs font-bold text-slate-800 outline-none cursor-pointer"
                                            >
                                                <option value="5">Top 5 Vehicles</option>
                                                <option value="10">Top 10 Vehicles</option>
                                                <option value="15">Top 15 Vehicles</option>
                                                <option value="ALL">Show All Vehicles</option>
                                            </select>
                                        </div>
                                    </div>
                                </div>

                                <div className="h-[320px]">
                                    {mileageLoading && (!filteredMileageData || filteredMileageData.length === 0) ? (
                                        <div className="flex h-full flex-col items-center justify-center gap-2 text-sm font-semibold text-slate-400">
                                            <Loader2 className="h-6 w-6 animate-spin text-sky-600" />
                                            <span>Loading vehicle mileage ({timeframeConfig[mileageTimeframe]?.label})...</span>
                                        </div>
                                    ) : filteredMileageData.length === 0 ? (
                                        <div className="flex h-full items-center justify-center text-sm text-slate-500">
                                            No vehicles found matching selected category filter.
                                        </div>
                                    ) : (
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart
                                                data={filteredMileageData}
                                                layout="vertical"
                                                margin={{ top: 10, right: 30, left: 40, bottom: 10 }}
                                            >
                                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#E2E8F0" />
                                                <XAxis type="number" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "#64748b" }} />
                                                <YAxis type="category" dataKey="vehicle" tickLine={false} axisLine={false} width={110} tick={{ fontSize: 11, fill: "#0f172a", fontWeight: 700 }} />
                                                <Tooltip
                                                    formatter={(value) => [`${Number(value).toLocaleString()} km`, timeframeConfig[mileageTimeframe]?.unit || "Mileage"]}
                                                    contentStyle={{ borderRadius: "12px", borderColor: "#E2E8F0" }}
                                                />
                                                <Bar dataKey="mileage" fill="#1C5E7A" radius={[0, 8, 8, 0]} />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    )}
                                </div>
                            </section>

                            {/* Category Comparison Bar Charts */}
                            <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                                {/* Fleet Utilization Bar Chart */}
                                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
                                    <div className="mb-4">
                                        <div className="flex items-center justify-between">
                                            <h2 className="text-base font-bold text-slate-900">
                                                Fleet Utilization by Category
                                            </h2>
                                            <span className="rounded-full bg-sky-50 px-2.5 py-0.5 text-[10px] font-bold text-sky-700 border border-sky-200">
                                                Current State Utilization %
                                            </span>
                                        </div>
                                        <p className="mt-1 text-xs text-slate-500">
                                            Percentage of active (moving/idling) units per dynamic fleet group
                                        </p>
                                    </div>

                                    <div className="h-[260px]">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart data={categoryMetrics} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
                                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                                                <XAxis dataKey="category" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "#0f172a", fontWeight: 700 }} />
                                                <YAxis domain={[0, 100]} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "#64748b" }} />
                                                <Tooltip formatter={(val) => [`${val}%`, "Utilization"]} contentStyle={{ borderRadius: "12px" }} />
                                                <Bar dataKey="utilizationPercent" fill="#0284c7" radius={[6, 6, 0, 0]} />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>

                                {/* Idle Vehicles Bar Chart */}
                                <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
                                    <div className="mb-4">
                                        <div className="flex items-center justify-between">
                                            <h2 className="text-base font-bold text-slate-900">
                                                Idle Duration by Category
                                            </h2>
                                            <span className="rounded-full bg-orange-50 px-2.5 py-0.5 text-[10px] font-bold text-orange-700 border border-orange-200">
                                                Idle Minutes by Group
                                            </span>
                                        </div>
                                        <p className="mt-1 text-xs text-slate-500">
                                            Cumulative live idling minutes compared across dynamic fleet categories
                                        </p>
                                    </div>

                                    <div className="h-[260px]">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <BarChart data={categoryMetrics} margin={{ top: 10, right: 20, left: 0, bottom: 20 }}>
                                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                                                <XAxis dataKey="category" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "#0f172a", fontWeight: 700 }} />
                                                <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: "#64748b" }} />
                                                <Tooltip formatter={(val) => [`${val} mins`, "Idle Duration"]} contentStyle={{ borderRadius: "12px" }} />
                                                <Bar dataKey="totalIdleMinutes" fill="#f97316" radius={[6, 6, 0, 0]} />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </div>
                                </div>
                            </section>

                            {/* Fleet Map Snapshot */}
                            <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-xs">
                                <div className="flex items-center justify-between mb-4">
                                    <div>
                                        <h2 className="text-base font-bold text-slate-900">
                                            Live Fleet Map Snapshot
                                        </h2>
                                        <p className="mt-0.5 text-xs text-slate-500">
                                            Geographic position of active units in real-time
                                        </p>
                                    </div>

                                    <Link
                                        to="/tracking"
                                        className="inline-flex items-center gap-1 text-xs font-bold text-[#1C5E7A] hover:underline"
                                    >
                                        Open Full Map Tracking
                                        <ArrowUpRight className="h-3.5 w-3.5" />
                                    </Link>
                                </div>

                                <div className="h-[400px] overflow-hidden rounded-xl border border-slate-200">
                                    <FleetMap
                                        vehicles={mapVehicles}
                                        selectedVehicleId={selectedVehicleId}
                                        selectedVehicle={selectedVehicle}
                                        onVehicleSelect={(vehicle) => {
                                            setSelectedVehicleId(vehicle.vehicle_id)
                                        }}
                                    />
                                </div>
                            </section>
                        </div>
                    )}
                </main>
            </div>

            {/* Vehicle Detail Drawer (Live Telemetry + Fuel Performance) */}
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
