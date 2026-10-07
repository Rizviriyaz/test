import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv('Backend/.env')

BASE_URL = "http://127.0.0.1:8000"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

def test_full_frontend_flow():
    print("=" * 65)
    print("VALIDATING FRONTEND API CONSUMPTION & AUTHENTICATION")
    print("=" * 65)

    # 1. Login with user credentials
    print("\n1. Supabase Authentication Test:")
    login_resp = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": SUPABASE_ANON_KEY},
        json={"email": "ATS-AI@gmail.com", "password": "ATS-AI@2026"},
        timeout=15,
    )
    assert login_resp.ok, f"Login failed: {login_resp.status_code} {login_resp.text}"
    token = login_resp.json().get("access_token")
    assert token, "No access token in login response"
    print(f"   [PASS] Authenticated user 'ATS-AI@gmail.com' successfully! Token length: {len(token)}")

    headers = {"Authorization": f"Bearer {token}"}

    # 2. Test Overview KPIs: /api/fuel/analytics/efficiency
    print("\n2. Fuel Overview KPIs (GET /api/fuel/analytics/efficiency for Today):")
    eff_resp = requests.get(
        f"{BASE_URL}/api/fuel/analytics/efficiency?date_from=2026-10-07&date_to=2026-10-07",
        headers=headers,
        timeout=45,
    )
    assert eff_resp.ok, f"Efficiency endpoint failed: {eff_resp.status_code} {eff_resp.text}"
    eff_data = eff_resp.json()
    print(f"   Response status: {eff_resp.status_code}")
    print(f"   Total Litres for Today: {eff_data.get('total_litres')} L")
    print(f"   Tracked Vehicles: {eff_data.get('matched_vehicle_count')}")
    print("   [PASS] Overview KPIs verified!")

    # 3. Test Historical Fuel Summary: /api/fuel/analytics/summary
    print("\n3. Historical Fuel Summary (GET /api/fuel/analytics/summary):")
    sum_resp = requests.get(f"{BASE_URL}/api/fuel/analytics/summary", headers=headers, timeout=20)
    assert sum_resp.ok, f"Summary failed: {sum_resp.status_code} {sum_resp.text}"
    sum_data = sum_resp.json()
    print(f"   Total historical litres: {sum_data.get('total_litres'):,.1f} L")
    print(f"   Total historical cost: AED {sum_data.get('total_cost'):,.2f}")
    print(f"   Total transactions: {sum_data.get('transaction_count'):,}")
    print(f"   Total vehicles: {sum_data.get('vehicle_count')}")
    assert sum_data.get("total_litres", 0) > 100000, "Expected > 100k litres"
    print("   [PASS] Historical Fuel Summary verified!")

    # 4. Test Fuel Type Breakdown: /api/fuel/analytics/fuel-types
    print("\n4. Fuel Product Breakdown (GET /api/fuel/analytics/fuel-types):")
    ft_resp = requests.get(f"{BASE_URL}/api/fuel/analytics/fuel-types", headers=headers, timeout=20)
    assert ft_resp.ok, f"Fuel types failed: {ft_resp.status_code} {ft_resp.text}"
    ft_data = ft_resp.json().get("data", [])
    print(f"   Dynamic fuel types detected: {len(ft_data)}")
    for ft in ft_data:
        print(f"   - {ft.get('fuel_type')}: {ft.get('litres'):,.1f} L (AED {ft.get('cost'):,.2f})")
    assert len(ft_data) > 0, "Expected fuel types"
    print("   [PASS] Fuel Types breakdown verified!")

    # 5. Test Vehicle Ranking: /api/fuel/analytics/efficiency/vehicles
    print("\n5. Vehicle Rankings (GET /api/fuel/analytics/efficiency/vehicles):")
    rank_resp = requests.get(
        f"{BASE_URL}/api/fuel/analytics/efficiency/vehicles?page=1&page_size=5&sort_by=litres&sort_order=desc&date_from=2026-10-07&date_to=2026-10-07",
        headers=headers,
        timeout=45,
    )
    assert rank_resp.ok, f"Ranking failed: {rank_resp.status_code} {rank_resp.text}"
    rank_data = rank_resp.json()
    print(f"   Total vehicles: {rank_data.get('total')}")
    print("   [PASS] Vehicle Ranking verified!")

    # 6. Test Vehicle Fuel List: /api/fuel/analytics/vehicles
    print("\n6. Vehicle Analytics List (GET /api/fuel/analytics/vehicles):")
    veh_resp = requests.get(f"{BASE_URL}/api/fuel/analytics/vehicles?page=1&page_size=5", headers=headers, timeout=20)
    assert veh_resp.ok, f"Vehicles list failed: {veh_resp.status_code} {veh_resp.text}"
    veh_data = veh_resp.json()
    print(f"   Total unique vehicles: {veh_data.get('total')}")
    for idx, v in enumerate(veh_data.get('data', []), 1):
        print(f"   #{idx}: {v.get('vehicle_identifier')} | {v.get('litres'):,.1f} L | Refuels: {v.get('transaction_count')}")
    assert veh_data.get('total') > 200, "Expected > 200 vehicles"
    print("   [PASS] Vehicle Analytics verified!")

    # 7. Test Import History: /api/fuel/imports
    print("\n7. Import History (GET /api/fuel/imports):")
    imp_resp = requests.get(f"{BASE_URL}/api/fuel/imports?limit=5", headers=headers, timeout=20)
    assert imp_resp.ok, f"Imports failed: {imp_resp.status_code} {imp_resp.text}"
    imports = imp_resp.json().get("imports", [])
    print(f"   Total import history records: {len(imports)}")
    for imp in imports[:2]:
        print(f"   - Import #{imp.get('id')}: {imp.get('file_name')} | Status: {imp.get('status')} | Rows: {imp.get('rows_imported')}")
    print("   [PASS] Import History verified!")

    print("\n" + "=" * 65)
    print("ALL FRONTEND API CONTRACTS & REAL DATA INTEGRATIONS PASSED (100%)")
    print("=" * 65)

if __name__ == "__main__":
    test_full_frontend_flow()
