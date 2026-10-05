"""
Quick verification of backend analytics endpoints.
"""
import requests

BASE = "http://127.0.0.1:8000"

endpoints = [
    "/api/fleet/health",
    "/api/fleet/utilization?hours=24",
    "/api/fleet/trips?hours=24",
    "/api/fleet/safety?hours=24",
    "/api/fleet/alerts",
    "/api/fleet/groups",
]

print("Testing Backend Analytics Endpoints...")
for ep in endpoints:
    url = BASE + ep
    try:
        r = requests.get(url, timeout=15)
        print(f"[OK {r.status_code}] {ep} (Response size: {len(r.text)} bytes)")
    except Exception as e:
        print(f"[ERROR] {ep}: {e}")
