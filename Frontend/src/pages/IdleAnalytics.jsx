import { useEffect, useMemo, useState } from "react"
import {
    Clock3,
    Truck,
    Timer,
    BarChart3,
    MapPin,
    AlertTriangle,
    CheckCircle2,
    ArrowUpRight,
} from "lucide-react"

import { getVehicles, getVehicleIdle, API_URL } from "../api/fleetApi"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"


const formatDuration = (seconds) => {
    const totalSeconds = Math.max(0, Number(seconds || 0))

    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const secs = Math.floor(totalSeconds % 60)

    if (hours > 0) {
        return `${hours}h ${minutes}m`
    }

    if (minutes > 0) {
        return `${minutes}m ${secs}s`
    }

    return `${secs}s`
}

const formatDateTime = (timestamp) => {
    if (!timestamp) return "—"

    return new Date(Number(timestamp) * 1000).toLocaleString()
}

const getIdleSeverity = (seconds) => {
    const duration = Number(seconds || 0)

    if (duration >= 3600) {
        return {
            label: "Critical",
            className: "bg-red-50 text-red-700",
            dot: "bg-red-500",
        }
    }

    if (duration >= 1800) {
        return {
            label: "Attention",
            className: "bg-orange-50 text-orange-700",
            dot: "bg-orange-500",
        }
    }

    if (duration >= 600) {
        return {
            label: "Monitor",
            className: "bg-yellow-50 text-yellow-700",
            dot: "bg-yellow-500",
        }
    }

    return {
        label: "Normal",
        className: "bg-green-50 text-green-700",
        dot: "bg-green-500",
    }
}

