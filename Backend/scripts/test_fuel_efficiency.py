"""
Comprehensive Test Suite for Phase 8: Fuel Efficiency Analytics Layer.
Validates:
1. Total litres, total cost, total distance.
2. L/100 km and cost/km formulas.
3. Vehicle efficiency records.
4. Fleet efficiency summary.
5. MANDATORY WEIGHTED FLEET CALCULATION (Weighted aggregation vs simple average).
6. Unmatched vehicles (not_tracked).
7. Matched vehicles without trips (no_distance_data).
8. Zero distance / zero fuel (no division by zero).
9. Date, vehicle, and fuel-type filtering.
10. Daily, weekly, and monthly trends.
11. Wialon request deduplication and caching.
12. Ambiguous vehicle match handling.
13. Pagination and multi-field sorting.
14. Live FastAPI TestClient endpoint integration.
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from app.main import app
from app.fuel.efficiency import (
    calculate_fleet_efficiency,
    calculate_vehicle_efficiency_records,
    paginate_and_sort_vehicle_efficiency,
    calculate_efficiency_trend,
    VALID_EFFICIENCY_SORT_FIELDS,
    VALID_EFFICIENCY_STATUSES,
)
from app.fuel.wialon_distance import (
    match_fuel_vehicle_to_wialon,
    get_fleet_distances_map,
    clear_distance_cache,
)


class TestFuelEfficiency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.auth_patcher = patch(
            "app.main.authenticate_access_token",
            return_value={"id": "admin-uuid", "email": "admin@ats.ae", "role": "admin"},
        )
        cls.auth_patcher.start()

    @classmethod
    def tearDownClass(cls):
        cls.auth_patcher.stop()

    def setUp(self):
        clear_distance_cache()

    # -------------------------------------------------------------
    # 1-8. Core Formulas & Weighted Fleet Calculation
    # -------------------------------------------------------------

    def test_01_mandatory_weighted_fleet_efficiency(self):
        """
        MANDATORY TEST:
        Vehicle A: 100 L / 100 km   = 100 L/100km
        Vehicle B: 100 L / 1,000 km = 10 L/100km
        Fleet Result MUST BE:
            (200 L / 1,100 km) * 100 = 18.18 L/100km
        NEVER simple average:
            (100 + 10) / 2 = 55 L/100km (INCORRECT)
        """
        mock_txs = [
            {"vehicle_identifier": "VEH-A", "litres": 100.0, "total_cost": 360.0},
            {"vehicle_identifier": "VEH-B", "litres": 100.0, "total_cost": 360.0},
        ]
        mock_dist_map = {"VEH-A": 100.0, "VEH-B": 1000.0}
        mock_units = [
            {"id": 1, "nm": "VEH-A"},
            {"id": 2, "nm": "VEH-B"},
        ]

        summary = calculate_fleet_efficiency(mock_txs, mock_dist_map, mock_units)

        self.assertEqual(summary.total_litres, 200.0)
        self.assertEqual(summary.total_cost, 720.0)
        self.assertEqual(summary.total_distance_km, 1100.0)
        # 200 / 1100 * 100 = 18.1818... -> 18.18
        self.assertEqual(summary.fuel_efficiency_l_per_100km, 18.18)
        self.assertNotEqual(summary.fuel_efficiency_l_per_100km, 55.0)

        # Cost/km: 720 / 1100 = 0.6545... -> 0.65
        self.assertEqual(summary.fuel_cost_per_km, 0.65)

    def test_02_individual_vehicle_efficiency_calculation(self):
        """Verify per-vehicle L/100km and cost/km calculation."""
        mock_txs = [
            {"vehicle_identifier": "CC-DXB-17428", "litres": 50.0, "total_cost": 180.0},
            {"vehicle_identifier": "CC-DXB-17428", "litres": 50.0, "total_cost": 180.0},
        ]
        mock_dist_map = {"17428": 500.0}
        mock_units = [{"id": 600691973, "nm": "17428"}]

        items = calculate_vehicle_efficiency_records(mock_txs, mock_dist_map, mock_units)
        self.assertEqual(len(items), 1)
        item = items[0]

        self.assertEqual(item.vehicle_identifier, "CC-DXB-17428")
        self.assertEqual(item.litres, 100.0)
        self.assertEqual(item.total_cost, 360.0)
        self.assertEqual(item.distance_km, 500.0)
        self.assertEqual(item.status, "matched")
        # (100 / 500) * 100 = 20.00 L/100km
        self.assertEqual(item.fuel_efficiency_l_per_100km, 20.0)
        # 360 / 500 = 0.72 AED/km
        self.assertEqual(item.fuel_cost_per_km, 0.72)

    # -------------------------------------------------------------
    # 9-12. Status Transitions & Edge Cases
    # -------------------------------------------------------------

    def test_03_unmatched_vehicle_handling(self):
        """Unmatched vehicle returns status 'not_tracked' with distance and efficiency as None."""
        mock_txs = [{"vehicle_identifier": "UNTRACKED-99", "litres": 80.0, "total_cost": 290.0}]
        mock_dist_map = {}
        mock_units = [{"id": 1, "nm": "OTHER-1"}]

        items = calculate_vehicle_efficiency_records(mock_txs, mock_dist_map, mock_units)
        self.assertEqual(len(items), 1)
        item = items[0]

        self.assertEqual(item.status, "not_tracked")
        self.assertIsNone(item.distance_km)
        self.assertIsNone(item.fuel_efficiency_l_per_100km)
        self.assertIsNone(item.fuel_cost_per_km)

    def test_04_matched_vehicle_without_trips(self):
        """Matched vehicle with 0 km returns status 'no_distance_data' with distance=0 and efficiency=None."""
        mock_txs = [{"vehicle_identifier": "T-DXB-15770", "litres": 60.0, "total_cost": 220.0}]
        mock_dist_map = {"15770": 0.0}
        mock_units = [{"id": 600446393, "nm": "15770"}]

        items = calculate_vehicle_efficiency_records(mock_txs, mock_dist_map, mock_units)
        self.assertEqual(len(items), 1)
        item = items[0]

        self.assertEqual(item.status, "no_distance_data")
        self.assertEqual(item.distance_km, 0.0)
        self.assertIsNone(item.fuel_efficiency_l_per_100km)
        self.assertIsNone(item.fuel_cost_per_km)

    def test_05_ambiguous_vehicle_match_handling(self):
        """If multiple Wialon units match the same identifier, flag as ambiguous without guessing."""
        mock_txs = [{"vehicle_identifier": "PLATE-1234", "litres": 50.0, "total_cost": 180.0}]
        # Two distinct units with different IDs matching the token '1234'
        mock_units = [
            {"id": 101, "nm": "1234"},
            {"id": 102, "nm": "1234"},
        ]

        match_res = match_fuel_vehicle_to_wialon("PLATE-1234", mock_units)
        self.assertTrue(match_res.get("ambiguous", False))

        items = calculate_vehicle_efficiency_records(mock_txs, {}, mock_units)
        self.assertEqual(items[0].status, "ambiguous")
        self.assertIsNone(items[0].fuel_efficiency_l_per_100km)

    def test_06_zero_fuel_and_zero_distance_safety(self):
        """Ensure no division by zero occurs with empty or zero datasets."""
        summary = calculate_fleet_efficiency([], {}, [])
        self.assertEqual(summary.total_litres, 0.0)
        self.assertEqual(summary.total_cost, 0.0)
        self.assertIsNone(summary.total_distance_km)
        self.assertIsNone(summary.fuel_efficiency_l_per_100km)
        self.assertIsNone(summary.fuel_cost_per_km)

    # -------------------------------------------------------------
    # 13-15. Pagination & Multi-field Sorting
    # -------------------------------------------------------------

    def test_07_pagination_and_sorting(self):
        """Verify sorting by litres, distance, efficiency, and pagination bounds."""
        mock_items = [
            calculate_vehicle_efficiency_records(
                [{"vehicle_identifier": f"V-{i}", "litres": float(i * 10), "total_cost": float(i * 36)}],
                {f"V-{i}": float(i * 100)},
                [{"id": i, "nm": f"V-{i}"}],
            )[0]
            for i in range(1, 11)
        ]

        # Sort by litres desc
        resp_desc = paginate_and_sort_vehicle_efficiency(mock_items, page=1, page_size=5, sort_by="litres", sort_order="desc")
        self.assertEqual(resp_desc.total, 10)
        self.assertEqual(len(resp_desc.data), 5)
        self.assertEqual(resp_desc.data[0].litres, 100.0)
        self.assertEqual(resp_desc.data[-1].litres, 60.0)

        # Page 2
        resp_p2 = paginate_and_sort_vehicle_efficiency(mock_items, page=2, page_size=5, sort_by="litres", sort_order="desc")
        self.assertEqual(len(resp_p2.data), 5)
        self.assertEqual(resp_p2.data[0].litres, 50.0)
        self.assertEqual(resp_p2.data[-1].litres, 10.0)

    # -------------------------------------------------------------
    # 16-18. Trends (Daily, Weekly, Monthly)
    # -------------------------------------------------------------

    def test_08_efficiency_trend_daily_weekly_monthly(self):
        """Verify daily, weekly, and monthly trend aggregation."""
        mock_txs = [
            {"transaction_date": "2026-08-03T10:00:00", "vehicle_identifier": "V-1", "litres": 50.0, "total_cost": 180.0},
            {"transaction_date": "2026-08-04T12:00:00", "vehicle_identifier": "V-1", "litres": 50.0, "total_cost": 180.0},
            {"transaction_date": "2026-08-10T09:00:00", "vehicle_identifier": "V-1", "litres": 100.0, "total_cost": 360.0},
        ]
        mock_dist_map = {"V-1": 1000.0}
        mock_units = [{"id": 1, "nm": "V-1"}]
        # Actual Wialon trips per day (12:00 UAE starts): 300 + 100 + 600 = 1000 km
        noon = lambda d: int(datetime(2026, 8, d, 8, 0, tzinfo=timezone.utc).timestamp())
        unit_trips = {1: {"status": "complete", "error": None, "trips": [
            {"start_time": noon(3), "end_time": noon(3) + 3600, "distance_km": 300.0},
            {"start_time": noon(4), "end_time": noon(4) + 3600, "distance_km": 100.0},
            {"start_time": noon(10), "end_time": noon(10) + 3600, "distance_km": 600.0},
        ]}}

        # Day trend: real per-day distance, never apportioned by fuel share
        day_trend = calculate_efficiency_trend(mock_txs, mock_dist_map, mock_units, group_by="day", unit_trips=unit_trips)
        self.assertEqual(len(day_trend.data), 3)
        self.assertEqual(day_trend.data[0].period, "2026-08-03")
        self.assertEqual([d.distance_km for d in day_trend.data], [300.0, 100.0, 600.0])
        self.assertEqual([d.fuel_efficiency_l_per_100km for d in day_trend.data], [16.67, 50.0, 16.67])

        # Month trend
        month_trend = calculate_efficiency_trend(mock_txs, mock_dist_map, mock_units, group_by="month", unit_trips=unit_trips)
        self.assertEqual(len(month_trend.data), 1)
        self.assertEqual(month_trend.data[0].period, "2026-08")
        self.assertEqual(month_trend.data[0].litres, 200.0)
        self.assertEqual(month_trend.data[0].distance_km, 1000.0)
        self.assertEqual(month_trend.data[0].fuel_efficiency_l_per_100km, 20.0)

    # -------------------------------------------------------------
    # 19-20. Wialon Request Optimization & Deduplication
    # -------------------------------------------------------------

    def test_09_wialon_caching_and_deduplication(self):
        """Verify repeated calls for same date range reuse cache without re-querying Wialon."""
        # Engine report and login are patched too so this test never reaches live Wialon
        with patch("app.fuel.wialon_distance.execute_wialon_group_trips_report") as mock_group, \
             patch("app.fuel.wialon_distance.execute_wialon_group_engine_idle_report",
                   return_value={"vehicles": [{"vehicle_name": "17428", "in_motion_seconds": 3600,
                                               "total_idle_seconds": 600, "engine_hours_seconds": 4200,
                                               "total_time_seconds": 86400, "incomplete_fields": []}]}), \
             patch("app.fuel.wialon_distance.login", return_value="mock-sid"):
            mock_group.return_value = [{"vehicle_name": "17428", "mileage_km": 500.0}]

            # Call 1
            m1 = get_fleet_distances_map("2026-08-03", "2026-08-31", sid="mock-sid")
            self.assertEqual(mock_group.call_count, 1)

            # Call 2 with identical bounds uses cache
            m2 = get_fleet_distances_map("2026-08-03", "2026-08-31", sid="mock-sid")
            self.assertEqual(mock_group.call_count, 1)
            self.assertEqual(m1, m2)

    # -------------------------------------------------------------
    # 21-25. Live FastAPI TestClient Route Tests
    # -------------------------------------------------------------

    # Live data changes as fuel statements are imported, so these tests check the response contract and
    # internal reconciliation (excluded_scope from the same response), never historical totals.
    LIVE_RANGE = {"date_from": "2026-08-03", "date_to": "2026-08-31"}
    BUCKET_KEYS = ("transaction_count", "litres", "cost", "refund_count", "refund_litres", "refund_cost", "identifier_count")

    def _assert_number(self, value, allow_none=False, non_negative=False, msg=""):
        if allow_none and value is None:
            return
        self.assertIsInstance(value, (int, float), msg)
        self.assertNotIsInstance(value, bool, msg)
        if non_negative:
            self.assertGreaterEqual(value, 0, msg)

    def _assert_count(self, value, msg=""):
        self.assertIsInstance(value, int, msg)
        self.assertNotIsInstance(value, bool, msg)
        self.assertGreaterEqual(value, 0, msg)

    def _assert_excluded_scope_reconciles(self, ex):
        """kpi + unmatched + ambiguous + suspected_duplicates == all_*; the three exclusions == excluded_*."""
        for bucket in ("kpi", "unmatched", "ambiguous", "suspected_duplicates"):
            self.assertIn(bucket, ex)
            for key in self.BUCKET_KEYS:
                self.assertIn(key, ex[bucket], f"{bucket}.{key}")
                self._assert_number(ex[bucket][key], msg=f"{bucket}.{key}")
            self._assert_count(ex[bucket]["transaction_count"], bucket)
            self._assert_count(ex[bucket]["identifier_count"], bucket)
            self.assertLessEqual(ex[bucket]["identifier_count"], ex[bucket]["transaction_count"], bucket)
        self._assert_count(ex["kpi"]["unit_count"], "kpi.unit_count")
        self.assertLessEqual(ex["kpi"]["unit_count"], ex["kpi"]["identifier_count"])

        excluded = ("unmatched", "ambiguous", "suspected_duplicates")
        for field, key in (("transaction_count", "transaction_count"), ("litres", "litres"), ("cost", "cost")):
            excl_sum = sum(ex[b][key] for b in excluded)
            self.assertAlmostEqual(ex[f"excluded_{field}"], excl_sum, delta=0.011, msg=f"excluded_{field}")
            self.assertAlmostEqual(ex[f"all_{field}"], ex["kpi"][key] + excl_sum, delta=0.011, msg=f"all_{field}")
        self.assertIsInstance(ex["merged_units"], list)
        self.assertIsInstance(ex["warnings"], list)

    def test_10_api_fleet_efficiency_endpoint(self):
        """GET /api/fuel/analytics/efficiency: summary schema, KPI/excluded reconciliation and weighted ratio (live data)."""
        resp = self.client.get(
            "/api/fuel/analytics/efficiency",
            params=self.LIVE_RANGE,
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(resp.status_code, 200, f"Error: {resp.text}")
        data = resp.json()

        # Schema and value types
        for key in ("transaction_count", "vehicle_count", "matched_vehicle_count", "unmatched_vehicle_count",
                    "vehicles_with_distance", "vehicles_without_distance", "unmatched_transactions_count",
                    "vehicles_telemetry_unknown", "vehicles_engine_hours_only", "vehicles_fuel_type_mixed"):
            self._assert_count(data[key], key)
        for key in ("total_litres", "total_cost", "tracked_litres", "tracked_cost", "unmatched_litres",
                    "unmatched_cost", "telemetry_litres", "engine_only_litres", "idle_rate_lph",
                    "min_efficiency_distance_km"):
            self._assert_number(data[key], msg=key)
        for key in ("total_distance_km", "total_driving_hours", "total_idle_hours", "total_engine_hours"):
            self._assert_number(data[key], allow_none=True, non_negative=True, msg=key)
        self._assert_number(data["fuel_efficiency_l_per_100km"], allow_none=True, non_negative=True)
        self._assert_number(data["fuel_cost_per_km"], allow_none=True, non_negative=True)
        self.assertIn(data["telematics_status"], ("complete", "partial", "unavailable"))
        self.assertIn(data["efficiency_suppressed_reason"], (None, "insufficient_distance", "non_positive_net_fuel"))
        self.assertEqual(data["efficiency_basis"], "purchase_based")
        self.assertIsInstance(data["data_warnings"], list)
        self.assertEqual(data["excluded_scope"]["period"], self.LIVE_RANGE)

        # Matched and excluded totals reconcile with the full fetched dataset
        ex = data["excluded_scope"]
        self._assert_excluded_scope_reconciles(ex)

        # Summary KPIs are exactly the KPI-scope bucket
        kpi = ex["kpi"]
        self.assertEqual(data["transaction_count"], kpi["transaction_count"])
        self.assertEqual(data["vehicle_count"], kpi["unit_count"])
        self.assertEqual(data["matched_vehicle_count"], kpi["unit_count"])
        self.assertAlmostEqual(data["total_litres"], kpi["litres"], places=2)
        self.assertAlmostEqual(data["total_cost"], kpi["cost"], places=2)

        # Legacy unmatched_* fields are unmatched + ambiguous identifiers
        um, amb = ex["unmatched"], ex["ambiguous"]
        self.assertEqual(data["unmatched_vehicle_count"], um["identifier_count"] + amb["identifier_count"])
        self.assertEqual(data["unmatched_transactions_count"], um["transaction_count"] + amb["transaction_count"])
        self.assertAlmostEqual(data["unmatched_litres"], um["litres"] + amb["litres"], delta=0.011)
        self.assertAlmostEqual(data["unmatched_cost"], um["cost"] + amb["cost"], delta=0.011)

        # Per-unit distance buckets never exceed the KPI fleet (non-positive net-fuel units are not exposed)
        self.assertLessEqual(
            data["vehicles_with_distance"] + data["vehicles_without_distance"]
            + data["vehicles_telemetry_unknown"] + data["vehicles_fuel_type_mixed"],
            data["vehicle_count"],
        )
        self.assertLessEqual(data["vehicles_engine_hours_only"], data["vehicle_count"])
        if data["vehicles_with_distance"] == 0:
            self.assertEqual(data["tracked_litres"], 0.0)
            self.assertIsNone(data["total_distance_km"])

        # Weighted ratio: tracked litres / total distance, never an average of vehicle ratios
        if data["fuel_efficiency_l_per_100km"] is not None:
            self.assertIsNone(data["efficiency_suppressed_reason"])
            dist = data["total_distance_km"]
            self.assertIsNotNone(dist)
            self.assertGreaterEqual(dist, data["min_efficiency_distance_km"])
            self.assertAlmostEqual(data["fuel_efficiency_l_per_100km"], data["tracked_litres"] / dist * 100, delta=0.02)
            self.assertAlmostEqual(data["fuel_cost_per_km"], data["tracked_cost"] / dist, delta=0.02)
        else:
            self.assertIsNone(data["fuel_cost_per_km"])

    def _get_vehicles(self, **params):
        resp = self.client.get(
            "/api/fuel/analytics/efficiency/vehicles",
            params={**self.LIVE_RANGE, **params},
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(resp.status_code, 200, f"Error: {resp.text}")
        return resp.json()

    def test_11_api_vehicles_efficiency_endpoint(self):
        """GET /api/fuel/analytics/efficiency/vehicles: schema, pagination, sorting and KPI reconciliation (live data)."""
        page1 = self._get_vehicles(page=1, page_size=10, sort_by="litres", sort_order="desc")

        self.assertEqual(page1["page"], 1)
        self.assertEqual(page1["page_size"], 10)
        self._assert_count(page1["total"], "total")
        self.assertEqual(len(page1["data"]), min(10, page1["total"]))
        self.assertIn(page1["telematics_status"], ("complete", "partial", "unavailable"))
        self._assert_excluded_scope_reconciles(page1["excluded_scope"])
        kpi = page1["excluded_scope"]["kpi"]

        # The default list is one record per Wialon unit in the KPI scope
        self.assertEqual(page1["total"], kpi["unit_count"])

        # Fetch every page so list totals can be reconciled with the KPI bucket
        full_size = 500
        items, page = [], 1
        while True:
            body = self._get_vehicles(page=page, page_size=full_size, sort_by="litres", sort_order="desc")
            self.assertEqual(body["total"], page1["total"])
            self.assertEqual(body["page"], page)
            items.extend(body["data"])
            if page * full_size >= body["total"]:
                break
            page += 1
        self.assertEqual(len(items), page1["total"])

        for item in items:
            vid = item["vehicle_identifier"]
            self.assertIsInstance(vid, str)
            self.assertTrue(vid.strip(), "vehicle_identifier must not be blank")
            self.assertIn(item["status"], ("matched", "no_distance_data"), vid)
            self.assertIsInstance(item["wialon_unit_id"], int, vid)
            self._assert_number(item["litres"], msg=vid)
            self._assert_number(item["total_cost"], msg=vid)
            self._assert_count(item["transaction_count"], vid)
            self.assertGreater(item["transaction_count"], 0, vid)
            self._assert_number(item["distance_km"], allow_none=True, non_negative=True, msg=vid)
            self._assert_number(item["fuel_efficiency_l_per_100km"], allow_none=True, non_negative=True, msg=vid)
            self._assert_number(item["fuel_cost_per_km"], allow_none=True, non_negative=True, msg=vid)
            self.assertIn(item["estimation_status"], ("estimated", "provisional", "unavailable"), vid)
            if item["fuel_efficiency_l_per_100km"] is not None:
                self.assertEqual(item["status"], "matched", vid)
                self.assertGreater(item["distance_km"], 0, vid)
                self.assertAlmostEqual(item["fuel_efficiency_l_per_100km"],
                                       item["litres"] / item["distance_km"] * 100, delta=0.02, msg=vid)

        # One record per unit: no unit appears twice
        unit_ids = [i["wialon_unit_id"] for i in items]
        self.assertEqual(len(unit_ids), len(set(unit_ids)))

        # List totals agree with the KPI bucket from the same response
        self.assertEqual(sum(i["transaction_count"] for i in items), kpi["transaction_count"])
        tolerance = 0.005 * len(items) + 0.01  # per-item 2 dp rounding
        self.assertAlmostEqual(sum(i["litres"] for i in items), kpi["litres"], delta=tolerance)
        self.assertAlmostEqual(sum(i["total_cost"] for i in items), kpi["cost"], delta=tolerance)

        # Sorted by litres desc across all pages
        litres = [i["litres"] for i in items]
        self.assertEqual(litres, sorted(litres, reverse=True))
        # Page 1 (size 10) is the head of the same ordering
        self.assertEqual([i["litres"] for i in page1["data"]], litres[:10])

        # Page 2 continues where page 1 ends; a page past the end is empty
        if page1["total"] > 10:
            page2 = self._get_vehicles(page=2, page_size=10, sort_by="litres", sort_order="desc")
            self.assertEqual([i["litres"] for i in page2["data"]], litres[10:20])
        beyond = self._get_vehicles(page=(page1["total"] // 10) + 2, page_size=10)
        self.assertEqual(beyond["data"], [])
        self.assertEqual(beyond["total"], page1["total"])

        # Ascending order is the reverse ordering of the same values
        asc = self._get_vehicles(page=1, page_size=10, sort_by="litres", sort_order="asc")
        self.assertEqual([i["litres"] for i in asc["data"]], sorted(litres)[:10])

    def test_12_api_trend_efficiency_endpoint(self):
        """GET /api/fuel/analytics/efficiency/trend returns day and month periods."""
        resp = self.client.get(
            "/api/fuel/analytics/efficiency/trend",
            params={
                "group_by": "day",
                "date_from": "2026-08-03",
                "date_to": "2026-08-31",
            },
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(resp.status_code, 200, f"Error: {resp.text}")
        data = resp.json()

        self.assertEqual(data["group_by"], "day")
        self.assertEqual(len(data["data"]), 29)
        self.assertEqual(data["data"][0]["period"], "2026-08-03")

    def test_13_api_validation_errors(self):
        """GET /api/fuel/analytics/efficiency endpoints reject invalid parameters."""
        # 1. Invalid date_from > date_to
        r1 = self.client.get(
            "/api/fuel/analytics/efficiency",
            params={"date_from": "2026-08-31", "date_to": "2026-08-03"},
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(r1.status_code, 400)

        # 2. Invalid group_by in trend
        r2 = self.client.get(
            "/api/fuel/analytics/efficiency/trend",
            params={"group_by": "hourly"},
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(r2.status_code, 400)

        # 3. Invalid sort_by in vehicles
        r3 = self.client.get(
            "/api/fuel/analytics/efficiency/vehicles",
            params={"sort_by": "invalid_field"},
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(r3.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
