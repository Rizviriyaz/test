"""Inspect live production fuel breakdown."""

import os
from collections import defaultdict
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}"}

txns = []
offset = 0
while True:
    r = requests.get(
        f"{url}/rest/v1/fuel_transactions?select=transaction_date,vehicle_identifier,fuel_type,litres,total_cost,unit_price&limit=1000&offset={offset}",
        headers=headers
    )
    data = r.json()
    if not data:
        break
    txns.extend(data)
    offset += len(data)
    if len(data) < 1000:
        break

print(f"Total transactions fetched: {len(txns)}")
total_l = sum(float(t.get("litres") or 0) for t in txns)
total_c = sum(float(t.get("total_cost") or 0) for t in txns)
vehicles = set(t.get("vehicle_identifier") for t in txns if t.get("vehicle_identifier"))
print(f"Total litres: {total_l:,.2f}")
print(f"Total cost: {total_c:,.2f}")
print(f"Unique vehicles: {len(vehicles)}")
print(f"Average unit price: {total_c / total_l:.2f}")

ft_map = defaultdict(lambda: {"litres": 0.0, "cost": 0.0, "count": 0})
for t in txns:
    ft = t.get("fuel_type") or "UNKNOWN"
    ft_map[ft]["litres"] += float(t.get("litres") or 0)
    ft_map[ft]["cost"] += float(t.get("total_cost") or 0)
    ft_map[ft]["count"] += 1

for ft, v in ft_map.items():
    avg = v["cost"] / v["litres"] if v["litres"] > 0 else 0
    print(f"  {ft}: {v['count']} txns, {v['litres']:,.2f} L, {v['cost']:,.2f} AED, avg={avg:.2f}")
