import { useCallback, useEffect, useState, useMemo } from "react"
import {
    HeartPulse, Battery, MapPin, Wifi,
    RefreshCw, Loader2, AlertCircle, Search,
    Clock3, Truck, ShieldCheck,
} from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { getFleetHealthAnalytics } from "../api/fleetApi"

// ── Utilities ─────────────────────────────────────────────────────────────────

const fmt = (n, d = 1) => (typeof n === "number" ? n.toFixed(d) : "—")

const batteryBadge = (status, voltage) => {
    const s = String(status || "").toUpperCase()
    if (s === "HEALTHY") return { label: `${fmt(voltage, 1)}V · Healthy`, cls: "text-emerald-700 bg-emerald-50 border-emerald-200" }
    if (s === "WARNING") return { label: `${fmt(voltage, 1)}V · Low`, cls: "text-amber-700 bg-amber-50 border-amber-200" }
    if (s === "CRITICAL") return { label: `${fmt(voltage, 1)}V · Critical`, cls: "text-red-700 bg-red-50 border-red-200" }
    return { label: voltage ? `${fmt(voltage, 1)}V` : "No Data", cls: "text-slate-500 bg-slate-100 border-slate-200" }
}

const gpsBadge = (status, sats) => {
    const s = String(status || "").toUpperCase()
    if (s === "EXCELLENT") return { label: `${sats ?? "—"} sats (Exc)`, cls: "text-emerald-700 bg-emerald-50 border-emerald-200" }
    if (s === "GOOD") return { label: `${sats ?? "—"} sats (Good)`, cls: "text-blue-700 bg-blue-50 border-blue-200" }
    if (s === "WEAK") return { label: `${sats ?? "—"} sats (Weak)`, cls: "text-amber-700 bg-amber-50 border-amber-200" }
    return { label: sats ? `${sats} sats` : "No GPS", cls: "text-slate-500 bg-slate-100 border-slate-200" }
}

const connBadge = (status) => {
    const s = String(status || "").toUpperCase()
    if (s === "STABLE") return { label: "Online", cls: "text-emerald-700 bg-emerald-50 border-emerald-200" }
    if (s === "DELAYED") return { label: "Delayed", cls: "text-amber-700 bg-amber-50 border-amber-200" }
    if (s === "OFFLINE") return { label: "Offline", cls: "text-red-700 bg-red-50 border-red-200" }
    return { label: "Unknown", cls: "text-slate-500 bg-slate-100 border-slate-200" }
}

const scoreBadge = (score) => {
    const sc = Number(score ?? 0)
    if (sc >= 85) return { cls: "text-emerald-700 bg-emerald-50 border-emerald-200", bar: "bg-emerald-500" }
    if (sc >= 65) return { cls: "text-amber-700 bg-amber-50 border-amber-200", bar: "bg-amber-500" }
    return { cls: "text-red-700 bg-red-50 border-red-200", bar: "bg-red-500" }
}

const fmtAgo = (s) => {
    if (s === null || s === undefined) return "—"
    const sec = Number(s)
    if (sec < 60) return `${sec}s ago`
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
    return `${Math.floor(sec / 86400)}d ago`
}

// ── Sub-components ────────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, sub, color = "blue", loading }) {
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
            <div className="p-2.5 rounded-xl bg-white/70 shadow-sm"><Icon size={20} /></div>
            <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1">{label}</p>
                {loading ? (
                    <div className="h-7 w-20 bg-slate-200 animate-pulse rounded-lg" />
                ) : (
                    <p className="text-2xl font-bold text-slate-800 leading-tight">{value}</p>
                )}
                {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
            </div>
        </div>
    )
}

