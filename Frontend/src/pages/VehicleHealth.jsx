import { useCallback, useEffect, useRef, useState, useMemo } from "react"
import { createPortal } from "react-dom"
import {
    Truck,
    AlertTriangle,
    X,
    ArrowUpDown,
    ChevronUp,
    ChevronDown,
    Activity,
    Battery,
    Wifi,
    Navigation,
    ShieldAlert,
} from "lucide-react"
import AppShell from "../components/Common/AppShell"
import PageHeader from "../components/Common/PageHeader"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import SearchInput from "../components/Common/SearchInput"
import { EmptyState, ErrorBanner, ErrorState, LoadingState } from "../components/Common/StateViews"
import { getFleetHealthAnalytics, peekFleetHealthAnalytics } from "../api/fleetApi"
import { formatUAETime, formatUAEDateTime } from "../utils/datetime"
import { isHealthyVehicle } from "../utils/fleetRules"

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

const vehicleLabel = (v) => v.registration_plate || v.plate || v.vehicle_name || `Unit ${v.vehicle_id}`

// Vehicle type is the unit's Wialon group (as the original table showed it), including "General Fleet"
const vehicleType = (v) => v.group || null

const hasScore = (v) => typeof v?.health_score === "number" && Number.isFinite(v.health_score)

// Device health is the backend's battery classification; nothing is recalculated here
const deviceHealth = (battery = {}) => {
    const s = String(battery.status || "").toUpperCase()
    if (s === "HEALTHY") return { label: "Healthy", tone: "good" }
    if (s === "WARNING" && battery.system_type === "Unknown") return { label: "Unclassified voltage", tone: "warn" }
    if (s === "WARNING") return { label: "Low battery", tone: "warn" }
    if (s === "CRITICAL") return { label: "Critical battery", tone: "bad" }
    return { label: "Unknown", tone: "none" }
}

const gpsFix = (gps = {}) => {
    const s = String(gps.status || "").toUpperCase()
    if (s === "STALE") return { label: "Stale fix", tone: "warn" }
    const satInfo = parseSatellites(gps.satellites)
    // A 255 or out-of-range count means the tracker had no valid fix, whatever quality was derived from it
    if (satInfo.isSentinel || (satInfo.count !== null && !satInfo.isValid)) return { label: "No valid fix", tone: "bad" }
    if (satInfo.count === 0) return { label: "No lock", tone: "warn" }
    if (s === "EXCELLENT") return { label: "Excellent", tone: "good" }
    if (s === "GOOD") return { label: "Good", tone: "good" }
    if (s === "WEAK") return { label: "Weak", tone: "warn" }
    return { label: "Unknown", tone: "none" }
}

