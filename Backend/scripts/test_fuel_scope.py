"""
Offline regression tests for Fuel Phase 3: one KPI scope for every endpoint and AI tool.

  - matched / unmatched / ambiguous / suspected cross-import duplicate partition
  - count, litres and cost reconciliation (separately; refunds signed) on every KPI endpoint
  - exact vehicle filter (no substring matches), unit-id grouping (distance counted once)
  - no transactions -> empty KPIs + warning + zero Wialon calls
  - excluded-transactions endpoint, 409 for ambiguous distance, AI tools on the same scope

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:  python scripts/test_fuel_scope.py
"""
import inspect
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

from app.main import app  # noqa: E402
import app.ai.tools as ai_tools  # noqa: E402
from app.fuel.scope import (  # noqa: E402
    UnitResolver, build_excluded_scope, coarse_vehicle_prefilter, filter_by_vehicle, partition_transactions,
    resolve_effective_dates,
)

AUTH = {"Authorization": "Bearer offline-test-token"}
FAKE_USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "t@ats.ae", "role": "admin"}
Q = "date_from=2026-08-03&date_to=2026-08-05"

UNITS = [
    {"id": 1, "nm": "7AUH55943"},
    {"id": 2, "nm": "17AUH55943"},
    {"id": 3, "nm": "1234"},
    {"id": 4, "nm": "1234"},
]


def tx(i, imp, vid, date, litres, cost, fuel="DIESEL"):
    return {"id": i, "import_id": imp, "vehicle_identifier": vid, "transaction_date": date,
            "litres": litres, "total_cost": cost, "fuel_type": fuel, "invoice_number": f"INV-{i if i != 5 else 4}",
            "odometer_km": None}


ROWS = [
    tx(1, 1, "7-AUH-55943", "2026-08-03T08:00:00", 100.0, 300.0),
    tx(2, 1, "7 AUH 55943", "2026-08-04T09:00:00", 50.0, 150.0),
    tx(3, 1, "7-AUH-55943", "2026-08-05T10:00:00", -10.0, -30.0),     # refund adjustment
    tx(4, 1, "17-AUH-55943", "2026-08-03T11:00:00", 80.0, 240.0),
    tx(5, 2, "17-AUH-55943", "2026-08-03T11:00:00", 80.0, 240.0),     # re-import of row 4 -> suspected duplicate
    dict(tx(6, 1, "17-AUH-55943", "2026-08-03T11:00:00", 80.0, 240.0), invoice_number="INV-4"),  # same import as 4 -> kept
    tx(7, 1, "PLATE-1234", "2026-08-04T12:00:00", 40.0, 120.0),       # ambiguous (two units named 1234)
    tx(8, 1, "ZZZ-999", "2026-08-04T13:00:00", 30.0, 90.0),           # unmatched
    tx(9, 1, "ZZZ-999", "2026-08-05T14:00:00", -5.0, -15.0),          # unmatched refund
]
ROWS[3]["invoice_number"] = "INV-4"
EXPECT = {
    "kpi": (5, 300.0, 900.0),
    "unmatched": (2, 25.0, 75.0),
    "ambiguous": (1, 40.0, 120.0),
    "suspected_duplicates": (1, 80.0, 240.0),
    "all": (9, 445.0, 1335.0),
}

BUNDLE = {
    "by_name": {
        "7AUH55943": {"mileage_km": 500.0, "driving_seconds": 36000, "idle_seconds": 7200, "engine_hours_seconds": 43200},
        "17AUH55943": {"mileage_km": 200.0, "driving_seconds": 18000, "idle_seconds": 3600, "engine_hours_seconds": 21600},
    },
    "duplicate_names": [], "status": "complete", "errors": [], "warnings": [],
}


