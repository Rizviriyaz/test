import { useCallback, useEffect, useState, useMemo } from "react"
import { Link } from "react-router-dom"
import {
    Truck,
    AlertTriangle,
    X,
    ArrowUpRight,
    ArrowUpDown,
    ChevronUp,
    ChevronDown,
    Activity,
    Battery,
    Wifi,
    Navigation,
    ShieldAlert,
    Clock,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import FleetStatusBadge from "../components/Common/FleetStatusBadge"
import { EmptyState, ErrorBanner, ErrorState, LoadingState } from "../components/Common/StateViews"
import {
    getFleetHealthAnalytics,
    peekFleetHealthAnalytics,
    getVehicleDetails,
    getVehicleHistory,
} from "../api/fleetApi"
import { formatUAETime, formatUAEDateTime } from "../utils/datetime"
import { isHealthyVehicle } from "../utils/fleetRules"
import { isValidCoordinate } from "../utils/coordinates"
import { normalizeVehicleStatus } from "../utils/vehicleStatus"
import { formatKm } from "../utils/knownTotals"

// ── Utilities & Parsers ────────────────────────────────────────────────────────

const fmt = (n, d = 1) => (typeof n === "number" && Number.isFinite(n) ? n.toFixed(d) : "—")

const fmtAgo = (s) => {
    if (s === null || s === undefined) return "—"
    const sec = Number(s)
    if (!Number.isFinite(sec)) return "—"
    if (sec < 60) return `${Math.max(0, Math.floor(sec))}s ago`
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
    return `${Math.floor(sec / 86400)}d ago`
}

const TONE = {
    good: "bg-status-moving",
    warn: "bg-status-idling",
    bad: "bg-status-offline",
    none: "bg-slate-300",
}

function parseSatellites(sats) {
    if (sats === null || sats === undefined || sats === "") {
        return { count: null, isValid: false, isSentinel: false, label: "No data" }
    }
    const n = Number(sats)
    if (!Number.isFinite(n)) {
        return { count: null, isValid: false, isSentinel: false, label: "No data" }
    }
    // 255 (0xFF) is an invalid sentinel value used by GPS tracker protocols to signify invalid fix
    if (n === 255) {
        return { count: 255, isValid: false, isSentinel: true, label: "255 (Invalid Sentinel)" }
    }
    if (n < 0 || n > 50) {
        return { count: n, isValid: false, isSentinel: false, label: `${n} (Invalid)` }
    }
    return { count: n, isValid: true, isSentinel: false, label: `${n} sats` }
}

const batteryBadge = (battery = {}) => {
    const s = String(battery.status || "").toUpperCase()
    const voltage = battery.voltage
    const last = battery.stale ? " · last reported" : ""
    if (s === "HEALTHY") return { label: `${fmt(voltage, 1)} V`, note: `Healthy${last}`, tone: "good" }
    if (s === "WARNING" && battery.system_type === "Unknown") return { label: `${fmt(voltage, 1)} V`, note: `Unclassified${last}`, tone: "warn" }
    if (s === "WARNING") return { label: `${fmt(voltage, 1)} V`, note: `Low${last}`, tone: "warn" }
    if (s === "CRITICAL") return { label: `${fmt(voltage, 1)} V`, note: `Critical${last}`, tone: "bad" }
    return { label: voltage != null ? `${fmt(voltage, 1)} V` : "No data", note: null, tone: "none" }
}

const gpsBadge = (status, sats, positionAge) => {
    const s = String(status || "").toUpperCase()
    // Last seen Moving with an old GPS fix although messages are recent: the satellites describe that old fix
    if (s === "STALE") return { label: `Fix ${fmtAgo(positionAge)}`, note: "Stale", tone: "warn" }

    const satInfo = parseSatellites(sats)
    if (satInfo.isSentinel) {
        return { label: "Invalid fix", note: "255 sentinel", tone: "bad" }
    }
    if (!satInfo.isValid && satInfo.count !== 0) {
        return { label: "No GPS", note: null, tone: "none" }
    }
    if (satInfo.count === 0) {
        return { label: "0 sats", note: "No lock", tone: "warn" }
    }
    if (s === "EXCELLENT") return { label: `${satInfo.count} sats`, note: "Excellent", tone: "good" }
    if (s === "GOOD") return { label: `${satInfo.count} sats`, note: "Good", tone: "good" }
    if (s === "WEAK") return { label: `${satInfo.count} sats`, note: "Weak", tone: "warn" }
    return { label: satInfo.count ? `${satInfo.count} sats` : "No GPS", note: null, tone: "none" }
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
    if (sc >= 85) return { bar: "bg-status-moving", text: "text-emerald-700", label: "Optimal", badge: "bg-emerald-50 text-emerald-700 border-emerald-200" }
    if (sc >= 65) return { bar: "bg-status-idling", text: "text-amber-800", label: "Warning", badge: "bg-amber-50 text-amber-800 border-amber-200" }
    return { bar: "bg-status-offline", text: "text-rose-700", label: "Critical", badge: "bg-rose-50 text-rose-700 border-rose-200" }
}

function getPrimaryIssue(vehicle) {
    if (!vehicle) return { label: "No data", tone: "none", priority: 99 }

    const bat = vehicle.battery || {}
    const gps = vehicle.gps || {}
    const conn = vehicle.connectivity || {}
    const batStatus = String(bat.status || "").toUpperCase()
    const gpsStatus = String(gps.status || "").toUpperCase()
    const connStatus = String(conn.status || "").toUpperCase()
    const satInfo = parseSatellites(gps.satellites)

    // Priority 1: Battery Critical
    if (batStatus === "CRITICAL") {
        return {
            label: `Critical Battery (${fmt(bat.voltage, 1)} V)${bat.stale ? " · last reported" : ""}`,
            category: "battery",
            tone: "bad",
            priority: 1,
        }
    }
    // Priority 2: Offline
    if (connStatus === "OFFLINE") {
        return {
            label: `Offline (${fmtAgo(conn.seconds_since_update)})`,
            category: "connectivity",
            tone: "bad",
            priority: 2,
        }
    }
    // Priority 3: Battery Warning
    if (batStatus === "WARNING") {
        const desc = bat.system_type === "Unknown" ? "Unclassified Voltage" : "Low Battery"
        return {
            label: `${desc} (${fmt(bat.voltage, 1)} V)${bat.stale ? " · last reported" : ""}`,
            category: "battery",
            tone: "warn",
            priority: 3,
        }
    }
    // Priority 4: Delayed Connectivity
    if (connStatus === "DELAYED") {
        return {
            label: `Telemetry Delayed (${fmtAgo(conn.seconds_since_update)})`,
            category: "connectivity",
            tone: "warn",
            priority: 4,
        }
    }
    // Priority 5: Stale GPS fix
    if (gpsStatus === "STALE") {
        return {
            label: `Stale GPS Fix (${fmtAgo(gps.seconds_since_position)})`,
            category: "gps",
            tone: "warn",
            priority: 5,
        }
    }
    // Priority 6: GPS Sentinel value 255
    if (satInfo.isSentinel) {
        return {
            label: "Invalid GPS Satellite Sentinel (255)",
            category: "gps",
            tone: "bad",
            priority: 6,
        }
    }
    // Priority 7: Weak GPS
    if (gpsStatus === "WEAK") {
        return {
            label: `Weak GPS Signal (${satInfo.count ?? 0} sats)`,
            category: "gps",
            tone: "warn",
            priority: 7,
        }
    }
    // Priority 8: No GPS fix / Unknown GPS
    if (gpsStatus === "UNKNOWN" || satInfo.count === null) {
        return {
            label: "No GPS Fix",
            category: "gps",
            tone: "none",
            priority: 8,
        }
    }
    // Priority 9: Degraded health score (< 80)
    const score = Number(vehicle.health_score ?? 0)
    if (score < 80) {
        return {
            label: `Degraded Health (${score}%)`,
            category: "health",
            tone: score < 65 ? "bad" : "warn",
            priority: 9,
        }
    }

    return {
        label: "None · All Systems Healthy",
        category: "healthy",
        tone: "good",
        priority: 10,
    }
}

function Signal({ label, note, tone }) {
    return (
        <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs">
            <span className={`h-2 w-2 shrink-0 rounded-full ${TONE[tone] || TONE.none}`} aria-hidden="true" />
            <span className={tone === "none" ? "text-slate-400" : "text-slate-800"}>{label}</span>
            {note && (
                <span className={tone === "bad" ? "text-rose-700 font-medium" : tone === "warn" ? "text-amber-800 font-medium" : "text-slate-500"}>
                    {note}
                </span>
            )}
        </span>
    )
}

const FILTERS = [
    { id: "all", label: "All" },
    { id: "healthy", label: "Healthy" },
    { id: "warning", label: "Warning" },
    { id: "critical", label: "Critical" },
    { id: "battery", label: "Battery Issues" },
    { id: "gps", label: "GPS Warnings" },
    { id: "offline", label: "Offline" },
    { id: "delayed", label: "Delayed" },
]

// ── Main Component ────────────────────────────────────────────────────────────

export default function VehicleHealth() {
    const [data, setData] = useState(() => peekFleetHealthAnalytics()?.data ?? null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [search, setSearch] = useState("")
    const [filter, setFilter] = useState("all")
    const [reloadKey, setReloadKey] = useState(0)

    // Table sorting
    const [sortField, setSortField] = useState("health_score")
    const [sortDirection, setSortDirection] = useState("asc") // asc = lowest score (urgent) first

    // Vehicle details slide-out panel state
    const [selectedVehicleId, setSelectedVehicleId] = useState(null)
    const [drawerTab, setDrawerTab] = useState("overview") // overview | diagnostics | info | history
    const [vehicleDetails, setVehicleDetails] = useState(null)
    const [vehicleHistory, setVehicleHistory] = useState(null)
    const [loadingHistory, setLoadingHistory] = useState(false)

    const openDrawer = useCallback((vehicleId) => {
        setSelectedVehicleId(vehicleId)
        setDrawerTab("overview")
        setVehicleDetails(null)
        setVehicleHistory(null)
        setLoadingHistory(true)
    }, [])

    const closeDrawer = useCallback(() => {
        setSelectedVehicleId(null)
        setVehicleDetails(null)
        setVehicleHistory(null)
    }, [])

    useEffect(() => {
        let active = true
        async function load() {
            try {
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

    // Derive active vehicle object
    const activeVehicle = useMemo(() => {
        if (!selectedVehicleId) return null
        return vehicles.find(v => v.vehicle_id === selectedVehicleId) || null
    }, [vehicles, selectedVehicleId])

    // Load extra metadata and diagnostic history when side panel opens
    useEffect(() => {
        if (!selectedVehicleId) return undefined
        let active = true

        getVehicleDetails(selectedVehicleId)
            .then(res => {
                if (active && res) setVehicleDetails(res.vehicle || res)
            })
            .catch(() => {})

        getVehicleHistory(selectedVehicleId, { hours: 24 })
            .then(res => {
                if (active && res) setVehicleHistory(res)
            })
            .catch(() => {})
            .finally(() => {
                if (active) setLoadingHistory(false)
            })

        return () => { active = false }
    }, [selectedVehicleId])

    // Close drawer on Escape key
    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === "Escape") closeDrawer()
        }
        if (selectedVehicleId) {
            window.addEventListener("keydown", handleKeyDown)
        }
        return () => window.removeEventListener("keydown", handleKeyDown)
    }, [selectedVehicleId, closeDrawer])

    // Offline and Delayed separate counts
    const offlineVehiclesCount = useMemo(() => {
        return vehicles.filter(v => v.connectivity?.status === "OFFLINE").length
    }, [vehicles])

    const delayedVehiclesCount = useMemo(() => {
        return vehicles.filter(v => v.connectivity?.status === "DELAYED").length
    }, [vehicles])

    // Fleet Health Distribution counts
    const distribution = useMemo(() => {
        const total = vehicles.length
        let optimal = 0
        let warning = 0
        let critical = 0

        for (const v of vehicles) {
            const sc = Number(v.health_score ?? 0)
            if (sc >= 85) optimal += 1
            else if (sc >= 65) warning += 1
            else critical += 1
        }

        return {
            total,
            optimal,
            warning,
            critical,
            pctOptimal: total > 0 ? Math.round((optimal / total) * 100) : 0,
            pctWarning: total > 0 ? Math.round((warning / total) * 100) : 0,
            pctCritical: total > 0 ? Math.round((critical / total) * 100) : 0,
        }
    }, [vehicles])

    // Diagnostic Subsystems Breakdown
    const subsystems = useMemo(() => {
        let batHealthy = 0, batWarning = 0, batCritical = 0, batNoData = 0
        let gpsGood = 0, gpsWeak = 0, gpsStale = 0, gpsInvalid = 0
        let connStable = 0, connDelayed = 0, connOffline = 0

        for (const v of vehicles) {
            const b = String(v.battery?.status || "").toUpperCase()
            if (b === "HEALTHY") batHealthy += 1
            else if (b === "WARNING") batWarning += 1
            else if (b === "CRITICAL") batCritical += 1
            else batNoData += 1

            const g = String(v.gps?.status || "").toUpperCase()
            const sat = parseSatellites(v.gps?.satellites)
            if (g === "STALE") gpsStale += 1
            else if (sat.isSentinel || g === "UNKNOWN" || sat.count === null) gpsInvalid += 1
            else if (g === "WEAK") gpsWeak += 1
            else gpsGood += 1

            const c = String(v.connectivity?.status || "").toUpperCase()
            if (c === "STABLE") connStable += 1
            else if (c === "DELAYED") connDelayed += 1
            else if (c === "OFFLINE") connOffline += 1
        }

        return {
            battery: { healthy: batHealthy, warning: batWarning, critical: batCritical, noData: batNoData },
            gps: { good: gpsGood, weak: gpsWeak, stale: gpsStale, invalid: gpsInvalid },
            connectivity: { stable: connStable, delayed: connDelayed, offline: connOffline },
        }
    }, [vehicles])

    // "Needs Attention" priority list: highest-priority vehicles
    const needsAttention = useMemo(() => {
        return vehicles
            .filter(v => !isHealthyVehicle(v))
            .sort((a, b) => {
                const issueA = getPrimaryIssue(a)
                const issueB = getPrimaryIssue(b)
                if (issueA.priority !== issueB.priority) {
                    return issueA.priority - issueB.priority
                }
                return (a.health_score ?? 0) - (b.health_score ?? 0)
            })
            .slice(0, 5)
    }, [vehicles])

    // Filtering
    const filtered = useMemo(() => {
        let v = vehicles
        if (search.trim()) {
            const q = search.toLowerCase()
            v = v.filter(x =>
                (x.registration_plate || "").toLowerCase().includes(q) ||
                (x.vehicle_name || "").toLowerCase().includes(q) ||
                String(x.vehicle_id).includes(q) ||
                (x.group || "").toLowerCase().includes(q)
            )
        }
        if (filter === "healthy") v = v.filter(isHealthyVehicle)
        if (filter === "warning") v = v.filter(x => x.battery?.status === "WARNING" || x.connectivity?.status === "DELAYED" || x.gps?.status === "WEAK" || x.gps?.status === "STALE")
        if (filter === "critical") v = v.filter(x => x.battery?.status === "CRITICAL" || x.connectivity?.status === "OFFLINE" || (x.health_score != null && x.health_score < 50))
        if (filter === "battery") v = v.filter(x => x.battery?.status === "WARNING" || x.battery?.status === "CRITICAL")
        if (filter === "gps") v = v.filter(x => x.gps?.status === "WEAK" || x.gps?.status === "STALE" || x.gps?.satellites === 255 || (x.gps?.status === "UNKNOWN" && x.connectivity?.status !== "OFFLINE"))
        if (filter === "offline") v = v.filter(x => x.connectivity?.status === "OFFLINE")
        if (filter === "delayed") v = v.filter(x => x.connectivity?.status === "DELAYED")
        return v
    }, [vehicles, search, filter])

    // Sorting
    const sortedVehicles = useMemo(() => {
        return [...filtered].sort((a, b) => {
            let cmp = 0
            if (sortField === "health_score") {
                cmp = (a.health_score ?? 0) - (b.health_score ?? 0)
            } else if (sortField === "vehicle") {
                const nameA = a.registration_plate || a.vehicle_name || String(a.vehicle_id)
                const nameB = b.registration_plate || b.vehicle_name || String(b.vehicle_id)
                cmp = nameA.localeCompare(nameB)
            } else if (sortField === "battery") {
                const vA = a.battery?.voltage ?? -1
                const vB = b.battery?.voltage ?? -1
                cmp = vA - vB
            } else if (sortField === "gps") {
                const sA = parseSatellites(a.gps?.satellites).count ?? -1
                const sB = parseSatellites(b.gps?.satellites).count ?? -1
                cmp = sA - sB
            } else if (sortField === "connectivity") {
                const cA = a.connectivity?.status || ""
                const cB = b.connectivity?.status || ""
                cmp = cA.localeCompare(cB)
            } else if (sortField === "last_seen") {
                const tA = a.connectivity?.seconds_since_update ?? 9999999
                const tB = b.connectivity?.seconds_since_update ?? 9999999
                cmp = tA - tB
            }
            return sortDirection === "desc" ? -cmp : cmp
        })
    }, [filtered, sortField, sortDirection])

    const toggleSort = (field) => {
        if (sortField === field) {
            setSortDirection(prev => (prev === "asc" ? "desc" : "asc"))
        } else {
            setSortField(field)
            setSortDirection(field === "health_score" ? "asc" : "desc")
        }
    }

    const sortIndicator = (field) => {
        if (sortField !== field) {
            return <ArrowUpDown className="h-3 w-3 text-slate-400 group-hover:text-slate-600 transition-colors" />
        }
        return sortDirection === "asc" ? (
            <ChevronUp className="h-3.5 w-3.5 text-brand" />
        ) : (
            <ChevronDown className="h-3.5 w-3.5 text-brand" />
        )
    }

    return (
        <AppShell>
            <PageHeader
                onRefresh={refresh}
                loading={loading}
                lastUpdated={data?.generated_at ? formatUAETime(data.generated_at) : null}
                lastUpdatedLabel="Data as of"
            />

            <div className="space-y-4">
                {/* Error Banner */}
                {error && data && <ErrorBanner message={`Refresh failed: ${error}`} onRetry={refresh} />}

                {/* 1. KPI Cards */}
                <KpiStrip label="Fleet diagnostics summary" className="grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
                    <KpiCard
                        label="Fleet vehicles"
                        loading={loading && !data}
                        value={data?.total_vehicles ?? "—"}
                        onClick={() => setFilter("all")}
                        selected={filter === "all"}
                        title="Show all vehicles"
                    />
                    <KpiCard
                        label="Healthy units"
                        dot="bg-status-moving"
                        loading={loading && !data}
                        value={data?.healthy_vehicles ?? "—"}
                        onClick={() => setFilter("healthy")}
                        selected={filter === "healthy"}
                        title="Show healthy vehicles (score ≥ 80 with no issues)"
                    />
                    <KpiCard
                        label="Avg health score"
                        loading={loading && !data}
                        value={data?.average_health_score != null ? `${data.average_health_score}%` : "—"}
                        valueColor={
                            data?.average_health_score >= 85
                                ? "text-emerald-700"
                                : data?.average_health_score >= 65
                                ? "text-amber-800"
                                : "text-rose-700"
                        }
                    />
                    <KpiCard
                        label="Battery issues"
                        dot={data?.battery_warnings > 0 ? "bg-status-idling" : undefined}
                        loading={loading && !data}
                        value={data?.battery_warnings ?? "—"}
                        valueColor={data?.battery_warnings > 0 ? "text-amber-800" : "text-slate-900"}
                        onClick={() => setFilter("battery")}
                        selected={filter === "battery"}
                        title="Show vehicles with battery warnings or critical voltage"
                    />
                    <KpiCard
                        label="GPS warnings"
                        dot={data?.gps_warnings > 0 ? "bg-status-idling" : undefined}
                        loading={loading && !data}
                        value={data?.gps_warnings ?? "—"}
                        valueColor={data?.gps_warnings > 0 ? "text-amber-800" : "text-slate-900"}
                        onClick={() => setFilter("gps")}
                        selected={filter === "gps"}
                        title="Show vehicles with weak GPS signal or stale fix"
                    />
                    <KpiCard
                        label="Offline / delayed"
                        dot={data?.connectivity_warnings > 0 ? "bg-status-offline" : undefined}
                        loading={loading && !data}
                        value={data?.connectivity_warnings ?? "—"}
                        valueColor={data?.connectivity_warnings > 0 ? "text-rose-700" : "text-slate-900"}
                        sub={vehicles.length > 0 ? `${offlineVehiclesCount} offline · ${delayedVehiclesCount} delayed` : null}
                        onClick={() => setFilter("offline")}
                        selected={filter === "offline" || filter === "delayed"}
                        title="Show offline or delayed vehicles"
                    />
                </KpiStrip>

                {/* 2. Compact Fleet Health Distribution Chart & Diagnostic Breakdown */}
                {data && vehicles.length > 0 && (
                    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        {/* Health Distribution */}
                        <section className="rounded-xl border border-slate-200/70 bg-white p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                                <div>
                                    <h2 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                                        Fleet Health Distribution
                                    </h2>
                                    <p className="text-[11px] text-slate-500">Overall unit health score tiers</p>
                                </div>
                                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-600 tabular-nums">
                                    {distribution.total} Units
                                </span>
                            </div>

                            {/* Multi-segment distribution bar */}
                            <div className="mt-3">
                                <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100 flex">
                                    <div
                                        className="h-full bg-status-moving transition-all"
                                        style={{ width: `${distribution.pctOptimal}%` }}
                                        title={`Optimal (≥85%): ${distribution.optimal} vehicles (${distribution.pctOptimal}%)`}
                                    />
                                    <div
                                        className="h-full bg-status-idling transition-all"
                                        style={{ width: `${distribution.pctWarning}%` }}
                                        title={`Warning (65-84%): ${distribution.warning} vehicles (${distribution.pctWarning}%)`}
                                    />
                                    <div
                                        className="h-full bg-status-offline transition-all"
                                        style={{ width: `${distribution.pctCritical}%` }}
                                        title={`Critical (<65%): ${distribution.critical} vehicles (${distribution.pctCritical}%)`}
                                    />
                                </div>
                            </div>

                            {/* Tiers Breakdown Chips */}
                            <div className="mt-3 grid grid-cols-3 gap-2">
                                <button
                                    type="button"
                                    onClick={() => setFilter("healthy")}
                                    className="flex flex-col rounded-lg border border-emerald-100 bg-emerald-50/50 p-2 text-left transition-colors hover:bg-emerald-50 cursor-pointer"
                                >
                                    <span className="flex items-center gap-1.5 text-[11px] font-semibold text-emerald-800">
                                        <span className="h-1.5 w-1.5 rounded-full bg-status-moving" />
                                        Optimal (≥85%)
                                    </span>
                                    <span className="mt-1 text-base font-bold text-emerald-900 tabular-nums">
                                        {distribution.optimal}{" "}
                                        <span className="text-xs font-normal text-emerald-700">({distribution.pctOptimal}%)</span>
                                    </span>
                                </button>

                                <button
                                    type="button"
                                    onClick={() => setFilter("warning")}
                                    className="flex flex-col rounded-lg border border-amber-100 bg-amber-50/50 p-2 text-left transition-colors hover:bg-amber-50 cursor-pointer"
                                >
                                    <span className="flex items-center gap-1.5 text-[11px] font-semibold text-amber-800">
                                        <span className="h-1.5 w-1.5 rounded-full bg-status-idling" />
                                        Warning (65–84%)
                                    </span>
                                    <span className="mt-1 text-base font-bold text-amber-900 tabular-nums">
                                        {distribution.warning}{" "}
                                        <span className="text-xs font-normal text-amber-700">({distribution.pctWarning}%)</span>
                                    </span>
                                </button>

                                <button
                                    type="button"
                                    onClick={() => setFilter("critical")}
                                    className="flex flex-col rounded-lg border border-rose-100 bg-rose-50/50 p-2 text-left transition-colors hover:bg-rose-50 cursor-pointer"
                                >
                                    <span className="flex items-center gap-1.5 text-[11px] font-semibold text-rose-800">
                                        <span className="h-1.5 w-1.5 rounded-full bg-status-offline" />
                                        Critical (&lt;65%)
                                    </span>
                                    <span className="mt-1 text-base font-bold text-rose-900 tabular-nums">
                                        {distribution.critical}{" "}
                                        <span className="text-xs font-normal text-rose-700">({distribution.pctCritical}%)</span>
                                    </span>
                                </button>
                            </div>
                        </section>

                        {/* Diagnostic Subsystems Breakdown */}
                        <section className="rounded-xl border border-slate-200/70 bg-white p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                            <div className="flex items-center justify-between pb-2 border-b border-slate-100">
                                <div>
                                    <h2 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                                        Diagnostic Subsystem Status
                                    </h2>
                                    <p className="text-[11px] text-slate-500">Live hardware telemetry verification</p>
                                </div>
                                <span className="inline-flex items-center gap-1 text-[11px] text-slate-500 font-medium">
                                    <Activity className="h-3 w-3 text-brand" />
                                    Active Sensors
                                </span>
                            </div>

                            <div className="mt-3 space-y-2 text-xs">
                                {/* Power & Battery */}
                                <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 border border-slate-100">
                                    <div className="flex items-center gap-2">
                                        <Battery className="h-3.5 w-3.5 text-slate-600 shrink-0" />
                                        <span className="font-medium text-slate-800">Power & Battery</span>
                                    </div>
                                    <div className="flex items-center gap-3 tabular-nums text-[11px]">
                                        <span className="text-emerald-700 font-semibold">{subsystems.battery.healthy} Healthy</span>
                                        {subsystems.battery.warning > 0 && <span className="text-amber-800 font-semibold">{subsystems.battery.warning} Low</span>}
                                        {subsystems.battery.critical > 0 && <span className="text-rose-700 font-semibold">{subsystems.battery.critical} Critical</span>}
                                        {subsystems.battery.noData > 0 && <span className="text-slate-400">{subsystems.battery.noData} No Data</span>}
                                    </div>
                                </div>

                                {/* GPS Navigation */}
                                <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 border border-slate-100">
                                    <div className="flex items-center gap-2">
                                        <Navigation className="h-3.5 w-3.5 text-slate-600 shrink-0" />
                                        <span className="font-medium text-slate-800">GPS & Satellites</span>
                                    </div>
                                    <div className="flex items-center gap-3 tabular-nums text-[11px]">
                                        <span className="text-emerald-700 font-semibold">{subsystems.gps.good} Locked</span>
                                        {subsystems.gps.weak > 0 && <span className="text-amber-800 font-semibold">{subsystems.gps.weak} Weak</span>}
                                        {subsystems.gps.stale > 0 && <span className="text-amber-800 font-semibold">{subsystems.gps.stale} Stale Fix</span>}
                                        {subsystems.gps.invalid > 0 && <span className="text-slate-500 font-medium">{subsystems.gps.invalid} No Fix</span>}
                                    </div>
                                </div>

                                {/* Cellular Connectivity */}
                                <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 border border-slate-100">
                                    <div className="flex items-center gap-2">
                                        <Wifi className="h-3.5 w-3.5 text-slate-600 shrink-0" />
                                        <span className="font-medium text-slate-800">Cellular Link</span>
                                    </div>
                                    <div className="flex items-center gap-3 tabular-nums text-[11px]">
                                        <span className="text-emerald-700 font-semibold">{subsystems.connectivity.stable} Online</span>
                                        {subsystems.connectivity.delayed > 0 && <span className="text-amber-800 font-semibold">{subsystems.connectivity.delayed} Delayed</span>}
                                        {subsystems.connectivity.offline > 0 && <span className="text-rose-700 font-semibold">{subsystems.connectivity.offline} Offline</span>}
                                    </div>
                                </div>
                            </div>
                        </section>
                    </div>
                )}

                {/* 3. Prominent "Needs Attention" List */}
                {data && vehicles.length > 0 && (
                    <section className="rounded-xl border border-slate-200/70 bg-white p-4 shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                            <div className="flex items-center gap-2">
                                <div className="flex h-6 w-6 items-center justify-center rounded-md bg-rose-50 text-rose-600">
                                    <ShieldAlert className="h-3.5 w-3.5" />
                                </div>
                                <div>
                                    <h2 className="text-xs font-bold uppercase tracking-wider text-slate-900">
                                        Needs Attention
                                    </h2>
                                    <p className="text-[11px] text-slate-500">Highest-priority fleet units requiring maintenance or inspection</p>
                                </div>
                            </div>
                            <span className="rounded-full bg-rose-50 px-2 py-0.5 text-xs font-semibold text-rose-700 tabular-nums border border-rose-200">
                                {needsAttention.length} Flagged
                            </span>
                        </div>

                        {needsAttention.length === 0 ? (
                            <div className="py-4 text-center text-xs text-slate-500">
                                All fleet units are currently operating within healthy diagnostic thresholds.
                            </div>
                        ) : (
                            <div className="mt-3 grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
                                {needsAttention.map((v) => {
                                    const issue = getPrimaryIssue(v)
                                    const sc = scoreTone(v.health_score)
                                    const bat = v.battery || {}
                                    const gps = v.gps || {}
                                    const conn = v.connectivity || {}

                                    return (
                                        <div
                                            key={v.vehicle_id}
                                            onClick={() => openDrawer(v.vehicle_id)}
                                            role="button"
                                            tabIndex={0}
                                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") openDrawer(v.vehicle_id) }}
                                            className="group relative flex flex-col justify-between rounded-lg border border-slate-200 bg-slate-50/50 p-3 transition-all hover:border-brand/60 hover:bg-white hover:shadow-sm cursor-pointer text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                                        >
                                            <div>
                                                <div className="flex items-start justify-between gap-1">
                                                    <div className="min-w-0">
                                                        <p className="truncate font-mono text-xs font-bold text-slate-900 group-hover:text-brand transition-colors">
                                                            {v.registration_plate || v.plate || v.vehicle_name || `Unit ${v.vehicle_id}`}
                                                        </p>
                                                        <p className="truncate text-[11px] text-slate-500">
                                                            {v.vehicle_name ? `${v.vehicle_name} · ` : ""}{v.group || `ID ${v.vehicle_id}`}
                                                        </p>
                                                    </div>
                                                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold border ${sc.badge}`}>
                                                        {v.health_score ?? 0}%
                                                    </span>
                                                </div>

                                                {/* Primary Issue Badge */}
                                                <div className="mt-2 rounded border border-rose-200/80 bg-rose-50/70 px-2 py-1 text-[11px] font-medium text-rose-800">
                                                    <span className="flex items-center gap-1 line-clamp-1">
                                                        <AlertTriangle className="h-3 w-3 shrink-0 text-rose-600" />
                                                        <span className="truncate">{issue.label}</span>
                                                    </span>
                                                </div>
                                            </div>

                                            {/* Quick diagnostics summary */}
                                            <div className="mt-3 flex items-center justify-between border-t border-slate-200/60 pt-2 text-[10px] text-slate-500">
                                                <span title="Battery">{bat.voltage != null ? `${fmt(bat.voltage, 1)}V` : "No Bat"}</span>
                                                <span>·</span>
                                                <span title="GPS">{gps.satellites != null ? `${gps.satellites}sats` : "No GPS"}</span>
                                                <span>·</span>
                                                <span title="Connectivity" className={conn.status === "OFFLINE" ? "text-rose-700 font-semibold" : conn.status === "DELAYED" ? "text-amber-800 font-semibold" : "text-emerald-700"}>
                                                    {conn.status === "STABLE" ? "Online" : conn.status || "—"}
                                                </span>
                                            </div>
                                        </div>
                                    )
                                })}
                            </div>
                        )}
                    </section>
                )}

                {/* 4. Full Searchable & Sortable Vehicle Table */}
                <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-[0_2px_8px_-2px_rgba(15,23,42,0.05),0_1px_3px_-1px_rgba(15,23,42,0.03)]">
                    {/* Controls Bar: Search & Filter Pills */}
                    <div className="flex flex-col gap-3 border-b border-slate-100 p-4 lg:flex-row lg:items-center lg:justify-between">
                        <SearchInput
                            id="health-search"
                            placeholder="Search plate, name, ID or group…"
                            value={search}
                            onChange={setSearch}
                            className="w-full sm:max-w-xs"
                        />

                        {/* Filter Buttons */}
                        <div
                            role="group"
                            aria-label="Filter by diagnostic status"
                            className="flex flex-wrap items-center gap-1 rounded-lg bg-slate-100 p-1"
                        >
                            {FILTERS.map((f) => {
                                const isActive = filter === f.id
                                return (
                                    <button
                                        key={f.id}
                                        id={`health-filter-${f.id}`}
                                        type="button"
                                        onClick={() => setFilter(f.id)}
                                        aria-pressed={isActive}
                                        className={`shrink-0 rounded-md px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 ${
                                            isActive
                                                ? "bg-white text-slate-900 shadow-xs"
                                                : "text-slate-600 hover:text-slate-900"
                                        }`}
                                    >
                                        {f.label}
                                    </button>
                                )
                            })}
                        </div>
                    </div>

                    {/* Table / States */}
                    {loading && !data ? (
                        <LoadingState label="Loading vehicle health telemetry…" className="rounded-none border-0" />
                    ) : error && !data ? (
                        <ErrorState message={error} onRetry={refresh} className="rounded-none border-0" />
                    ) : sortedVehicles.length === 0 ? (
                        <EmptyState
                            icon={Truck}
                            className="rounded-none border-0"
                            title={vehicles.length === 0 ? "No vehicles reported" : "No vehicles match your search and filter criteria"}
                            action={(search || filter !== "all") && (
                                <button
                                    type="button"
                                    onClick={() => { setSearch(""); setFilter("all") }}
                                    className="text-xs font-medium text-brand hover:underline underline-offset-2 cursor-pointer"
                                >
                                    Clear filters and search
                                </button>
                            )}
                        />
                    ) : (
                        <>
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[960px] text-xs">
                                    <thead>
                                        <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] font-semibold text-slate-600">
                                            <th scope="col" className="px-4 py-2.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleSort("vehicle")}
                                                    className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                >
                                                    Vehicle {sortIndicator("vehicle")}
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleSort("health_score")}
                                                    className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                >
                                                    Health Score {sortIndicator("health_score")}
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleSort("battery")}
                                                    className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                >
                                                    Battery {sortIndicator("battery")}
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleSort("gps")}
                                                    className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                >
                                                    GPS / Satellites {sortIndicator("gps")}
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleSort("connectivity")}
                                                    className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                >
                                                    Connectivity {sortIndicator("connectivity")}
                                                </button>
                                            </th>
                                            <th scope="col" className="px-3 py-2.5">
                                                <button
                                                    type="button"
                                                    onClick={() => toggleSort("last_seen")}
                                                    className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                >
                                                    Last Seen {sortIndicator("last_seen")}
                                                </button>
                                            </th>
                                            <th scope="col" className="px-4 py-2.5">
                                                Primary Issue
                                            </th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {sortedVehicles.map((v) => {
                                            const bat = v.battery || {}
                                            const gps = v.gps || {}
                                            const conn = v.connectivity || {}
                                            const sc = scoreTone(v.health_score)
                                            const issue = getPrimaryIssue(v)
                                            const isSelected = selectedVehicleId === v.vehicle_id

                                            return (
                                                <tr
                                                    key={v.vehicle_id}
                                                    onClick={() => openDrawer(v.vehicle_id)}
                                                    className={`transition-colors cursor-pointer hover:bg-slate-50/90 ${
                                                        isSelected ? "bg-brand-light/70" : ""
                                                    }`}
                                                >
                                                    {/* Vehicle Column */}
                                                    <td className="max-w-[220px] px-4 py-2.5">
                                                        <p className="truncate font-mono text-[13px] font-bold text-slate-900 hover:text-brand transition-colors">
                                                            {v.registration_plate || v.plate || v.vehicle_name || `Unit ${v.vehicle_id}`}
                                                        </p>
                                                        <p className="truncate text-[11px] text-slate-500">
                                                            {v.vehicle_name ? `${v.vehicle_name} · ` : ""}{v.group || `ID ${v.vehicle_id}`}
                                                        </p>
                                                    </td>

                                                    {/* Health Score Column */}
                                                    <td className="px-3 py-2.5">
                                                        <div className="flex items-center gap-2">
                                                            <span className={`w-9 text-right font-bold tabular-nums ${sc.text}`}>
                                                                {v.health_score ?? 0}%
                                                            </span>
                                                            <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                                                                <div
                                                                    className={`h-full rounded-full ${sc.bar}`}
                                                                    style={{ width: `${Math.min(100, Math.max(0, v.health_score ?? 0))}%` }}
                                                                />
                                                            </div>
                                                        </div>
                                                    </td>

                                                    {/* Battery Column */}
                                                    <td className="px-3 py-2.5">
                                                        <Signal {...batteryBadge(bat)} />
                                                    </td>

                                                    {/* GPS / Satellites Column */}
                                                    <td className="px-3 py-2.5">
                                                        <Signal {...gpsBadge(gps.status, gps.satellites, gps.seconds_since_position)} />
                                                    </td>

                                                    {/* Connectivity Column */}
                                                    <td className="px-3 py-2.5">
                                                        <Signal {...connBadge(conn.status)} />
                                                    </td>

                                                    {/* Last Seen Column */}
                                                    <td className="whitespace-nowrap px-3 py-2.5 tabular-nums text-slate-600">
                                                        <span title={conn.last_message_ts ? formatUAEDateTime(conn.last_message_ts) : "—"}>
                                                            {fmtAgo(conn.seconds_since_update)}
                                                        </span>
                                                    </td>

                                                    {/* Primary Issue Column */}
                                                    <td className="max-w-[240px] px-4 py-2.5">
                                                        {issue.tone === "good" ? (
                                                            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700">
                                                                <span className="h-1.5 w-1.5 rounded-full bg-status-moving" />
                                                                None · All healthy
                                                            </span>
                                                        ) : (
                                                            <span className={`inline-flex items-center gap-1 truncate rounded px-2 py-0.5 text-[11px] font-medium border ${
                                                                issue.tone === "bad"
                                                                    ? "border-rose-200 bg-rose-50 text-rose-800"
                                                                    : "border-amber-200 bg-amber-50 text-amber-800"
                                                            }`}>
                                                                <AlertTriangle className="h-3 w-3 shrink-0" />
                                                                <span className="truncate">{issue.label}</span>
                                                            </span>
                                                        )}
                                                    </td>
                                                </tr>
                                            )
                                        })}
                                    </tbody>
                                </table>
                            </div>

                            {/* Table Footer */}
                            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-2.5 text-xs text-slate-500">
                                <div>
                                    Showing <span className="font-semibold text-slate-900 tabular-nums">{sortedVehicles.length}</span> of{" "}
                                    <span className="font-semibold text-slate-900 tabular-nums">{vehicles.length}</span> fleet vehicles
                                </div>
                                <span className="text-[11px] text-slate-400">Click any row to view full diagnostics</span>
                            </div>
                        </>
                    )}
                </section>
            </div>

            {/* 5. Vehicle Details Slide-Out Panel */}
            {activeVehicle && (
                <div
                    className="fixed inset-0 z-50 overflow-hidden"
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="slideout-vehicle-title"
                >
                    {/* Backdrop */}
                    <div
                        className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity"
                        onClick={closeDrawer}
                        aria-hidden="true"
                    />

                    <div className="fixed inset-y-0 right-0 flex max-w-full pl-10 pointer-events-none">
                        <div className="pointer-events-auto w-screen max-w-md sm:max-w-lg bg-white shadow-2xl flex flex-col border-l border-slate-200">
                            {/* Panel Header */}
                            <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4 bg-slate-50/70">
                                <div className="min-w-0 pr-3">
                                    <div className="flex items-center gap-2">
                                        <h2
                                            id="slideout-vehicle-title"
                                            className="font-mono text-base font-bold text-slate-900 truncate"
                                        >
                                            {activeVehicle.registration_plate || activeVehicle.plate || activeVehicle.vehicle_name || `Unit ${activeVehicle.vehicle_id}`}
                                        </h2>
                                        <FleetStatusBadge status={activeVehicle.status} size="xs" />
                                    </div>
                                    <p className="mt-0.5 truncate text-xs text-slate-500">
                                        {activeVehicle.vehicle_name ? `${activeVehicle.vehicle_name} · ` : ""}
                                        Group: {activeVehicle.group || "General Fleet"} · ID: {activeVehicle.vehicle_id}
                                    </p>
                                </div>

                                <div className="flex items-center gap-2">
                                    <div className={`rounded px-2 py-0.5 text-xs font-bold border ${scoreTone(activeVehicle.health_score).badge}`}>
                                        {activeVehicle.health_score ?? 0}% Health
                                    </div>
                                    <button
                                        type="button"
                                        onClick={closeDrawer}
                                        className="rounded-md p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                                        aria-label="Close vehicle details"
                                    >
                                        <X className="h-4 w-4" />
                                    </button>
                                </div>
                            </div>

                            {/* Section Navigation Tabs */}
                            <div className="flex border-b border-slate-200 bg-white px-5 pt-2 text-xs font-medium">
                                {[
                                    { id: "overview", label: "Overview" },
                                    { id: "diagnostics", label: "Diagnostics" },
                                    { id: "info", label: "Vehicle Info" },
                                    { id: "history", label: "History" },
                                ].map((tab) => (
                                    <button
                                        key={tab.id}
                                        type="button"
                                        onClick={() => setDrawerTab(tab.id)}
                                        className={`border-b-2 px-3 py-2 cursor-pointer transition-colors ${
                                            drawerTab === tab.id
                                                ? "border-brand text-brand font-semibold"
                                                : "border-transparent text-slate-500 hover:text-slate-800"
                                        }`}
                                    >
                                        {tab.label}
                                    </button>
                                ))}
                            </div>

                            {/* Panel Scrollable Content */}
                            <div className="flex-1 overflow-y-auto p-5 text-xs">
                                {/* SECTION 1: OVERVIEW */}
                                {drawerTab === "overview" && (
                                    <div className="space-y-4">
                                        <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
                                            <div className="flex items-center justify-between mb-2">
                                                <span className="font-semibold text-slate-800">Health Status</span>
                                                <span className={`font-bold ${scoreTone(activeVehicle.health_score).text}`}>
                                                    {scoreTone(activeVehicle.health_score).label} ({activeVehicle.health_score ?? 0}%)
                                                </span>
                                            </div>
                                            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
                                                <div
                                                    className={`h-full rounded-full ${scoreTone(activeVehicle.health_score).bar}`}
                                                    style={{ width: `${Math.min(100, Math.max(0, activeVehicle.health_score ?? 0))}%` }}
                                                />
                                            </div>
                                            <p className="mt-2 text-[11px] text-slate-500">
                                                Transparent score evaluated from real-time power, GPS satellite geometry, and telemetry packet freshness.
                                            </p>
                                        </div>

                                        <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Vehicle ID</dt>
                                                <dd className="font-mono font-medium text-slate-900">{activeVehicle.vehicle_id}</dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Name</dt>
                                                <dd className="font-medium text-slate-900">{activeVehicle.vehicle_name || "—"}</dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Hardware / Type</dt>
                                                <dd className="font-medium text-slate-900">
                                                    {vehicleDetails?.hardware_type || vehicleDetails?.type || activeVehicle.type || "Commercial Fleet Unit"}
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Assigned Group</dt>
                                                <dd className="font-medium text-slate-900">{activeVehicle.group || "General Fleet"}</dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Connectivity</dt>
                                                <dd><Signal {...connBadge(activeVehicle.connectivity?.status)} /></dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Last Seen</dt>
                                                <dd className="text-right">
                                                    <span className="font-medium text-slate-900 tabular-nums">
                                                        {activeVehicle.connectivity?.last_message_ts ? formatUAEDateTime(activeVehicle.connectivity.last_message_ts) : "—"}
                                                    </span>
                                                    <span className="block text-[11px] text-slate-500">
                                                        {fmtAgo(activeVehicle.connectivity?.seconds_since_update)}
                                                    </span>
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Primary Diagnostic Issue</dt>
                                                <dd className="text-right">
                                                    <span className={`font-semibold ${
                                                        getPrimaryIssue(activeVehicle).tone === "bad"
                                                            ? "text-rose-700"
                                                            : getPrimaryIssue(activeVehicle).tone === "warn"
                                                            ? "text-amber-800"
                                                            : "text-emerald-700"
                                                    }`}>
                                                        {getPrimaryIssue(activeVehicle).label}
                                                    </span>
                                                </dd>
                                            </div>
                                        </dl>
                                    </div>
                                )}

                                {/* SECTION 2: DIAGNOSTICS */}
                                {drawerTab === "diagnostics" && (
                                    <div className="space-y-4">
                                        {/* Battery Block */}
                                        <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-xs">
                                            <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                                                <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                                                    <Battery className="h-4 w-4 text-brand" />
                                                    <span>Battery & Power Subsystem</span>
                                                </div>
                                                <Signal {...batteryBadge(activeVehicle.battery)} />
                                            </div>

                                            <dl className="mt-2.5 divide-y divide-slate-100 text-xs">
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Main Battery Voltage</dt>
                                                    <dd className="font-mono font-bold text-slate-900">
                                                        {activeVehicle.battery?.voltage != null ? `${fmt(activeVehicle.battery.voltage, 2)} V` : "No data"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">System Classification</dt>
                                                    <dd className="font-medium text-slate-800">
                                                        {activeVehicle.battery?.system_type || "Unknown"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Internal Backup Battery</dt>
                                                    <dd className="font-mono text-slate-800">
                                                        {activeVehicle.battery?.internal_voltage ? `${fmt(activeVehicle.battery.internal_voltage, 2)} V` : "—"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Telemetry Freshness</dt>
                                                    <dd className="font-medium text-slate-800">
                                                        {activeVehicle.battery?.stale ? (
                                                            <span className="text-amber-800">Last reported (device offline/delayed)</span>
                                                        ) : (
                                                            <span className="text-emerald-700">Live active reading</span>
                                                        )}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Reading Timestamp</dt>
                                                    <dd className="tabular-nums text-slate-700">
                                                        {activeVehicle.connectivity?.last_message_ts ? formatUAEDateTime(activeVehicle.connectivity.last_message_ts) : "—"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Source</dt>
                                                    <dd className="text-slate-500">Wialon CAN-Bus / Sensor Telemetry</dd>
                                                </div>
                                            </dl>
                                        </div>

                                        {/* GPS Block */}
                                        <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-xs">
                                            <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                                                <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                                                    <Navigation className="h-4 w-4 text-brand" />
                                                    <span>GPS & Satellites Subsystem</span>
                                                </div>
                                                <Signal {...gpsBadge(activeVehicle.gps?.status, activeVehicle.gps?.satellites, activeVehicle.gps?.seconds_since_position)} />
                                            </div>

                                            <dl className="mt-2.5 divide-y divide-slate-100 text-xs">
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Satellite Count</dt>
                                                    <dd className="font-mono font-bold text-slate-900">
                                                        {parseSatellites(activeVehicle.gps?.satellites).label}
                                                    </dd>
                                                </div>
                                                {parseSatellites(activeVehicle.gps?.satellites).isSentinel && (
                                                    <div className="rounded bg-rose-50 p-2 text-rose-800 text-[11px]">
                                                        Value 255 detected: tracker protocol sentinel byte indicating invalid GPS fix.
                                                    </div>
                                                )}
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Signal Quality</dt>
                                                    <dd className="font-semibold text-slate-800">
                                                        {activeVehicle.gps?.status || "UNKNOWN"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Fix Validity</dt>
                                                    <dd className="text-slate-800">
                                                        {activeVehicle.gps?.status === "STALE" ? (
                                                            <span className="text-amber-800 font-medium">Stale Fix ({fmtAgo(activeVehicle.gps?.seconds_since_position)} old)</span>
                                                        ) : isValidCoordinate(activeVehicle.gps?.latitude, activeVehicle.gps?.longitude) ? (
                                                            <span className="text-emerald-700 font-medium">Valid 3D Fix</span>
                                                        ) : (
                                                            <span className="text-slate-400">No Valid Coordinate Fix</span>
                                                        )}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">HDOP / Precision</dt>
                                                    <dd className="tabular-nums text-slate-800">
                                                        {activeVehicle.gps?.hdop != null ? `HDOP ${activeVehicle.gps.hdop}` : "—"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Position Fix Timestamp</dt>
                                                    <dd className="tabular-nums text-slate-700">
                                                        {activeVehicle.gps?.position_timestamp ? formatUAEDateTime(activeVehicle.gps.position_timestamp) : "—"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Source</dt>
                                                    <dd className="text-slate-500">Wialon GPS Receiver Telemetry</dd>
                                                </div>
                                            </dl>
                                        </div>

                                        {/* Connectivity Block */}
                                        <div className="rounded-lg border border-slate-200 bg-white p-3.5 shadow-xs">
                                            <div className="flex items-center justify-between border-b border-slate-100 pb-2">
                                                <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                                                    <Wifi className="h-4 w-4 text-brand" />
                                                    <span>Cellular Telemetry Link</span>
                                                </div>
                                                <Signal {...connBadge(activeVehicle.connectivity?.status)} />
                                            </div>

                                            <dl className="mt-2.5 divide-y divide-slate-100 text-xs">
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Connection State</dt>
                                                    <dd className="font-semibold text-slate-900">
                                                        {activeVehicle.connectivity?.status || "UNKNOWN"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Telemetry Freshness</dt>
                                                    <dd className="tabular-nums text-slate-800 font-medium">
                                                        {fmtAgo(activeVehicle.connectivity?.seconds_since_update)}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Status Description</dt>
                                                    <dd className="text-slate-800">
                                                        {activeVehicle.connectivity?.status === "OFFLINE" ? (
                                                            <span className="text-rose-700 font-medium">No messages received for &gt; 12 hours</span>
                                                        ) : activeVehicle.connectivity?.status === "DELAYED" ? (
                                                            <span className="text-amber-800 font-medium">Messages delayed (&gt; 10 minutes)</span>
                                                        ) : (
                                                            <span className="text-emerald-700 font-medium">Connection active and healthy</span>
                                                        )}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Last Message Timestamp</dt>
                                                    <dd className="tabular-nums text-slate-700">
                                                        {activeVehicle.connectivity?.last_message_ts ? formatUAEDateTime(activeVehicle.connectivity.last_message_ts) : "—"}
                                                    </dd>
                                                </div>
                                                <div className="flex justify-between py-1.5">
                                                    <dt className="text-slate-500">Source</dt>
                                                    <dd className="text-slate-500">Tracker Cellular Link / Wialon Gateway</dd>
                                                </div>
                                            </dl>
                                        </div>
                                    </div>
                                )}

                                {/* SECTION 3: VEHICLE INFORMATION */}
                                {drawerTab === "info" && (
                                    <div className="space-y-4">
                                        <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Plate Number</dt>
                                                <dd className="font-mono font-bold text-slate-900">
                                                    {activeVehicle.registration_plate || activeVehicle.plate || "—"}
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Current Status</dt>
                                                <dd className="flex items-center gap-1.5">
                                                    <FleetStatusBadge status={activeVehicle.status} size="xs" />
                                                    <span className="text-[11px] text-slate-500 font-medium capitalize">
                                                        ({normalizeVehicleStatus(activeVehicle).toLowerCase()})
                                                    </span>
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Current Speed</dt>
                                                <dd className="font-mono text-slate-900">{activeVehicle.speed ?? 0} km/h</dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Ignition State</dt>
                                                <dd className="font-medium text-slate-900">
                                                    {activeVehicle.ignition === true ? "On (Running)" : activeVehicle.ignition === false ? "Off (Stopped)" : "Unknown"}
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">GPS Coordinates</dt>
                                                <dd className="font-mono text-slate-800 text-right">
                                                    {isValidCoordinate(activeVehicle.gps?.latitude, activeVehicle.gps?.longitude) ? (
                                                        <span>
                                                            {Number(activeVehicle.gps.latitude).toFixed(4)}°, {Number(activeVehicle.gps.longitude).toFixed(4)}°
                                                        </span>
                                                    ) : (
                                                        <span className="text-slate-400">No valid fix</span>
                                                    )}
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Odometer / Mileage</dt>
                                                <dd className="tabular-nums font-medium text-slate-900">
                                                    {formatKm(vehicleDetails?.mileage ?? vehicleDetails?.odometer ?? activeVehicle.mileage ?? activeVehicle.odometer) ?? "—"}
                                                </dd>
                                            </div>
                                            <div className="flex justify-between px-3.5 py-2.5">
                                                <dt className="text-slate-500">Engine Hours</dt>
                                                <dd className="tabular-nums font-medium text-slate-900">
                                                    {vehicleDetails?.engine_hours ? `${vehicleDetails.engine_hours} h` : "—"}
                                                </dd>
                                            </div>
                                        </dl>

                                        {/* Live Tracking Link */}
                                        <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                                            <h3 className="font-semibold text-slate-800 mb-1">Live Map Tracking</h3>
                                            <p className="text-[11px] text-slate-500 mb-3">
                                                Follow this vehicle live on the interactive fleet tracking map.
                                            </p>
                                            {isValidCoordinate(activeVehicle.gps?.latitude, activeVehicle.gps?.longitude) ? (
                                                <Link
                                                    to={`/tracking?vehicleId=${encodeURIComponent(activeVehicle.vehicle_id)}`}
                                                    className="inline-flex items-center gap-1.5 rounded-md bg-brand px-3 py-2 text-xs font-semibold text-white shadow-xs hover:bg-brand/90 transition-colors cursor-pointer"
                                                >
                                                    Open in Live Tracking
                                                    <ArrowUpRight className="h-3.5 w-3.5" />
                                                </Link>
                                            ) : (
                                                <div className="rounded border border-slate-200 bg-white p-2.5 text-[11px] text-slate-500">
                                                    Live tracking unavailable: No valid GPS coordinates reported for this unit.
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}

                                {/* SECTION 4: HISTORY */}
                                {drawerTab === "history" && (
                                    <div className="space-y-3">
                                        <div className="flex items-center justify-between pb-1 border-b border-slate-100">
                                            <h3 className="font-semibold text-slate-800">24-Hour Telemetry History</h3>
                                            <span className="text-[11px] text-slate-400">Timestamped Wialon readings</span>
                                        </div>

                                        {loadingHistory ? (
                                            <LoadingState label="Reading historical diagnostic telemetry…" className="rounded-lg border-0 py-8" />
                                        ) : vehicleHistory?.telemetry && vehicleHistory.telemetry.length > 0 ? (
                                            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
                                                <table className="w-full text-[11px]">
                                                    <thead>
                                                        <tr className="border-b border-slate-200 bg-slate-50 text-left font-semibold text-slate-600">
                                                            <th className="px-3 py-2">Timestamp</th>
                                                            <th className="px-2 py-2">Status</th>
                                                            <th className="px-2 py-2">Speed</th>
                                                            <th className="px-2 py-2">Battery</th>
                                                            <th className="px-2 py-2">Sats</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-slate-100">
                                                        {vehicleHistory.telemetry.slice(0, 20).map((pt, idx) => (
                                                            <tr key={idx} className="hover:bg-slate-50">
                                                                <td className="whitespace-nowrap px-3 py-1.5 tabular-nums text-slate-700">
                                                                    {formatUAETime(pt.timestamp)}
                                                                </td>
                                                                <td className="px-2 py-1.5">
                                                                    <FleetStatusBadge status={pt.status} size="xs" />
                                                                </td>
                                                                <td className="px-2 py-1.5 tabular-nums text-slate-600">
                                                                    {pt.speed != null ? `${pt.speed} km/h` : "—"}
                                                                </td>
                                                                <td className="px-2 py-1.5 tabular-nums text-slate-600">
                                                                    {pt.sensors?.battery_voltage != null ? `${Number(pt.sensors.battery_voltage).toFixed(1)} V` : "—"}
                                                                </td>
                                                                <td className="px-2 py-1.5 tabular-nums text-slate-600">
                                                                    {pt.location?.satellites != null ? pt.location.satellites : "—"}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ) : (
                                            <EmptyState
                                                icon={Clock}
                                                title="No diagnostic history available"
                                                description="No timestamped telemetry messages or diagnostic readings were recorded for this vehicle in the last 24 hours."
                                                className="rounded-lg border border-slate-200 bg-slate-50/50 py-8"
                                            />
                                        )}
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </AppShell>
    )
}
