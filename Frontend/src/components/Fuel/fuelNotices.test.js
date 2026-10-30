// Offline tests for the fuel dashboard's data notices. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { fuelScopeNotices } from "./fuelNotices.js"

const keys = (notices) => notices.map((n) => n.key)

test("no notices for a closed month with clean scope and telematics", () => {
    const efficiency = { telematics_status: "complete", unmatched_transactions_count: 0, excluded_scope: { monthly_summary_rows: 61, warnings: [] } }
    assert.deepEqual(fuelScopeNotices({ efficiency, selectedMonth: "2026-09", today: "2026-10-30" }), [])
})

test("current month is flagged month-to-date only in month mode", () => {
    assert.deepEqual(keys(fuelScopeNotices({ efficiency: null, selectedMonth: "2026-10", today: "2026-10-30" })), ["mtd"])
    assert.deepEqual(fuelScopeNotices({ efficiency: null, selectedMonth: "2026-10", today: "2026-10-30", monthMode: false }), [])
})

test("unmapped monthly records are named as included in the totals, never silently dropped", () => {
    const efficiency = { unmatched_transactions_count: 2, unmatched_litres: 2993.19, excluded_scope: { all_transaction_count: 61 } }
    const [notice] = fuelScopeNotices({ efficiency, selectedMonth: "2026-08", today: "2026-10-30" })
    assert.equal(notice.key, "unmapped")
    assert.equal(notice.text, "2 monthly record(s) (2,993.2 L) match no single Wialon unit: included in fuel totals and listed as unmapped, without distance or idle data.")
})

test("dated rows are reported as not used only for a period with monthly data", () => {
    const scope = (all) => ({ all_transaction_count: all, dated_not_reported: { transaction_count: 2335 } })
    const withData = fuelScopeNotices({ efficiency: { excluded_scope: scope(61) }, selectedMonth: "2026-08", today: "2026-10-30" })
    assert.deepEqual(keys(withData), ["dated"])
    assert.match(withData[0].text, /^2335 dated transaction row\(s\) .* monthly summaries only\.$/)
    // Without monthly data the empty state explains it: no duplicate notice
    assert.deepEqual(fuelScopeNotices({ efficiency: { excluded_scope: scope(0) }, selectedMonth: "2026-08", today: "2026-10-30" }), [])
})

test("notices keep their order", () => {
    const efficiency = {
        telematics_status: "partial",
        data_warnings: ["Report timed out"],
        unmatched_transactions_count: 1,
        unmatched_litres: 25,
        excluded_scope: {
            all_transaction_count: 4,
            dated_not_reported: { transaction_count: 3 },
            warnings: ["2 monthly-summary row(s) for 2026-09 are excluded"],
        },
    }
    const notices = fuelScopeNotices({ efficiency, selectedMonth: "2026-10", today: "2026-10-30" })
    assert.deepEqual(keys(notices), ["mtd", "unmapped", "dated", "range-0", "telematics"])
    assert.equal(notices[4].text, "Wialon telematics incomplete: Report timed out")
})

test("pending telematics say the values will appear", () => {
    const efficiency = { telematics_status: "unavailable", excluded_scope: {} }
    const [notice] = fuelScopeNotices({ efficiency, selectedMonth: "2026-09", today: "2026-10-30", telematicsPending: true })
    assert.match(notice.text, /still running/)
})
