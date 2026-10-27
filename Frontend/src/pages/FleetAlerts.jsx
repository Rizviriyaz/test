import { useCallback, useEffect, useState, useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import {
    AlertTriangle,
    Info,
    XCircle,
    CheckCircle2,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
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
        iconCls: "text-rose-600",
        textCls: "text-rose-700",
        dot: "bg-status-offline",
        rule: "before:bg-status-offline",
    },
    WARNING: {
        icon: AlertTriangle,
        label: "Warning",
        iconCls: "text-amber-600",
        textCls: "text-amber-800",
        dot: "bg-status-idling",
        rule: "before:bg-status-idling",
    },
    INFO: {
        icon: Info,
        label: "Info",
        iconCls: "text-slate-400",
        textCls: "text-slate-600",
        dot: "bg-slate-400",
        rule: "before:bg-transparent",
    },
}

// ── Sub-components ────────────────────────────────────────────────────────────

function AlertRow({ alert }) {
    const sev = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.INFO
    const Icon = sev.icon
    const vehicleLabel = alert.registration_plate || alert.plate || alert.vehicle_name || "Fleet-wide"

    return (
        <li className={`relative grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 px-4 py-3 before:absolute before:inset-y-0 before:left-0 before:w-0.5 ${sev.rule} md:grid-cols-[auto_minmax(0,1fr)_minmax(0,220px)_auto] md:items-start`}>
            <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${sev.iconCls}`} aria-hidden="true" />

            <div className="min-w-0">
                <p className="text-[13px] font-medium leading-snug text-slate-900">
                    <span className={`mr-2 text-xs font-medium ${sev.textCls}`}>{sev.label}</span>
                    {alert.title || alert.message}
                    {alert.value && (
                        <span className="ml-2 whitespace-nowrap font-mono text-xs text-slate-600">{alert.value}</span>
                    )}
                </p>
                {alert.title && alert.message && alert.title !== alert.message && (
                    <p className="mt-0.5 text-xs leading-relaxed text-slate-600">{alert.message}</p>
                )}
            </div>

            <div className="col-start-2 mt-1.5 min-w-0 text-xs md:col-start-auto md:mt-0">
                <p className="truncate">
                    <span className="font-mono font-semibold text-slate-800">{vehicleLabel}</span>
                    {alert.registration_plate && alert.vehicle_name && alert.registration_plate !== alert.vehicle_name && (
                        <span className="text-slate-500"> · {alert.vehicle_name}</span>
                    )}
                </p>
                <p className="truncate text-[11px] text-slate-500">
                    {[alert.group, alert.vehicle_id ? `Unit #${alert.vehicle_id}` : null].filter(Boolean).join(" · ")}
                </p>
            </div>

            {/* The vehicle's last reported message (or GPS fix, or the harsh event's time), not when the condition began */}
            <p className="col-start-2 mt-1 whitespace-nowrap text-[11px] tabular-nums text-slate-500 md:col-start-auto md:mt-0.5 md:text-right"
               title={alert.alert_type === "HARSH_DRIVING" ? "Event time" : "Vehicle's last reported message"}>
                {alert.timestamp ? `${alert.alert_type === "HARSH_DRIVING" ? "" : "Last reported "}${formatDateTime(alert.timestamp)}` : "—"}
            </p>
        </li>
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
                onRefresh={refresh}
                loading={loading}
                lastUpdated={data?.generated_at ? formatUAETime(data.generated_at) : (lastUpdated ? formatUAETime(lastUpdated) : null)}
                lastUpdatedLabel={data?.generated_at ? "Data as of" : "Updated"}
            />

            <div className="space-y-4">
                {data?.safety_partial && (
                    <div role="status" className="flex items-center gap-2 rounded-lg border border-amber-200/80 bg-amber-50/70 px-3 py-2 text-xs text-amber-900 shadow-2xs">
                        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
                        <span>
                            Partial: harsh-driving alerts could not be checked for {data.safety_unavailable_vehicles?.length || "some"} vehicle(s)
                            {data.safety_unavailable_vehicles?.length ? ` (${data.safety_unavailable_vehicles.slice(0, 5).join(", ")}${data.safety_unavailable_vehicles.length > 5 ? "…" : ""})` : ""}.
                        </span>
                    </div>
                )}
                {/* Severity counts double as the severity filter */}
                <KpiStrip label="Alert severity filter" className="grid-cols-2 md:grid-cols-4">
                    <KpiCard
                        label="All alerts"
                        value={counts.total}
                        selected={sevFilter === "ALL"}
                        onClick={() => handleFilterChange("ALL")}
                        loading={loading && !data}
                        title="Show all alerts"
                    />
                    <KpiCard
                        label="Critical"
                        dot="bg-status-offline"
                        value={counts.critical}
                        valueColor={counts.critical > 0 ? "text-rose-700" : "text-slate-900"}
                        selected={sevFilter === "CRITICAL"}
                        onClick={() => handleFilterChange("CRITICAL")}
                        loading={loading && !data}
                        title="Show critical alerts only"
                    />
                    <KpiCard
                        label="Warning"
                        dot="bg-status-idling"
                        value={counts.warning}
                        valueColor={counts.warning > 0 ? "text-amber-800" : "text-slate-900"}
                        selected={sevFilter === "WARNING"}
                        onClick={() => handleFilterChange("WARNING")}
                        loading={loading && !data}
                        title="Show warning alerts only"
                    />
                    <KpiCard
                        label="Info"
                        dot="bg-slate-400"
                        value={counts.info}
                        selected={sevFilter === "INFO"}
                        onClick={() => handleFilterChange("INFO")}
                        loading={loading && !data}
                        title="Show info alerts only"
                    />
                </KpiStrip>

                <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                    <div className="flex flex-col gap-2 border-b border-slate-100 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
                        <SearchInput
                            id="alerts-search"
                            value={search}
                            onChange={setSearch}
                            placeholder="Search alert, vehicle or group…"
                            className="w-full sm:max-w-xs"
                        />
                        <p className="text-xs tabular-nums text-slate-500" aria-live="polite">
                            <span className="font-medium text-slate-800">{filtered.length}</span>{" "}
                            {sevFilter === "ALL" ? "" : `${SEVERITY_CONFIG[sevFilter]?.label.toLowerCase() || sevFilter.toLowerCase()} `}
                            event{filtered.length !== 1 ? "s" : ""}
                            {data?.alerts_capped && (
                                <span title="Only the newest harsh-driving events are listed; the counts above include all of them">
                                    {" "}· showing {data.displayed_alerts ?? allAlerts.length} of {data.total_alerts} alerts
                                    {" "}(newest {data.harsh_driving_displayed} of {data.harsh_driving_total} harsh-driving events listed)
                                </span>
                            )}
                        </p>
                    </div>

                    {loading && !data ? (
                        <LoadingState label="Loading fleet alerts…" className="border-0" />
                    ) : error ? (
                        <ErrorState message={error} onRetry={refresh} className="border-0" />
                    ) : filtered.length === 0 ? (
                        <EmptyState
                            icon={CheckCircle2}
                            className="border-0"
                            title={allAlerts.length === 0 ? "No active alerts" : "No alerts match your filters"}
                            message={allAlerts.length === 0 ? "All telemetry is within normal thresholds." : undefined}
                            action={(search || sevFilter !== "ALL") && (
                                <button
                                    type="button"
                                    onClick={() => {
                                        setSearch("")
                                        handleFilterChange("ALL")
                                    }}
                                    className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                >
                                    Clear filters
                                </button>
                            )}
                        />
                    ) : (
                        <ul className="divide-y divide-slate-100">
                            {filtered.map((alert, i) => (
                                <AlertRow
                                    key={alert.id || `${alert.vehicle_id}-${alert.alert_type}-${i}`}
                                    alert={alert}
                                />
                            ))}
                        </ul>
                    )}
                </section>
            </div>
        </AppShell>
    )
}
