import { useCallback, useEffect, useState, useMemo } from "react"
import {
    HeartPulse, Battery, MapPin, Wifi,
    Clock3, Truck, ShieldCheck,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import { EmptyState, ErrorState, LoadingState } from "../components/Common/StateViews"
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

function Badge({ label, cls }) {
    return <span className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{label}</span>
}

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
                icon={HeartPulse}
                onRefresh={refresh}
                loading={loading}
            />

            <div className="space-y-5">
                <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                    <KpiCard
                        icon={Truck}
                        label="Fleet Vehicles"
                        loading={loading}
                        value={data?.total_vehicles ?? "—"}
                        iconBg="bg-blue-50 text-blue-600 border border-blue-100"
                    />
                    <KpiCard
                        icon={ShieldCheck}
                        label="Healthy Units"
                        loading={loading}
                        value={data?.healthy_vehicles ?? "—"}
                        iconBg="bg-emerald-50 text-emerald-600 border border-emerald-100"
                    />
                    <KpiCard
                        icon={HeartPulse}
                        label="Avg Health Score"
                        loading={loading}
                        value={data?.average_health_score != null ? `${data.average_health_score}%` : "—"}
                        iconBg="bg-teal-50 text-teal-600 border border-teal-100"
                    />
                    <KpiCard
                        icon={Battery}
                        label="Battery Issues"
                        loading={loading}
                        value={data?.battery_warnings ?? 0}
                        iconBg={data?.battery_warnings > 0 ? "bg-amber-50 text-amber-600 border border-amber-100" : "bg-emerald-50 text-emerald-600 border border-emerald-100"}
                        valueColor={data?.battery_warnings > 0 ? "text-amber-600" : "text-slate-900"}
                    />
                    <KpiCard
                        icon={MapPin}
                        label="GPS Warnings"
                        loading={loading}
                        value={data?.gps_warnings ?? 0}
                        iconBg={data?.gps_warnings > 0 ? "bg-purple-50 text-purple-600 border border-purple-100" : "bg-emerald-50 text-emerald-600 border border-emerald-100"}
                        valueColor={data?.gps_warnings > 0 ? "text-purple-600" : "text-slate-900"}
                    />
                    <KpiCard
                        icon={Wifi}
                        label="Offline / Delayed"
                        loading={loading}
                        value={data?.connectivity_warnings ?? 0}
                        iconBg={data?.connectivity_warnings > 0 ? "bg-rose-50 text-rose-600 border border-rose-100" : "bg-emerald-50 text-emerald-600 border border-emerald-100"}
                        valueColor={data?.connectivity_warnings > 0 ? "text-rose-600" : "text-slate-900"}
                    />
                </div>

                {/* Search + filter bar */}
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <SearchInput
                        id="health-search"
                        placeholder="Search plate, name, ID or group…"
                        value={search}
                        onChange={setSearch}
                        className="w-full sm:max-w-sm"
                    />
                    <div className="flex items-center gap-1 overflow-x-auto rounded-lg border border-slate-200 bg-slate-50 p-0.5 sm:ml-auto">
                        {["all", "healthy", "warning", "critical"].map(f => (
                            <button
                                key={f}
                                id={`health-filter-${f}`}
                                type="button"
                                onClick={() => setFilter(f)}
                                aria-pressed={filter === f}
                                className={`shrink-0 rounded-md px-3 py-1 text-xs font-semibold capitalize transition-colors cursor-pointer ${
                                    filter === f
                                        ? "bg-[#33758F] text-white shadow-2xs"
                                        : "text-slate-600 hover:text-slate-900"
                                }`}
                            >
                                {f}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Vehicle health table */}
                {loading ? (
                    <LoadingState label="Loading vehicle health…" />
                ) : error ? (
                    <ErrorState message={error} onRetry={refresh} />
                ) : filtered.length === 0 ? (
                    <EmptyState
                        icon={Truck}
                        title={vehicles.length === 0 ? "No vehicles reported" : "No vehicles match your filters"}
                        action={(search || filter !== "all") && (
                            <button
                                type="button"
                                onClick={() => { setSearch(""); setFilter("all") }}
                                className="text-xs font-semibold text-[#33758F] hover:underline cursor-pointer"
                            >
                                Clear filters
                            </button>
                        )}
                    />
                ) : (
                    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xs">
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[880px] text-sm">
                                <thead>
                                    <tr className="border-b border-slate-200 bg-slate-50/70 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                                        <th className="px-4 py-2.5">Vehicle</th>
                                        <th className="px-3 py-2.5">Status</th>
                                        <th className="px-3 py-2.5">Health</th>
                                        <th className="px-3 py-2.5">Battery</th>
                                        <th className="px-3 py-2.5">GPS</th>
                                        <th className="px-3 py-2.5">Connectivity</th>
                                        <th className="px-3 py-2.5">Last Seen</th>
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
                                            <tr key={v.vehicle_id} className="transition-colors hover:bg-slate-50/70">
                                                <td className="max-w-[220px] px-4 py-2.5">
                                                    <p className="truncate font-mono text-xs font-bold text-slate-900">{v.registration_plate || v.plate || v.vehicle_name || `Vehicle ${v.vehicle_id}`}</p>
                                                    <p className="truncate text-[11px] text-slate-400">{v.vehicle_name ? `${v.vehicle_name} · ` : ""}{v.group || `ID ${v.vehicle_id}`}</p>
                                                </td>
                                                <td className="px-3 py-2.5">
                                                    <FleetStatusBadge status={v.status} size="xs" />
                                                </td>
                                                <td className="px-3 py-2.5">
                                                    <div className="flex items-center gap-2">
                                                        <div className="h-1.5 w-14 overflow-hidden rounded-full bg-slate-100">
                                                            <div
                                                                className={`h-full rounded-full ${scSt.bar}`}
                                                                style={{ width: `${Math.min(100, Math.max(0, v.health_score ?? 0))}%` }}
                                                            />
                                                        </div>
                                                        <span className="text-xs font-bold tabular-nums text-slate-800">{v.health_score ?? 0}%</span>
                                                    </div>
                                                </td>
                                                <td className="px-3 py-2.5"><Badge label={batSt.label} cls={batSt.cls} /></td>
                                                <td className="px-3 py-2.5"><Badge label={gpsSt.label} cls={gpsSt.cls} /></td>
                                                <td className="px-3 py-2.5"><Badge label={connSt.label} cls={connSt.cls} /></td>
                                                <td className="px-3 py-2.5">
                                                    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-slate-600">
                                                        <Clock3 size={12} className="text-slate-400" />
                                                        {fmtAgo(conn.seconds_since_update)}
                                                    </span>
                                                </td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                        <div className="border-t border-slate-200 bg-slate-50/60 px-4 py-2.5 text-xs text-slate-500">
                            <strong className="font-semibold text-slate-800">{filtered.length}</strong> of {vehicles.length} vehicles
                        </div>
                    </div>
                )}
            </div>
        </AppShell>
    )
}
