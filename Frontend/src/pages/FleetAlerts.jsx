import { useCallback, useEffect, useState, useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Bell,
    AlertTriangle,
    Info,
    XCircle,
    Loader2,
    AlertCircle,
    Truck,
    CheckCircle2,
    Clock3,
    Tag,
} from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import PageHeader from "../components/Common/PageHeader"
import KpiCard from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import { getFleetAlertsAnalytics } from "../api/fleetApi"
import { formatUAEDateTime, formatUAETime } from "../utils/datetime"

// ── Utilities ─────────────────────────────────────────────────────────────────

const formatDateTime = (ts) => formatUAEDateTime(ts)

const SEVERITY_CONFIG = {
    CRITICAL: {
        icon: XCircle,
        label: "Critical",
        badgeCls: "text-red-700 bg-red-50 border-red-200",
        iconCls: "text-red-500",
        borderCls: "border-l-red-500",
    },
    WARNING: {
        icon: AlertTriangle,
        label: "Warning",
        badgeCls: "text-amber-700 bg-amber-50 border-amber-200",
        iconCls: "text-amber-500",
        borderCls: "border-l-amber-500",
    },
    INFO: {
        icon: Info,
        label: "Info",
        badgeCls: "text-[#33758F] bg-[#F0F6F9] border-[#D1E2EA]",
        iconCls: "text-[#33758F]",
        borderCls: "border-l-[#33758F]",
    },
}

// ── Sub-components ────────────────────────────────────────────────────────────

