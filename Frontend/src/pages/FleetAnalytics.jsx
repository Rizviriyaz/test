import { useEffect, useState, useMemo } from "react"
import {
    BarChart3, Route, Clock3, ShieldCheck,
    TrendingUp, Truck, Users, RefreshCw,
    Loader2, AlertCircle, ChevronDown,
} from "lucide-react"
import {
    ResponsiveContainer, BarChart, Bar,
    XAxis, YAxis, CartesianGrid, Tooltip, Legend,
    PieChart, Pie, Cell,
} from "recharts"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import {
    getFleetUtilizationAnalytics,
    getFleetTripsAnalytics,
    getFleetSafetyAnalytics,
    getFleetGroupsAnalytics,
} from "../api/fleetApi"

// ── Utilities ─────────────────────────────────────────────────────────────────

const fmt = (n, d = 1) => (typeof n === "number" ? n.toFixed(d) : "0")
const COLORS = {
    moving: "#3b82f6",
    idle: "#f59e0b",
    stopped: "#6b7280",
    danger: "#ef4444",
    purple: "#8b5cf6",
}
const PIE_COLORS = ["#3b82f6", "#f59e0b", "#6b7280", "#10b981", "#8b5cf6"]

// ── Sub-components ────────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, color = "blue", loading }) {
    const colorMap = {
        blue: "from-blue-500/10 to-blue-600/5 text-blue-600 border-blue-200/60",
        amber: "from-amber-500/10 to-amber-600/5 text-amber-600 border-amber-200/60",
        green: "from-emerald-500/10 to-emerald-600/5 text-emerald-600 border-emerald-200/60",
        red: "from-red-500/10 to-red-600/5 text-red-600 border-red-200/60",
        purple: "from-violet-500/10 to-violet-600/5 text-violet-600 border-violet-200/60",
        teal: "from-teal-500/10 to-teal-600/5 text-teal-600 border-teal-200/60",
    }
    const cls = colorMap[color] || colorMap.blue
    return (
        <div className={`bg-gradient-to-br ${cls} border rounded-2xl p-5 flex items-start gap-4 transition-all hover:shadow-md`}>
            <div className="p-2.5 rounded-xl bg-white/70 shadow-sm">
                <Icon size={20} />
            </div>
            <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">{label}</p>
                {loading ? (
                    <div className="h-7 w-20 bg-slate-200 animate-pulse rounded-lg" />
                ) : (
                    <p className="text-2xl font-bold text-slate-800 leading-tight">{value}</p>
                )}
            </div>
        </div>
    )
}

