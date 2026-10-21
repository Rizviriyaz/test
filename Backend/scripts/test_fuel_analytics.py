"""Test suite for Phase 5: Fuel Analytics Backend APIs."""

import json
import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

# Add parent directory
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, backend_dir)

from app.main import app
from app.fuel.analytics import (
    calculate_fuel_summary,
    calculate_fuel_trend,
    calculate_fuel_type_analytics,
    calculate_vehicle_analytics,
)


MOCK_TRANSACTIONS = [
    {
        "transaction_date": "2026-08-03T08:30:00",
        "vehicle_identifier": "DXB-101",
        "fuel_type": "DIESEL",
        "litres": 100.0,
        "total_cost": 380.0,
        "unit_price": 3.80,
    },
    {
        "transaction_date": "2026-08-03T14:15:00",
        "vehicle_identifier": "AUH-202",
        "fuel_type": "SPECIAL",
        "litres": 50.0,
        "total_cost": 174.5,
        "unit_price": 3.49,
    },
    {
        "transaction_date": "2026-08-10T09:00:00",  # Different week (next Monday)
        "vehicle_identifier": "DXB-101",
        "fuel_type": "DIESEL",
        "litres": 80.0,
        "total_cost": 304.0,
        "unit_price": 3.80,
    },
    {
        "transaction_date": "2026-08-15T18:00:00",
        "vehicle_identifier": "SHJ-303",
        "fuel_type": "SUPER",
        "litres": 40.0,
        "total_cost": 144.0,
        "unit_price": 3.60,
    },
]


def run_unit_tests():
    print("==================================================")
    print("RUNNING FUEL ANALYTICS UNIT TESTS")
    print("==================================================")

    # 1. Summary overall totals
    print("\n--- Test 1: Summary calculation ---")
    summary = calculate_fuel_summary(MOCK_TRANSACTIONS)
    assert summary.total_litres == 270.0
    assert summary.total_cost == 1002.50
    assert summary.transaction_count == 4
    assert summary.vehicle_count == 3
    assert summary.average_unit_price == round(1002.50 / 270.0, 2)
    assert len(summary.fuel_type_breakdown) == 3
    # Breakdown sorted by litres desc: DIESEL (180.0), SPECIAL (50.0), SUPER (40.0)
    assert summary.fuel_type_breakdown[0].fuel_type == "DIESEL"
    assert summary.fuel_type_breakdown[0].litres == 180.0
    assert summary.fuel_type_breakdown[0].transactions == 2
    print("[PASS] Summary calculations accurate.")

    # 2. Trend daily grouping
    print("\n--- Test 2: Trend daily grouping ---")
    trend_day = calculate_fuel_trend(MOCK_TRANSACTIONS, "day")
    assert trend_day.group_by == "day"
    assert len(trend_day.data) == 3  # 2026-08-03, 2026-08-10, 2026-08-15
    assert trend_day.data[0].period == "2026-08-03"
    assert trend_day.data[0].litres == 150.0  # 100 + 50
    assert trend_day.data[0].transactions == 2
    print("[PASS] Daily trend accurate.")

    # 3. Trend weekly grouping
    print("\n--- Test 3: Trend weekly grouping ---")
    trend_week = calculate_fuel_trend(MOCK_TRANSACTIONS, "week")
    assert trend_week.group_by == "week"
    # 2026-08-03 is Monday (week 1), 2026-08-10 is Monday (week 2), 2026-08-15 is Saturday in week 2
    assert len(trend_week.data) == 2  # Week starting 2026-08-03, and Week starting 2026-08-10
    assert trend_week.data[0].period == "2026-08-03"
    assert trend_week.data[0].litres == 150.0
    assert trend_week.data[1].period == "2026-08-10"
    assert trend_week.data[1].litres == 120.0  # 80 + 40
    print("[PASS] Weekly trend accurate.")

    # 4. Trend monthly grouping
    print("\n--- Test 4: Trend monthly grouping ---")
    trend_month = calculate_fuel_trend(MOCK_TRANSACTIONS, "month")
    assert trend_month.group_by == "month"
    assert len(trend_month.data) == 1
    assert trend_month.data[0].period == "2026-08"
    assert trend_month.data[0].litres == 270.0
    assert trend_month.data[0].transactions == 4
    print("[PASS] Monthly trend accurate.")

    # 5. Vehicle analytics, sorting & pagination
    print("\n--- Test 5: Vehicle analytics aggregation, sorting, and pagination ---")
    v_res = calculate_vehicle_analytics(MOCK_TRANSACTIONS, page=1, page_size=2, sort_by="litres", sort_order="desc")
    assert v_res.total == 3
    assert len(v_res.data) == 2
    # DXB-101 has 180 L, AUH-202 has 50 L, SHJ-303 has 40 L
    assert v_res.data[0].vehicle_identifier == "DXB-101"
    assert v_res.data[0].litres == 180.0
    assert v_res.data[0].transaction_count == 2
    assert v_res.data[0].average_litres_per_transaction == 90.0
    assert v_res.data[1].vehicle_identifier == "AUH-202"

    # Page 2
    v_res_p2 = calculate_vehicle_analytics(MOCK_TRANSACTIONS, page=2, page_size=2, sort_by="litres", sort_order="desc")
    assert len(v_res_p2.data) == 1
    assert v_res_p2.data[0].vehicle_identifier == "SHJ-303"

    # Sort by total_cost asc
    v_res_asc = calculate_vehicle_analytics(MOCK_TRANSACTIONS, page=1, page_size=10, sort_by="total_cost", sort_order="asc")
    assert v_res_asc.data[0].vehicle_identifier == "SHJ-303"  # 144.0 AED is lowest
    print("[PASS] Vehicle analytics aggregation, sorting, and pagination accurate.")

    # 6. Fuel-type analytics
    print("\n--- Test 6: Fuel-type aggregation ---")
    ft_res = calculate_fuel_type_analytics(MOCK_TRANSACTIONS)
    assert len(ft_res.data) == 3
    assert ft_res.data[0].fuel_type == "DIESEL"
    assert ft_res.data[0].litres == 180.0
    assert ft_res.data[0].cost == 684.0
    print("[PASS] Fuel-type aggregation accurate.")

    # 7. Empty result set handling
    print("\n--- Test 7: Empty result set handling ---")
    empty_summary = calculate_fuel_summary([])
    assert empty_summary.total_litres == 0.0
    assert empty_summary.transaction_count == 0
    assert empty_summary.fuel_type_breakdown == []

    empty_trend = calculate_fuel_trend([], "day")
    assert empty_trend.data == []

    empty_veh = calculate_vehicle_analytics([], page=1, page_size=25)
    assert empty_veh.total == 0
    assert empty_veh.data == []

    empty_ft = calculate_fuel_type_analytics([])
    assert empty_ft.data == []
    print("[PASS] Empty results handled cleanly without crashes.")

    # 8. Invalid parameters rejection
    print("\n--- Test 8: Invalid parameter rejection ---")
    try:
        calculate_fuel_trend(MOCK_TRANSACTIONS, "hourly")
        assert False, "Should have failed on invalid group_by"
    except ValueError as e:
        assert "Unsupported group_by" in str(e)

    try:
        calculate_vehicle_analytics(MOCK_TRANSACTIONS, page=0)
        assert False, "Should have failed on page < 1"
    except ValueError as e:
        assert "Page number" in str(e)

    try:
        calculate_vehicle_analytics(MOCK_TRANSACTIONS, sort_by="invalid_col")
        assert False, "Should have failed on invalid sort_by"
    except ValueError as e:
        assert "Invalid sort_by" in str(e)

    print("[PASS] Invalid parameters rejected properly.")


