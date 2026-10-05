"""
Targeted deep dive into vehicle 23494 and all units with safety parameters.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
import requests

WIALON_TOKEN = os.getenv("WIALON_TOKEN", "").strip()
WIALON_API   = os.getenv("WIALON_API", "").strip()

def post(svc, params, sid=None):
    payload = {"svc": svc, "params": json.dumps(params)}
    if sid:
        payload["sid"] = sid
    resp = requests.post(WIALON_API, data=payload, timeout=30)
    return resp.json()

sid = post("token/login", {"token": WIALON_TOKEN})["eid"]

# Search all units
res = post("core/search_items", {
    "spec": {"itemsType": "avl_unit", "propName": "sys_name", "propValueMask": "*", "sortType": "sys_name"},
    "force": 1,
    "flags": 1 | 256 | 1024 | 4096,
    "from": 0,
    "to": 0
}, sid=sid)

units = res.get("items", [])
print(f"Total units: {len(units)}")

target_unit = None
for u in units:
    if "23494" in u.get("nm", "") or str(u.get("id")) == "23494":
        target_unit = u
        break

if not target_unit:
    print("Listing all unit names and IDs:")
    for u in units:
        print(f"ID: {u['id']}, Name: {u.get('nm')}")
    # Pick unit with 23494 or similar
    target_unit = next((u for u in units if "23494" in u.get("nm", "")), units[0])

print(f"\n--- Target Unit: {target_unit.get('nm')} (ID: {target_unit['id']}) ---")
print("Configured Sensors:")
for sk, sv in (target_unit.get("sens") or {}).items():
    print(f"  Sensor ID: {sk}, Name: {sv.get('n')}, Type: {sv.get('t')}, Param: {sv.get('p')}")

now_utc = int(time.time())
start_utc = now_utc - 86400

mres = post("messages/load_interval", {
    "itemId": target_unit["id"],
    "timeFrom": start_utc,
    "timeTo": now_utc,
    "flags": 1,
    "flagsMask": 0,
    "loadCount": 5000,
    "calcSensors": 1
}, sid=sid)

msgs = mres.get("messages", [])
print(f"\nFetched {len(msgs)} messages in 24h for unit {target_unit.get('nm')}")

# Inspect parameters in messages
param_keys = set()
sensor_keys = set()
s4_values = []
s5_values = []
s6_values = []
s7_values = []
s9_values = []
io253_values = []
io254_values = []

for m in msgs:
    p = m.get("p") or {}
    s = m.get("sensors") or {}
    param_keys.update(p.keys())
    sensor_keys.update(s.keys())
    if "4" in s: s4_values.append(s["4"].get("value"))
    if "5" in s: s5_values.append(s["5"].get("value"))
    if "6" in s: s6_values.append(s["6"].get("value"))
    if "7" in s: s7_values.append(s["7"].get("value"))
    if "9" in s: s9_values.append(s["9"].get("value"))
    if "io_253" in p: io253_values.append(p["io_253"])
    if "io_254" in p: io254_values.append(p["io_254"])

print(f"\nParameters present: {sorted(list(param_keys))}")
print(f"Sensors present: {sorted(list(sensor_keys))}")
print(f"Sensor 4 (Accel) values sample: {s4_values[:20] if s4_values else 'None'} (Total counts of 1: {s4_values.count(1)})")
print(f"Sensor 5 (Brake) values sample: {s5_values[:20] if s5_values else 'None'} (Total counts of 1: {s5_values.count(1)})")
print(f"Sensor 6 (Corner) values sample: {s6_values[:20] if s6_values else 'None'} (Total counts of 1: {s6_values.count(1)})")
print(f"Sensor 7 (Green/Eco) values sample: {s7_values[:20] if s7_values else 'None'}")
print(f"Sensor 9 (G-force) values sample: {s9_values[:20] if s9_values else 'None'}")
print(f"io_253 values sample: {io253_values[:20] if io253_values else 'None'} (Count: {len(io253_values)})")
print(f"io_254 values sample: {io254_values[:20] if io254_values else 'None'} (Count: {len(io254_values)})")

# Check all other vehicles for sensor 4/5/6 and io_253
print("\n--- SAFETY SENSORS ACROSS ALL 30 VEHICLES ---")
for u in units:
    uid = u["id"]
    unm = u.get("nm")
    sens = u.get("sens") or {}
    s_names = [f"ID {k}:{v.get('n')}(param={v.get('p')})" for k, v in sens.items() if any(w in v.get('n', '').lower() for w in ['accel', 'brake', 'corner', 'safety', 'eco', 'g-force', 'green'])]
    if s_names:
        print(f"Unit {unm} (ID {uid}): {', '.join(s_names)}")
