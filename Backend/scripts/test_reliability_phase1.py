"""
Offline tests for the Phase 1 reliability and data-access fixes (performance audit):

  - /api/vehicles: a Wialon failure serves the last good fleet marked stale with its own read time, backs off (no
    Wialon call per poll), never a fabricated fleet; fresh reads carry stale=False
  - Fuel monthly-summary reporting reads dated rows with only the columns their totals need (summary table layout),
    and every reported figure (KPI scope, dated_not_reported counts, litres, cost, refunds) is exactly the same
  - /api/fuel/analytics/latest-month: the newest month with a monthly summary matched to exactly one Wialon unit
    (unmatched, ambiguous and future months skipped), reading no dated rows; equal to the matched monthly trend rule;
    no summaries -> null without contacting Wialon; a failed read -> 503

(Fleet trips: no per-unit fallback, last good result marked stale, bounded backoff: test_trips_mileage_integrity.py.)
Wialon and Supabase are mocked; OfflineGuard blocks every outbound HTTP call.
Run from Backend/:  python scripts/test_reliability_phase1.py
"""
import os
import sys
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import AUTH, FAKE_USER, UNITS, Api, run, summary  # noqa: E402

import app.fuel.db as fuel_db  # noqa: E402
import app.main as main  # noqa: E402
from app.fuel.scope import build_scope  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402


# ─── /api/vehicles ─────────────────────────────────────────────────────────────

FLEET = [{"vehicle_id": 1, "vehicle_name": "V-1", "status": "MOVING"}, {"vehicle_id": 2, "vehicle_name": "V-2", "status": "STOPPED"}]


def _live_patches(stack_live):
    main._cache.clear()
    main._failure_backoff.clear()
    return [patch("app.main.authenticate_access_token", return_value=FAKE_USER),
            patch("app.main.login", return_value="sid"),
            patch("app.main.get_all_vehicles", return_value=[]),
            patch("app.main.get_calculated_data", return_value=[]),
            patch("app.main.get_vehicle_groups", return_value=[]),
            patch("app.main.get_vehicle_live_data", stack_live)]


def test_live_vehicles_last_good_after_wialon_failure():
    live = MagicMock(side_effect=lambda *a, **k: [dict(v) for v in FLEET])
    patches = _live_patches(live)
    for p in patches:
        p.start()
    try:
        client = TestClient(main.app)
        good = client.get("/api/vehicles", headers=AUTH).json()
        assert good["stale"] is False and good["count"] == 2 and good["retrieved_at"], good
        # The live read expires (8 s TTL) and Wialon fails: the last good fleet, marked stale, with its own read time
        for entry in main._cache._data.values():
            if isinstance(entry[1], dict) and "time" in entry[1]:
                entry[1]["time"] -= 60
        live.side_effect = WialonAPIError("Wialon down")
        r = client.get("/api/vehicles", headers=AUTH)
        assert r.status_code == 200, r.text
        stale = r.json()
        assert stale["stale"] is True and stale["retrieved_at"] == good["retrieved_at"], stale
        assert stale["vehicles"] == good["vehicles"], "the last valid list, unchanged (statuses untouched)"
        # During the backoff the poll is answered without asking Wialon
        calls = live.call_count
        assert client.get("/api/vehicles", headers=AUTH).json()["stale"] is True
        assert live.call_count == calls, "no Wialon read during the backoff"
        # Nothing retained and Wialon failing: an error (502), never an empty or invented fleet
        main._cache.clear()
        main._failure_backoff.clear()
        assert client.get("/api/vehicles", headers=AUTH).status_code == 502
    finally:
        for p in reversed(patches):
            p.stop()
        main._cache.clear()
        main._failure_backoff.clear()
    print("[PASS] /api/vehicles: Wialon failing -> last good fleet marked stale with its read time; no Wialon read "
          "during the backoff; nothing retained -> 502, never an invented fleet.")


# ─── Fuel: dated rows read with the columns their totals need ──────────────────

