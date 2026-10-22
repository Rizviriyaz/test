"""
Characterization test for the SHARED Wialon report parsers (app/wialon_reports.py) and the non-fuel
features built on them (fleet idle analytics, vehicle idle, vehicle trips, dashboard mileage,
fleet utilization, fleet trips).

Classification: MOCKED / offline. Wialon is replaced by FakeReportEngine (plain-string cells, like
production) and every outbound HTTP call is blocked by OfflineGuard.

  python scripts/test_wialon_reports_characterization.py --capture   # record golden from current code
  python scripts/test_wialon_reports_characterization.py             # compare current code to golden

The golden file pins every value the current code produces for this fixture. Parser changes must
keep all of it identical (new keys may be added; existing keys/values may not change).
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, UNITS, FakeReportEngine, live_records, normalize_for_compare,
)

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "wialon_reports_golden.json")
FROM_TS, TO_TS = BASE_TS, BASE_TS + 3 * 86400 - 1


def collect_outputs():
    import app.main as main
    import app.fleet_analytics as fa
    from app import wialon_reports as wr

    out = {}
    engine = FakeReportEngine()
    fleet_patches = [
        patch("app.wialon_reports.wialon_request", engine),
        patch("app.main.login", return_value="sid"),
        patch("app.main.get_vehicle_groups", return_value=GROUPS),
        patch("app.main.get_all_vehicles", return_value=UNITS),
        patch("app.main.get_calculated_data", return_value=CALCULATED),
        patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()),
        patch("app.fleet_analytics.login", return_value="sid"),
        patch("app.fleet_analytics.get_vehicle_groups", return_value=GROUPS),
        patch("app.fleet_analytics.get_all_vehicles", return_value=UNITS),
        patch("app.fleet_analytics.get_calculated_data", return_value=CALCULATED),
        patch("app.fleet_analytics.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()),
    ]
    for p in fleet_patches:
        p.start()
    try:
        main._cache.clear()
        fa._analytics_cache.clear()
        out["execute_wialon_unit_trips_report"] = wr.execute_wialon_unit_trips_report("sid", 101, FROM_TS, TO_TS)
        out["execute_wialon_unit_engine_report"] = wr.execute_wialon_unit_engine_report("sid", 101, FROM_TS, TO_TS)
        out["execute_wialon_group_trips_report"] = wr.execute_wialon_group_trips_report("sid", FROM_TS, TO_TS)
        out["execute_wialon_group_engine_idle_report(events)"] = wr.execute_wialon_group_engine_idle_report("sid", FROM_TS, TO_TS)
        out["execute_wialon_group_engine_idle_report(summary)"] = wr.execute_wialon_group_engine_idle_report(
            "sid", FROM_TS, TO_TS, include_events=False)

        out["GET /api/idle/fleet-analytics"] = main.get_fleet_idle_analytics(
            from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=True)
        out["GET /api/idle/fleet-analytics?include_events=false"] = main.get_fleet_idle_analytics(
            from_time=FROM_TS, to_time=TO_TS, include_events=False, skip_cache=True)
        out["GET /api/vehicles/101/idle"] = main.get_vehicle_idle(101, from_time=FROM_TS, to_time=TO_TS)
        out["GET /api/vehicles/101/trips"] = main.get_vehicle_trips(101, from_time=FROM_TS, to_time=TO_TS)
        out["GET /api/dashboard/mileage"] = main.get_dashboard_mileage(timeframe="custom", from_time=FROM_TS, to_time=TO_TS)
        out["GET /api/fleet/utilization"] = fa.get_fleet_utilization_analytics(hours=24)
        out["GET /api/fleet/trips"] = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    finally:
        for p in reversed(fleet_patches):
            p.stop()
    return normalize_for_compare(json.loads(json.dumps(out, default=str)))


def diff(expected, actual, path="$"):
    """Existing keys/values must match exactly; keys only present in `actual` are allowed (additive)."""
    problems = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        for k, v in expected.items():
            if k not in actual:
                problems.append(f"{path}.{k}: missing")
            else:
                problems.extend(diff(v, actual[k], f"{path}.{k}"))
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [f"{path}: list length {len(expected)} != {len(actual) if isinstance(actual, list) else type(actual).__name__}"]
        for i, (e, a) in enumerate(zip(expected, actual)):
            problems.extend(diff(e, a, f"{path}[{i}]"))
    elif expected != actual or type(expected) is not type(actual):
        problems.append(f"{path}: expected {expected!r}, got {actual!r}")
    return problems


def main_entry():
    capture = "--capture" in sys.argv
    guard = OfflineGuard().start()
    # Report executors discover the account's "All Unit" group; serve the fixture groups offline
    patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS).start()
    try:
        outputs = collect_outputs()
    finally:
        guard.stop()
    guard.assert_no_live_calls()

    if capture:
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(outputs, f, indent=1, sort_keys=True)
        print(f"[CAPTURED] {len(outputs)} outputs written to {GOLDEN_PATH}")
        return 0

    with open(GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)
    failures = 0
    for name, expected in golden.items():
        problems = diff(expected, outputs.get(name))
        if problems:
            failures += 1
            print(f"[FAIL] {name}: {len(problems)} difference(s)")
            for p in problems[:15]:
                print(f"       {p}")
        else:
            print(f"[PASS] {name}: identical to golden (existing keys)")
    print(f"{len(golden) - failures}/{len(golden)} characterized outputs unchanged")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main_entry())