const satelliteText = (sats) => {
    const satInfo = parseSatellites(sats)
    if (satInfo.isValid) return String(satInfo.count)
    if (satInfo.count !== null) return "Invalid reading"
    return "Not available"
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

function DetailRow({ label, children }) {
    return (
        <div className="flex items-start justify-between gap-4 py-2">
            <dt className="shrink-0 text-slate-500">{label}</dt>
            <dd className="min-w-0 text-right font-medium text-slate-900">{children}</dd>
        </div>
    )
}

function NotAvailable() {
    return <span className="font-normal text-slate-400">Not available</span>
}

// Current vs last-reported tag for a group of readings, with the time the reading was taken
function ReadingTag({ historical, ts }) {
    return historical ? (
        <span className="text-[11px] font-medium text-amber-800">
            Last reported{ts ? ` · ${formatUAEDateTime(ts)}` : ""}
        </span>
    ) : (
        <span className="text-[11px] font-medium text-emerald-700">Current</span>
    )
}

function DetailSection({ icon: Icon, title, tag, children }) {
    return (
        <section>
            <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-1.5">
                <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-700">
                    <Icon className="h-3.5 w-3.5 text-brand" aria-hidden="true" />
                    {title}
                </h3>
                {tag}
            </div>
            <dl className="divide-y divide-slate-100">{children}</dl>
        </section>
    )
}

function VehicleHealthModal({ vehicle, onClose }) {
    const closeRef = useRef(null)

    useEffect(() => {
        const handleKeyDown = (e) => {
            if (e.key === "Escape") onClose()
        }
        window.addEventListener("keydown", handleKeyDown)
        closeRef.current?.focus()
        return () => window.removeEventListener("keydown", handleKeyDown)
    }, [onClose])

    const bat = vehicle.battery || {}
    const gps = vehicle.gps || {}
    const conn = vehicle.connectivity || {}
    const connStatus = String(conn.status || "").toUpperCase()
    // Delayed/offline telemetry means every reading below is the last one received, not a live value
    const telemetryHistorical = connStatus === "DELAYED" || connStatus === "OFFLINE"
    const gpsHistorical = telemetryHistorical || String(gps.status || "").toUpperCase() === "STALE"
    const sc = scoreTone(vehicle.health_score)
    const type = vehicleType(vehicle)
    const device = deviceHealth(bat)
    const fix = gpsFix(gps)

    return createPortal(
        <div
            className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-[2px] animate-in fade-in duration-200"
            onClick={onClose}
        >
            <div
                className="relative flex max-h-[90vh] w-full max-w-md flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="health-modal-title"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-5 py-3.5">
                    <div className="min-w-0">
                        <h2 id="health-modal-title" className="truncate font-mono text-base font-bold text-slate-900">
                            {vehicleLabel(vehicle)}
                        </h2>
                        <p className="truncate text-xs text-slate-500">{type || "Vehicle type not available"}</p>
                    </div>
                    <button
                        ref={closeRef}
                        type="button"
                        onClick={onClose}
                        className="-mr-1.5 shrink-0 rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                        aria-label="Close vehicle details"
                    >
                        <X className="h-4 w-4" />
                    </button>
                </div>

                <div className="space-y-4 overflow-y-auto px-5 py-4 text-xs">
                    <DetailSection icon={Truck} title="Vehicle overview">
                        <DetailRow label="Vehicle number">
                            <span className="font-mono">{vehicleLabel(vehicle)}</span>
                        </DetailRow>
                        <DetailRow label="Vehicle type">{type || <NotAvailable />}</DetailRow>
                        <DetailRow label="Overall health">
                            {hasScore(vehicle) ? (
                                <span className={`font-bold tabular-nums ${sc.text}`}>{vehicle.health_score}%</span>
                            ) : (
                                <span className="font-normal text-slate-400">Unknown</span>
                            )}
                        </DetailRow>
                        <DetailRow label="Connectivity">
                            <Signal {...connBadge(conn.status)} />
                        </DetailRow>
                        <DetailRow label="Last reported">
                            {conn.last_message_ts ? (
                                <>
                                    <span className="tabular-nums">{formatUAEDateTime(conn.last_message_ts)}</span>
                                    <span className="block text-[11px] font-normal text-slate-500">
                                        {fmtAgo(conn.seconds_since_update)}
                                    </span>
                                </>
                            ) : (
                                <NotAvailable />
                            )}
                        </DetailRow>
                    </DetailSection>

                    <DetailSection
                        icon={Battery}
                        title="Device information"
                        tag={bat.voltage != null && <ReadingTag historical={telemetryHistorical} ts={conn.last_message_ts} />}
                    >
                        <DetailRow label="Battery voltage">
                            {bat.voltage != null ? <span className="font-mono">{fmt(bat.voltage, 2)} V</span> : <NotAvailable />}
                        </DetailRow>
                        <DetailRow label="Device health">
                            {device.tone === "none" ? <NotAvailable /> : <Signal label={device.label} tone={device.tone} />}
                        </DetailRow>
                    </DetailSection>

                    <DetailSection
                        icon={Navigation}
                        title="GPS information"
                        tag={gps.satellites != null && <ReadingTag historical={gpsHistorical} ts={gps.position_timestamp || conn.last_message_ts} />}
                    >
                        <DetailRow label="Satellites">
                            <span className={parseSatellites(gps.satellites).isValid ? "font-mono" : "font-normal text-slate-400"}>
                                {satelliteText(gps.satellites)}
                            </span>
                        </DetailRow>
                        <DetailRow label="GPS fix">
                            {fix.tone === "none" ? <NotAvailable /> : <Signal label={fix.label} tone={fix.tone} />}
                        </DetailRow>
                    </DetailSection>
                </div>
            </div>
        </div>,
        document.body
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

    // Vehicle details modal state
    const [selectedVehicleId, setSelectedVehicleId] = useState(null)

    const openDetails = useCallback((vehicleId) => {
        setSelectedVehicleId(vehicleId)
    }, [])

    const closeDetails = useCallback(() => {
        setSelectedVehicleId(null)
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
                                            onClick={() => openDetails(v.vehicle_id)}
                                            role="button"
                                            tabIndex={0}
                                            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetails(v.vehicle_id) } }}
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
                                                <span title="GPS">{parseSatellites(gps.satellites).isValid ? `${gps.satellites}sats` : "No GPS"}</span>
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
                                <table className="w-full min-w-[560px] text-xs">
                                    <thead>
                                        <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] font-semibold text-slate-600">
                                            {[
                                                { field: "vehicle", label: "Vehicle", className: "px-4" },
                                                { field: "health_score", label: "Health", className: "px-3" },
                                                { field: "connectivity", label: "Connectivity", className: "px-3" },
                                                { field: "last_seen", label: "Last Reported", className: "px-4" },
                                            ].map((col) => (
                                                <th
                                                    key={col.field}
                                                    scope="col"
                                                    className={`${col.className} py-2.5`}
                                                    aria-sort={sortField === col.field ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                                                >
                                                    <button
                                                        type="button"
                                                        onClick={() => toggleSort(col.field)}
                                                        className="group inline-flex items-center gap-1.5 font-semibold text-slate-700 hover:text-slate-900 cursor-pointer"
                                                    >
                                                        {col.label} {sortIndicator(col.field)}
                                                    </button>
                                                </th>
                                            ))}
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100">
                                        {sortedVehicles.map((v) => {
                                            const conn = v.connectivity || {}
                                            const sc = scoreTone(v.health_score)
                                            const type = vehicleType(v)
                                            const isSelected = selectedVehicleId === v.vehicle_id

                                            return (
                                                <tr
                                                    key={v.vehicle_id}
                                                    onClick={() => openDetails(v.vehicle_id)}
                                                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetails(v.vehicle_id) } }}
                                                    tabIndex={0}
                                                    aria-label={`View health details for ${vehicleLabel(v)}`}
                                                    className={`group transition-colors cursor-pointer hover:bg-slate-50/90 focus-visible:outline-none focus-visible:bg-slate-50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand/40 ${
                                                        isSelected ? "bg-brand-light/70" : ""
                                                    }`}
                                                >
                                                    {/* Vehicle number and type */}
                                                    <td className="max-w-[260px] px-4 py-2">
                                                        <p className="truncate font-mono text-[13px] font-bold text-slate-900 group-hover:text-brand transition-colors">
                                                            {vehicleLabel(v)}
                                                        </p>
                                                        <p className={`truncate text-[11px] ${type ? "text-slate-500" : "text-slate-400"}`}>
                                                            {type || "Type not available"}
                                                        </p>
                                                    </td>

                                                    {/* Overall health */}
                                                    <td className="px-3 py-2">
                                                        {hasScore(v) ? (
                                                            <div className="flex items-center gap-2">
                                                                <span className={`w-9 text-right font-bold tabular-nums ${sc.text}`}>
                                                                    {v.health_score}%
                                                                </span>
                                                                <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                                                                    <div
                                                                        className={`h-full rounded-full ${sc.bar}`}
                                                                        style={{ width: `${Math.min(100, Math.max(0, v.health_score))}%` }}
                                                                    />
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <span className="text-slate-400">Unknown</span>
                                                        )}
                                                    </td>

                                                    {/* Connectivity */}
                                                    <td className="px-3 py-2">
                                                        <Signal {...connBadge(conn.status)} />
                                                    </td>

                                                    {/* Last reported */}
                                                    <td className="whitespace-nowrap px-4 py-2 tabular-nums">
                                                        {conn.last_message_ts ? (
                                                            <>
                                                                <span className="text-slate-800">{formatUAEDateTime(conn.last_message_ts)}</span>
                                                                <span className="block text-[11px] text-slate-500">{fmtAgo(conn.seconds_since_update)}</span>
                                                            </>
                                                        ) : (
                                                            <span className="text-slate-400">Not available</span>
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
                                <span className="text-[11px] text-slate-400">Click a row to view vehicle details</span>
                            </div>
                        </>
                    )}
                </section>
            </div>

            {/* 5. Vehicle Details Modal */}
            {activeVehicle && <VehicleHealthModal vehicle={activeVehicle} onClose={closeDetails} />}
        </AppShell>
    )
}