def db_fetch(rows):
    """Mimic the database filters: date bounds and case-insensitive substring (ilike) vehicle/fuel filters."""
    def fetch(filters, bearer_token=None):
        out = list(rows)
        v = filters.get("vehicle_identifier")
        if v:
            out = [r for r in out if v.lower() in r["vehicle_identifier"].lower()]
        f = filters.get("fuel_type")
        if f:
            out = [r for r in out if f.lower() in (r.get("fuel_type") or "").lower()]
        return [dict(r) for r in out]
    return fetch


class Api:
    def __init__(self, rows=ROWS, units=UNITS):
        self.login = MagicMock(return_value="sid")
        self.units = MagicMock(return_value=units)
        self.bundle = MagicMock(return_value=BUNDLE)
        self.trips = MagicMock(side_effect=lambda ids, f, t, sid, units=None: {
            i: {"status": "complete", "error": None, "trips": [{"start_time": 1785744000, "end_time": 1785747600,
                                                                 "distance_km": {1: 500.0, 2: 200.0}.get(i, 0.0)}]} for i in ids})
        self.detail = MagicMock(return_value={
            "distance_km": 500.0, "trip_count": 3, "driving_duration_hours": 10.0, "idle_duration_hours": 2.0,
            "engine_hours": 12.0, "stopped_hours": 60.0, "idle_events_count": 0, "idle_events": [], "trips": [],
            "has_data": True, "status": "complete", "driving_basis": "engine_report_in_motion",
            "distance_source": "wialon_unit_trips_report", "warnings": []})
        self.fetch = MagicMock(side_effect=db_fetch(rows))
        self.stack = ExitStack()

    def __enter__(self):
        for target, obj in [
            ("app.main.authenticate_access_token", MagicMock(return_value=FAKE_USER)),
            ("app.fuel.routes.fetch_fuel_transactions_for_analytics", self.fetch),
            ("app.fuel.routes.login", self.login),
            ("app.fuel.routes.get_all_vehicles", self.units),
            ("app.fuel.routes.get_fleet_telematics_bundle", self.bundle),
            ("app.fuel.routes.get_units_trips_for_range", self.trips),
            ("app.fuel.routes.get_fleet_trips", MagicMock(return_value=None)),
            ("app.fuel.routes.get_vehicle_telematics_detail", self.detail),
        ]:
            self.stack.enter_context(patch(target, obj))
        self.client = TestClient(app)
        return self

    def __exit__(self, *exc):
        self.stack.close()

    def get(self, path):
        r = self.client.get(path, headers=AUTH)
        assert r.status_code == 200, (path, r.status_code, r.text[:300])
        return r.json()


def _triple(block):
    return (block["transaction_count"], block["litres"], block["cost"])


def assert_reconciles(scope, expect=EXPECT):
    assert _triple(scope["kpi"]) == expect["kpi"], scope["kpi"]
    for k in ("unmatched", "ambiguous", "suspected_duplicates"):
        assert _triple(scope[k]) == expect[k], (k, scope[k])
    assert (scope["all_transaction_count"], scope["all_litres"], scope["all_cost"]) == expect["all"]
    # count, litres and cost each reconcile separately
    for i, key in enumerate(("transaction_count", "litres", "cost")):
        parts = scope["kpi"][key] + scope["unmatched"][key] + scope["ambiguous"][key] + scope["suspected_duplicates"][key]
        total = (scope["all_transaction_count"], scope["all_litres"], scope["all_cost"])[i]
        assert round(parts, 2) == round(total, 2), (key, parts, total)


# ─── Tests ─────────────────────────────────────────────────────────────────────

