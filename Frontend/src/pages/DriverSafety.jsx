import { useEffect, useState, useMemo } from "react"
import {
    ShieldCheck, ShieldAlert, AlertTriangle,
    RefreshCw, Loader2, AlertCircle, Search,
    Truck, ChevronDown, TrendingDown,
} from "lucide-react"
import {
    ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { getFleetSafetyAnalytics } from "../api/fleetApi"
import { formatUAEDateTime } from "../utils/datetime"

// ── Utilities ─────────────────────────────────────────────────────────────────

const riskLevel = (events) => {
    const ev = Number(events || 0)
    if (ev >= 10) return { label: "High Risk",    cls: "text-red-700 bg-red-50 border-red-200" }
    if (ev >= 4)  return { label: "Medium Risk",  cls: "text-amber-700 bg-amber-50 border-amber-200" }
    if (ev >= 1)  return { label: "Low Risk",     cls: "text-yellow-700 bg-yellow-50 border-yellow-200" }
    return               { label: "Safe",         cls: "text-emerald-700 bg-emerald-50 border-emerald-200" }
}

const eventBadge = (type) => {
    if (type === "Harsh Braking") return "text-red-700 bg-red-50 border-red-200"
    if (type === "Harsh Acceleration") return "text-amber-700 bg-amber-50 border-amber-200"
    if (type === "Harsh Cornering") return "text-purple-700 bg-purple-50 border-purple-200"
    return "text-slate-700 bg-slate-100 border-slate-200"
}

// ── Sub-components ────────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, sub, color = "blue", loading }) {
    const colorMap = {
        blue:   "from-blue-500/10 to-blue-600/5 text-blue-600 border-blue-200/60",
        amber:  "from-amber-500/10 to-amber-600/5 text-amber-600 border-amber-200/60",
        green:  "from-emerald-500/10 to-emerald-600/5 text-emerald-600 border-emerald-200/60",
        red:    "from-red-500/10 to-red-600/5 text-red-600 border-red-200/60",
        purple: "from-violet-500/10 to-violet-600/5 text-violet-600 border-violet-200/60",
    }
    return (
        <div className={`bg-gradient-to-br ${colorMap[color] || colorMap.blue} border rounded-2xl p-5 flex items-start gap-4 transition-all hover:shadow-md`}>
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

// ── Main Component ────────────────────────────────────────────────────────────

export default function DriverSafety() {
    const [hours, setHours] = useState(24)
    const [data, setData]     = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError]   = useState(null)
    const [search, setSearch] = useState("")
    const [activeTab, setActiveTab] = useState("vehicles") // "vehicles" | "events"

    const load = async (mountedRef = { current: true }) => {
        setLoading(true); setError(null)
        try {
            const res = await getFleetSafetyAnalytics(hours)
            if (mountedRef.current) setData(res)
        } catch (e) {
            if (mountedRef.current) setError(e.message)
        } finally {
            if (mountedRef.current) setLoading(false)
        }
    }

    useEffect(() => {
        const mountedRef = { current: true }
        load(mountedRef)
        return () => { mountedRef.current = false }
    }, [hours])

    const vehicles = useMemo(() => {
        if (!data?.by_vehicle) return []
        let v = data.by_vehicle
        if (search.trim()) {
            const q = search.toLowerCase()
            v = v.filter(x => (x.vehicle_name || "").toLowerCase().includes(q) || (x.group || "").toLowerCase().includes(q))
        }
        return v
    }, [data, search])

    const events = useMemo(() => {
        if (!data?.events) return []
        let evList = data.events
        if (search.trim()) {
            const q = search.toLowerCase()
            evList = evList.filter(x =>
                (x.vehicle_name || "").toLowerCase().includes(q) ||
                (x.event_type || "").toLowerCase().includes(q) ||
                (x.group || "").toLowerCase().includes(q)
            )
        }
        return evList
    }, [data, search])

    const chartData = useMemo(() => {
        if (!data?.by_vehicle) return []
        return data.by_vehicle
            .filter(v => (v.total || v.total_events || 0) > 0).slice(0, 10)
            .map(v => ({
                name: v.vehicle_name?.split(" ").slice(-2).join(" ") || `V${v.vehicle_id}`,
                braking:   v.braking || v.harsh_braking || 0,
                accel:     v.accel || v.harsh_acceleration || 0,
                cornering: v.cornering || v.harsh_cornering || 0,
            }))
    }, [data])

    const summary = data?.summary || {}

    return (
        <SidebarProvider>
            <div className="flex h-screen bg-slate-50 font-sans overflow-hidden">
                <Sidebar />
                <div className="flex-1 flex flex-col overflow-hidden">
                    <header className="flex-none bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between gap-4">
                        <div>
                            <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
                                <ShieldAlert size={20} className="text-red-500" />
                                Driver Safety Analytics
                            </h1>
                            <p className="text-xs text-slate-500 mt-0.5">Harsh Braking · Acceleration · Cornering G-Force Detection</p>
                        </div>
                        <div className="flex items-center gap-3">
                            <div className="relative">
                                <select
                                    id="safety-period"
                                    value={hours}
                                    onChange={e => setHours(Number(e.target.value))}
                                    className="appearance-none bg-white border border-slate-200 rounded-xl pl-3 pr-8 py-2 text-sm font-medium text-slate-700 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500/30"
                                >
                                    <option value={24}>Last 24 h</option>
                                    <option value={168}>Last 7 days</option>
                                    <option value={720}>Last 30 days</option>
                                </select>
                                <ChevronDown size={14} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
                            </div>
                            <button
                                id="safety-refresh"
                                onClick={() => load({ current: true })}
                                className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors cursor-pointer"
                            >
                                <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                                Refresh
                            </button>
                        </div>
                    </header>

                    <main className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
                        {/* KPIs */}
                        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                            <KpiCard icon={ShieldCheck}   label="Total Events"     loading={loading} value={summary.total_events ?? 0} color="red" />
                            <KpiCard icon={AlertTriangle} label="Harsh Braking"    loading={loading} value={summary.harsh_braking ?? 0} color="red" />
                            <KpiCard icon={TrendingDown}  label="Harsh Accel."     loading={loading} value={summary.harsh_acceleration ?? 0} color="amber" />
                            <KpiCard icon={ShieldAlert}   label="Harsh Cornering"  loading={loading} value={summary.harsh_cornering ?? 0} color="purple" />
                            <KpiCard icon={Truck}         label="Units Affected"   loading={loading} value={summary.vehicles_affected ?? 0} color="blue" />
                        </div>

                        {/* Chart */}
                        <div className="bg-white rounded-2xl border border-slate-200 p-5 shadow-sm">
                            <div className="mb-4">
                                <h2 className="text-base font-bold text-slate-800">Safety Events Breakdown</h2>
                                <p className="text-xs text-slate-500 mt-0.5">Top vehicles ranked by total incidents</p>
                            </div>
                            {loading ? (
                                <div className="h-56 flex items-center justify-center"><Loader2 size={24} className="animate-spin text-slate-400" /></div>
                            ) : chartData.length === 0 ? (
                                <div className="flex flex-col items-center justify-center py-16 gap-3 text-slate-400">
                                    <ShieldCheck size={36} className="text-emerald-400" />
                                    <p className="text-sm font-medium">No safety events recorded in this period 🎉</p>
                                </div>
                            ) : (
                                <ResponsiveContainer width="100%" height={240}>
                                    <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 40, left: 0 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                                        <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-35} textAnchor="end" />
                                        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                                        <Tooltip />
                                        <Legend />
                                        <Bar dataKey="braking"   name="Harsh Braking" stackId="a" fill="#ef4444" />
                                        <Bar dataKey="accel"     name="Harsh Accel."  stackId="a" fill="#f59e0b" />
                                        <Bar dataKey="cornering" name="Cornering"     stackId="a" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                                    </BarChart>
                                </ResponsiveContainer>
                            )}
                        </div>

                        {/* Tabs & Search */}
                        <div className="flex items-center justify-between gap-4 flex-wrap">
                            <div className="flex items-center gap-2 bg-slate-200/60 p-1 rounded-xl">
                                <button
                                    id="tab-vehicles"
                                    onClick={() => setActiveTab("vehicles")}
                                    className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                                        activeTab === "vehicles" ? "bg-white text-slate-800 shadow-sm" : "text-slate-600 hover:text-slate-800"
                                    }`}
                                >
                                    Vehicle Summary ({vehicles.length})
                                </button>
                                <button
                                    id="tab-events"
                                    onClick={() => setActiveTab("events")}
                                    className={`px-4 py-1.5 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                                        activeTab === "events" ? "bg-white text-slate-800 shadow-sm" : "text-slate-600 hover:text-slate-800"
                                    }`}
                                >
                                    Event Feed ({events.length})
                                </button>
                            </div>

                            <div className="relative flex-1 max-w-sm">
                                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input
                                    id="safety-search"
                                    type="text"
                                    placeholder={activeTab === "vehicles" ? "Search vehicle or group..." : "Search event or vehicle..."}
                                    value={search}
                                    onChange={e => setSearch(e.target.value)}
                                    className="w-full pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/30"
                                />
                            </div>
                        </div>

                        {/* Content: Vehicle Table OR Event Feed */}
                        {loading ? (
                            <div className="flex items-center justify-center py-24"><Loader2 size={28} className="animate-spin text-slate-400" /></div>
                        ) : error ? (
                            <div className="flex flex-col items-center justify-center py-24 text-slate-400 gap-3">
                                <AlertCircle size={32} />
                                <p className="text-sm font-medium">{error}</p>
                            </div>
                        ) : activeTab === "vehicles" ? (
                            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="bg-slate-50 border-b border-slate-200">
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-5 py-3.5">Vehicle</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Risk Level</th>
                                                <th className="text-center text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Total Events</th>
                                                <th className="text-center text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Braking</th>
                                                <th className="text-center text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Acceleration</th>
                                                <th className="text-center text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Cornering</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {vehicles.length === 0 ? (
                                                <tr>
                                                    <td colSpan={6} className="text-center text-slate-400 py-12 text-sm">No vehicles found matching criteria</td>
                                                </tr>
                                            ) : vehicles.map(v => {
                                                const tot = v.total ?? v.total_events ?? 0
                                                const risk = riskLevel(tot)
                                                return (
                                                    <tr key={v.vehicle_id || v.vehicle_name} className="hover:bg-slate-50/70 transition-colors">
                                                        <td className="px-5 py-3.5">
                                                            <div className="flex items-center gap-2.5">
                                                                <div className="w-8 h-8 rounded-lg bg-red-50 flex items-center justify-center flex-none">
                                                                    <Truck size={15} className="text-red-500" />
                                                                </div>
                                                                <div className="min-w-0">
                                                                    <p className="font-semibold text-slate-800 truncate">{v.vehicle_name || `Vehicle ${v.vehicle_id}`}</p>
                                                                    <p className="text-xs text-slate-400">{v.group || "General Fleet"}</p>
                                                                </div>
                                                            </div>
                                                        </td>
                                                        <td className="px-4 py-3.5">
                                                            <span className={`inline-flex items-center text-xs font-semibold px-2.5 py-0.5 rounded-full border ${risk.cls}`}>{risk.label}</span>
                                                        </td>
                                                        <td className="px-4 py-3.5 text-center font-bold text-slate-800">{tot}</td>
                                                        <td className="px-4 py-3.5 text-center text-red-600 font-semibold">{v.braking ?? v.harsh_braking ?? 0}</td>
                                                        <td className="px-4 py-3.5 text-center text-amber-600 font-semibold">{v.accel ?? v.harsh_acceleration ?? 0}</td>
                                                        <td className="px-4 py-3.5 text-center text-purple-600 font-semibold">{v.cornering ?? v.harsh_cornering ?? 0}</td>
                                                    </tr>
                                                )
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        ) : (
                            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="bg-slate-50 border-b border-slate-200">
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-5 py-3.5">Vehicle</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Event Type</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Speed</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">G-Force</th>
                                                <th className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wide px-4 py-3.5">Timestamp</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-100">
                                            {events.length === 0 ? (
                                                <tr>
                                                    <td colSpan={5} className="text-center text-slate-400 py-12 text-sm">No safety events recorded in this period</td>
                                                </tr>
                                            ) : events.map(ev => (
                                                <tr key={ev.id} className="hover:bg-slate-50/70 transition-colors">
                                                    <td className="px-5 py-3.5">
                                                        <div className="flex items-center gap-2">
                                                            <Truck size={14} className="text-slate-400 flex-none" />
                                                            <div>
                                                                <p className="font-semibold text-slate-800 text-xs">{ev.vehicle_name}</p>
                                                                <p className="text-[11px] text-slate-400">{ev.group}</p>
                                                            </div>
                                                        </div>
                                                    </td>
                                                    <td className="px-4 py-3.5">
                                                        <span className={`inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full border ${eventBadge(ev.event_type)}`}>
                                                            {ev.event_type}
                                                        </span>
                                                    </td>
                                                    <td className="px-4 py-3.5 font-medium text-slate-700">
                                                        {ev.speed_kmh != null ? `${ev.speed_kmh} km/h` : "—"}
                                                    </td>
                                                    <td className="px-4 py-3.5 font-medium text-slate-700">
                                                        {ev.g_force != null ? `${ev.g_force}g` : "—"}
                                                    </td>
                                                    <td className="px-4 py-3.5 text-xs text-slate-500">
                                                        {formatUAEDateTime(ev.timestamp)}
                                                    </td>
                                                </tr>
                                            ))}
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