def run_api_endpoint_tests():
    print("\n==================================================")
    print("RUNNING FASTAPI ENDPOINT INTEGRATION TESTS")
    print("==================================================")
    client = TestClient(app)

    # 9. Auth rejection without token
    print("\n--- Test 9: Unauthenticated request rejection ---")
    res_unauth = client.get("/api/fuel/analytics/summary")
    assert res_unauth.status_code == 401
    print("[PASS] Unauthenticated request rejected with 401.")

    # Mock auth and Wialon units: every mock identifier resolves to exactly one unit, so all rows are in KPI scope
    mock_units = [{"id": 1, "nm": "DXB-101"}, {"id": 2, "nm": "AUH-202"}, {"id": 3, "nm": "SHJ-303"}]
    with patch("app.main.authenticate_access_token", return_value={"id": "test-uuid", "email": "admin@ats.ae", "role": "admin"}), \
         patch("app.fuel.routes.login", return_value="mock-sid"), \
         patch("app.fuel.routes.get_all_vehicles", return_value=mock_units):
        auth_headers = {"Authorization": "Bearer test-valid-jwt"}

        # 10. Summary endpoint with mock DB
        print("\n--- Test 10: GET /api/fuel/analytics/summary ---")
        with patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=MOCK_TRANSACTIONS):
            res_sum = client.get("/api/fuel/analytics/summary", headers=auth_headers)
            assert res_sum.status_code == 200
            data_sum = res_sum.json()
            assert data_sum["total_litres"] == 270.0
            assert data_sum["transaction_count"] == 4
            print("[PASS] GET /api/fuel/analytics/summary returned correct response.")

        # 11. Trend endpoint with mock DB & invalid group_by test
        print("\n--- Test 11: GET /api/fuel/analytics/trend & invalid parameter handling ---")
        with patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=MOCK_TRANSACTIONS):
            # Valid group_by
            res_trend = client.get("/api/fuel/analytics/trend?group_by=day", headers=auth_headers)
            assert res_trend.status_code == 200
            data_trend = res_trend.json()
            assert data_trend["group_by"] == "day"
            assert len(data_trend["data"]) == 3

            # Invalid group_by
            res_bad_trend = client.get("/api/fuel/analytics/trend?group_by=year", headers=auth_headers)
            assert res_bad_trend.status_code == 400
            assert "Unsupported group_by" in res_bad_trend.json()["detail"]
            print("[PASS] GET /api/fuel/analytics/trend works and rejects invalid group_by.")

        # 12. Vehicle analytics endpoint with mock DB & invalid params test
        print("\n--- Test 12: GET /api/fuel/analytics/vehicles & sorting/pagination ---")
        with patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=MOCK_TRANSACTIONS):
            res_veh = client.get("/api/fuel/analytics/vehicles?page=1&page_size=2&sort_by=litres&sort_order=desc", headers=auth_headers)
            assert res_veh.status_code == 200
            data_veh = res_veh.json()
            assert data_veh["total"] == 3
            assert len(data_veh["data"]) == 2
            assert data_veh["data"][0]["vehicle_identifier"] == "DXB-101"

            # Invalid sort_by
            res_bad_sort = client.get("/api/fuel/analytics/vehicles?sort_by=nonexistent", headers=auth_headers)
            assert res_bad_sort.status_code == 400

            # Invalid date format
            res_bad_date = client.get("/api/fuel/analytics/vehicles?date_from=03-08-2026", headers=auth_headers)
            assert res_bad_date.status_code == 400
            print("[PASS] GET /api/fuel/analytics/vehicles works and validates parameters.")

        # 13. Fuel-types endpoint with mock DB
        print("\n--- Test 13: GET /api/fuel/analytics/fuel-types ---")
        with patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=MOCK_TRANSACTIONS):
            res_ft = client.get("/api/fuel/analytics/fuel-types", headers=auth_headers)
            assert res_ft.status_code == 200
            data_ft = res_ft.json()
            assert len(data_ft["data"]) == 3
            assert data_ft["data"][0]["fuel_type"] == "DIESEL"
            print("[PASS] GET /api/fuel/analytics/fuel-types returned correct response.")