def test_partition_buckets_and_refunds():
    part = partition_transactions(ROWS, UnitResolver(UNITS))
    assert sorted(r["id"] for r in part.matched) == [1, 2, 3, 4, 6]
    assert [r["id"] for r in part.suspected_duplicates] == [5]
    assert [r["id"] for r in part.ambiguous] == [7] and part.ambiguous[0]["_candidates"] == ["1234"]
    assert sorted(r["id"] for r in part.unmatched) == [8, 9]
    scope = build_excluded_scope(part)
    assert_reconciles(scope)
    assert scope["kpi"]["refund_count"] == 1 and scope["kpi"]["refund_litres"] == -10.0
    assert scope["unmatched"]["refund_cost"] == -15.0
    assert scope["merged_units"] == [{"wialon_unit_id": 1, "wialon_unit_name": "7AUH55943",
                                      "identifiers": ["7 AUH 55943", "7-AUH-55943"]}]
    print("[PASS] Partition: matched / unmatched / ambiguous / cross-import duplicate; refunds signed; reconciles.")


def test_duplicate_rules():
    same_import = [tx(1, 1, "A", "2026-08-03T08:00:00", 10.0, 30.0), tx(2, 1, "A", "2026-08-03T08:00:00", 10.0, 30.0)]
    for r in same_import:
        r["invoice_number"] = "X"
    part = partition_transactions(same_import, UnitResolver([{"id": 9, "nm": "A"}]))
    assert len(part.matched) == 2 and not part.suspected_duplicates
    cross = [dict(same_import[0]), dict(same_import[1], import_id=2)]
    part = partition_transactions(cross, UnitResolver([{"id": 9, "nm": "A"}]))
    assert [r["id"] for r in part.matched] == [1] and [r["id"] for r in part.suspected_duplicates] == [2]
    refund_vs_purchase = [dict(same_import[0]), dict(same_import[1], import_id=2, litres=-10.0, total_cost=-30.0)]
    part = partition_transactions(refund_vs_purchase, UnitResolver([{"id": 9, "nm": "A"}]))
    assert not part.suspected_duplicates, "a refund is never a duplicate of the purchase"
    print("[PASS] Same-import repeats kept; cross-import repeats excluded (lowest id kept); refunds never duplicates.")


def test_exact_vehicle_filter():
    res = UnitResolver(UNITS)
    assert coarse_vehicle_prefilter("7-AUH-55943") == "55943"
    kept, _ = filter_by_vehicle(ROWS, "7-AUH-55943", res)
    assert sorted(r["id"] for r in kept) == [1, 2, 3], "both identifiers of unit 1, never 17-AUH-55943"
    kept, _ = filter_by_vehicle(ROWS, "7 AUH 55943", res)
    assert sorted(r["id"] for r in kept) == [1, 2, 3]
    kept, _ = filter_by_vehicle(ROWS, "7-AUH-5594", res)
    assert kept == [], "a substring never selects 17-AUH-55943 / 7-AUH-55943"
    kept, warn = filter_by_vehicle(ROWS, "PLATE-1234", res)
    assert [r["id"] for r in kept] == [7] and warn
    with Api() as api:
        s = api.get(f"/api/fuel/analytics/summary?{Q}&vehicle_identifier=7-AUH-5594")
        assert s["transaction_count"] == 0 and s["excluded_scope"]["warnings"]
        s = api.get(f"/api/fuel/analytics/summary?{Q}&vehicle_identifier=7-AUH-55943")
        assert (s["transaction_count"], s["total_litres"], s["vehicle_count"]) == (3, 140.0, 1)
        assert api.fetch.call_args_list[-1].args[0]["vehicle_identifier"] == "55943"
    print("[PASS] Exact vehicle filter: same unit or identical identifier only; DB ilike is only a coarse pre-filter.")