def test_dated_rows_read_with_count_columns_only_for_summary_reporting():
    captured = []

    def pages(url, params, headers, what, deadline=None):
        captured.append((url.rsplit("/", 1)[-1], dict(params)))
        return []

    with patch.object(fuel_db, "_fetch_all_pages", side_effect=pages), \
         patch.object(fuel_db, "summary_table_enabled", return_value=True), \
         patch.object(fuel_db, "get_supabase_rest_url", side_effect=lambda t: f"https://x/rest/v1/{t}"), \
         patch.object(fuel_db, "_read_headers", return_value={}):
        filters = {"date_from": "2026-08-01", "date_to": "2026-08-31", "vehicle_identifier": None, "fuel_type": None}
        fuel_db.fetch_fuel_transactions_for_analytics({**filters, "dated_counts_only": True})
        fuel_db.fetch_fuel_transactions_for_analytics(dict(filters))
    (t1, slim), (s1, summary_slim), (t2, full), (s2, summary_full) = captured
    assert (t1, t2, s1, s2) == ("fuel_transactions", "fuel_transactions", fuel_db.SUMMARY_TABLE, fuel_db.SUMMARY_TABLE)
    assert slim["select"] == fuel_db.DATED_COUNT_COLUMNS == "vehicle_identifier,litres,total_cost,transaction_date"
    assert "card_number" in full["select"], "the statement-row listing keeps every column"
    assert summary_slim == summary_full, "monthly summaries are read exactly as before"
    assert {k: v for k, v in slim.items() if k != "select"} == {k: v for k, v in full.items() if k != "select"}
    # The legacy layout keeps summaries in fuel_transactions: every column, whatever the flag
    captured.clear()
    with patch.object(fuel_db, "_fetch_all_pages", side_effect=pages), \
         patch.object(fuel_db, "summary_table_enabled", return_value=False), \
         patch.object(fuel_db, "get_supabase_rest_url", side_effect=lambda t: f"https://x/rest/v1/{t}"), \
         patch.object(fuel_db, "_read_headers", return_value={}):
        fuel_db.fetch_fuel_transactions_for_analytics({**filters, "dated_counts_only": True})
    assert "card_number" in captured[0][1]["select"]
    print("[PASS] Fuel read: monthly-summary reporting reads dated rows with 4 columns (summary table layout); the "
          "listing, the summaries and the legacy layout read every column.")


def _dated(i, vid, date, litres, cost):
    return {"id": i, "import_id": 3, "vehicle_identifier": vid, "transaction_date": date, "litres": litres,
            "total_cost": cost, "fuel_type": "DIESEL", "unit_price": 3.0, "odometer_km": 1000 + i, "station_name": "S",
            "invoice_number": f"D-{i}", "driver_name": "D", "card_number": "C"}


SUMMARIES = [summary(1, 1, "7-AUH-55943", 90.0, 270.0), summary(2, 1, "7 AUH 55943", 50.0, 150.0),
             summary(4, 1, "17-AUH-55943", 160.0, 480.0, invoice="SUM-4"),
             summary(5, 2, "17-AUH-55943", 160.0, 480.0, invoice="SUM-4"),
             summary(7, 1, "PLATE-1234", 40.0, 120.0), summary(8, 1, "ZZZ-999", 25.0, 75.0)]
DATED = [_dated(10, "7-AUH-55943", "2026-08-03T08:00:00", 33.0, 99.0), _dated(11, "ZZZ-999", "2026-08-04T09:00:00", 12.5, 41.25),
         _dated(12, "7-AUH-55943", "2026-08-05T10:00:00", -5.0, -15.0), _dated(13, "NEW-777", "2026-08-06T11:00:00", 20.0, 60.0)]


def test_slim_dated_rows_give_exactly_the_same_reporting_figures():
    slim_cols = fuel_db.DATED_COUNT_COLUMNS.split(",")
    slim_dated = [{k: r[k] for k in slim_cols} for r in DATED]
    filters = {"date_from": "2026-08-01", "date_to": "2026-08-31", "vehicle_identifier": None, "fuel_type": None}
    for vehicle in (None, "7-AUH-55943"):
        f = dict(filters, vehicle_identifier=vehicle)
        out = []
        for dated in (DATED, slim_dated):
            scope = build_scope(f, lambda _f, rows=SUMMARIES + dated: [dict(r) for r in rows], lambda: "sid",
                                lambda sid: UNITS, summary_only=True)
            out.append((scope.excluded_scope, sorted((r["id"], r.get("_unit_id")) for r in scope.matched)))
        assert out[0] == out[1], (vehicle, out)
    full = out[0][0]
    # dated_not_reported is exact: rows, litres, cost, refunds and identifiers of the vehicle's dated rows
    assert (full["dated_not_reported"]["transaction_count"], full["dated_not_reported"]["litres"],
            full["dated_not_reported"]["refund_count"]) == (2, 28.0, 1), full["dated_not_reported"]
    print("[PASS] Slim dated rows -> identical KPI scope, excluded_scope and dated_not_reported (rows, litres, cost, "
          "refunds, identifiers), with and without a vehicle filter.")


