import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.vehicles import get_all_vehicles, get_calculated_data, get_vehicle_live_data
from app.fleet_analytics import (
    get_fleet_utilization_analytics,
    get_trips_mileage_analytics,
    get_fleet_groups_analytics,
)

from app.wialon import login
from app.normalize import is_valid_sensor_value, normalize_driver_id
from app.wialon_reports import parse_float_safe, parse_duration_to_seconds
from app.status import calculate_status, calculate_idle_duration

def run_regression_tests():
    print("=" * 60)
    print("RUNNING FOCUSED REGRESSION CHECKS")
    print("=" * 60)

    # 1. Float Parsing & Report Cell Extraction
    assert parse_float_safe(12.34) == 12.34
    assert parse_float_safe("1,234.56 km") == 1234.56
    assert parse_float_safe("45,8 km/h") == 45.8
    assert parse_float_safe({"v": 89.2, "t": "89.2 km"}) == 89.2
    assert parse_float_safe({"t": "12.5"}) == 12.5
    assert parse_float_safe("---") == 0.0
    assert parse_float_safe(None) == 0.0
    print("  [PASS] parse_float_safe handles primitives, dicts, comma/dot decimals, and units")

    # 2. Duration Parsing
    assert parse_duration_to_seconds("01:30:00") == 5400
    assert parse_duration_to_seconds("15:30") == 930
    assert parse_duration_to_seconds({"v": 3600}) == 3600
    assert parse_duration_to_seconds({"t": "00:05:00"}) == 300
    print("  [PASS] parse_duration_to_seconds handles strings, numbers, and dict cells")

    # 3. Driver Normalization
    for ph in ["0000000000000000", "0", "---", "none", "null", "N/A", "unknown"]:
        assert normalize_driver_id(ph) is None, f"Failed to filter placeholder {ph}"
    assert normalize_driver_id("DRV-8491") == "DRV-8491"
    print("  [PASS] normalize_driver_id filters all hardware RFID/iButton placeholders")

    # 4. Sensor Validity & Sentinel Protection
    assert is_valid_sensor_value(1) is True
    assert is_valid_sensor_value(0) is True
    assert is_valid_sensor_value(-348201.3876) is False
    assert is_valid_sensor_value(-150000) is False
    assert is_valid_sensor_value(None) is False
    print("  [PASS] is_valid_sensor_value correctly detects Wialon negative sentinels")

    # 5. 12V and 24V Ignition Semantics (No false positives on 24V resting battery voltage)
    # A 24V vehicle with engine OFF has resting voltage ~25.4V, but its ignition sensor outputs 0
    msgs_engine_off = [{
        "tp": "ud",
        "t": 1000,
        "pos": {"s": 0},
        "sensors": {"11": {"value": 0}},
        "p": {"pwr_ext": 25.4}
    }]
    sens_cfg = {"11": {"n": "Ignition", "t": "engine operation", "p": "pwr_ext"}}
    idle_dur = calculate_idle_duration(msgs_engine_off, 1100, sens_cfg)
    assert idle_dur == 0, f"Expected 0 idle duration for engine-off 24V vehicle, got {idle_dur}"

    # Engine ON (sensor output is 1)
    msgs_engine_on = [
        {"tp": "ud", "t": 1000, "pos": {"s": 0}, "sensors": {"11": {"value": 1}}, "p": {"pwr_ext": 28.2}},
        {"tp": "ud", "t": 1060, "pos": {"s": 0}, "sensors": {"11": {"value": 1}}, "p": {"pwr_ext": 28.2}},
    ]
    idle_dur_on = calculate_idle_duration(msgs_engine_on, 1100, sens_cfg)
    assert idle_dur_on == 100, f"Expected 100s idle duration, got {idle_dur_on}"
    print("  [PASS] calculate_idle_duration correctly differentiates 24V resting vs running states")