def run_live_production_data_test():
    print("\n==================================================")
    print("VERIFYING ANALYTICS AGAINST REAL PRODUCTION SUPABASE DATA")
    print("==================================================")
    client = TestClient(app)

    # Use service role key to authenticate via real bearer token or mock auth that forwards to backend
    with patch("app.main.authenticate_access_token", return_value={"id": "admin-uuid", "email": "admin@ats.ae", "role": "admin"}):
        auth_headers = {"Authorization": "Bearer valid-admin-token"}

        # 1. Summary on real data
        res = client.get("/api/fuel/analytics/summary", headers=auth_headers)
        assert res.status_code == 200, f"Failed: {res.text}"
        data = res.json()
        print(f"Production Summary: {data['transaction_count']} txns, {data['total_litres']:,.2f} L, {data['total_cost']:,.2f} AED, {data['vehicle_count']} vehicles, avg_price={data['average_unit_price']}")

        assert data["transaction_count"] == 2335, f"Expected 2335, got {data['transaction_count']}"
        assert abs(data["total_litres"] - 110553.52) < 0.01, f"Expected 110553.52, got {data['total_litres']}"
        assert abs(data["total_cost"] - 404532.95) < 0.01, f"Expected 404532.95, got {data['total_cost']}"
        assert data["vehicle_count"] == 238, f"Expected 238, got {data['vehicle_count']}"
        assert data["average_unit_price"] == 3.66, f"Expected 3.66, got {data['average_unit_price']}"
        assert len(data["fuel_type_breakdown"]) == 3
        print("[PASS] Production summary matches ground truth exactly: 2335 txns, 110,553.52 L, 404,532.95 AED, 238 vehicles.")

        # 2. Date filtered summary (e.g. date_from=2026-08-03, date_to=2026-08-31)
        res_date = client.get("/api/fuel/analytics/summary?date_from=2026-08-03&date_to=2026-08-31", headers=auth_headers)
        assert res_date.status_code == 200
        data_date = res_date.json()
        assert data_date["transaction_count"] == 2335
        print("[PASS] Date filtering (including timestamp bounds on date_to) returned all 2,335 transactions.")

        # 3. Vehicle filtering summary
        res_veh_filt = client.get("/api/fuel/analytics/summary?vehicle_identifier=7-AUH-55943", headers=auth_headers)
        assert res_veh_filt.status_code == 200
        data_vf = res_veh_filt.json()
        print(f"Vehicle '7-AUH-55943' filter: {data_vf['transaction_count']} txns, {data_vf['total_litres']} L")
        assert data_vf["transaction_count"] > 0
        assert data_vf["vehicle_count"] == 1
        print("[PASS] Vehicle filtering returned correct vehicle metrics.")

        # 4. Fuel-type filtering summary
        res_ft_filt = client.get("/api/fuel/analytics/summary?fuel_type=SUPER", headers=auth_headers)
        assert res_ft_filt.status_code == 200
        data_ft = res_ft_filt.json()
        print(f"Fuel type 'SUPER' filter: {data_ft['transaction_count']} txns, {data_ft['total_litres']} L")
        assert data_ft["transaction_count"] == 73
        assert data_ft["total_litres"] == 3393.41
        print("[PASS] Fuel type filtering returned correct metrics.")

        # 5. Trend daily
        res_trend = client.get("/api/fuel/analytics/trend?group_by=day", headers=auth_headers)
        assert res_trend.status_code == 200
        trend_data = res_trend.json()
        print(f"Daily trend periods count: {len(trend_data['data'])}")
        assert len(trend_data["data"]) == 29  # August 3 to August 31
        total_trend_l = sum(p["litres"] for p in trend_data["data"])
        assert abs(total_trend_l - 110553.52) < 0.05
        print("[PASS] Daily trend aggregated all 29 days with correct total volume.")

        # 6. Trend weekly
        res_week = client.get("/api/fuel/analytics/trend?group_by=week", headers=auth_headers)
        assert res_week.status_code == 200
        week_data = res_week.json()
        print(f"Weekly trend periods count: {len(week_data['data'])}")
        assert len(week_data["data"]) == 5  # 5 calendar weeks in August 2026
        print("[PASS] Weekly trend aggregated 5 calendar weeks.")

        # 7. Trend monthly
        res_month = client.get("/api/fuel/analytics/trend?group_by=month", headers=auth_headers)
        assert res_month.status_code == 200
        month_data = res_month.json()
        assert len(month_data["data"]) == 1
        assert month_data["data"][0]["period"] == "2026-08"
        assert month_data["data"][0]["transactions"] == 2335
        print("[PASS] Monthly trend aggregated August 2026 accurately.")

        # 8. Vehicle analytics rankings
        res_vehicles = client.get("/api/fuel/analytics/vehicles?page=1&page_size=25&sort_by=litres&sort_order=desc", headers=auth_headers)
        assert res_vehicles.status_code == 200
        veh_data = res_vehicles.json()
        assert veh_data["total"] == 238
        assert len(veh_data["data"]) == 25
        top_veh = veh_data["data"][0]
        print(f"Top vehicle by litres: {top_veh['vehicle_identifier']} ({top_veh['litres']:,.2f} L, {top_veh['transaction_count']} txns)")
        assert top_veh["litres"] >= veh_data["data"][1]["litres"]
        print("[PASS] Vehicle analytics pagination & ranking verified (Total: 238 vehicles).")

        # 9. Fuel-type analytics
        res_types = client.get("/api/fuel/analytics/fuel-types", headers=auth_headers)
        assert res_types.status_code == 200
        types_data = res_types.json()
        assert len(types_data["data"]) == 3
        print(f"Fuel types: {[f['fuel_type'] for f in types_data['data']]}")
        print("[PASS] Fuel types analytics verified.")


if __name__ == "__main__":
    # Offline by default. run_live_production_data_test reads real Supabase data and pins a past snapshot
    # of production totals, so it only runs when explicitly requested with --live.
    import traceback
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from offline_guard import LiveCallBlocked, OfflineGuard

    sections = [run_unit_tests, run_api_endpoint_tests]
    if "--live" in sys.argv:
        sections.append(run_live_production_data_test)
    else:
        print("[SKIP] run_live_production_data_test (live Supabase); pass --live to run it.")

    failures = 0
    for section in sections:
        guard = None if section is run_live_production_data_test else OfflineGuard().start()
        try:
            section()
            if guard:
                guard.assert_no_live_calls()
        except (Exception, LiveCallBlocked):  # one failing section must not hide the others' results
            failures += 1
            print(f"[FAIL] {section.__name__}")
            traceback.print_exc()
        finally:
            if guard:
                guard.stop()

    print("\n==================================================")
    if failures:
        print(f"{failures} of {len(sections)} analytics test section(s) FAILED")
        sys.exit(1)
    print(f"All {len(sections)} analytics test section(s) passed.")
    print("==================================================")