def test_every_kpi_endpoint_reconciles():
    with Api() as api:
        bodies = {
            "summary": api.get(f"/api/fuel/analytics/summary?{Q}"),
            "trend": api.get(f"/api/fuel/analytics/trend?{Q}&group_by=month"),
            "vehicles": api.get(f"/api/fuel/analytics/vehicles?{Q}&page_size=100"),
            "fuel_types": api.get(f"/api/fuel/analytics/fuel-types?{Q}"),
            "efficiency": api.get(f"/api/fuel/analytics/efficiency?{Q}"),
            "eff_vehicles": api.get(f"/api/fuel/analytics/efficiency/vehicles?{Q}&page_size=100"),
            "eff_trend": api.get(f"/api/fuel/analytics/efficiency/trend?{Q}&group_by=month"),
            "waste": api.get(f"/api/fuel/analytics/waste?{Q}"),
            "transactions": api.get(f"/api/fuel/transactions?{Q}&page_size=100"),
        }
    for name, b in bodies.items():
        assert_reconciles(b["excluded_scope"]), name
    kpi_litres = {
        "summary": bodies["summary"]["total_litres"],
        "trend": sum(d["litres"] for d in bodies["trend"]["data"]),
        "vehicles": sum(d["litres"] for d in bodies["vehicles"]["data"]),
        "fuel_types": sum(d["litres"] for d in bodies["fuel_types"]["data"]),
        "efficiency": bodies["efficiency"]["total_litres"],
        "eff_vehicles": sum(d["litres"] for d in bodies["eff_vehicles"]["data"]),
        "eff_trend": sum(d["litres"] for d in bodies["eff_trend"]["data"]),
        "transactions": sum(t["litres"] for t in bodies["transactions"]["transactions"]),
    }
    assert set(round(v, 2) for v in kpi_litres.values()) == {300.0}, kpi_litres
    assert bodies["summary"]["transaction_count"] == bodies["efficiency"]["transaction_count"] == bodies["transactions"]["total"] == 5
    assert bodies["summary"]["total_cost"] == bodies["efficiency"]["total_cost"] == 900.0
    eff = bodies["efficiency"]
    assert (eff["unmatched_transactions_count"], eff["unmatched_litres"], eff["unmatched_cost"]) == (3, 65.0, 195.0)
    assert eff["unmatched_vehicle_count"] == 2
    print("[PASS] All 9 KPI endpoints: identical KPI scope; count, litres and cost reconcile with excluded rows.")


def test_unit_grouping_counts_distance_once():
    with Api() as api:
        eff = api.get(f"/api/fuel/analytics/efficiency?{Q}")
        veh = api.get(f"/api/fuel/analytics/efficiency/vehicles?{Q}&page_size=100")
        fv = api.get(f"/api/fuel/analytics/vehicles?{Q}&page_size=100")
    assert eff["total_distance_km"] == 700.0, eff["total_distance_km"]  # not 1,200 (unit 1 once)
    assert eff["vehicle_count"] == 2
    u1 = [v for v in veh["data"] if v["wialon_unit_id"] == 1]
    assert len(u1) == 1 and u1[0]["litres"] == 140.0 and u1[0]["distance_km"] == 500.0
    assert u1[0]["merged_identifiers"] == ["7 AUH 55943", "7-AUH-55943"]
    assert u1[0]["fuel_efficiency_l_per_100km"] == 28.0
    assert len(fv["data"]) == 2 and any(d["merged_identifiers"] for d in fv["data"])
    print("[PASS] Fuel and distance grouped by Wialon unit id: two identifiers, one unit, 500 km counted once.")


def test_no_transactions_zero_wialon_calls():
    with Api(rows=[]) as api:
        for path in ("/api/fuel/analytics/summary", "/api/fuel/analytics/efficiency", "/api/fuel/analytics/efficiency/trend",
                     "/api/fuel/analytics/efficiency/vehicles", "/api/fuel/analytics/waste", "/api/fuel/transactions",
                     "/api/fuel/analytics/trend", "/api/fuel/analytics/vehicles", "/api/fuel/analytics/fuel-types"):
            b = api.get(path)
            assert b["excluded_scope"]["warnings"], path
            assert b["excluded_scope"]["all_transaction_count"] == 0
        eff = api.get("/api/fuel/analytics/efficiency")
        assert eff["total_litres"] == 0.0 and eff["fuel_efficiency_l_per_100km"] is None and eff["data_warnings"]
        assert api.login.call_count == 0 and api.units.call_count == 0
        assert api.bundle.call_count == 0 and api.trips.call_count == 0
    assert resolve_effective_dates([], None, None) is None
    print("[PASS] No transactions and no dates: empty KPIs with a warning; zero Wialon calls (no fixed fallback period).")


