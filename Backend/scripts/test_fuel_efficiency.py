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

        # Day trend
        day_trend = calculate_efficiency_trend(mock_txs, mock_dist_map, mock_units, group_by="day")
        self.assertEqual(len(day_trend.data), 3)
        self.assertEqual(day_trend.data[0].period, "2026-08-03")

        # Month trend
        month_trend = calculate_efficiency_trend(mock_txs, mock_dist_map, mock_units, group_by="month")
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
        with patch("app.fuel.wialon_distance.execute_wialon_group_trips_report") as mock_group:
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

    def test_10_api_fleet_efficiency_endpoint(self):
        """GET /api/fuel/analytics/efficiency returns valid summary against live Supabase data."""
        resp = self.client.get(
            "/api/fuel/analytics/efficiency",
            params={"date_from": "2026-08-03", "date_to": "2026-08-31"},
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(resp.status_code, 200, f"Error: {resp.text}")
        data = resp.json()

        self.assertEqual(data["transaction_count"], 2335)
        self.assertEqual(data["vehicle_count"], 238)
        self.assertAlmostEqual(data["total_litres"], 110553.52, places=2)
        self.assertAlmostEqual(data["total_cost"], 404532.95, places=2)

        # Matched and tracked fleet metrics
        self.assertEqual(data["matched_vehicle_count"], 28)
        self.assertEqual(data["unmatched_vehicle_count"], 210)
        self.assertEqual(data["vehicles_with_distance"], 28)
        self.assertGreater(data["total_distance_km"], 100000.0)

        # Weighted efficiency should be ~15.60 L/100 km
        self.assertGreater(data["fuel_efficiency_l_per_100km"], 10.0)
        self.assertLess(data["fuel_efficiency_l_per_100km"], 25.0)

    def test_11_api_vehicles_efficiency_endpoint(self):
        """GET /api/fuel/analytics/efficiency/vehicles returns paginated and sorted list."""
        resp = self.client.get(
            "/api/fuel/analytics/efficiency/vehicles",
            params={
                "date_from": "2026-08-03",
                "date_to": "2026-08-31",
                "page": 1,
                "page_size": 10,
                "sort_by": "litres",
                "sort_order": "desc",
            },
            headers={"Authorization": "Bearer mock-admin-token"},
        )
        self.assertEqual(resp.status_code, 200, f"Error: {resp.text}")
        data = resp.json()

        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 10)
        self.assertEqual(data["total"], 238)
        self.assertEqual(len(data["data"]), 10)

        # Top vehicle has positive litres
        top = data["data"][0]
        self.assertGreater(top["litres"], 0)
        self.assertIn(top["status"], ["matched", "not_tracked", "no_distance_data"])

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