def run_verification():
    run_regression_tests()

    print("\n" + "=" * 60)
    print("RUNNING LIVE END-TO-END VERIFICATION")
    print("=" * 60)

    sid = login()

    # 1. Vehicles & Status Invariant
    t0 = time.time()
    raw_units = get_all_vehicles(sid)
    calc_data = get_calculated_data(sid, raw_units)
    vehicles = get_vehicle_live_data(sid, raw_units, calc_data)
    t_v = time.time() - t0
    total = len(vehicles)
    moving = sum(1 for v in vehicles if v.get("status") == "MOVING")
    idling = sum(1 for v in vehicles if v.get("status") == "IDLING")
    stopped = sum(1 for v in vehicles if v.get("status") == "STOPPED")
    offline = sum(1 for v in vehicles if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))
    delayed = sum(1 for v in vehicles if v.get("connection_status") == "DELAYED")

    print(f"\n1. VEHICLES SNAPSHOT ({t_v:.2f}s):")
    print(f"   Total: {total}")
    print(f"   Moving: {moving}, Idling: {idling}, Stopped: {stopped}, Offline: {offline}")
    print(f"   Sum (M+I+S+Off): {moving + idling + stopped + offline}")
    print(f"   Delayed: {delayed}")
    
    assert moving + idling + stopped + offline == total, f"Invariant failed: {moving}+{idling}+{stopped}+{offline} != {total}"
    print("   [PASS] INVARIANT VERIFIED: Moving + Idling + Stopped + Offline == Total Vehicles")

    # 2. Utilization Analytics (Group Engine Report)
    t0 = time.time()
    util = get_fleet_utilization_analytics(hours=24)
    t_u = time.time() - t0
    util_vehicles = util.get('vehicles', [])
    print(f"\n2. FLEET UTILIZATION ({t_u:.2f}s):")
    print(f"   Fleet Avg Utilization: {util.get('fleet_utilization_pct')}%")
    print(f"   Total Driving Hours: {util.get('total_driving_hours')}h")
    print(f"   Total Idle Hours: {util.get('total_idle_hours')}h")
    print(f"   Total Engine Hours: {util.get('total_engine_on_hours')}h")
    print(f"   Vehicles count in util: {len(util_vehicles)}")
    assert len(util_vehicles) == total, "Utilization vehicle count mismatch"
    print("   [PASS] UTILIZATION DATA VERIFIED")

    # 3. Trips & Mileage Analytics (Group Trips Report)
    t0 = time.time()
    mileage = get_trips_mileage_analytics(hours=24, timeframe="today")
    t_m = time.time() - t0
    m_sum = mileage.get('summary', {})
    m_vehicles = mileage.get('vehicles', [])
    print(f"\n3. TRIPS & MILEAGE ({t_m:.2f}s):")
    print(f"   Total Mileage: {m_sum.get('total_distance_km')} km")
    print(f"   Total Trips: {m_sum.get('total_trips')}")
    print(f"   Vehicles count in mileage: {len(m_vehicles)}")
    assert len(m_vehicles) == total, "All units mileage count mismatch"
    print("   [PASS] TRIPS & MILEAGE DATA VERIFIED")

    # 4. Fleet Groups Analytics
    t0 = time.time()
    groups = get_fleet_groups_analytics()
    t_g = time.time() - t0
    print(f"\n4. FLEET GROUPS ({t_g:.2f}s):")
    print(f"   Groups Count: {len(groups.get('groups', []))}")
    print(f"   Total Group Vehicles: {sum(g.get('total_vehicles', 0) for g in groups.get('groups', []))}")
    for g in groups.get('groups', []):
        print(f"     Group '{g.get('group_name')}': {g.get('total_vehicles')} units (M:{g.get('moving')} I:{g.get('idling')} S:{g.get('stopped')} Off:{g.get('offline')})")
        assert g.get('moving') + g.get('idling') + g.get('stopped') + g.get('offline') == g.get('total_vehicles'), f"Group invariant failed for {g.get('group_name')}"
    print("   [PASS] FLEET GROUPS INVARIANT VERIFIED")

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_verification()