function Badge({ label, cls }) {
    return <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full border ${cls}`}>{label}</span>
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function VehicleHealth() {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [search, setSearch] = useState("")
    const [filter, setFilter] = useState("all") // all | issues | warning | healthy

    const load = useCallback(async (mountedRef = { current: true }) => {
        setLoading(true); setError(null)
        try {
            const res = await getFleetHealthAnalytics()
            if (mountedRef.current) setData(res)
        } catch (e) {
            if (mountedRef.current) setError(e.message)
        } finally {
            if (mountedRef.current) setLoading(false)
        }
    }, [])

    useEffect(() => {
        const mountedRef = { current: true }
        load(mountedRef)
        return () => { mountedRef.current = false }
    }, [load])

    const vehicles = useMemo(() => {
        if (!data?.vehicles) return []
        return data.vehicles
    }, [data])

    const filtered = useMemo(() => {
        let v = vehicles
        if (search.trim()) {
            const q = search.toLowerCase()
            v = v.filter(x => (x.vehicle_name || "").toLowerCase().includes(q) || String(x.vehicle_id).includes(q) || (x.group || "").toLowerCase().includes(q))
        }
        if (filter === "critical") v = v.filter(x => x.battery?.status === "CRITICAL" || x.connectivity?.status === "OFFLINE")
        if (filter === "warning")  v = v.filter(x => x.battery?.status === "WARNING"  || x.connectivity?.status === "DELAYED" || x.gps?.status === "WEAK")
        if (filter === "healthy")  v = v.filter(x => (x.health_score ?? 0) >= 80 && x.connectivity?.status === "STABLE")
        return v
    }, [vehicles, search, filter])

    return (
        <SidebarProvider>
            <div className="flex h-screen bg-slate-50 font-sans overflow-hidden">
                <Sidebar />
                <div className="flex-1 flex flex-col overflow-hidden">
                    {/* Header */}
                    <header className="flex-none bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between gap-4">
                        <div>
                            <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
                                <HeartPulse size={20} className="text-red-500" />
                                Vehicle Health Diagnostics
                            </h1>
                            <p className="text-xs text-slate-500 mt-0.5">Battery Voltage · GPS Satellite Quality · Telemetry Connectivity · Health Scores</p>
                        </div>
                        <button
                            id="health-refresh"
                            onClick={() => load({ current: true })}
                            className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors cursor-pointer"
                        >
                            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                            Refresh
                        </button>
                    </header>

                    <main className="flex-1 overflow-y-auto px-6 py-6 space-y-6">

                        {/* KPIs */}
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                            <KpiCard icon={Truck} label="Fleet Vehicles" loading={loading}
                                value={data?.total_vehicles ?? "—"} color="blue" />
                            <KpiCard icon={ShieldCheck} label="Healthy Units" loading={loading}
                                value={data?.healthy_vehicles ?? "—"} color="green" />
                            <KpiCard icon={HeartPulse} label="Avg Health Score" loading={loading}
                                value={data?.average_health_score != null ? `${data.average_health_score}%` : "—"} color="teal" />
                            <KpiCard icon={Battery} label="Battery Issues" loading={loading}
                                value={data?.battery_warnings ?? 0} color={data?.battery_warnings > 0 ? "amber" : "green"} />
                            <KpiCard icon={MapPin} label="GPS Warnings" loading={loading}
                                value={data?.gps_warnings ?? 0} color={data?.gps_warnings > 0 ? "purple" : "green"} />
                            <KpiCard icon={Wifi} label="Offline / Delayed" loading={loading}
                                value={data?.connectivity_warnings ?? 0} color={data?.connectivity_warnings > 0 ? "red" : "green"} />
                        </div>

                        {/* Search + filter bar */}
                        <div className="flex gap-3 flex-wrap items-center">
                            <div className="relative flex-1 min-w-48">
                                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    id="health-search"
                                    type="text"
                                    placeholder="Search by vehicle name, ID, or group..."
                                    value={search}
                                    onChange={e => setSearch(e.target.value)}
                                    className="w-full pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/30"
                                />
                            </div>
                            {["all", "healthy", "warning", "critical"].map(f => (
                                <button
                                    key={f}
                                    id={`health-filter-${f}`}
                                    onClick={() => setFilter(f)}
                                    className={`px-4 py-2 rounded-xl text-sm font-semibold transition-colors capitalize cursor-pointer ${
                                        filter === f
                                        ? "bg-blue-600 text-white shadow-sm"
                                        : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
                                    }`}
                                >
                                    {f}
                                </button>
                            ))}
                        </div>

                        {/* Vehicle health table */}
                        {loading ? (
                            <div className="flex items-center justify-center py-24">
                                <Loader2 size={28} className="animate-spin text-slate-400" />
                            </div>
                        ) : error ? (
                            <div className="flex flex-col items-center justify-center py-24 text-slate-400 gap-3">
                                <AlertCircle size={32} />
                                <p className="text-sm font-medium">{error}</p>
                                <button onClick={() => load({ current: true })} className="text-blue-600 text-sm font-semibold hover:underline">Retry</button>
                            </div>
                        ) : filtered.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-24 text-slate-400 gap-3">
                                <AlertCircle size={32} />
                                <p className="text-sm font-medium">No vehicles match your filter</p>
                            </div>
                        ) : (
                            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="bg-slate-50 border-b border-slate-200">
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-5 py-3.5">Vehicle</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Status</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Health Score</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Battery</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">GPS Quality</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Connectivity</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Last Seen</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {filtered.map(v => {
                                                const bat = v.battery || {}
                                                const gps = v.gps || {}
                                                const conn = v.connectivity || {}
                                                const batSt = batteryBadge(bat.status, bat.voltage)
                                                const gpsSt = gpsBadge(gps.status, gps.satellites)
                                                const connSt = connBadge(conn.status)
                                                const scSt = scoreBadge(v.health_score)

                                                return (
                                                    <tr key={v.vehicle_id} className="hover:bg-slate-50/70 transition-colors">
                                                        <td className="px-5 py-3.5">
                                                            <div className="flex items-center gap-2.5">
                                                                <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center flex-none">
                                                                    <Truck size={15} className="text-blue-600" />
                                                                </div>
                                                                <div className="min-w-0">
                                                                    <p className="font-semibold text-slate-800 truncate">{v.vehicle_name || `Vehicle ${v.vehicle_id}`}</p>
                                                                    <p className="text-xs text-slate-400">{v.group || `ID ${v.vehicle_id}`}</p>
                                                                </div>
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
                                                                v.status === "MOVING"  ? "text-blue-700 bg-blue-50 border border-blue-200" :
                                                                v.status === "IDLING"  ? "text-amber-700 bg-amber-50 border border-amber-200" :
                                                                v.status === "OFFLINE" ? "text-red-700 bg-red-50 border-red-200" :
                                                                                        "text-slate-600 bg-slate-100 border border-slate-200"
                                                                }`}>
                                                                <span className={`w-1.5 h-1.5 rounded-full ${v.status === "MOVING" ? "bg-blue-500" : v.status === "IDLING" ? "bg-amber-500" : v.status === "OFFLINE" ? "bg-red-500" : "bg-slate-400"}`} />
                                                                {v.status || "STOPPED"}
                                                            </span>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <div className="flex items-center gap-2">
                                                                <div className="w-16 bg-slate-100 rounded-full h-2 overflow-hidden">
                                                                    <div
                                                                        className={`h-full rounded-full ${scSt.bar}`}
                                                                        style={{ width: `${Math.min(100, Math.max(0, v.health_score ?? 0))}%` }}
                                                                    />
                                                                </div>
                                                                <span className="text-xs font-bold text-slate-800">{v.health_score ?? 0}%</span>
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <div className="flex items-center gap-1.5">
                                                                <Battery size={14} className={bat.voltage ? "text-slate-600" : "text-slate-300"} />
                                                                <Badge label={batSt.label} cls={batSt.cls} />
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <div className="flex items-center gap-1.5">
                                                                <MapPin size={14} className="text-slate-400" />
                                                                <Badge label={gpsSt.label} cls={gpsSt.cls} />
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <div className="flex items-center gap-1.5">
                                                                <Wifi size={14} className="text-slate-400" />
                                                                <Badge label={connSt.label} cls={connSt.cls} />
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <div className="flex items-center gap-1.5">
                                                                <Clock3 size={13} className="text-slate-400" />
                                                                <span className="text-slate-600 text-xs">{fmtAgo(conn.seconds_since_update)}</span>
                                                            </div>
                                                        </td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}

                        <div className="h-6" />
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
