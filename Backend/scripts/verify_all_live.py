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

def run_verification():
    print("=" * 60)
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
    print(f"   Moving: {moving}, Idling: {idling}, Stopped: {stopped}")
    print(f"   Sum (M+I+S): {moving + idling + stopped}")
    print(f"   Offline (delayed/disconnected): {offline} (Delayed: {delayed})")
    
    assert moving + idling + stopped == total, f"Invariant failed: {moving}+{idling}+{stopped} != {total}"
    print("   [PASS] INVARIANT VERIFIED: Moving + Idling + Stopped == Total Vehicles")

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
        assert g.get('moving') + g.get('idling') + g.get('stopped') == g.get('total_vehicles'), f"Group invariant failed for {g.get('group_name')}"
    print("   [PASS] FLEET GROUPS INVARIANT VERIFIED")

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_verification()
