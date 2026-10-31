// Pure data-notice rules of the fuel dashboard (unit-tested in fuelNotices.test.js). Monthly summaries are the only
// fuel reporting source. Each notice is { key, tone: "info" | "warn", text, title? }, most relevant first.
import { excludedSummary, statementSplit } from "./fuelLabels.js"
import { isCurrentMonth, monthLabel } from "./fuelPeriod.js"

const litresText = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 1 })

export function fuelScopeNotices({ efficiency, selectedMonth, today, monthMode = true, telematicsPending = false }) {
    const scope = efficiency?.excluded_scope
    const notices = []
    if (monthMode && isCurrentMonth(selectedMonth, today)) {
        notices.push({ key: "mtd", tone: "info", text: `${monthLabel(selectedMonth)} is in progress: month-to-date figures.` })
    }
    // Records with no single Wialon unit stay in the Excel statement totals; the matched fleet never includes them
    const unmappedRows = Number(efficiency?.unmatched_transactions_count) || 0
    if (unmappedRows > 0) {
        // The same litres as the KPI cards and Data Coverage: statement − matched at 0.1 L
        const unmappedLitres = statementSplit(efficiency.total_litres, efficiency.matched_litres, 1).rest
        notices.push({
            key: "unmapped",
            tone: "warn",
            text: `${unmappedRows} monthly record(s)${unmappedLitres === null ? "" : ` (${litresText(unmappedLitres)} L)`} match no single Wialon vehicle: included in the Excel statement totals only, listed in Data Coverage.`,
        })
    }
    // A period with data: its dated statement rows are not reported (the empty state says so for a period without)
    const datedRows = Number(scope?.dated_not_reported?.transaction_count) || 0
    if (datedRows > 0 && Number(scope?.all_transaction_count) > 0) {
        notices.push({
            key: "dated",
            tone: "info",
            text: `${datedRows} dated transaction row(s) in this period are not reported: fuel is reported from monthly summaries only.`,
        })
    }
    const summaryRangeWarnings = (Array.isArray(scope?.warnings) ? scope.warnings : []).filter((w) => /monthly-summary/i.test(w))
    summaryRangeWarnings.forEach((w, i) => notices.push({ key: `range-${i}`, tone: "warn", text: w }))
    const partialSummaries = excludedSummary(scope)?.partialSummaries || 0
    if (partialSummaries > 0 && summaryRangeWarnings.length === 0) {
        notices.push({ key: "partial", tone: "warn", text: `${partialSummaries} monthly-summary row(s) excluded: the range does not cover their whole month.` })
    }
    const telematicsWarnings = Array.isArray(efficiency?.data_warnings) ? efficiency.data_warnings : []
    // "unavailable": Wialon reports failed or did not answer in time; fuel figures are shown, telematics values are "—"
    if (efficiency?.telematics_status === "partial" || efficiency?.telematics_status === "unavailable") {
        notices.push({
            key: "telematics",
            tone: "warn",
            text: telematicsPending
                ? "Wialon reports are still running. Distance, driving and idle values will appear automatically."
                : `Wialon telematics ${efficiency.telematics_status === "unavailable" ? "unavailable" : "incomplete"}${telematicsWarnings[0] ? `: ${telematicsWarnings[0]}` : "."}`,
            title: telematicsWarnings.join("\n"),
        })
    }
    return notices
}
