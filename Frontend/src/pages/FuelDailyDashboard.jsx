import { useCallback, useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { ArrowLeft, CalendarX } from "lucide-react"
import AppShell from "../components/Common/AppShell"
import SectionCard from "../components/Common/SectionCard"
import KpiCard, { KpiStrip } from "../components/Common/KpiCard"
import Pagination from "../components/Common/Pagination"
import { EmptyState, ErrorState, LoadingState } from "../components/Common/StateViews"
import { useFuelFilters } from "../context/useFuelFilters"
import { getFuelDaily, getFuelTransactions } from "../api/fleetApi"
import FuelHeader from "../components/Fuel/FuelHeader"
import FuelTrendChart from "../components/Fuel/FuelTrendChart"
import VehicleFuelDetailModal from "../components/Fuel/VehicleFuelDetailModal"
import { DAILY_COVERAGE_REASONS, dailySearch, datedDailyAvailability, daysInMonth, parseMonthParam } from "../components/Fuel/fuelPeriod"
import { fixedOrDash, fuelTypeLabel, isRefundAdjustment } from "../components/Fuel/fuelLabels"
import { isMonthlySummaryRow } from "../components/Fuel/fuelImportState"
import { formatMeasurement } from "../components/Fuel/telemetryFormat"
import { getUAETodayStr } from "../utils/datetime"

const PAGE_SIZE = 50

const backLink = "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"

// Dated totals from /analytics/daily (statement-verified: every day is covered by the imported dated statements, so a
// day without rows is a zero within those statements; other suppliers' fuel cannot be detected).
// Monthly-summary rows are never placed on days; they are reported in monthly_summary_excluded.
function DatedTotals({ daily, month }) {
    const rows = Array.isArray(daily?.data) ? daily.data : []
    const sum = (key) => rows.reduce((total, r) => total + (Number(r?.[key]) || 0), 0)
    const daysWithRows = rows.filter((r) => Number(r?.transactions) > 0).length
    const excludedRows = Number(daily?.monthly_summary_excluded?.rows) || 0
    const refunds = sum("refund_count")

    return (
        <div className="space-y-1.5">
            <KpiStrip label="Dated fuel totals" className="grid-cols-2 sm:grid-cols-4">
                <KpiCard label="Dated Fuel" value={`${formatMeasurement(sum("litres"), 1)} L`} sub="Net of refunds" />
                <KpiCard label="Dated Cost" value={`AED ${formatMeasurement(sum("cost"), 0)}`} sub="Net of refunds" />
                <KpiCard label="Dated Refuels" value={sum("dated_purchase_count").toLocaleString("en-US")} sub={refunds > 0 ? `Plus ${refunds} refund adjustment(s)` : "No refund adjustments"} />
                <KpiCard label="Days with Transactions" value={`${daysWithRows} / ${daysInMonth(month)}`} sub="Other days: none in imported statements" />
            </KpiStrip>
            <p className="text-[11px] text-slate-500" role="note">
                {daily?.coverage_note || "Zero days are zeroes within the imported statements; fuel from a supplier whose statement was not imported cannot be detected."}
            </p>
            {excludedRows > 0 && (
                <p className="text-[11px] text-slate-500">{excludedRows} monthly-summary row(s) not shown: monthly totals have no daily dates.</p>
            )}
        </div>
    )
}

// Dated transactions only. The endpoint also returns monthly-summary rows (date_basis reporting_month_end): they are
// hidden here, never shown as refuels on their reporting date.
function DatedTransactions({ queryParams, onSelectVehicle }) {
    const [page, setPage] = useState(1)
    const [state, setState] = useState({ loading: true, error: null, rows: [], total: 0, hidden: 0 })
    const [attempt, setAttempt] = useState(0)

    useEffect(() => {
        let active = true
        getFuelTransactions({ ...queryParams, page, page_size: PAGE_SIZE })
            .then((res) => {
                if (!active) return
                const all = Array.isArray(res?.transactions) ? res.transactions : []
                const dated = all.filter((t) => !isMonthlySummaryRow(t))
                setState({ loading: false, error: null, rows: dated, total: Number(res?.total) || 0, hidden: all.length - dated.length })
            })
            .catch((err) => {
                if (active) setState({ loading: false, error: err.message || "Failed to load transactions.", rows: [], total: 0, hidden: 0 })
            })
        return () => { active = false }
    }, [queryParams, page, attempt])

    const totalPages = Math.max(1, Math.ceil(state.total / PAGE_SIZE))
    const retry = () => {
        setState((s) => ({ ...s, loading: true, error: null }))
        setAttempt((n) => n + 1)
    }

    return (
        <SectionCard title="Dated Transactions" flush>
            {state.loading ? (
                <LoadingState className="border-0" />
            ) : state.error ? (
                <ErrorState message={state.error} onRetry={retry} className="border-0" />
            ) : state.rows.length === 0 ? (
                <EmptyState title="No dated transactions" className="border-0" />
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] text-xs">
                        <thead className="border-b border-slate-100 bg-slate-50/60 text-[11px] text-slate-500">
                            <tr>
                                <th scope="col" className="px-4 py-2 text-left font-medium">Date</th>
                                <th scope="col" className="px-4 py-2 text-left font-medium">Vehicle</th>
                                <th scope="col" className="px-4 py-2 text-left font-medium">Fuel type</th>
                                <th scope="col" className="px-4 py-2 text-right font-medium">Litres</th>
                                <th scope="col" className="px-4 py-2 text-right font-medium">Cost (AED)</th>
                                <th scope="col" className="px-4 py-2 text-left font-medium">Station</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {state.rows.map((t) => (
                                <tr key={t.id} className="hover:bg-slate-50/60">
                                    <td className="px-4 py-2 tabular-nums text-slate-700">{String(t.transaction_date || "—").slice(0, 16).replace("T", " ")}</td>
                                    <td className="px-4 py-2">
                                        <button
                                            type="button"
                                            onClick={() => onSelectVehicle(t.vehicle_identifier)}
                                            className="font-mono font-semibold text-slate-900 hover:text-brand cursor-pointer"
                                        >
                                            {t.vehicle_identifier}
                                        </button>
                                    </td>
                                    <td className="px-4 py-2 text-slate-600">{fuelTypeLabel(t.fuel_type)}</td>
                                    <td className="px-4 py-2 text-right tabular-nums text-slate-900">
                                        {formatMeasurement(t.litres, 1)}
                                        {isRefundAdjustment(t) && <span className="ml-1.5 rounded bg-slate-100 px-1 text-[10px] text-slate-600">Refund</span>}
                                    </td>
                                    <td className="px-4 py-2 text-right tabular-nums text-slate-700">{fixedOrDash(t.total_cost)}</td>
                                    <td className="max-w-[200px] truncate px-4 py-2 text-slate-500" title={t.station_name || undefined}>{t.station_name || "—"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {!state.loading && !state.error && state.total > 0 && (
                <Pagination
                    page={page}
                    totalPages={totalPages}
                    onPageChange={(p) => { setState((s) => ({ ...s, loading: true })); setPage(p) }}
                    start={(page - 1) * PAGE_SIZE + 1}
                    end={Math.min(page * PAGE_SIZE, state.total)}
                    total={state.total}
                    noun="statement rows"
                >
                    {state.hidden > 0 && <span>{state.hidden} monthly-summary row(s) hidden on this page</span>}
                </Pagination>
            )}
        </SectionCard>
    )
}

export default function FuelDailyDashboard() {
    const { queryParams, periodLabel, selectedMonth, setMonth, vehicle, setVehicle } = useFuelFilters()
    const [searchParams, setSearchParams] = useSearchParams()
    const [applied, setApplied] = useState(false)
    const [coverage, setCoverage] = useState({ loading: true, error: null, result: null })
    const [attempt, setAttempt] = useState(0)
    const [selectedVehicleDetail, setSelectedVehicleDetail] = useState(null)

    // A shared link's month and vehicle win on entry; afterwards the header filters drive the URL
    useEffect(() => {
        const urlMonth = parseMonthParam(searchParams.get("month"), getUAETodayStr())
        /* eslint-disable react-hooks/set-state-in-effect -- applies the link's month and vehicle to the shared fuel filters once */
        setMonth(urlMonth || selectedMonth)
        setVehicle(searchParams.get("vehicle") || "")
        setApplied(true)
        /* eslint-enable react-hooks/set-state-in-effect */
        // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once on entry
    }, [])

    useEffect(() => {
        if (!applied) return
        const next = dailySearch(selectedMonth, vehicle)
        if (next !== searchParams.toString()) setSearchParams(next, { replace: true })
    }, [applied, selectedMonth, vehicle, searchParams, setSearchParams])

    // Daily views need dated transaction rows in the month; monthly summaries alone have none (see datedDailyAvailability)
    const checkCoverage = useCallback(async (active) => {
        try {
            const res = await getFuelDaily(queryParams)
            if (active()) setCoverage({ loading: false, error: null, result: datedDailyAvailability(res), daily: res })
        } catch (err) {
            if (active()) setCoverage({ loading: false, error: err.message || "Failed to check daily coverage.", result: null })
        }
    }, [queryParams])

    useEffect(() => {
        if (!applied) return undefined
        let alive = true
        // eslint-disable-next-line react-hooks/set-state-in-effect -- the coverage check shows its loading state on every filter change
        setCoverage({ loading: true, error: null, result: null })
        checkCoverage(() => alive)
        return () => { alive = false }
    }, [applied, checkCoverage, attempt])

    const filterKey = new URLSearchParams(queryParams).toString()

    const monthlyLink = (
        <Link to="/fuel" className={backLink}>
            <ArrowLeft className="h-3.5 w-3.5 text-slate-500" />
            Back to Monthly
        </Link>
    )

    return (
        <AppShell>
            <FuelHeader title="Daily Fuel Dashboard" subtitle={periodLabel} allowCustomRange={false} actions={monthlyLink} />

            {!applied || coverage.loading ? (
                <LoadingState label="Checking daily coverage…" />
            ) : coverage.error ? (
                <ErrorState message={coverage.error} onRetry={() => setAttempt((n) => n + 1)} />
            ) : !coverage.result?.available ? (
                <EmptyState
                    icon={CalendarX}
                    title={`Daily dashboard unavailable for ${periodLabel}`}
                    message={DAILY_COVERAGE_REASONS[coverage.result?.reason] || DAILY_COVERAGE_REASONS.unknown}
                    action={monthlyLink}
                />
            ) : (
                <div className="space-y-5">
                    {/* Keyed on the filters: a new month or vehicle remounts each section with a fresh loading state */}
                    <DatedTotals key={`totals-${filterKey}`} daily={coverage.daily} month={selectedMonth} />
                    <FuelTrendChart />
                    <DatedTransactions key={`tx-${filterKey}`} queryParams={queryParams} onSelectVehicle={setSelectedVehicleDetail} />
                </div>
            )}

            {selectedVehicleDetail && (
                <VehicleFuelDetailModal
                    vehicleIdentifier={selectedVehicleDetail}
                    onClose={() => setSelectedVehicleDetail(null)}
                    dateFrom={queryParams.date_from}
                    dateTo={queryParams.date_to}
                    idleThresholdMode="per_day"
                />
            )}
        </AppShell>
    )
}