function AlertCard({ alert }) {
    const sev = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.INFO
    const Icon = sev.icon

    return (
        <div className={`flex gap-4 p-4 rounded-xl border border-slate-200 border-l-4 ${sev.borderCls} bg-white shadow-xs transition-all hover:shadow-sm hover:border-slate-300`}>
            <div className={`flex-none mt-0.5 ${sev.iconCls}`}>
                <Icon size={20} />
            </div>
            <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="min-w-0 flex-1">
                        <p className="font-bold text-slate-900 text-sm leading-snug">
                            {alert.title || alert.message}
                        </p>
                        {alert.title && alert.message && alert.title !== alert.message && (
                            <p className="text-xs text-slate-600 mt-1 leading-relaxed">
                                {alert.message}
                            </p>
                        )}
                    </div>
                    <div className="flex items-center gap-2 flex-none">
                        {alert.value && (
                            <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700 border border-slate-200">
                                {alert.value}
                            </span>
                        )}
                        <span className={`text-[11px] font-bold px-2.5 py-0.5 rounded-full border ${sev.badgeCls}`}>
                            {sev.label}
                        </span>
                    </div>
                </div>

                <div className="flex items-center gap-4 mt-3 text-xs text-slate-400 flex-wrap">
                    <div className="flex items-center gap-1.5 text-slate-700 font-semibold">
                        <Truck size={13} className="text-slate-400" />
                        <span>{alert.registration_plate || alert.plate || alert.vehicle_name || "Fleet-wide"}</span>
                        {alert.registration_plate && alert.vehicle_name && alert.registration_plate !== alert.vehicle_name && (
                            <span className="text-slate-400 font-normal">· {alert.vehicle_name}</span>
                        )}
                        {alert.vehicle_id && (
                            <span className="text-slate-400 font-normal">· Unit #{alert.vehicle_id}</span>
                        )}
                    </div>
                    {alert.group && (
                        <span className="text-slate-600 bg-slate-100 px-2 py-0.5 rounded-md text-[11px] font-medium border border-slate-200/60">
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
    const [searchParams, setSearchParams] = useSearchParams()
    const initialSev = (searchParams.get("severity") || "ALL").toUpperCase()

    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [search, setSearch] = useState("")
    const [sevFilter, setSevFilter] = useState(initialSev)
    const [lastUpdated, setLastUpdated] = useState(null)

    const load = useCallback(async (mountedRef = { current: true }) => {
        setLoading(true)
        setError(null)
        try {
            const res = await getFleetAlertsAnalytics()
            if (mountedRef.current) {
                setData(res)
                setLastUpdated(new Date())
            }
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

    // Update query param when filter changes
    const handleFilterChange = (key) => {
        setSevFilter(key)
        if (key === "ALL") {
            searchParams.delete("severity")
        } else {
            searchParams.set("severity", key)
        }
        setSearchParams(searchParams, { replace: true })
    }

    const allAlerts = useMemo(() => data?.alerts || [], [data])

    const filtered = useMemo(() => {
        let a = allAlerts
        if (sevFilter !== "ALL") a = a.filter(x => x.severity === sevFilter)
        if (search.trim()) {
            const q = search.toLowerCase()
            a = a.filter(x =>
                (x.message || "").toLowerCase().includes(q) ||
                (x.title || "").toLowerCase().includes(q) ||
                (x.registration_plate || "").toLowerCase().includes(q) ||
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
            <div className="flex h-screen bg-white text-slate-900 font-sans overflow-hidden antialiased">
                <Sidebar />
                <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                    <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-8 sm:py-8 space-y-6">
                        {/* ─── 1. PAGE HEADER ─────────────────────────────────── */}
                        <PageHeader
                            title="Fleet Telemetry Alerts"
                            subtitle="Critical battery · Offline units · Sensor thresholds · Operational telemetry warnings"
                            icon={Bell}
                            onRefresh={() => load({ current: true })}
                            loading={loading}
                            lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
                        />

                        {/* ─── 2. SEVERITY KPI CARDS ───────────────────────────── */}
                        <section aria-label="Alert Severity Breakdown">
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
                                <KpiCard
                                    label="Total Alerts"
                                    value={counts.total}
                                    valueColor="text-slate-900"
                                    sub="All active events"
                                    selected={sevFilter === "ALL"}
                                    onClick={() => handleFilterChange("ALL")}
                                    loading={loading && !data}
                                    title="Show all alerts"
                                />

                                <KpiCard
                                    label="Critical"
                                    value={counts.critical}
                                    valueColor="text-red-600"
                                    sub="Immediate risk"
                                    badge={counts.critical > 0 ? "Action Required" : undefined}
                                    badgeCls="bg-red-50 text-red-700 border-red-200"
                                    selected={sevFilter === "CRITICAL"}
                                    onClick={() => handleFilterChange("CRITICAL")}
                                    loading={loading && !data}
                                    title="Show critical alerts only"
                                />

                                <KpiCard
                                    label="Warning"
                                    value={counts.warning}
                                    valueColor="text-amber-600"
                                    sub="Threshold alerts"
                                    badge={counts.warning > 0 ? "Review" : undefined}
                                    badgeCls="bg-amber-50 text-amber-700 border-amber-200"
                                    selected={sevFilter === "WARNING"}
                                    onClick={() => handleFilterChange("WARNING")}
                                    loading={loading && !data}
                                    title="Show warning alerts only"
                                />

                                <KpiCard
                                    label="Info"
                                    value={counts.info}
                                    valueColor="text-[#33758F]"
                                    sub="Operational notices"
                                    selected={sevFilter === "INFO"}
                                    onClick={() => handleFilterChange("INFO")}
                                    loading={loading && !data}
                                    title="Show info alerts only"
                                />
                            </div>
                        </section>

                        {/* ─── 3. SEARCH BAR & ACTIVE FILTER STATUS ────────────── */}
                        <section className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                            <div className="w-full sm:max-w-md">
                                <SearchInput
                                    id="alerts-search"
                                    value={search}
                                    onChange={setSearch}
                                    placeholder="Search alert title, message, vehicle, group…"
                                />
                            </div>

                            <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
                                <span>Filter:</span>
                                <span className="bg-slate-100 px-2 py-0.5 rounded-md text-slate-800 font-bold border border-slate-200">
                                    {sevFilter === "ALL" ? "All Severities" : sevFilter}
                                </span>
                                <span>•</span>
                                <span>{filtered.length} event{filtered.length !== 1 ? "s" : ""}</span>
                            </div>
                        </section>

                        {/* ─── 4. ALERTS LIST & EMPTY STATES ──────────────────── */}
                        {loading && !data ? (
                            <div className="flex flex-col items-center justify-center py-24 rounded-xl border border-slate-200 bg-white">
                                <Loader2 size={28} className="animate-spin text-[#33758F]" />
                                <p className="mt-3 text-xs font-bold text-slate-800 uppercase tracking-wide">
                                    Loading active fleet alerts…
                                </p>
                            </div>
                        ) : error ? (
                            <div className="flex flex-col items-center justify-center py-20 rounded-xl border border-red-200 bg-red-50/50 p-6 text-center">
                                <AlertCircle size={32} className="text-red-500 mb-2" />
                                <p className="text-sm font-semibold text-red-700">{error}</p>
                                <button
                                    type="button"
                                    onClick={() => load({ current: true })}
                                    className="mt-3 text-xs font-bold text-[#33758F] hover:underline cursor-pointer"
                                >
                                    Retry Loading
                                </button>
                            </div>
                        ) : filtered.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-20 rounded-xl border border-slate-200 bg-white text-center p-6">
                                <CheckCircle2 size={40} className="text-emerald-500 mb-2" />
                                <h3 className="text-sm font-bold text-slate-800">
                                    {allAlerts.length === 0
                                        ? "No Active Telemetry Alerts"
                                        : "No Alerts Match Your Filter"}
                                </h3>
                                <p className="text-xs text-slate-500 mt-1 max-w-sm">
                                    {allAlerts.length === 0
                                        ? "Fleet operations and telemetry health are running smoothly within normal thresholds."
                                        : "Try selecting a different severity filter or clearing your search query."}
                                </p>
                                {search && (
                                    <button
                                        type="button"
                                        onClick={() => setSearch("")}
                                        className="mt-3 text-xs font-bold text-[#33758F] hover:underline cursor-pointer"
                                    >
                                        Clear search filter
                                    </button>
                                )}
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {filtered.map((alert, i) => (
                                    <AlertCard
                                        key={alert.id || `${alert.vehicle_id}-${alert.alert_type}-${i}`}
                                        alert={alert}
                                    />
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
