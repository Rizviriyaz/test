"""
=============================================================
WIALON DATA INSPECTOR
=============================================================
Standalone read-only discovery script for ATS Fleet Management.

PURPOSE:
    Inspect every unit in the Wialon account and report all data
    currently available - sensors, parameters, position, groups,
    drivers, mileage, engine hours, and any other fields Wialon
    returns - WITHOUT modifying any existing application code or
    any data in Wialon / Supabase.

OUTPUT:
    - Terminal report (human-readable)
    - wialon_data_inspection.json  (raw full data per unit)
    - wialon_data_summary.json     (aggregated discovery summary)

USAGE:
    cd Backend
    python scripts/wialon_data_inspector.py

REQUIREMENTS:
    requests, python-dotenv  (already in requirements.txt)
=============================================================
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

# Locate .env relative to this script
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
_ENV_PATH = os.path.join(_BACKEND_DIR, ".env")

try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH)
except ImportError:
    if os.path.isfile(_ENV_PATH):
        with open(_ENV_PATH) as _fh:
            for _line in _fh:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

try:
    import requests
except ImportError:
    print("[FATAL] 'requests' library is not installed.")
    print("        Run:  pip install requests")
    sys.exit(1)

WIALON_TOKEN = os.getenv("WIALON_TOKEN", "").strip()
WIALON_API   = os.getenv("WIALON_API", "").strip()

if not WIALON_TOKEN:
    print("[FATAL] WIALON_TOKEN is not set in .env")
    sys.exit(1)
if not WIALON_API:
    print("[FATAL] WIALON_API is not set in .env")
    sys.exit(1)

_OUTPUT_DIR           = _SCRIPT_DIR
INSPECTION_JSON_PATH  = os.path.join(_OUTPUT_DIR, "wialon_data_inspection.json")
SUMMARY_JSON_PATH     = os.path.join(_OUTPUT_DIR, "wialon_data_summary.json")

WIALON_INVALID = -348201.3876

# =============================================================================
# LOW-LEVEL WIALON HTTP HELPERS
# =============================================================================

def _wialon_post(svc, params, sid=None):
    payload = {"svc": svc, "params": json.dumps(params)}
    if sid:
        payload["sid"] = sid
    for attempt in range(3):
        try:
            resp = requests.post(WIALON_API, data=payload, timeout=30)
            return resp.json()
        except Exception as exc:
            if attempt == 2:
                return {"error": -1, "reason": str(exc)}
            time.sleep(0.5 * (attempt + 1))
    return {"error": -1, "reason": "max retries exceeded"}


def wialon_login():
    result = _wialon_post("token/login", {"token": WIALON_TOKEN})
    if "error" in result:
        raise RuntimeError("Wialon login failed: {}".format(result))
    sid = result.get("eid")
    if not sid:
        raise RuntimeError("No session ID in login response: {}".format(result))
    return sid


def wialon_request(svc, params, sid):
    result = _wialon_post(svc, params, sid)
    if isinstance(result, dict) and result.get("error") == 1:
        new_sid = wialon_login()
        result  = _wialon_post(svc, params, new_sid)
    return result


# =============================================================================
# VALUE HELPERS
# =============================================================================

def is_valid(value):
    if value is None:
        return False
    if isinstance(value, (int, float)):
        if abs(value - WIALON_INVALID) < 0.001:
            return False
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"", "---", "null", "none", "n/a", "na", "invalid", "-348201.3876"}:
            return False
    return True


def ts_to_str(ts):
    if not is_valid(ts):
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


# =============================================================================
# WIALON DATA FETCH FUNCTIONS
# =============================================================================

UNIT_FLAGS = 1 + 2 + 8 + 256 + 512 + 1024 + 4096 + 8192 + 16384 + 32768


def fetch_all_units(sid):
    print("  -> Fetching all units from Wialon ...")
    result = wialon_request(
        "core/search_items",
        {
            "spec": {
                "itemsType":     "avl_unit",
                "propName":      "sys_name",
                "propValueMask": "*",
                "sortType":      "sys_name"
            },
            "force": 1,
            "flags": UNIT_FLAGS,
            "from":  0,
            "to":    0
        },
        sid
    )
    if "error" in result:
        print("  [ERROR] fetch_all_units: {}".format(result))
        return []
    return result.get("items", [])


def fetch_all_groups(sid):
    print("  -> Fetching all unit groups ...")
    result = wialon_request(
        "core/search_items",
        {
            "spec": {
                "itemsType":     "avl_unit_group",
                "propName":      "sys_name",
                "propValueMask": "*",
                "sortType":      "sys_name"
            },
            "force": 1,
            "flags": 1,
            "from":  0,
            "to":    0
        },
        sid
    )
    if "error" in result:
        print("  [ERROR] fetch_all_groups: {}".format(result))
        return []
    return result.get("items", [])


def fetch_calculated_last(sid, unit_ids):
    if not unit_ids:
        return {}
    print("  -> Fetching calc_last for {} units ...".format(len(unit_ids)))
    result = wialon_request("unit/calc_last", {"itemIds": unit_ids}, sid)
    if isinstance(result, list):
        return {item.get("i"): item for item in result if isinstance(item, dict)}
    if isinstance(result, dict) and "error" in result:
        print("  [ERROR] fetch_calculated_last: {}".format(result))
    return {}


# =============================================================================
# SENSOR DECODING
# =============================================================================

SENSOR_TYPES_MAP = {
    0:  "Custom",
    1:  "Digital inputs",
    2:  "Analog inputs",
    4:  "Fuel level",
    5:  "Temperature",
    7:  "Ignition",
    8:  "Engine revolution",
    9:  "Useful mileage",
    10: "Odometer",
    11: "Engine hours counter",
    12: "Engine state",
    13: "Engine efficiency",
    14: "Alarm mode",
    15: "Harsh acceleration",
    16: "Harsh braking",
    17: "Harsh turning",
    19: "Voltage",
    20: "Weight",
    21: "Driver code",
    22: "LLS fuel level",
    23: "Fuel use counter",
    27: "Input voltage",
    28: "GSM signal",
    29: "RFID reader",
    30: "Speed",
    31: "Digital outputs",
    33: "Private mode",
    35: "External power voltage",
    36: "Eco score",
    37: "Crash detection",
    40: "TPMS",
    41: "Fatigue driving",
    42: "Driving style",
    44: "Coolant temperature",
    48: "Door",
    55: "RPM",
    56: "Accelerometer",
}


def decode_sensor(sensor_id, scfg, calc_sensors):
    raw_t = scfg.get("t")
    if isinstance(raw_t, int):
        type_label = SENSOR_TYPES_MAP.get(raw_t, "Type #{}".format(raw_t))
    elif isinstance(raw_t, str):
        type_label = raw_t.title()
    else:
        type_label = "Unknown"

    calc       = calc_sensors.get(str(sensor_id)) or {}
    raw_value  = calc.get("value")
    fmt        = calc.get("format") or {}
    fmt_value  = fmt.get("value")
    fmt_unit   = fmt.get("units", scfg.get("m", ""))

    return {
        "id":               sensor_id,
        "name":             scfg.get("n", ""),
        "type_id":          raw_t,
        "type_label":       type_label,
        "source_parameter": scfg.get("p", ""),
        "unit":             scfg.get("m", ""),
        "description":      scfg.get("d", ""),
        "formula":          scfg.get("f", ""),
        "validation":       scfg.get("vt"),
        "raw_value":        raw_value if is_valid(raw_value) else None,
        "formatted_value":  fmt_value,
        "formatted_unit":   fmt_unit,
        "has_value":        is_valid(raw_value),
    }


# =============================================================================
# PARAMETER DECODING
# =============================================================================

def decode_params(prms):
    if not prms or not isinstance(prms, dict):
        return []
    TYPE_MAP = {1: "double", 2: "long", 3: "string"}
    out = []
    for name, pobj in prms.items():
        if isinstance(pobj, dict):
            t = pobj.get("t")
            v = pobj.get("v")
            type_label = TYPE_MAP.get(t, "type_{}".format(t))
        else:
            v = pobj
            t = None
            type_label = type(pobj).__name__
        out.append({
            "name":       name,
            "value":      v,
            "type_id":    t,
            "type_label": type_label,
        })
    out.sort(key=lambda x: x["name"])
    return out


# =============================================================================
# UNIT INSPECTOR
# =============================================================================

def inspect_unit(unit, calc, groups_map):
    uid  = unit.get("id")
    name = unit.get("nm", "")

    basic = {
        "unit_id":       uid,
        "unit_name":     name,
        "unit_class":    unit.get("cls"),
        "hardware_type": unit.get("hw"),
        "imei":          unit.get("uid"),
        "imei_2":        unit.get("uid2"),
        "phone":         unit.get("ph"),
        "phone2":        unit.get("ph2"),
        "groups":        groups_map.get(uid, []),
    }

    custom_fields = {}
    for fid, fobj in (unit.get("flds") or {}).items():
        if isinstance(fobj, dict):
            custom_fields[fobj.get("n", fid)] = fobj.get("v")

    admin_fields = {}
    for fid, fobj in (unit.get("aflds") or {}).items():
        if isinstance(fobj, dict):
            admin_fields[fobj.get("n", fid)] = fobj.get("v")

    properties = unit.get("prp") or {}

    # Position
    pos_src = (calc or {}).get("pos") or unit.get("pos") or {}
    lat     = pos_src.get("y")
    lon     = pos_src.get("x")
    spd     = pos_src.get("s")
    course  = pos_src.get("c")
    alt_raw = pos_src.get("z")
    sats    = pos_src.get("sc")

    # Timestamp from unit pos or lmsg
    pos_ts = (
        unit.get("pos", {}).get("t")
        or (calc or {}).get("pos", {}).get("t")
        or unit.get("lmsg", {}).get("t")
    )

    if isinstance(spd, dict):
        spd = spd.get("value")
    if isinstance(alt_raw, dict):
        alt_raw = alt_raw.get("value")

    position = {
        "latitude":             lat if is_valid(lat) else None,
        "longitude":            lon if is_valid(lon) else None,
        "speed_kmh":            spd if is_valid(spd) else None,
        "course_deg":           course if is_valid(course) else None,
        "altitude_m":           alt_raw if is_valid(alt_raw) else None,
        "satellites":           sats,
        "pos_timestamp":        ts_to_str(pos_ts),
        "pos_timestamp_raw":    pos_ts,
        "has_valid_position":   is_valid(lat) and is_valid(lon),
    }

    # Last message timestamp
    lmsg = unit.get("lmsg") or (calc or {}).get("lmsg") or {}
    lmsg_ts = lmsg.get("t") if isinstance(lmsg, dict) else lmsg
    position["last_message_at"]     = ts_to_str(lmsg_ts)
    position["last_message_ts_raw"] = lmsg_ts

    # Sensors
    configured_sensors = unit.get("sens") or {}
    calc_sensors       = (calc or {}).get("sensors") or {}
    sensors = []
    for sid_key, scfg in configured_sensors.items():
        if not scfg:
            continue
        sensors.append(decode_sensor(sid_key, scfg, calc_sensors))
    sensors.sort(key=lambda s: s["name"].lower())

    # Raw telemetry parameters (from unit last message and calc prms)
    lmsg_prms  = unit.get("lmsg", {}).get("p") or {}
    calc_prms  = (calc or {}).get("prms") or {}
    unit_prms  = unit.get("prms") or {}

    merged_prms = {}
    if isinstance(lmsg_prms, dict):
        merged_prms.update(lmsg_prms)
    if isinstance(calc_prms, dict):
        merged_prms.update(calc_prms)
    if isinstance(unit_prms, dict):
        merged_prms.update(unit_prms)

    parameters = decode_params(merged_prms)

    # Counters / Mileage
    def _extract_counter(c, key):
        raw = (c or {}).get(key)
        if isinstance(raw, dict):
            return raw.get("value")
        return raw

    mileage      = _extract_counter(calc, "mileage")
    engine_hours = _extract_counter(calc, "engine_hours")
    counters = {
        "mileage_km":         mileage      if is_valid(mileage)      else None,
        "engine_hours":       engine_hours if is_valid(engine_hours) else None,
        "hw_mileage_m":       unit.get("cnm"),
        "hw_engine_hours_s":  unit.get("cneh"),
        "hw_traffic_kb":      unit.get("cnkb"),
    }

    # Status
    spd_val = position["speed_kmh"]
    status_info = {
        "speed_kmh":    spd_val,
        "has_position": position["has_valid_position"],
        "moving":       (spd_val > 0) if spd_val is not None else None,
    }

    # Driver from calc_last drvrs or drvr_code parameter
    driver_raw  = (calc or {}).get("drvrs") or []
    drivers_out = []
    if isinstance(driver_raw, list):
        for drv in driver_raw:
            if isinstance(drv, dict):
                drivers_out.append({
                    "driver_id":   drv.get("id"),
                    "driver_name": drv.get("n"),
                    "from":        ts_to_str(drv.get("from")),
                    "to":          ts_to_str(drv.get("to")),
                })

    known_calc_keys = {"i", "pos", "sensors", "prms", "mileage", "engine_hours", "drvrs", "lmsg"}
    raw_calc_extras = {k: (calc or {}).get(k) for k in (calc or {}).keys() if k not in known_calc_keys}

    return {
        "basic":             basic,
        "custom_fields":     custom_fields,
        "admin_fields":      admin_fields,
        "properties":        properties,
        "position":          position,
        "status":            status_info,
        "sensors":           sensors,
        "parameters":        parameters,
        "counters":          counters,
        "drivers":           drivers_out,
        "raw_calc_extras":   raw_calc_extras,
        "_raw_unit_keys":    sorted(unit.keys()),
        "_raw_calc_keys":    sorted((calc or {}).keys()),
    }


# =============================================================================
# TERMINAL REPORT
# =============================================================================

SEP  = "=" * 70
DASH = "-" * 50


def _pf(label, value):
    print("    {:<30}  {}".format(label, value))


def print_unit_report(rec, index, total):
    basic   = rec["basic"]
    pos     = rec["position"]
    sensors = rec["sensors"]
    params  = rec["parameters"]
    counters= rec["counters"]
    status  = rec["status"]
    drivers = rec["drivers"]

    print("\n{}".format(DASH))
    print("UNIT {}/{}:  {}  (ID: {})".format(index, total, basic["unit_name"], basic["unit_id"]))
    print(DASH)

    print("\n  [Basic Information]")
    _pf("Unit ID",          basic["unit_id"])
    _pf("Name",             basic["unit_name"])
    _pf("IMEI / Device UID",basic["imei"] or "-")
    _pf("Hardware type ID", basic["hardware_type"] or "-")
    _pf("Phone",            basic["phone"] or "-")
    _pf("Phone 2",          basic["phone2"] or "-")
    _pf("Groups",           ", ".join(basic["groups"]) if basic["groups"] else "-")

    if rec["custom_fields"]:
        print("\n  [Custom Fields]")
        for k, v in rec["custom_fields"].items():
            _pf(k, v)

    if rec["admin_fields"]:
        print("\n  [Admin Fields]")
        for k, v in rec["admin_fields"].items():
            _pf(k, v)

    print("\n  [Position]")
    _pf("Latitude",         pos["latitude"])
    _pf("Longitude",        pos["longitude"])
    _pf("Speed (km/h)",     pos["speed_kmh"])
    _pf("Course (deg)",     pos["course_deg"])
    _pf("Altitude (m)",     pos["altitude_m"])
    _pf("Satellites",       pos["satellites"])
    _pf("Position time",    pos["pos_timestamp"] or "-")
    _pf("Last message",     pos["last_message_at"] or "-")
    _pf("Valid position",   pos["has_valid_position"])

    print("\n  [Status]")
    _pf("Moving",           status["moving"])
    _pf("Speed (km/h)",     status["speed_kmh"])

    print("\n  [Counters / Mileage]")
    _pf("Calculated Mileage (km)",  counters["mileage_km"])
    _pf("Engine Hours",             counters["engine_hours"])
    _pf("Hardware Mileage (m)",     counters["hw_mileage_m"])
    _pf("Hardware Eng. Hours (s)",  counters["hw_engine_hours_s"])
    _pf("GPRS Traffic (KB)",        counters["hw_traffic_kb"])

    if drivers:
        print("\n  [Drivers]")
        for d in drivers:
            print("    - {} (ID: {})  from: {}  to: {}".format(
                d["driver_name"], d["driver_id"], d["from"] or "?", d["to"] or "now"))
    else:
        print("\n  [Drivers]  (none assigned via resource)")

    print("\n  [Sensors]  ({} configured)".format(len(sensors)))
    if sensors:
        for i, s in enumerate(sensors, 1):
            if s["has_value"]:
                val_str = "{}".format(s["raw_value"])
                if s["formatted_value"] is not None:
                    val_str += " ({} {})".format(s["formatted_value"], s["formatted_unit"])
            else:
                val_str = "-"
            print("    {:2d}. {}".format(i, s["name"]))
            print("         ID: {}  |  Type: {}  |  Source Param: {}".format(
                s["id"], s["type_label"], s["source_parameter"] or "-"))
            print("         Unit: {}  |  Description: {}".format(
                s["unit"] or "-", s["description"] or "-"))
            print("         Current Value: {}".format(val_str))
    else:
        print("    (no sensors configured)")

    print("\n  [Telemetry Parameters]  ({} available)".format(len(params)))
    if params:
        for p in params:
            print("    {:<25}  {:<25}  [{}]".format(
                p["name"], str(p["value"]), p["type_label"]))
    else:
        print("    (no parameters returned)")


# =============================================================================
# SUMMARY BUILDER
# =============================================================================

def build_summary(inspected, groups):
    all_param_names   = set()
    all_sensor_names  = set()
    all_sensor_types  = set()
    units_with_pos    = 0
    units_moving      = 0
    all_unit_keys     = set()
    all_calc_keys     = set()
    sensor_type_dist  = {}
    param_dist        = {}

    for rec in inspected:
        for p in rec["parameters"]:
            all_param_names.add(p["name"])
            param_dist[p["name"]] = param_dist.get(p["name"], 0) + 1
        for s in rec["sensors"]:
            all_sensor_names.add(s["name"])
            all_sensor_types.add(s["type_label"])
            sensor_type_dist[s["type_label"]] = sensor_type_dist.get(s["type_label"], 0) + 1
        if rec["position"]["has_valid_position"]:
            units_with_pos += 1
        if rec["status"]["moving"]:
            units_moving += 1
        all_unit_keys.update(rec["_raw_unit_keys"])
        all_calc_keys.update(rec["_raw_calc_keys"])

    group_details = []
    for g in groups:
        group_details.append({
            "group_id":   g.get("id"),
            "group_name": g.get("nm"),
            "unit_count": len(g.get("u", [])),
            "unit_ids":   g.get("u", []),
        })

    # Compare with existing application usage
    # Existing app uses:
    # Sensors: ignition, battery, odometer, harsh acceleration, harsh braking, harsh cornering, crash detection, driver id
    # Parameters: not directly parsed except via sensors
    CURRENTLY_USED_SENSORS = {
        "ignition", "battery", "odometer", "driver id",
        "harsh acceleration", "harsh braking", "harsh cornering", "crash detection"
    }

    unused_sensors = [
        s for s in sorted(all_sensor_names)
        if s.lower() not in CURRENTLY_USED_SENSORS
    ]

    return {
        "units_discovered":              len(inspected),
        "units_with_valid_position":     units_with_pos,
        "units_moving":                  units_moving,
        "groups_discovered":             group_details,
        "sensor_names_discovered":       sorted(all_sensor_names),
        "sensor_types_discovered":       sorted(all_sensor_types),
        "sensor_type_distribution":      sensor_type_dist,
        "parameter_names_discovered":    sorted(all_param_names),
        "parameter_distribution":        param_dist,
        "discovered_sensors_not_in_dashboard": unused_sensors,
        "raw_unit_object_keys":          sorted(all_unit_keys),
        "raw_calc_object_keys":          sorted(all_calc_keys),
        "inspection_timestamp":          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# =============================================================================
# MAIN
# =============================================================================

def main():
    print()
    print(SEP)
    print("  WIALON DATA INSPECTOR")
    print("  ATS Fleet Management -- Read-Only Discovery")
    print(SEP)
    print("\n  API:   {}".format(WIALON_API))
    print("  Token: {}...{}".format(WIALON_TOKEN[:12], WIALON_TOKEN[-8:]))
    print()

    print("[1/5] Authenticating with Wialon ...")
    try:
        sid = wialon_login()
        print("      OK  Session ID: {}...{}".format(sid[:8], sid[-6:]))
    except RuntimeError as exc:
        print("\n  [FATAL] {}".format(exc))
        sys.exit(1)

    print("\n[2/5] Fetching unit groups ...")
    groups = fetch_all_groups(sid)
    print("      {} group(s) found".format(len(groups)))

    groups_map = {}
    for g in groups:
        gname = g.get("nm", "").strip()
        for uid in g.get("u", []):
            groups_map.setdefault(uid, [])
            if gname and gname not in groups_map[uid]:
                groups_map[uid].append(gname)

    print("\n[3/5] Fetching all units ...")
    units = fetch_all_units(sid)
    print("      {} unit(s) found".format(len(units)))

    if not units:
        print("\n  [WARNING] No units returned. Check token permissions.")
        sys.exit(0)

    print("\n[4/5] Fetching calculated/live data (batch) ...")
    unit_ids  = [u.get("id") for u in units if u.get("id")]
    calc_map  = fetch_calculated_last(sid, unit_ids)
    print("      calc_last data received for {} unit(s)".format(len(calc_map)))

    print("\n[5/5] Inspecting {} unit(s) ...\n".format(len(units)))
    print(SEP)
    print("  UNIT INSPECTION REPORT")
    print(SEP)

    inspected_records = []
    for idx, unit in enumerate(units, 1):
        uid  = unit.get("id")
        calc = calc_map.get(uid)
        rec  = inspect_unit(unit, calc, groups_map)
        inspected_records.append(rec)
        print_unit_report(rec, idx, len(units))

    print("\n{}".format(SEP))
    print("  GROUPS SUMMARY")
    print(SEP)
    if groups:
        for g in groups:
            unit_count = len(g.get("u", []))
            print("  * [{}]  {}  --  {} unit(s)".format(
                g.get("id"), g.get("nm", "(unnamed)"), unit_count))
    else:
        print("  (no groups found)")

    print("\n{}".format(SEP))
    print("  SAVING OUTPUT FILES")
    print(SEP)

    try:
        with open(INSPECTION_JSON_PATH, "w", encoding="utf-8") as fh:
            json.dump({"units": inspected_records, "groups": groups}, fh, indent=2, default=str)
        print("\n  OK  Raw inspection saved:  {}".format(INSPECTION_JSON_PATH))
    except Exception as exc:
        print("\n  [ERROR] Could not save inspection JSON: {}".format(exc))

    summary = build_summary(inspected_records, groups)

    try:
        with open(SUMMARY_JSON_PATH, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        print("  OK  Summary saved:         {}".format(SUMMARY_JSON_PATH))
    except Exception as exc:
        print("  [ERROR] Could not save summary JSON: {}".format(exc))

    print("\n{}".format(SEP))
    print("  DISCOVERY SUMMARY")
    print(SEP)
    _pf("Units discovered",              summary["units_discovered"])
    _pf("Units with valid GPS",          summary["units_with_valid_position"])
    _pf("Units currently moving",        summary["units_moving"])
    _pf("Groups discovered",             len(summary["groups_discovered"]))
    _pf("Unique sensor names",           len(summary["sensor_names_discovered"]))
    _pf("Unique sensor types",           len(summary["sensor_types_discovered"]))
    _pf("Unique raw parameters found",   len(summary["parameter_names_discovered"]))

    print("\n  Sensor types found:")
    for stype, count in sorted(summary["sensor_type_distribution"].items()):
        print("    * {:<30}  ({} instance(s))".format(stype, count))

    print("\n  Sensors discovered that are NOT in current dashboard:")
    for s in summary["discovered_sensors_not_in_dashboard"]:
        print("    * {}".format(s))

    print("\n  Telemetry parameters discovered across units:")
    sorted_params = sorted(summary["parameter_distribution"].items(), key=lambda x: -x[1])
    for pname, count in sorted_params:
        print("    * {:<25}  ({} unit(s))".format(pname, count))

    print("\n{}".format(SEP))
    print("  INSPECTION COMPLETE")
    print("{}\n".format(SEP))


if __name__ == "__main__":
    main()
