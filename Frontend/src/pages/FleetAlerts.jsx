import { useCallback, useEffect, useState, useMemo } from "react"
import {
    Bell, AlertTriangle, Info, XCircle,
    RefreshCw, Loader2, AlertCircle, Search,
    Truck, CheckCircle2, Clock3, Tag,
} from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import { getFleetAlertsAnalytics } from "../api/fleetApi"
import { formatUAEDateTime } from "../utils/datetime"

// ── Utilities ─────────────────────────────────────────────────────────────────

const formatDateTime = (ts) => {
    return formatUAEDateTime(ts)
}

const SEVERITY_MAP = {
    CRITICAL: {
        icon: XCircle,
        label: "Critical",
        badgeCls: "text-red-700 bg-red-50 border-red-200",
        iconCls: "text-red-500",
        borderCls: "border-l-red-500",
        cardBg: "hover:bg-red-50/20",
    },
    WARNING: {
        icon: AlertTriangle,
        label: "Warning",
        badgeCls: "text-amber-700 bg-amber-50 border-amber-200",
        iconCls: "text-amber-500",
        borderCls: "border-l-amber-500",
        cardBg: "hover:bg-amber-50/20",
    },
    INFO: {
        icon: Info,
        label: "Info",
        badgeCls: "text-blue-700 bg-blue-50 border-blue-200",
        iconCls: "text-blue-500",
        borderCls: "border-l-blue-400",
        cardBg: "hover:bg-blue-50/20",
    },
}

// ── Sub-components ────────────────────────────────────────────────────────────

