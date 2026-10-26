import { useCallback, useEffect, useState, useMemo } from "react"
import { Truck } from "lucide-react"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import { EmptyState, ErrorState, LoadingState } from "../components/Common/StateViews"
import { getFleetHealthAnalytics } from "../api/fleetApi"

// ── Utilities ─────────────────────────────────────────────────────────────────

const fmt = (n, d = 1) => (typeof n === "number" ? n.toFixed(d) : "—")

const TONE = {
    good: "bg-status-moving",
    warn: "bg-status-idling",
    bad: "bg-status-offline",
    none: "bg-slate-300",
}

const batteryBadge = (status, voltage) => {
    const s = String(status || "").toUpperCase()
    if (s === "HEALTHY") return { label: `${fmt(voltage, 1)} V`, note: "Healthy", tone: "good" }
    if (s === "WARNING") return { label: `${fmt(voltage, 1)} V`, note: "Low", tone: "warn" }
    if (s === "CRITICAL") return { label: `${fmt(voltage, 1)} V`, note: "Critical", tone: "bad" }
    return { label: voltage ? `${fmt(voltage, 1)} V` : "No data", note: null, tone: "none" }
}

const gpsBadge = (status, sats) => {
    const s = String(status || "").toUpperCase()
    if (s === "EXCELLENT") return { label: `${sats ?? "—"} sats`, note: "Excellent", tone: "good" }
    if (s === "GOOD") return { label: `${sats ?? "—"} sats`, note: "Good", tone: "good" }
    if (s === "WEAK") return { label: `${sats ?? "—"} sats`, note: "Weak", tone: "warn" }
    return { label: sats ? `${sats} sats` : "No GPS", note: null, tone: "none" }
}

const connBadge = (status) => {
    const s = String(status || "").toUpperCase()
    if (s === "STABLE") return { label: "Online", tone: "good" }
    if (s === "DELAYED") return { label: "Delayed", tone: "warn" }
    if (s === "OFFLINE") return { label: "Offline", tone: "bad" }
    return { label: "Unknown", tone: "none" }
}

const scoreTone = (score) => {
    const sc = Number(score ?? 0)
    if (sc >= 85) return { bar: "bg-status-moving", text: "text-slate-900" }
    if (sc >= 65) return { bar: "bg-status-idling", text: "text-amber-800" }
    return { bar: "bg-status-offline", text: "text-rose-700" }
}

const fmtAgo = (s) => {
    if (s === null || s === undefined) return "—"
    const sec = Number(s)
    if (sec < 60) return `${sec}s ago`
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
    return `${Math.floor(sec / 86400)}d ago`
}

function Signal({ label, note, tone }) {
    return (
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs">
            <span className={`h-2 w-2 shrink-0 rounded-full ${TONE[tone]}`} aria-hidden="true" />
            <span className={tone === "none" ? "text-slate-400" : "text-slate-800"}>{label}</span>
            {note && <span className={tone === "bad" ? "text-rose-700" : tone === "warn" ? "text-amber-800" : "text-slate-500"}>{note}</span>}
        </span>
    )
}

const FILTERS = [
    { id: "all", label: "All" },
    { id: "healthy", label: "Healthy" },
    { id: "warning", label: "Warning" },
    { id: "critical", label: "Critical" },
]

// ── Main Component ────────────────────────────────────────────────────────────