function SectionTitle({ title, sub }) {
    return (
        <div className="mb-4">
            <h2 className="text-base font-bold text-slate-800">{title}</h2>
            {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
        </div>
    )
}

function EmptyState({ msg = "No data available" }) {
    return (
        <div className="flex flex-col items-center justify-center py-16 text-slate-400 gap-3">
            <AlertCircle size={32} />
            <p className="text-sm font-medium">{msg}</p>
        </div>
    )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function FleetAnalytics() {
    const [hours, setHours] = useState(24)

    const [utilData, setUtilData] = useState(null)
    const [tripsData, setTripsData] = useState(null)
    const [safetyData, setSafetyData] = useState(null)
    const [groupsData, setGroupsData] = useState(null)

    const [utilLoading, setUtilLoading] = useState(true)
    const [tripsLoading, setTripsLoading] = useState(true)
    const [safetyLoading, setSafetyLoading] = useState(true)
    const [groupsLoading, setGroupsLoading] = useState(true)

    const [utilError, setUtilError] = useState(null)
    const [tripsError, setTripsError] = useState(null)
    const [safetyError, setSafetyError] = useState(null)
    const [groupsError, setGroupsError] = useState(null)

    const loadUtil = async (mountedRef = { current: true }) => {
        setUtilLoading(true); setUtilError(null)
        try {
            const data = await getFleetUtilizationAnalytics(hours)
            if (mountedRef.current) setUtilData(data)
        } catch (e) {
            if (mountedRef.current) setUtilError(e.message)
        } finally {
            if (mountedRef.current) setUtilLoading(false)
        }
    }

    const loadTrips = async (mountedRef = { current: true }) => {
        setTripsLoading(true); setTripsError(null)
        try {
            const data = await getFleetTripsAnalytics(hours)
            if (mountedRef.current) setTripsData(data)
        } catch (e) {
            if (mountedRef.current) setTripsError(e.message)
        } finally {
            if (mountedRef.current) setTripsLoading(false)
        }
    }

    const loadSafety = async (mountedRef = { current: true }) => {
        setSafetyLoading(true); setSafetyError(null)
        try {
            const data = await getFleetSafetyAnalytics(hours)
            if (mountedRef.current) setSafetyData(data)
        } catch (e) {
            if (mountedRef.current) setSafetyError(e.message)
        } finally {
            if (mountedRef.current) setSafetyLoading(false)
        }
    }

    const loadGroups = async (mountedRef = { current: true }) => {
        setGroupsLoading(true); setGroupsError(null)
        try {
            const data = await getFleetGroupsAnalytics()
            if (mountedRef.current) setGroupsData(data)
        } catch (e) {
            if (mountedRef.current) setGroupsError(e.message)
        } finally {
            if (mountedRef.current) setGroupsLoading(false)
        }
    }

    const loadAll = () => {
        const ref = { current: true }
        loadUtil(ref); loadTrips(ref); loadSafety(ref); loadGroups(ref)
    }

    useEffect(() => {
        const mountedRef = { current: true }
        loadGroups(mountedRef)
        return () => { mountedRef.current = false }
    }, [])

    useEffect(() => {
        const mountedRef = { current: true }
        loadUtil(mountedRef)
        loadTrips(mountedRef)
        loadSafety(mountedRef)
        return () => { mountedRef.current = false }
    }, [hours])

    const utilizationPie = useMemo(() => {
        if (!utilData) return []
        return [
            { name: "Driving", value: Math.round((utilData.total_driving_hours || 0) * 60) },
            { name: "Idling",  value: Math.round((utilData.total_idle_hours || 0) * 60) },
        ].filter(d => d.value > 0)
    }, [utilData])

    const topMilageBars = useMemo(() => {
        if (!tripsData?.vehicles) return []
        return tripsData.vehicles.slice(0, 8).map(v => ({
            name: v.vehicle_name?.split(" ").slice(-2).join(" ") || `V${v.vehicle_id}`,
            km: parseFloat((v.gps_mileage_km || 0).toFixed(1)),
        }))
    }, [tripsData])

    const safetyBars = useMemo(() => {
        if (!safetyData?.by_vehicle) return []
        return safetyData.by_vehicle
            .filter(v => (v.total || 0) > 0).slice(0, 8)
            .map(v => ({
                name: v.vehicle_name?.split(" ").slice(-2).join(" ") || `V${v.vehicle_id}`,
                braking:   v.braking   || 0,
                accel:     v.accel     || 0,
                cornering: v.cornering || 0,
            }))
    }, [safetyData])

    const groupsBarData = useMemo(() => {
        if (!groupsData?.groups) return []
        return groupsData.groups.map(g => ({
            name: g.group_name,
            km: parseFloat((g.distance_24h_km ?? g.distance_km ?? 0).toFixed(1)),
            trips: g.trips_24h ?? g.trip_count ?? 0,
            vehicles: g.total_vehicles ?? g.vehicle_count ?? 0,
            moving: g.moving ?? 0,
            idling: g.idling ?? 0,
            stopped: g.stopped ?? 0,
            offline: g.offline ?? (Array.isArray(g.vehicles) ? g.vehicles.filter(v => v.connection_status === "OFFLINE" || v.is_offline || v.status === "OFFLINE").length : 0),
            safety: g.safety_events_24h ?? 0,
        }))
    }, [groupsData])

    return (
        <SidebarProvider>
            <div className="flex h-screen bg-slate-50 font-sans overflow-hidden">
                <Sidebar />
                <div className="flex-1 flex flex-col overflow-hidden">
                    {/* Header */}
                    <header className="flex-none bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between gap-4">
                        <div>
                            <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
                                <BarChart3 size={20} className="text-blue-500" />
                                Fleet Analytics
                            </h1>
                            <p className="text-xs text-slate-500 mt-0.5">
                                Utilization · Trips · Mileage · Safety · Group breakdown
                            </p>
                        </div>
                        <div className="flex items-center gap-3">
                            <div className="relative">
                                <select
                                    id="analytics-period"
                                    value={hours}
                                    onChange={e => setHours(Number(e.target.value))}
                                    className="appearance-none bg-white border border-slate-200 rounded-xl pl-3 pr-8 py-2 text-sm font-medium text-slate-700 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/30"
                                >
                                    <option value={24}>Last 24 h</option>
                                    <option value={48}>Last 48 h</option>
                                    <option value={168}>Last 7 d</option>
                                </select>
                                <ChevronDown size={14} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                            </div>
                            <button
                                id="analytics-refresh"
                                onClick={loadAll}
                                className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors cursor-pointer"
                            >
                                <RefreshCw size={14} />
                                Refresh
                            </button>
                        </div>
                    </header>

                    <main className="flex-1 overflow-y-auto px-6 py-6 space-y-8">
                        {/* Section 1 — Utilization KPIs */}
                        <section>
                            <SectionTitle title="Fleet Utilization" sub={`Aggregated across all vehicles · Last ${hours}h`} />
                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                                <KpiCard icon={Truck} label="Total Vehicles" loading={utilLoading}
                                    value={utilData?.vehicles?.length ?? "—"} color="blue" />
                                <KpiCard icon={TrendingUp} label="Driving Hours" loading={utilLoading}
                                    value={`${(utilData?.total_driving_hours || 0).toFixed(1)}h`} color="blue" />
                                <KpiCard icon={Clock3} label="Idle Hours" loading={utilLoading}
                                    value={`${(utilData?.total_idle_hours || 0).toFixed(1)}h`} color="amber" />
                                <KpiCard icon={Route} label="Total Distance" loading={tripsLoading}
                                    value={tripsError ? "Unavailable" : `${fmt(tripsData?.summary?.total_distance_km, 0)} km`} color="green" />
                                <KpiCard icon={Route} label="Total Trips" loading={tripsLoading}
                                    value={tripsError ? "Unavailable" : tripsData?.summary?.total_trips ?? 0} color="purple" />
                                <KpiCard icon={TrendingUp} label="Fleet Util %" loading={utilLoading}
                                    value={`${utilData?.fleet_utilization_pct ?? 0}%`} color="teal" />
                            </div>
                        </section>

                        {/* Section 2 — Charts */}
                        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                            {/* Pie: time distribution */}
                            <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
                                <SectionTitle title="Time Distribution" sub="Driving vs Idle vs Stopped (minutes)" />
                                {utilLoading ? (
                                    <div className="h-52 flex items-center justify-center"><Loader2 size={24} className="animate-spin text-slate-400" /></div>
                                ) : utilError ? <EmptyState msg={utilError} />
                                : utilizationPie.length === 0 ? <EmptyState />
                                : (
                                    <ResponsiveContainer width="100%" height={220}>
                                        <PieChart>
                                            <Pie data={utilizationPie} cx="50%" cy="50%" innerRadius={55} outerRadius={85}
                                                paddingAngle={3} dataKey="value">
                                                {utilizationPie.map((_, i) => <Cell key={i} fill={PIE_COLORS[i]} />)}
                                            </Pie>
                                            <Tooltip formatter={v => [`${v} min`]} />
                                            <Legend />
                                        </PieChart>
                                    </ResponsiveContainer>
                                )}
                            </div>

                            {/* Bar: distance by vehicle */}
                            <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
                                <SectionTitle title="Distance by Vehicle" sub="Top vehicles by km driven" />
                                {tripsLoading ? (
                                    <div className="h-52 flex items-center justify-center"><Loader2 size={24} className="animate-spin text-slate-400" /></div>
                                ) : tripsError ? <EmptyState msg={tripsError} />
                                : topMilageBars.length === 0 ? <EmptyState />
                                : (
                                    <ResponsiveContainer width="100%" height={220}>
                                        <BarChart data={topMilageBars} margin={{ top: 5, right: 10, bottom: 40, left: 0 }}>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                            <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-35} textAnchor="end" />
                                            <YAxis tick={{ fontSize: 10 }} unit=" km" />
                                            <Tooltip formatter={v => [`${v} km`]} />
                                            <Bar dataKey="km" fill={COLORS.moving} radius={[4, 4, 0, 0]} name="Distance (km)" />
                                        </BarChart>
                                    </ResponsiveContainer>
                                )}
                            </div>
                        </section>

                        {/* Section 3 — Trips Summary KPIs */}
                        <section>
                            <SectionTitle title="Trips Summary" sub={`All vehicles · Last ${hours}h`} />
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <KpiCard icon={Route} label="Total Trips" loading={tripsLoading}
                                    value={tripsError ? "Unavailable" : tripsData?.summary?.total_trips ?? 0} color="purple" />
                                <KpiCard icon={TrendingUp} label="Total Distance" loading={tripsLoading}
                                    value={tripsError ? "Unavailable" : `${fmt(tripsData?.summary?.total_distance_km, 0)} km`} color="green" />
                                <KpiCard icon={Truck} label="Avg Distance/Vehicle" loading={tripsLoading}
                                    value={tripsError ? "Unavailable" : `${fmt(tripsData?.summary?.average_trip_distance_km, 1)} km`} color="blue" />
                                <KpiCard icon={Route} label="Longest Trip" loading={tripsLoading}
                                    value={tripsError ? "Unavailable" : `${fmt(tripsData?.summary?.longest_trip_km, 1)} km`} color="teal" />
                            </div>
                        </section>

                        {/* Section 4 — Safety */}
                        <section>
                            <div className="mb-4">
                                <SectionTitle title="Driver Safety Events" sub={`Harsh events · Last ${hours}h`} />
                            </div>

                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                                <KpiCard icon={ShieldCheck} label="Total Events" loading={safetyLoading}
                                    value={safetyData?.summary?.total_events ?? 0} color="red" />
                                <KpiCard icon={ShieldCheck} label="Harsh Braking" loading={safetyLoading}
                                    value={safetyData?.summary?.harsh_braking ?? 0} color="red" />
                                <KpiCard icon={ShieldCheck} label="Harsh Accel." loading={safetyLoading}
                                    value={safetyData?.summary?.harsh_acceleration ?? 0} color="amber" />
                                <KpiCard icon={ShieldCheck} label="Cornering" loading={safetyLoading}
                                    value={safetyData?.summary?.harsh_cornering ?? 0} color="purple" />
                            </div>

                            <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
                                <SectionTitle title="Safety Events by Vehicle" sub="Stacked event type breakdown" />
                                {safetyLoading ? (
                                    <div className="h-56 flex items-center justify-center"><Loader2 size={24} className="animate-spin text-slate-400" /></div>
                                ) : safetyError ? <EmptyState msg={safetyError} />
                                : safetyBars.length === 0 ? <EmptyState msg="No safety events recorded in this period 🎉" />
                                : (
                                    <ResponsiveContainer width="100%" height={240}>
                                        <BarChart data={safetyBars} margin={{ top: 5, right: 10, bottom: 40, left: 0 }}>
                                            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                            <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-35} textAnchor="end" />
                                            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                                            <Tooltip />
                                            <Legend />
                                            <Bar dataKey="braking" name="Harsh Braking" stackId="a" fill={COLORS.danger} />
                                            <Bar dataKey="accel" name="Harsh Accel." stackId="a" fill={COLORS.idle} />
                                            <Bar dataKey="cornering" name="Cornering" stackId="a" fill={COLORS.purple} radius={[4, 4, 0, 0]} />
                                        </BarChart>
                                    </ResponsiveContainer>
                                )}
                            </div>
                        </section>

                        {/* Section 5 — Group analytics */}
                        <section>
                            <SectionTitle title="Group Analytics" sub="Rollup by Wialon vehicle group" />
                            {groupsLoading ? (
                                <div className="h-32 flex items-center justify-center"><Loader2 size={24} className="animate-spin text-slate-400" /></div>
                            ) : groupsError ? <EmptyState msg={groupsError} />
                            : groupsBarData.length === 0 ? <EmptyState msg="No group data available" />
                            : (
                                <div className="space-y-3">
                                    {groupsBarData.map((g, i) => (
                                        <div key={i} className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm flex items-center gap-4 hover:border-blue-300 transition-colors">
                                            <div className="w-9 h-9 rounded-xl bg-blue-50 flex items-center justify-center flex-none">
                                                <Users size={16} className="text-blue-600" />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <p className="text-sm font-semibold text-slate-800 truncate">{g.name}</p>
                                                <p className="text-xs text-slate-500">
                                                    {g.vehicles} vehicles · <span className="text-blue-600 font-medium">{g.moving} moving</span> · <span className="text-amber-600 font-medium">{g.idling} idling</span> · <span className="text-slate-600 font-medium">{g.stopped} stopped</span>{g.offline !== null && <> · <span className="text-red-600 font-medium">{g.offline} offline</span></>}
                                                </p>
                                            </div>
                                            <div className="grid grid-cols-3 gap-6 text-right">
                                                <div>
                                                    <p className="text-xs text-slate-500">Distance (24h)</p>
                                                    <p className="text-sm font-bold text-slate-800">{g.km} km</p>
                                                </div>
                                                <div>
                                                    <p className="text-xs text-slate-500">Trips (24h)</p>
                                                    <p className="text-sm font-bold text-slate-800">{g.trips}</p>
                                                </div>
                                                <div>
                                                    <p className="text-xs text-slate-500">Safety (24h)</p>
                                                    <p className="text-sm font-bold text-red-600">{g.safety} events</p>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </section>

                        <div className="h-6" />
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