# ─── Opening month ─────────────────────────────────────────────────────────────

def _month_row(vid, month):
    return {"vehicle_identifier": vid, "reporting_month": f"{month}-01"}


# Newest first, as the read returns them. Later months hold only unmatched / ambiguous / future records.
MONTH_ROWS = [_month_row("7-AUH-55943", "2099-01"),      # future: never opened
              _month_row("ZZZ-999", "2026-10"),          # unmatched only
              _month_row("PLATE-1234", "2026-09"),       # ambiguous only (two units named 1234)
              _month_row("17-AUH-55943", "2026-08"),     # matched -> the answer
              _month_row("7-AUH-55943", "2026-07")]


def _legacy_rows():
    """The same summaries in the legacy layout (fuel_transactions rows dated at their month end)."""
    ends = {"2099-01": "2099-01-31", "2026-10": "2026-10-31", "2026-09": "2026-09-30", "2026-08": "2026-08-31",
            "2026-07": "2026-07-31"}
    return [dict(summary(i, 1, r["vehicle_identifier"], 10.0, 30.0, invoice=f"M-{i}"),
                 transaction_date=f"{ends[r['reporting_month'][:7]]}T00:00:00") for i, r in enumerate(MONTH_ROWS, 1)]


def test_latest_month_endpoint_matched_rule_and_no_dated_reads():
    with Api(rows=_legacy_rows(), units=UNITS) as api, \
         patch("app.fuel.routes.summary_table_enabled", return_value=True), \
         patch("app.fuel.routes.fetch_summary_identifier_months", return_value=[dict(r) for r in MONTH_ROWS]) as months:
        body = api.get("/api/fuel/analytics/latest-month")
        assert months.call_count == 1 and api.fetch.call_count == 0, "summary identifiers/months only: no dated rows"
        api.bundle.assert_not_called()
    assert body == {"month": "2026-08", "source": "monthly_summaries"}, body
    # The same answer as the previous rule (matched monthly trend, latest month with rows, not after this month)
    with Api(rows=_legacy_rows(), units=UNITS) as api, patch("app.fuel.routes.summary_table_enabled", return_value=False):
        legacy = api.get("/api/fuel/analytics/latest-month")
        trend = api.get("/api/fuel/analytics/trend?group_by=month")
    rule = max(r["period"][:7] for r in trend["data"] if r["transactions"] > 0 and r["period"][:7] <= "2026-10")
    assert legacy["month"] == rule == "2026-08" and legacy["source"] == "monthly_trend", (legacy, rule)
    print("[PASS] Latest month: 2026-08 (unmatched Oct, ambiguous Sep and future months skipped), from summary "
          "identifiers and months only; equal to the matched monthly-trend rule.")


def test_latest_month_without_summaries_or_with_a_failed_read():
    with Api(rows=[], units=UNITS) as api, patch("app.fuel.routes.summary_table_enabled", return_value=True), \
         patch("app.fuel.routes.fetch_summary_identifier_months", return_value=[]):
        body = api.get("/api/fuel/analytics/latest-month")
        api.login.assert_not_called()
        api.units.assert_not_called()
    assert body["month"] is None
    with Api(rows=[], units=UNITS) as api, patch("app.fuel.routes.summary_table_enabled", return_value=True), \
         patch("app.fuel.routes.fetch_summary_identifier_months", side_effect=fuel_db.FuelDataFetchError("down")):
        r = api.client.get("/api/fuel/analytics/latest-month", headers=AUTH)
    assert r.status_code == 503, r.text
    print("[PASS] Latest month: no summaries -> null without contacting Wialon; a failed read -> 503.")


TESTS = [
    test_live_vehicles_last_good_after_wialon_failure,
    test_dated_rows_read_with_count_columns_only_for_summary_reporting,
    test_slim_dated_rows_give_exactly_the_same_reporting_figures,
    test_latest_month_endpoint_matched_rule_and_no_dated_reads,
    test_latest_month_without_summaries_or_with_a_failed_read,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "PHASE 1 - RELIABILITY AND DATA ACCESS (OFFLINE)") else 0)
