import os
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("WIALON_TOKEN")
API_URL = os.getenv("WIALON_API")
SID = None

import time

def wialon_request(svc, params, sid=None, is_retry=False, max_retries=3):
    global SID

    current_sid = sid or SID

    data = {
        "svc": svc,
        "params": json.dumps(params)
    }

    if current_sid:
        data["sid"] = current_sid

    for attempt in range(max_retries):
        try:
            response = requests.post(
                API_URL,
                data=data,
                timeout=30
            )

            res_json = response.json()
        except Exception as e:
            if attempt == max_retries - 1:
                return {"error": -1, "reason": str(e)}
            time.sleep(0.3 * (attempt + 1))
            continue

        # Handle Wialon Error 1003 (LIMIT api_concurrent) with backoff
        if isinstance(res_json, dict) and res_json.get("error") == 1003:
            time.sleep(0.25 * (attempt + 1))
            continue

        # Handle Wialon Error 1 (Invalid session) automatically
        if isinstance(res_json, dict) and res_json.get("error") == 1 and not is_retry and svc != "token/login":
            print("Wialon session invalid (error 1). Refreshing session...")
            if SID == current_sid or not SID:
                SID = None
                new_sid = login(force=True)
            else:
                new_sid = SID

            return wialon_request(svc, params, sid=new_sid, is_retry=True)

        return res_json

    return res_json


def login(force=False):
    global SID

    # Reuse existing session
    if SID and not force:
        return SID

    login_data = wialon_request(
        "token/login",
        {
            "token": TOKEN
        },
        sid=None,
        is_retry=True
    )

    if "error" in login_data:
        SID = None
        raise Exception(
            f"Wialon login failed: {login_data}"
        )

    SID = login_data.get("eid")

    return SID


if __name__ == "__main__":

    sid = login()

    print("Wialon login successful")

def get_vehicle_history(
    sid,
    vehicle_id,
    hours=6,
    from_time=None,
    to_time=None
):

    if from_time is not None and to_time is not None:
        time_from = int(from_time)
        current_time = int(to_time)
    else:
        current_time = int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )
        time_from = current_time - (
            hours * 3600
        )

    message_data = wialon_request(
        "messages/load_interval",
        {
            "itemId": vehicle_id,
            "timeFrom": time_from,
            "timeTo": current_time,

            # Request position messages
            "flags": 1,

            "flagsMask": 0,

            # Maximum messages to load
            "loadCount": 1000,

            # Calculate sensor values
            "calcSensors": 1
        },
        sid
    )

    if "error" in message_data:
        print(
            f"History request failed "
            f"for vehicle {vehicle_id}:"
        )
        print(message_data)
        return []

    return message_data.get(
        "messages",
        []
    )


def get_vehicle_groups(sid):
    """
    Fetch all dynamic unit groups from Wialon.
    """
    group_data = wialon_request(
        "core/search_items",
        {
            "spec": {
                "itemsType": "avl_unit_group",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name"
            },
            "force": 1,
            "flags": 1,
            "from": 0,
            "to": 0
        },
        sid
    )

    if "error" in group_data:
        print("Vehicle group search failed:", group_data)
        return []

    return group_data.get("items", [])