def test_vehicle_detail_uses_unit_and_scope():
    with Api() as api:
        d = api.get(f"/api/fuel/analytics/vehicle-detail?vehicle_identifier=7%20AUH%2055943&{Q}")
        assert api.units.call_count == 1, "unit list fetched once"
    s = d["vehicle_summary"]
    assert s["wialon_unit_id"] == 1 and s["litres"] == 140.0 and len(d["transactions"]) == 3
    assert s["driving_duration_hours"] == 10.0 and s["distance_km"] == 500.0
    assert d["value_classification"]["distance_km"] == "recorded"
    print("[PASS] Vehicle detail: summary by unit id, all identifiers of the unit, telemetry never withheld by name.")


def test_excluded_endpoint_and_ambiguous_distance():
    with Api() as api:
        ex = api.get(f"/api/fuel/analytics/excluded?{Q}")
        assert ex["total"] == 3
        reasons = {(g["reason"], g["vehicle_identifier"]) for g in ex["data"]}
        assert reasons == {("unmatched", "ZZZ-999"), ("ambiguous", "PLATE-1234"), ("suspected_duplicate", "17-AUH-55943")}
        unm = next(g for g in ex["data"] if g["reason"] == "unmatched")
        assert (unm["transaction_count"], unm["litres"], unm["cost"]) == (2, 25.0, 75.0)
        assert unm["first_date"] == "2026-08-04" and unm["last_date"] == "2026-08-05"
        only = api.get(f"/api/fuel/analytics/excluded?{Q}&reason=ambiguous&page_size=1")
        assert only["total"] == 1 and only["data"][0]["candidate_unit_names"] == ["1234"]
        bad = api.client.get(f"/api/fuel/analytics/excluded?reason=bogus", headers=AUTH)
        assert bad.status_code == 400
        r = api.client.get(f"/api/fuel/analytics/distance?vehicle_identifier=PLATE-1234&{Q}", headers=AUTH)
        assert r.status_code == 409 and "several Wialon units" in r.json()["detail"]
        waste = api.get(f"/api/fuel/analytics/waste?{Q}")
        types = [a["anomaly_type"] for a in waste["anomalies"]]
        assert types.count("unmatched_vehicle") == 2 and "duplicate_candidate" in types
    print("[PASS] /analytics/excluded groups and reasons; ambiguous distance is 409; /waste reports excluded identifiers.")


def test_ai_tools_share_scope_and_have_no_fixed_dates():
    src = inspect.getsource(ai_tools)
    assert "2026" not in src, "AI tools must not hardcode a period"
    fetch = MagicMock(side_effect=db_fetch(ROWS))
    with patch("app.fuel.db.fetch_fuel_transactions_for_analytics", fetch), \
         patch("app.ai.tools.login", return_value="sid"), \
         patch("app.ai.tools.get_all_vehicles", return_value=UNITS), \
         patch("app.fuel.wialon_distance.get_fleet_telematics_bundle", return_value=BUNDLE) as bundle:
        s = ai_tools.tool_get_fuel_summary()
        e = ai_tools.tool_get_fleet_fuel_efficiency()
        r = ai_tools.tool_get_vehicle_fuel_ranking(sort_by="litres", limit=5, date_from="2026-08-03", date_to="2026-08-05")
        v = ai_tools.tool_get_vehicle_fuel_performance("7-AUH-5594")
        v_ok = ai_tools.tool_get_vehicle_fuel_performance("7 AUH 55943")
    assert s["total_litres"] == 300.0 and s["excluded_scope"]["excluded_transactions"] == 4
    assert e["total_litres"] == 300.0 and e["total_distance_km"] == 700.0 and e["period"] == {"date_from": "2026-08-03", "date_to": "2026-08-05"}
    assert bundle.call_args.args[:2] == ("2026-08-03", "2026-08-05"), "dates come from the data"
    assert [x["litres"] for x in r["vehicles"]] == [160.0, 140.0]
    assert "error" in v, "a substring never matches another vehicle"
    assert v_ok["wialon_unit"] == "7AUH55943" and v_ok["litres"] == 140.0
    import app.fuel.db as db
    from app.fuel.db import FuelDataFetchError
    with patch("app.fuel.db.fetch_fuel_transactions_for_analytics", side_effect=FuelDataFetchError("down")):
        assert "error" in ai_tools.tool_get_fuel_summary()
    print("[PASS] AI tools: same scope/dates/exact filter as the API, excluded rows reported, no fixed 2026 dates.")


