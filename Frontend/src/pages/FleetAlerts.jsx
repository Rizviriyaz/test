import { useCallback, useEffect, useState, useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import {
    Bell,
    AlertTriangle,
    Info,
    XCircle,
    Truck,
    CheckCircle2,
    Clock3,
    Tag,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import { EmptyState, ErrorState, LoadingState } from "../components/Common/StateViews"
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
        <div className={`flex gap-3 rounded-xl border border-l-4 border-slate-200 ${sev.borderCls} bg-white p-3.5 shadow-xs transition-colors hover:border-slate-300`}>
            <div className={`mt-0.5 flex-none ${sev.iconCls}`}>
                <Icon size={18} />
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

                <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-slate-400">
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

    // Bumped by refresh(); the effect refetches and ignores responses from a superseded request
    const [reloadKey, setReloadKey] = useState(0)

    useEffect(() => {
        let active = true
        async function load() {
            try {
                // A manual refresh bypasses the 30 s client cache
                const res = await getFleetAlertsAnalytics({ skipCache: reloadKey > 0 })
                if (active) {
                    setData(res)
                    setLastUpdated(new Date())
                }
            } catch (e) {
                if (active) setError(e.message)
            } finally {
                if (active) setLoading(false)
            }
        }
        load()
        return () => { active = false }
    }, [reloadKey])

    const refresh = useCallback(() => {
        setLoading(true)
        setError(null)
        setReloadKey((k) => k + 1)
    }, [])

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
        <AppShell>
            <PageHeader
                title="Fleet Alerts"
                subtitle="Battery, connectivity and sensor threshold events"
                icon={Bell}
                onRefresh={refresh}
                loading={loading}
                lastUpdated={lastUpdated ? formatUAETime(lastUpdated) : null}
            />

            <div className="space-y-5">
                {/* ─── SEVERITY KPI CARDS ───────────────────────────── */}
                <section aria-label="Alert Severity Breakdown">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
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

                {/* ─── SEARCH BAR & ACTIVE FILTER STATUS ────────────── */}
                <section className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <SearchInput
                        id="alerts-search"
                        value={search}
                        onChange={setSearch}
                        placeholder="Search alert, vehicle or group…"
                        className="w-full sm:max-w-sm"
                    />

                    <p className="text-xs text-slate-500">
                        <strong className="font-semibold text-slate-800">{filtered.length}</strong>{" "}
                        {sevFilter === "ALL" ? "" : `${SEVERITY_CONFIG[sevFilter]?.label.toLowerCase() || sevFilter.toLowerCase()} `}
                        event{filtered.length !== 1 ? "s" : ""}
                    </p>
                </section>

                {/* ─── ALERTS LIST & EMPTY STATES ──────────────────── */}
                {loading && !data ? (
                    <LoadingState label="Loading fleet alerts…" />
                ) : error ? (
                    <ErrorState message={error} onRetry={refresh} />
                ) : filtered.length === 0 ? (
                    <EmptyState
                        icon={CheckCircle2}
                        title={allAlerts.length === 0 ? "No active alerts" : "No alerts match your filters"}
                        message={allAlerts.length === 0 ? "All telemetry is within normal thresholds." : undefined}
                        action={(search || sevFilter !== "ALL") && (
                            <button
                                type="button"
                                onClick={() => {
                                    setSearch("")
                                    handleFilterChange("ALL")
                                }}
                                className="text-xs font-semibold text-[#33758F] hover:underline cursor-pointer"
                            >
                                Clear filters
                            </button>
                        )}
                    />
                ) : (
                    <div className="space-y-2.5">
                        {filtered.map((alert, i) => (
                            <AlertCard
                                key={alert.id || `${alert.vehicle_id}-${alert.alert_type}-${i}`}
                                alert={alert}
                            />
                        ))}
                    </div>
                )}
            </div>
        </AppShell>
    )
}
