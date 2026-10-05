import os
import time
import json
import logging
import requests
from app.performance_diagnostics import request_trace_id
from app.utils.datetime_helpers import now_timestamp
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("WIALON_TOKEN")
API_URL = os.getenv("WIALON_API")
SID = None

import threading
# Session refresh can call login() while handling Wialon's expired-session
# response. Re-entrant locking keeps refresh serialized without self-deadlocking.
_login_lock = threading.RLock()


class WialonAPIError(RuntimeError):
    """An upstream Wialon request failed rather than returning empty data."""

def wialon_request(svc, params, sid=None, is_retry=False, max_retries=3, timeout=30):
    global SID

    current_sid = sid or SID

    data = {
        "svc": svc,
        "params": json.dumps(params)
    }

    if current_sid:
        data["sid"] = current_sid

    for attempt in range(max_retries):
        request_started = time.perf_counter()
        try:
            response = requests.post(
                API_URL,
                data=data,
                timeout=timeout
            )

            res_json = response.json()
        except Exception as e:
            logging.info(
                "PERF wialon end request_id=%s service=%s duration_ms=%.1f attempt=%s outcome=transport_error",
                request_trace_id.get(), svc, (time.perf_counter() - request_started) * 1000, attempt + 1,
            )
            if attempt == max_retries - 1:
                raise WialonAPIError(f"Wialon transport failure for {svc}") from e
            time.sleep(0.3 * (attempt + 1))
            continue

        outcome = f"wialon_error_{res_json.get('error')}" if isinstance(res_json, dict) and "error" in res_json else "success"
        logging.info(
            "PERF wialon end request_id=%s service=%s duration_ms=%.1f attempt=%s outcome=%s",
            request_trace_id.get(), svc, (time.perf_counter() - request_started) * 1000, attempt + 1, outcome,
        )

        # Handle Wialon Error 1003 (LIMIT api_concurrent) with backoff
        if isinstance(res_json, dict) and res_json.get("error") == 1003:
            time.sleep(0.25 * (attempt + 1))
            continue

        # Handle Wialon Error 1 (Invalid session) automatically
        if isinstance(res_json, dict) and res_json.get("error") == 1 and not is_retry and svc != "token/login":
            with _login_lock:
                if SID == current_sid or not SID:
                    print("Wialon session invalid (error 1). Refreshing session...")
                    new_sid = login(force=True)
                else:
                    new_sid = SID

            return wialon_request(svc, params, sid=new_sid, is_retry=True)

        return res_json

    return res_json


def login(force=False):
    global SID

    with _login_lock:
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
        current_time = now_timestamp()
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

            # Maximum messages to load (scale with requested duration to avoid truncation)
            "loadCount": min(50000, max(5000, int(hours * 250))),

            # Calculate sensor values
            "calcSensors": 1
        },
        sid
    )

    if isinstance(message_data, dict) and "error" in message_data:
        reason = message_data.get("reason", message_data.get("error"))
        raise WialonAPIError(f"Wialon history request failed for vehicle {vehicle_id}: {reason}")

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

    if isinstance(group_data, dict) and "error" in group_data:
        raise WialonAPIError(f"Wialon vehicle group search failed: {group_data}")

    return group_data.get("items", [])