export default function VehicleHealth() {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [search, setSearch] = useState("")
    const [filter, setFilter] = useState("all") // all | healthy | warning | critical

    // Bumped by refresh(); the effect refetches and ignores responses from a superseded request
    const [reloadKey, setReloadKey] = useState(0)

    useEffect(() => {
        let active = true
        async function load() {
            try {
                // A manual refresh bypasses the 30 s client cache
                const res = await getFleetHealthAnalytics({ skipCache: reloadKey > 0 })
                if (active) setData(res)
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

    const vehicles = useMemo(() => {
        if (!data?.vehicles) return []
        return data.vehicles
    }, [data])

    const filtered = useMemo(() => {
        let v = vehicles
        if (search.trim()) {
            const q = search.toLowerCase()
            v = v.filter(x => (x.registration_plate || "").toLowerCase().includes(q) || (x.vehicle_name || "").toLowerCase().includes(q) || String(x.vehicle_id).includes(q) || (x.group || "").toLowerCase().includes(q))
        }
        if (filter === "critical") v = v.filter(x => x.battery?.status === "CRITICAL" || x.connectivity?.status === "OFFLINE")
        if (filter === "warning")  v = v.filter(x => x.battery?.status === "WARNING"  || x.connectivity?.status === "DELAYED" || x.gps?.status === "WEAK")
        if (filter === "healthy")  v = v.filter(x => (x.health_score ?? 0) >= 80 && x.connectivity?.status === "STABLE")
        return v
    }, [vehicles, search, filter])

    return (
        <AppShell>
            <PageHeader
                title="Vehicle Health"
                subtitle="Battery, GPS and telemetry connectivity"
                onRefresh={refresh}
                loading={loading}
            />

            <div className="space-y-4">
                <KpiStrip label="Fleet health summary" className="grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
                    <KpiCard label="Fleet vehicles" loading={loading} value={data?.total_vehicles ?? "—"} />
                    <KpiCard
                        label="Healthy units"
                        dot="bg-status-moving"
                        loading={loading}
                        value={data?.healthy_vehicles ?? "—"}
                    />
                    <KpiCard
                        label="Avg health score"
                        loading={loading}
                        value={data?.average_health_score != null ? `${data.average_health_score}%` : "—"}
                    />
                    <KpiCard
                        label="Battery issues"
                        dot={data?.battery_warnings > 0 ? "bg-status-idling" : undefined}
                        loading={loading}
                        value={data?.battery_warnings ?? 0}
                        valueColor={data?.battery_warnings > 0 ? "text-amber-800" : "text-slate-900"}
                    />
                    <KpiCard
                        label="GPS warnings"
                        dot={data?.gps_warnings > 0 ? "bg-status-idling" : undefined}
                        loading={loading}
                        value={data?.gps_warnings ?? 0}
                        valueColor={data?.gps_warnings > 0 ? "text-amber-800" : "text-slate-900"}
                    />
                    <KpiCard
                        label="Offline / delayed"
                        dot={data?.connectivity_warnings > 0 ? "bg-status-offline" : undefined}
                        loading={loading}
                        value={data?.connectivity_warnings ?? 0}
                        valueColor={data?.connectivity_warnings > 0 ? "text-rose-700" : "text-slate-900"}
                    />
                </KpiStrip>

                <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                    <div className="flex flex-col gap-2 border-b border-slate-100 px-4 py-2.5 sm:flex-row sm:items-center">
                        <SearchInput
                            id="health-search"
                            placeholder="Search plate, name, ID or group…"
                            value={search}
                            onChange={setSearch}
                            className="w-full sm:max-w-xs"
                        />
                        <div role="group" aria-label="Filter by health" className="flex items-center gap-0.5 self-start rounded-md bg-slate-100 p-0.5 sm:ml-auto sm:self-auto">
                            {FILTERS.map(f => (
                                <button
                                    key={f.id}
                                    id={`health-filter-${f.id}`}
                                    type="button"
                                    onClick={() => setFilter(f.id)}
                                    aria-pressed={filter === f.id}
                                    className={`shrink-0 rounded px-3 py-1 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                        filter === f.id
                                            ? "bg-white text-slate-900 shadow-[0_0_0_1px_rgba(15,23,42,0.08)]"
                                            : "text-slate-600 hover:text-slate-900"
                                    }`}
                                >
                                    {f.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    {loading ? (
                        <LoadingState label="Loading vehicle health…" className="rounded-none border-0" />
                    ) : error ? (
                        <ErrorState message={error} onRetry={refresh} className="rounded-none border-0" />
                    ) : filtered.length === 0 ? (
                        <EmptyState
                            icon={Truck}
                            className="rounded-none border-0"
                            title={vehicles.length === 0 ? "No vehicles reported" : "No vehicles match your filters"}
                            action={(search || filter !== "all") && (
                                <button
                                    type="button"
                                    onClick={() => { setSearch(""); setFilter("all") }}
                                    className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                >
                                    Clear filters
                                </button>
                            )}
                        />
                    ) : (
                        <>
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[880px] text-xs">
                                    <thead>
                                        <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] text-slate-500">
                                            <th scope="col" className="px-4 py-2 font-medium">Vehicle</th>
                                            <th scope="col" className="px-3 py-2 font-medium">Status</th>
                                            <th scope="col" className="px-3 py-2 font-medium">Health</th>
                                            <th scope="col" className="px-3 py-2 font-medium">Battery</th>
                                            <th scope="col" className="px-3 py-2 font-medium">GPS</th>
                                            <th scope="col" className="px-3 py-2 font-medium">Connectivity</th>
                                            <th scope="col" className="px-4 py-2 text-right font-medium">Last seen</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {filtered.map(v => {
                                            const bat = v.battery || {}
                                            const gps = v.gps || {}
                                            const conn = v.connectivity || {}
                                            const sc = scoreTone(v.health_score)

                                            return (
                                                <tr key={v.vehicle_id} className="transition-colors hover:bg-slate-50">
                                                    <td className="max-w-[240px] px-4 py-2">
                                                        <p className="truncate font-mono text-[13px] font-semibold text-slate-900">{v.registration_plate || v.plate || v.vehicle_name || `Vehicle ${v.vehicle_id}`}</p>
                                                        <p className="truncate text-[11px] text-slate-500">{v.vehicle_name ? `${v.vehicle_name} · ` : ""}{v.group || `ID ${v.vehicle_id}`}</p>
                                                    </td>
                                                    <td className="px-3 py-2">
                                                        <FleetStatusBadge status={v.status} size="xs" />
                                                    </td>
                                                    <td className="px-3 py-2">
                                                        <div className="flex items-center gap-2">
                                                            <span className={`w-9 text-right font-semibold tabular-nums ${sc.text}`}>{v.health_score ?? 0}%</span>
                                                            <div className="h-1 w-16 overflow-hidden rounded-full bg-slate-100">
                                                                <div
                                                                    className={`h-full rounded-full ${sc.bar}`}
                                                                    style={{ width: `${Math.min(100, Math.max(0, v.health_score ?? 0))}%` }}
                                                                />
                                                            </div>
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2"><Signal {...batteryBadge(bat.status, bat.voltage)} /></td>
                                                    <td className="px-3 py-2"><Signal {...gpsBadge(gps.status, gps.satellites)} /></td>
                                                    <td className="px-3 py-2"><Signal {...connBadge(conn.status)} /></td>
                                                    <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums text-slate-600">
                                                        {fmtAgo(conn.seconds_since_update)}
                                                    </td>
                                                </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            </div>
                            <div className="border-t border-slate-200 px-4 py-2 text-xs tabular-nums text-slate-500">
                                <span className="font-medium text-slate-800">{filtered.length}</span> of {vehicles.length} vehicles
                            </div>
                        </>
                    )}
                </section>
            </div>
        </AppShell>
    )
}