function AlertCard({ alert }) {
    const sev = SEVERITY_MAP[alert.severity] || SEVERITY_MAP.INFO
    const Icon = sev.icon
    return (
        <div className={`flex gap-4 p-4 rounded-2xl border border-l-4 ${sev.borderCls} bg-white shadow-sm transition-all hover:shadow-md ${sev.cardBg}`}>
            <div className={`flex-none mt-0.5 ${sev.iconCls}`}>
                <Icon size={20} />
            </div>
            <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="min-w-0 flex-1">
                        <p className="font-bold text-slate-800 text-sm">{alert.title || alert.message}</p>
                        {alert.title && alert.message && alert.title !== alert.message && (
                            <p className="text-xs text-slate-600 mt-1">{alert.message}</p>
                        )}
                    </div>
                    <div className="flex items-center gap-2 flex-none">
                        {alert.value && (
                            <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
                                {alert.value}
                            </span>
                        )}
                        <span className={`text-xs font-bold px-2.5 py-0.5 rounded-full border ${sev.badgeCls}`}>
                            {sev.label}
                        </span>
                    </div>
                </div>

                <div className="flex items-center gap-4 mt-3 text-xs text-slate-400 flex-wrap">
                    <div className="flex items-center gap-1 text-slate-600 font-medium">
                        <Truck size={13} className="text-slate-400" />
                        <span>{alert.vehicle_name || "Fleet-wide"}</span>
                        {alert.vehicle_id && <span className="text-slate-400 font-normal">· ID {alert.vehicle_id}</span>}
                    </div>
                    {alert.group && (
                        <span className="text-slate-500 bg-slate-100 px-2 py-0.5 rounded-md text-[11px]">
                            {alert.group}
                        </span>
                    )}
                    {alert.alert_type && (
                        <div className="flex items-center gap-1 text-slate-500">
                            <Tag size={11} className="text-slate-400" />
                            <span>{alert.alert_type}</span>
                        </div>
                    )}
                    {alert.timestamp && (
                        <div className="flex items-center gap-1 text-slate-400 ml-auto">
                            <Clock3 size={12} />
                            <span>{formatDateTime(alert.timestamp)}</span>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function FleetAlerts() {
    const [data, setData]         = useState(null)
    const [loading, setLoading]   = useState(true)
    const [error, setError]       = useState(null)
    const [search, setSearch]     = useState("")
    const [sevFilter, setSevFilter] = useState("ALL")

    const load = useCallback(async (mountedRef = { current: true }) => {
        setLoading(true); setError(null)
        try {
            const res = await getFleetAlertsAnalytics()
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

    const allAlerts = useMemo(() => data?.alerts || [], [data])

    const filtered = useMemo(() => {
        let a = allAlerts
        if (sevFilter !== "ALL") a = a.filter(x => x.severity === sevFilter)
        if (search.trim()) {
            const q = search.toLowerCase()
            a = a.filter(x =>
                (x.message || "").toLowerCase().includes(q) ||
                (x.title || "").toLowerCase().includes(q) ||
                (x.vehicle_name || "").toLowerCase().includes(q) ||
                (x.alert_type || "").toLowerCase().includes(q) ||
                (x.group || "").toLowerCase().includes(q)
            )
        }
        return a
    }, [allAlerts, sevFilter, search])

    const counts = useMemo(() => ({
        total:    data?.total_alerts ?? allAlerts.length,
        critical: data?.critical_count ?? allAlerts.filter(a => a.severity === "CRITICAL").length,
        warning:  data?.warning_count ?? allAlerts.filter(a => a.severity === "WARNING").length,
        info:     data?.info_count ?? allAlerts.filter(a => a.severity === "INFO").length,
    }), [data, allAlerts])

    return (
        <SidebarProvider>
            <div className="flex h-screen bg-slate-50 font-sans overflow-hidden">
                <Sidebar />
                <div className="flex-1 flex flex-col overflow-hidden">
                    <header className="flex-none bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between gap-4">
                        <div>
                            <h1 className="text-lg font-bold text-slate-800 flex items-center gap-2">
                                <Bell size={20} className="text-amber-500" />
                                Fleet Telemetry Alerts
                            </h1>
                            <p className="text-xs text-slate-500 mt-0.5">Critical Battery · Offline Units · Extreme Safety Clusters · Telemetry Warnings</p>
                        </div>
                        <button
                            id="alerts-refresh"
                            onClick={() => load({ current: true })}
                            className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2 rounded-xl transition-colors cursor-pointer"
                        >
                            <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
                            Refresh
                        </button>
                    </header>

                    <main className="flex-1 overflow-y-auto px-6 py-6 space-y-6">

                        {/* KPI summary chips */}
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            {[
                                { label: "Total Alerts", value: counts.total,    color: "bg-slate-100 border-slate-200 text-slate-800",  key: "ALL" },
                                { label: "Critical",     value: counts.critical,  color: "bg-red-50 border-red-200 text-red-700",        key: "CRITICAL" },
                                { label: "Warning",      value: counts.warning,   color: "bg-amber-50 border-amber-200 text-amber-700",  key: "WARNING" },
                                { label: "Info",         value: counts.info,      color: "bg-blue-50 border-blue-200 text-blue-700",    key: "INFO" },
                            ].map(c => (
                                <button
                                    key={c.key}
                                    id={`alert-filter-${c.key.toLowerCase()}`}
                                    onClick={() => setSevFilter(c.key)}
                                    className={`${c.color} border rounded-2xl p-4 text-left transition-all hover:shadow-md cursor-pointer ${
                                        sevFilter === c.key ? "ring-2 ring-blue-500 shadow-sm" : ""
                                    }`}
                                >
                                    <p className="text-xs font-semibold uppercase tracking-wide mb-1 opacity-75">{c.label}</p>
                                    {loading ? (
                                        <div className="h-7 w-12 bg-black/10 animate-pulse rounded-lg" />
                                    ) : (
                                        <p className="text-2xl font-bold">{c.value}</p>
                                    )}
                                </button>
                            ))}
                        </div>

                        {/* Search */}
                        <div className="relative max-w-sm">
                            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                id="alerts-search"
                                type="text"
                                placeholder="Search alert title, message, vehicle, group..."
                                value={search}
                                onChange={e => setSearch(e.target.value)}
                                className="w-full pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-xl bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/30"
                            />
                        </div>

                        {/* Alert list */}
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
                                <CheckCircle2 size={40} className="text-emerald-500" />
                                <p className="text-sm font-medium text-slate-700">
                                    {allAlerts.length === 0 ? "No active alerts — fleet health is optimal! 🎉" : "No alerts match your filter"}
                                </p>
                            </div>
                        ) : (
                            <div className="space-y-3">
                                <p className="text-xs text-slate-500 font-medium">{filtered.length} alert{filtered.length !== 1 ? "s" : ""}</p>
                                {filtered.map((alert, i) => (
                                    <AlertCard key={alert.id || `${alert.vehicle_id}-${i}`} alert={alert} />
                                ))}
                            </div>
                        )}

                        <div className="h-6" />
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
