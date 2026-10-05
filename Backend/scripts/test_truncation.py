"""
Investigate 24h vs 7d message loading and event counts.
"""

import json
import os
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

res = post("core/search_items", {
    "spec": {"itemsType": "avl_unit", "propName": "sys_name", "propValueMask": "*", "sortType": "sys_name"},
    "force": 1,
    "flags": 1 | 256 | 1024 | 4096,
    "from": 0,
    "to": 0
}, sid=sid)

units = res.get("items", [])
now_utc = int(time.time())

print("Testing 3 units with loadCount=1000 vs loadCount=20000 for 24h and 7d (168h)...")

for u in units[:3]:
    uid = u["id"]
    unm = u.get("nm")

    # 24h with 1000
    m_24h_1k = post("messages/load_interval", {
        "itemId": uid, "timeFrom": now_utc - 86400, "timeTo": now_utc,
        "flags": 1, "flagsMask": 0, "loadCount": 1000, "calcSensors": 1
    }, sid=sid).get("messages", [])

    # 24h with 20000
    m_24h_20k = post("messages/load_interval", {
        "itemId": uid, "timeFrom": now_utc - 86400, "timeTo": now_utc,
        "flags": 1, "flagsMask": 0, "loadCount": 20000, "calcSensors": 1
    }, sid=sid).get("messages", [])

    # 7d with 1000
    m_7d_1k = post("messages/load_interval", {
        "itemId": uid, "timeFrom": now_utc - 7*86400, "timeTo": now_utc,
        "flags": 1, "flagsMask": 0, "loadCount": 1000, "calcSensors": 1
    }, sid=sid).get("messages", [])

    # 7d with 20000
    m_7d_20k = post("messages/load_interval", {
        "itemId": uid, "timeFrom": now_utc - 7*86400, "timeTo": now_utc,
        "flags": 1, "flagsMask": 0, "loadCount": 20000, "calcSensors": 1
    }, sid=sid).get("messages", [])

    print(f"\nUnit {unm} (ID {uid}):")
    print(f"  24h (loadCount=1000):  {len(m_24h_1k)} msgs")
    print(f"  24h (loadCount=20000): {len(m_24h_20k)} msgs")
    print(f"  7d  (loadCount=1000):  {len(m_7d_1k)} msgs (Truncated! Covers only from {datetime.fromtimestamp(m_7d_1k[0]['t'], tz=timezone.utc).strftime('%m-%d %H:%M') if m_7d_1k else 'N/A'} to {datetime.fromtimestamp(m_7d_1k[-1]['t'], tz=timezone.utc).strftime('%m-%d %H:%M') if m_7d_1k else 'N/A'})")
    print(f"  7d  (loadCount=20000): {len(m_7d_20k)} msgs (Full 7 days!)")