def test_review_fixes_scope_and_routes():
    from app.wialon import WialonAPIError
    assert coarse_vehicle_prefilter("Bus 7") is None, "short identifiers are not substring-prefiltered"
    with Api() as api:
        txs = api.get(f"/api/fuel/transactions?{Q}&page_size=100")["transactions"]
        assert [t["transaction_date"][:10] for t in txs] == sorted([t["transaction_date"][:10] for t in txs], reverse=True)
        assert txs[0]["id"] == 3, "newest first, as the previous database listing"
        eff = api.get("/api/fuel/analytics/efficiency")
        assert eff["excluded_scope"]["period"] == {"date_from": "2026-08-03", "date_to": "2026-08-05"}
        found = api.get(f"/api/fuel/analytics/efficiency/vehicles?{Q}&vehicle_identifier=7%20AUH")
        assert [v["wialon_unit_id"] for v in found["data"]] == [1], "search matches merged identifiers"
        bad = api.client.get("/api/fuel/transactions?date_from=2026-08-01),or=(id.gt.0", headers=AUTH)
        assert bad.status_code == 400
        api.bundle.side_effect = WialonAPIError("down")
        tr = api.get(f"/api/fuel/analytics/efficiency/trend?{Q}&group_by=month")
        assert tr["data"][0]["litres"] == 300.0 and tr["data"][0]["distance_km"] is None
        assert tr["telematics_status"] == "partial" and tr["data_warnings"]
    assert "error" in ai_tools.tool_get_fuel_summary(date_from="2026-08-01),or=(x")
    print("[PASS] Review fixes: newest-first list, shared period, merged-id search, date validation, trend degrades on Wialon failure.")


TESTS = [
    test_partition_buckets_and_refunds,
    test_duplicate_rules,
    test_exact_vehicle_filter,
    test_every_kpi_endpoint_reconciles,
    test_unit_grouping_counts_distance_once,
    test_no_transactions_zero_wialon_calls,
    test_vehicle_detail_uses_unit_and_scope,
    test_excluded_endpoint_and_ambiguous_distance,
    test_ai_tools_share_scope_and_have_no_fixed_dates,
    test_review_fixes_scope_and_routes,
]


def reset_fuel_read_caches():
    """Retained fuel reads (transactions, Wialon units) must not carry one test's mocked data into the next."""
    import app.fuel.routes as routes
    from app.fuel.db import clear_fuel_read_caches
    clear_fuel_read_caches()
    routes._UNITS_CACHE.clear()


def run(tests, title):
    print("=" * 60)
    print(title)
    print("=" * 60)
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in tests:
            try:
                reset_fuel_read_caches()
                t()
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
    print("=" * 60)
    print(f"{len(tests) - failures}/{len(tests)} test functions passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PHASE 3 - KPI SCOPE, MATCHING, EXCLUSIONS (OFFLINE)") else 0)