function IdleAnalytics() {
    const [vehicles, setVehicles] = useState([])
    const [selectedVehicleId, setSelectedVehicleId] = useState("")
    const [hours, setHours] = useState(24)
    const [rangeMode, setRangeMode] = useState("preset") // 'preset' | 'custom'

    // Default custom range: last 7 days
    const todayStr = new Date().toISOString().split("T")[0]
    const sevenDaysAgoStr = new Date(Date.now() - 7 * 86400 * 1000).toISOString().split("T")[0]

    const [startDate, setStartDate] = useState(sevenDaysAgoStr)
    const [endDate, setEndDate] = useState(todayStr)

    const [idleData, setIdleData] = useState(null)
    const [loadingVehicles, setLoadingVehicles] = useState(true)
    const [loadingIdle, setLoadingIdle] = useState(false)
    const [error, setError] = useState("")

    // Load vehicles dynamically
    useEffect(() => {
        let mounted = true

        const loadVehicles = async () => {
            try {
                const data = await getVehicles()

                if (!mounted) return

                const fleet = data.vehicles || []

                setVehicles(fleet)

                if (fleet.length > 0) {
                    setSelectedVehicleId(String(fleet[0].vehicle_id))
                }
            } catch (err) {
                console.error(err)

                if (mounted) {
                    setError("Failed to load vehicles")
                }
            } finally {
                if (mounted) {
                    setLoadingVehicles(false)
                }
            }
        }

        loadVehicles()

        return () => {
            mounted = false
        }
    }, [])

    // Load historical idle analytics
    useEffect(() => {
        if (!selectedVehicleId) return

        let mounted = true

        const loadIdleAnalytics = async () => {
            try {
                setLoadingIdle(true)
                setError("")

                const options = {}
                if (rangeMode === "custom" && startDate && endDate) {
                    options.from_time = Math.floor(new Date(`${startDate}T00:00:00`).getTime() / 1000)
                    options.to_time = Math.floor(new Date(`${endDate}T23:59:59`).getTime() / 1000)
                } else {
                    options.hours = hours
                }

                const data = await getVehicleIdle(selectedVehicleId, options)

                if (!mounted) return

                setIdleData(data)
            } catch (err) {
                console.error(err)

                if (mounted) {
                    setIdleData(null)
                    setError("Failed to load idle analytics")
                }
            } finally {
                if (mounted) {
                    setLoadingIdle(false)
                }
            }
        }

        loadIdleAnalytics()

        return () => {
            mounted = false
        }
    }, [selectedVehicleId, hours, rangeMode, startDate, endDate])

    const selectedVehicle = useMemo(() => {
        return vehicles.find(
            (vehicle) =>
                String(vehicle.vehicle_id) === String(selectedVehicleId)
        )
    }, [vehicles, selectedVehicleId])

    const periods = useMemo(() => idleData?.periods || [], [idleData?.periods])
    const summary = idleData?.summary

    const sortedPeriods = useMemo(() => {
        return [...periods].sort(
            (a, b) =>
                Number(b.duration || 0) -
                Number(a.duration || 0)
        )
    }, [periods])

    const timeline = useMemo(() => {
        if (periods.length === 0) {
            return {
                start: 0,
                end: 0,
                duration: 0,
            }
        }

        const start = Math.min(
            ...periods.map((period) =>
                Number(period.start_time)
            )
        )

        const end = Math.max(
            ...periods.map((period) =>
                Number(period.end_time)
            )
        )

        return {
            start,
            end,
            duration: Math.max(end - start, 1),
        }
    }, [periods])

    return (
        <SidebarProvider>
        <div className="flex min-h-screen bg-[#F8FAFC]">
            <Sidebar />

            <div className="flex min-w-0 flex-1 flex-col">

                <main className="flex-1 space-y-6 p-8">
                    {/* Header */}
                    <section className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="flex items-center gap-2">
                                <Clock3 className="h-5 w-5 text-[#1C5E7A]" />

                                <h1 className="text-xl font-bold text-slate-900">
                                    Idle Analytics
                                </h1>
                            </div>

                            <p className="mt-1 text-sm text-slate-500">
                                Historical idling behaviour and idle-period analysis
                            </p>
                        </div>

                        <div className="flex flex-wrap items-center gap-3">
                            {/* Vehicle selector */}
                            <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
                                <Truck className="h-4 w-4 text-slate-400" />

                                <select
                                    value={selectedVehicleId}
                                    onChange={(event) =>
                                        setSelectedVehicleId(
                                            event.target.value
                                        )
                                    }
                                    disabled={loadingVehicles}
                                    className="bg-transparent text-sm font-semibold text-slate-800 outline-none"
                                >
                                    {vehicles.map((vehicle) => (
                                        <option
                                            key={vehicle.vehicle_id}
                                            value={vehicle.vehicle_id}
                                        >
                                            {vehicle.vehicle_name ||
                                                `Vehicle ${vehicle.vehicle_id}`}
                                        </option>
                                    ))}
                                </select>
                            </div>

                            {/* Mode Switcher */}
                            <div className="flex items-center rounded-xl border border-slate-200 bg-slate-100 p-1 shadow-sm">
                                <button
                                    type="button"
                                    onClick={() => setRangeMode("preset")}
                                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                                        rangeMode === "preset"
                                            ? "bg-white text-slate-900 shadow-sm"
                                            : "text-slate-600 hover:text-slate-900"
                                    }`}
                                >
                                    Preset Hours
                                </button>

                                <button
                                    type="button"
                                    onClick={() => setRangeMode("custom")}
                                    className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                                        rangeMode === "custom"
                                            ? "bg-white text-slate-900 shadow-sm"
                                            : "text-slate-600 hover:text-slate-900"
                                    }`}
                                >
                                    Custom Date Range
                                </button>
                            </div>

                            {/* Time range selector */}
                            {rangeMode === "preset" ? (
                                <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm">
                                    <Timer className="h-4 w-4 text-slate-400" />

                                    <select
                                        value={hours}
                                        onChange={(event) =>
                                            setHours(
                                                Number(event.target.value)
                                            )
                                        }
                                        className="bg-transparent text-sm font-semibold text-slate-800 outline-none"
                                    >
                                        <option value={1}>Last 1 hour</option>
                                        <option value={6}>Last 6 hours</option>
                                        <option value={12}>Last 12 hours</option>
                                        <option value={24}>Last 1 day (24 hours)</option>
                                        <option value={48}>Last 48 hours</option>
                                        <option value={168}>Last 1 week (7 days)</option>
                                        <option value={720}>Last 1 month (30 days)</option>
                                    </select>
                                </div>
                            ) : (
                                <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-1.5 shadow-sm">
                                    <input
                                        type="date"
                                        value={startDate}
                                        onChange={(e) => setStartDate(e.target.value)}
                                        className="bg-transparent text-xs font-semibold text-slate-800 outline-none"
                                    />
                                    <span className="text-xs text-slate-400">to</span>
                                    <input
                                        type="date"
                                        value={endDate}
                                        onChange={(e) => setEndDate(e.target.value)}
                                        className="bg-transparent text-xs font-semibold text-slate-800 outline-none"
                                    />
                                </div>
                            )}
                        </div>
                    </section>


                    {error && (
                        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                            {error}
                        </div>
                    )}

                    {loadingIdle ? (
                        <div className="rounded-xl border border-slate-200 bg-white px-6 py-16 text-center shadow-sm">
                            <Clock3 className="mx-auto h-8 w-8 animate-pulse text-slate-300" />

                            <p className="mt-3 text-sm font-medium text-slate-700">
                                Loading idle analytics...
                            </p>

                            <p className="mt-1 text-xs text-slate-400">
                                Analyzing historical vehicle activity
                            </p>
                        </div>
                    ) : (
                        <>
                            {/* Selected vehicle */}
                            <section className="rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
                                <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                                    <div>
                                        <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                                            Selected Vehicle
                                        </p>

                                        <div className="mt-1 flex items-center gap-2">
                                            <Truck className="h-4 w-4 text-slate-500" />

                                            <h2 className="text-sm font-semibold text-slate-900">
                                                {selectedVehicle?.vehicle_name ||
                                                    "Unknown Vehicle"}
                                            </h2>

                                            <span className="text-xs text-slate-400">
                                                ID {selectedVehicleId}
                                            </span>
                                        </div>
                                    </div>

                                    {selectedVehicle && (
                                        <div className="flex items-center gap-4 text-xs">
                                            <span className="text-slate-500">
                                                Current status:
                                            </span>

                                            <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-700">
                                                {selectedVehicle.status ||
                                                    "Unknown"}
                                            </span>

                                            <span className="font-medium text-slate-600">
                                                {Math.round(
                                                    Number(
                                                        selectedVehicle.speed ||
                                                        0
                                                    )
                                                )}{" "}
                                                km/h
                                            </span>
                                        </div>
                                    )}
                                </div>
                            </section>

                            {/* KPI cards */}
                            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                                <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                                    <div className="flex items-center justify-between">
                                        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-orange-50">
                                            <Clock3 className="h-4 w-4 text-orange-500" />
                                        </div>

                                        <span className="text-xs text-slate-400">
                                            {hours}h window
                                        </span>
                                    </div>

                                    <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-400">
                                        Total Idle Time
                                    </p>

                                    <p className="mt-1 text-2xl font-semibold text-slate-900">
                                        {formatDuration(
                                            summary?.total_idle_seconds
                                        )}
                                    </p>
                                </div>

                                <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50">
                                        <BarChart3 className="h-4 w-4 text-blue-500" />
                                    </div>

                                    <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-400">
                                        Idle Events
                                    </p>

                                    <p className="mt-1 text-2xl font-semibold text-slate-900">
                                        {summary?.idle_periods ?? 0}
                                    </p>
                                </div>

                                <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-purple-50">
                                        <Timer className="h-4 w-4 text-purple-500" />
                                    </div>

                                    <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-400">
                                        Average Idle
                                    </p>

                                    <p className="mt-1 text-2xl font-semibold text-slate-900">
                                        {formatDuration(
                                            summary?.average_idle_seconds
                                        )}
                                    </p>
                                </div>

                                <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                                    <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-red-50">
                                        <AlertTriangle className="h-4 w-4 text-red-500" />
                                    </div>

                                    <p className="mt-4 text-xs font-medium uppercase tracking-wider text-slate-400">
                                        Longest Idle
                                    </p>

                                    <p className="mt-1 text-2xl font-semibold text-slate-900">
                                        {formatDuration(
                                            summary?.longest_idle_seconds
                                        )}
                                    </p>
                                </div>
                            </section>

                            {/* Timeline */}
                            <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h2 className="text-sm font-semibold text-slate-900">
                                            Idle Timeline
                                        </h2>

                                        <p className="mt-1 text-xs text-slate-500">
                                            Idle periods detected in the selected history window
                                        </p>
                                    </div>

                                    <span className="text-xs font-medium text-slate-400">
                                        {periods.length} periods
                                    </span>
                                </div>

                                {periods.length === 0 ? (
                                    <div className="py-12 text-center">
                                        <CheckCircle2 className="mx-auto h-8 w-8 text-green-400" />

                                        <p className="mt-3 text-sm font-medium text-slate-700">
                                            No idle periods detected
                                        </p>

                                        <p className="mt-1 text-xs text-slate-400">
                                            The vehicle had no detected idle events in this period.
                                        </p>
                                    </div>
                                ) : (
                                    <>
                                        <div className="relative mt-8 h-14 rounded-lg bg-slate-100">
                                            {periods.map(
                                                (period, index) => {
                                                    const start =
                                                        Number(
                                                            period.start_time
                                                        )

                                                    const end =
                                                        Number(
                                                            period.end_time
                                                        )

                                                    const left =
                                                        ((start -
                                                            timeline.start) /
                                                            timeline.duration) *
                                                        100

                                                    const width =
                                                        Math.max(
                                                            ((end - start) /
                                                                timeline.duration) *
                                                            100,
                                                            1
                                                        )

                                                    return (
                                                        <div
                                                            key={`${start}-${index}`}
                                                            className="absolute top-2 h-10 rounded-md bg-orange-400/80"
                                                            style={{
                                                                left: `${Math.max(
                                                                    0,
                                                                    left
                                                                )}%`,
                                                                width: `${Math.min(
                                                                    width,
                                                                    100
                                                                )}%`,
                                                            }}
                                                            title={`${formatDuration(
                                                                period.duration
                                                            )} idle`}
                                                        />
                                                    )
                                                }
                                            )}
                                        </div>

                                        <div className="mt-3 flex justify-between text-[11px] text-slate-400">
                                            <span>
                                                {formatDateTime(
                                                    timeline.start
                                                )}
                                            </span>

                                            <span>
                                                {formatDateTime(
                                                    timeline.end
                                                )}
                                            </span>
                                        </div>
                                    </>
                                )}
                            </section>

                            {/* Idle periods table */}
                            <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
                                <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
                                    <div>
                                        <h2 className="text-sm font-semibold text-slate-900">
                                            Idle Periods
                                        </h2>

                                        <p className="mt-1 text-xs text-slate-500">
                                            Detailed breakdown of detected idle events
                                        </p>
                                    </div>

                                    <span className="text-xs font-medium text-slate-400">
                                        Sorted by duration
                                    </span>
                                </div>

                                {sortedPeriods.length === 0 ? (
                                    <div className="px-5 py-12 text-center">
                                        <Clock3 className="mx-auto h-8 w-8 text-slate-300" />

                                        <p className="mt-3 text-sm font-medium text-slate-700">
                                            No idle periods
                                        </p>
                                    </div>
                                ) : (
                                    <div className="overflow-x-auto">
                                        <table className="w-full min-w-[850px]">
                                            <thead>
                                                <tr className="border-b border-slate-100 bg-slate-50/70">
                                                    <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                        #
                                                    </th>

                                                    <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                        Start
                                                    </th>

                                                    <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                        End
                                                    </th>

                                                    <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                        Duration
                                                    </th>

                                                    <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                        Severity
                                                    </th>

                                                    <th className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                                        Location
                                                    </th>
                                                </tr>
                                            </thead>

                                            <tbody className="divide-y divide-slate-100">
                                                {sortedPeriods.map(
                                                    (
                                                        period,
                                                        index
                                                    ) => {
                                                        const severity =
                                                            getIdleSeverity(
                                                                period.duration
                                                            )

                                                        return (
                                                            <tr
                                                                key={`${period.start_time}-${index}`}
                                                                className="transition hover:bg-slate-50"
                                                            >
                                                                <td className="px-5 py-4 text-sm font-medium text-slate-500">
                                                                    {index +
                                                                        1}
                                                                </td>

                                                                <td className="px-5 py-4 text-sm text-slate-600">
                                                                    {formatDateTime(
                                                                        period.start_time
                                                                    )}
                                                                </td>

                                                                <td className="px-5 py-4 text-sm text-slate-600">
                                                                    {formatDateTime(
                                                                        period.end_time
                                                                    )}
                                                                </td>

                                                                <td className="px-5 py-4">
                                                                    <span className="text-sm font-semibold text-slate-800">
                                                                        {formatDuration(
                                                                            period.duration
                                                                        )}
                                                                    </span>
                                                                </td>

                                                                <td className="px-5 py-4">
                                                                    <span
                                                                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${severity.className}`}
                                                                    >
                                                                        <span
                                                                            className={`h-1.5 w-1.5 rounded-full ${severity.dot}`}
                                                                        />

                                                                        {
                                                                            severity.label
                                                                        }
                                                                    </span>
                                                                </td>

                                                                <td className="px-5 py-4">
                                                                    {period.location?.latitude !== undefined && period.location?.longitude !== undefined ? (
                                                                        <a
                                                                            href={`https://www.google.com/maps?q=${period.location.latitude},${period.location.longitude}`}
                                                                            target="_blank"
                                                                            rel="noopener noreferrer"
                                                                            className="group inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-xs font-semibold text-slate-800 shadow-2xs transition hover:border-[#1C5E7A] hover:bg-slate-50"
                                                                            title="Click to view location on map"
                                                                        >
                                                                            <MapPin className="h-3.5 w-3.5 text-amber-600 group-hover:scale-110 transition-transform" />
                                                                            <span>
                                                                                {Number(period.location.latitude).toFixed(5)}, {Number(period.location.longitude).toFixed(5)}
                                                                            </span>
                                                                            <ArrowUpRight className="h-3 w-3 text-slate-400 group-hover:text-slate-900" />
                                                                        </a>
                                                                    ) : (
                                                                        <span className="text-xs text-slate-400">Location unavailable</span>
                                                                    )}
                                                                </td>

                                                            </tr>
                                                        )
                                                    }
                                                )}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </section>

                            {/* Summary */}
                            {summary && (
                                <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
                                    <div className="flex items-center gap-2">
                                        <BarChart3 className="h-4 w-4 text-slate-500" />

                                        <h2 className="text-sm font-semibold text-slate-900">
                                            Idle Behaviour Summary
                                        </h2>
                                    </div>

                                    <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3">
                                        <div className="rounded-lg bg-slate-50 p-4">
                                            <p className="text-xs text-slate-400">
                                                Total idle minutes
                                            </p>

                                            <p className="mt-1 text-lg font-semibold text-slate-800">
                                                {Number(
                                                    summary.total_idle_minutes ||
                                                    0
                                                ).toFixed(1)}{" "}
                                                min
                                            </p>
                                        </div>

                                        <div className="rounded-lg bg-slate-50 p-4">
                                            <p className="text-xs text-slate-400">
                                                Longest idle period
                                            </p>

                                            <p className="mt-1 text-lg font-semibold text-slate-800">
                                                {formatDuration(
                                                    summary.longest_idle_seconds
                                                )}
                                            </p>
                                        </div>

                                        <div className="rounded-lg bg-slate-50 p-4">
                                            <p className="text-xs text-slate-400">
                                                Average idle period
                                            </p>

                                            <p className="mt-1 text-lg font-semibold text-slate-800">
                                                {formatDuration(
                                                    summary.average_idle_seconds
                                                )}
                                            </p>
                                        </div>
                                    </div>
                                </section>
                            )}

                            <div className="flex justify-end">
                                <a
                                    href="/vehicles"
                                    className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 transition hover:text-slate-900"
                                >
                                    View vehicle details
                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                </a>
                            </div>
                        </>
                    )}
                </main>
            </div>
        </div>
        </SidebarProvider>
    )
}

export default IdleAnalytics