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


_hw_types_cache = None


def get_hardware_types(sid=None):
    """
    Fetch and cache hardware/device types from Wialon.
    """
    global _hw_types_cache
    if _hw_types_cache is not None:
        return _hw_types_cache

    try:
        hw_list = wialon_request("core/get_hw_types", {}, sid=sid)
        if isinstance(hw_list, list):
            _hw_types_cache = {
                item["id"]: item.get("name", f"Hardware {item['id']}")
                for item in hw_list
                if isinstance(item, dict) and "id" in item
            }
            return _hw_types_cache
    except Exception as e:
        logging.warning("Failed to fetch Wialon hardware types: %s", e)

    return {}


def audit_wialon_fleet(sid=None):
    """
    Perform a read-only audit of accessible Wialon units, groups,
    raw telemetry parameters, configured sensors, and detected data categories.
    """
    current_sid = sid or login()

    search_result = wialon_request(
        "core/search_items",
        {
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name"
            },
            "force": 1,
            "flags": 4611686018427387903,
            "from": 0,
            "to": 0
        },
        current_sid
    )

    if isinstance(search_result, dict) and "error" in search_result:
        raise WialonAPIError(f"Wialon vehicle audit search failed: {search_result}")

    units = search_result.get("items", [])
    groups_data = get_vehicle_groups(current_sid)
    hw_types = get_hardware_types(current_sid)

    unit_to_groups = {}
    groups_found = []
    for g in groups_data:
        g_name = (g.get("nm") or "").strip()
        if g_name and g_name not in groups_found:
            groups_found.append(g_name)
        for uid in g.get("u", []):
            if uid not in unit_to_groups:
                unit_to_groups[uid] = []
            if g_name and g_name not in unit_to_groups[uid]:
                unit_to_groups[uid].append(g_name)

    gps_fields_master = set()
    raw_params_master = set()
    all_sensors = []

    vehicles_with = {
        "fuel": [],
        "engine_hours": [],
        "temperature": [],
        "battery_voltage": [],
        "speed": [],
        "position": []
    }

    vehicle_audits = []

    for u in units:
        u_id = u.get("id")
        u_name = u.get("nm", "Unknown")
        hw_id = u.get("hw")
        hw_name = hw_types.get(hw_id) if hw_id else None
        hw_display = hw_name or (f"Hardware ID {hw_id}" if hw_id else "Unavailable")
        uid = u.get("uid") or u.get("uid2") or None
        unit_groups = unit_to_groups.get(u_id, [])

        # 1. GPS / Position data
        pos = u.get("pos") or {}
        lmsg = u.get("lmsg") or {}
        lmsg_pos = lmsg.get("pos") or {}
        effective_pos = pos or lmsg_pos

        has_lat = effective_pos.get("y") is not None
        has_lon = effective_pos.get("x") is not None
        has_alt = effective_pos.get("z") is not None
        has_spd = effective_pos.get("s") is not None
        has_crs = effective_pos.get("c") is not None
        has_sats = effective_pos.get("sc") is not None
        has_ts = effective_pos.get("t") is not None

        gps_fields = []
        if has_lat:
            gps_fields.append("latitude")
            gps_fields_master.add("latitude")
        if has_lon:
            gps_fields.append("longitude")
            gps_fields_master.add("longitude")
        if has_alt:
            gps_fields.append("altitude")
            gps_fields_master.add("altitude")
        if has_spd:
            gps_fields.append("speed")
            gps_fields_master.add("speed")
        if has_crs:
            gps_fields.append("course")
            gps_fields_master.add("course")
        if has_sats:
            gps_fields.append("satellites")
            gps_fields_master.add("satellites")
        if has_ts:
            gps_fields.append("timestamp")
            gps_fields_master.add("timestamp")

        has_position = bool(has_lat and has_lon)

        # 2. Raw telemetry parameters
        lmsg_params = lmsg.get("p") or {}
        unit_prms = u.get("prms") or {}
        combined_params = {}
        if isinstance(unit_prms, dict):
            combined_params.update(unit_prms)
        if isinstance(lmsg_params, dict):
            combined_params.update(lmsg_params)

        param_names = sorted(list(combined_params.keys()))
        for p in param_names:
            raw_params_master.add(p)

        # 3. Sensors
        sens_dict = u.get("sens") or {}
        unit_sensors = []
        for s_id, s_conf in sens_dict.items():
            if not isinstance(s_conf, dict):
                continue
            sensor_obj = {
                "vehicle_id": u_id,
                "vehicle_name": u_name,
                "sensor_id": s_conf.get("id", s_id),
                "sensor_name": s_conf.get("n", ""),
                "sensor_type": s_conf.get("t", ""),
                "sensor_parameter": s_conf.get("p", ""),
                "units": s_conf.get("m", ""),
                "description": s_conf.get("d", ""),
                "configuration": s_conf.get("c", "")
            }
            unit_sensors.append(sensor_obj)
            all_sensors.append(sensor_obj)

        # 4. Common category detection based strictly on actual returned data
        s_names_lower = [str(s.get("n", "")).lower() for s in sens_dict.values() if isinstance(s, dict)]
        s_types_lower = [str(s.get("t", "")).lower() for s in sens_dict.values() if isinstance(s, dict)]
        s_params_lower = [str(s.get("p", "")).lower() for s in sens_dict.values() if isinstance(s, dict)]
        p_names_lower = [str(k).lower() for k in param_names]

        # Fuel detection: sensor type/name or telemetry param
        has_fuel_sensor = any("fuel" in n or "fls" in n for n in s_names_lower) or any("fuel" in t for t in s_types_lower) or any("fuel" in p or "fls" in p for p in s_params_lower)
        has_fuel_param = any("fuel" in p or "fls" in p for p in p_names_lower)
        has_fuel = bool(has_fuel_sensor or has_fuel_param)

        # Engine hours detection: engine/hour/ignition sensor type/name or raw parameter
        has_eh_sensor = any("engine" in n or "hour" in n or "ignition" in n or "idling" in n for n in s_names_lower) or any("engine" in t or "hour" in t for t in s_types_lower)
        has_eh_param = any("engine" in p or "ign" in p or p in {"io_239", "io_1", "io_240"} for p in p_names_lower)
        has_eh = bool(has_eh_sensor or has_eh_param)

        # Temperature detection: temperature sensor or raw parameter
        has_temp_sensor = any("temp" in n for n in s_names_lower) or any("temp" in t for t in s_types_lower) or any("temp" in p for p in s_params_lower)
        has_temp_param = any("temp" in p for p in p_names_lower)
        has_temp = bool(has_temp_sensor or has_temp_param)

        # Battery / voltage detection: voltage/battery sensor or raw parameter
        has_bat_sensor = any("volt" in t or "volt" in n or "batt" in n or "power" in n for n, t in zip(s_names_lower, s_types_lower)) or any("volt" in p or "pwr" in p or "batt" in p for p in s_params_lower)
        has_bat_param = any("pwr" in p or "batt" in p or "volt" in p or p in {"io_66", "io_67"} for p in p_names_lower)
        has_bat = bool(has_bat_sensor or has_bat_param)

        # Speed detection: position speed or speed sensor/param
        has_speed_data = bool(has_spd or any("speed" in n for n in s_names_lower) or any("speed" in p for p in p_names_lower))

        # Categorize vehicle
        v_ref = {"id": u_id, "name": u_name}
        if has_fuel:
            vehicles_with["fuel"].append(v_ref)
        if has_eh:
            vehicles_with["engine_hours"].append(v_ref)
        if has_temp:
            vehicles_with["temperature"].append(v_ref)
        if has_bat:
            vehicles_with["battery_voltage"].append(v_ref)
        if has_speed_data:
            vehicles_with["speed"].append(v_ref)
        if has_position:
            vehicles_with["position"].append(v_ref)

        # Debugging / Data availability
        data_availability = {
            "position_available": has_position,
            "parameters_available": bool(param_names),
            "sensors_configured": bool(unit_sensors),
            "notes": []
        }
        if not has_position:
            data_availability["notes"].append("No valid GPS position coordinates available")
        if not param_names:
            data_availability["notes"].append("No raw telemetry parameters received")
        if not unit_sensors:
            data_availability["notes"].append("No configured sensors found for this vehicle")

        vehicle_audits.append({
            "unit_id": u_id,
            "unit_name": u_name,
            "hardware_type": hw_display,
            "hardware_type_id": hw_id,
            "uid": uid,
            "groups": unit_groups,
            "gps": {
                "available": has_position,
                "latitude": effective_pos.get("y"),
                "longitude": effective_pos.get("x"),
                "altitude": effective_pos.get("z"),
                "speed": effective_pos.get("s"),
                "course": effective_pos.get("c"),
                "satellites": effective_pos.get("sc"),
                "timestamp": effective_pos.get("t"),
                "fields_found": gps_fields
            },
            "raw_parameters": {
                "count": len(param_names),
                "parameter_names": param_names,
                "latest_values": combined_params
            },
            "sensors": unit_sensors,
            "categories_detected": {
                "fuel": has_fuel,
                "engine_hours": has_eh,
                "temperature": has_temp,
                "battery_voltage": has_bat,
                "speed": has_speed_data,
                "position": has_position
            },
            "data_availability": data_availability
        })

    return {
        "success": True,
        "summary": {
            "vehicle_count": len(units),
            "parameter_count": len(raw_params_master),
            "sensor_count": len(all_sensors),
            "group_count": len(groups_found)
        },
        "gps_fields_found": sorted(list(gps_fields_master)),
        "raw_parameters_found": sorted(list(raw_params_master)),
        "groups_found": groups_found,
        "vehicles_with": vehicles_with,
        "sensors": all_sensors,
        "vehicles": vehicle_audits
    }
