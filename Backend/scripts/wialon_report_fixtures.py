"""
In-memory fake of Wialon's report engine for offline tests.

Replaces app.wialon_reports.wialon_request. It serves report/exec_report, report/get_result_rows,
report/get_result_subrows and report/cleanup_result from fixture tables shaped like the recorded
template headers (see comments in app/wialon_reports.py and scripts/audit_wialon_distance.py).
Cells are plain strings for durations/mileage and dicts for time/location, as production returns.
"""

import copy

TEMPLATE_UNIT_ENGINE = 1
TEMPLATE_GROUP_ENGINE = 2
TEMPLATE_UNIT_TRIPS = 4
TEMPLATE_GROUP_TRIPS = 5

UNIT_TRIPS_HEADER = ["Grouping", "Beginning", "Initial location", "End", "Final location",
                     "Total time", "Engine hours", "Avg speed", "Max speed", "Mileage"]
UNIT_ENGINE_HEADER = ["Grouping", "Beginning", "Initial location", "End", "Final location",
                      "Total time", "Engine hours", "In motion", "Idling", "Mileage"]
GROUP_TRIPS_HEADER = ["Grouping", "Beginning", "Initial location", "End", "Final location",
                      "Mileage", "Total time", "Engine hours", "Avg. speed", "Max. speed", "Driver"]
GROUP_ENGINE_HEADER = ["Grouping", "Beginning", "Initial location", "End", "Final location",
                       "Total time", "Engine hours", "In motion", "Idling", "Mileage", "Avg. speed", "Max. speed"]

BASE_TS = 1785715200  # 2026-08-03 00:00:00 UTC


def _t(ts, label):
    return {"t": label, "v": ts}


def _loc(label, x, y):
    return {"t": label, "x": x, "y": y}


def unit_trip_row(i, start, minutes, engine, km):
    end = start + minutes * 60
    return {"c": [
        str(i), _t(start, f"start-{i}"), _loc(f"From {i}", 55.27 + i / 100, 25.20 + i / 100),
        _t(end, f"end-{i}"), _loc(f"To {i}", 55.30 + i / 100, 25.25 + i / 100),
        f"0:{minutes:02d}:00", engine, f"{30 + i} km/h", {"t": f"{80 + i} km/h", "v": 80 + i}, f"{km:.2f} km",
    ], "d": 0, "t1": start, "t2": end}


def unit_engine_row(i, start, total, engine, motion, idling, km, subrows=0):
    return {"c": [
        str(i), _t(start, f"start-{i}"), _loc(f"Yard {i}", 55.1, 25.1), _t(start + 3600, f"end-{i}"),
        _loc(f"Yard {i}b", 55.2, 25.2), total, engine, motion, idling, f"{km:.2f} km",
    ], "d": subrows, "t1": start, "t2": start + 3600}


def unit_engine_subrow(j, start, idle_seconds):
    h, rem = divmod(idle_seconds, 3600)
    m, s = divmod(rem, 60)
    return {"c": [
        str(j), _t(start, f"s-{j}"), _t(start + idle_seconds, f"e-{j}"), _loc(f"Idle spot {j}", 55.3 + j / 1000, 25.3),
        _loc("x", 0, 0), "0:10:00", "0:10:00", "0:00:00", f"{h}:{m:02d}:{s:02d}",
    ], "t1": start, "t2": start + idle_seconds}


def group_trip_row(name, km, total="10:00:00", engine="8:00:00", avg="45 km/h", mx="110 km/h", driver="Driver"):
    return {"c": [name, _t(BASE_TS, "b"), _loc("A", 55, 25), _t(BASE_TS + 86000, "e"), _loc("B", 55, 25),
                  f"{km:.2f} km", total, engine, avg, mx, driver], "d": 0}


def group_engine_row(name, total, engine, motion, idling, km, subrows=0, t1=BASE_TS, t2=BASE_TS + 86000):
    return {"c": [name, _t(t1, "b"), _loc(f"{name} start", 55.27, 25.20), _t(t2, "e"), _loc(f"{name} end", 55.28, 25.21),
                  total, engine, motion, idling, f"{km:.2f} km", "40 km/h", "100 km/h"], "d": subrows, "t1": t1, "t2": t2}


def group_engine_subrow(j, start, idle_seconds):
    h, rem = divmod(idle_seconds, 3600)
    m, s = divmod(rem, 60)
    return {"c": [str(j), _t(start, f"s-{j}"), _loc(f"Hotspot {j % 3}", 55.40 + (j % 3) / 100, 25.10),
                  _t(start + idle_seconds, f"e-{j}"), _loc("x", 0, 0), "0:30:00", "0:30:00", "0:00:00",
                  f"{h}:{m:02d}:{s:02d}"], "t1": start, "t2": start + idle_seconds}


def default_tables():
    """Fixture tables kept below the current row caps so outputs can be characterized exactly."""
    unit_trips = [unit_trip_row(i, BASE_TS + i * 7200, 30 + i * 5, f"0:{25 + i * 5:02d}:00", 10.5 * i) for i in range(1, 4)]
    unit_engine_rows = [
        unit_engine_row(1, BASE_TS, "8:00:00", "5:00:00", "4:00:00", "1:00:00", 120.0, subrows=2),
        unit_engine_row(2, BASE_TS + 86400, "6:00:00", "3:30:00", "3:00:00", "0:30:00", 80.0, subrows=1),
        unit_engine_row(3, BASE_TS + 2 * 86400, "2:00:00", "0:00:00", "0:00:00", "0:00:00", 0.0, subrows=0),
    ]
    unit_engine_subrows = {
        0: [unit_engine_subrow(0, BASE_TS + 600, 1800), unit_engine_subrow(1, BASE_TS + 5000, 1800)],
        1: [unit_engine_subrow(2, BASE_TS + 86400 + 900, 1800)],
    }
    group_trips = [group_trip_row("UNIT-A", 250.75, driver="Ali"), group_trip_row("UNIT-B", 1234.5, driver=""),
                   group_trip_row("UNIT-C", 0.0)]
    group_engine_rows = [
        group_engine_row("UNIT-A", "24:00:00", "6:00:00", "4:30:00", "1:30:00", 180.25, subrows=3),
        group_engine_row("UNIT-B", "24:00:00", "9:15:30", "8:00:00", "1:15:30", 640.0, subrows=0),  # idle, no subrows
        group_engine_row("UNIT-C", "24:00:00", "0:00:00", "0:00:00", "0:00:00", 0.0, subrows=0),
    ]
    group_engine_subrows = {0: [group_engine_subrow(j, BASE_TS + 3600 * (j + 1), 1800) for j in range(3)]}
    return {
        TEMPLATE_UNIT_TRIPS: {"header": UNIT_TRIPS_HEADER, "rows": unit_trips, "subrows": {}},
        TEMPLATE_UNIT_ENGINE: {"header": UNIT_ENGINE_HEADER, "rows": unit_engine_rows, "subrows": unit_engine_subrows},
        TEMPLATE_GROUP_TRIPS: {"header": GROUP_TRIPS_HEADER, "rows": group_trips, "subrows": {}},
        TEMPLATE_GROUP_ENGINE: {"header": GROUP_ENGINE_HEADER, "rows": group_engine_rows, "subrows": group_engine_subrows},
    }


class FakeReportEngine:
    """
    Callable replacement for wialon_request covering the report/* services.
    Options:
      include_header     - send tables[0]["header"] (production does; set False to test fallback)
      max_rows_per_call  - simulate a server returning fewer rows than requested
      fail_exec          - set of template ids whose exec_report returns an error
      empty_tables       - set of template ids whose exec_report returns no tables
    """

    def __init__(self, tables=None, include_header=True, max_rows_per_call=None, fail_exec=None, empty_tables=None,
                 subrow_error_rows=None):
        self.tables = tables if tables is not None else default_tables()
        self.include_header = include_header
        self.max_rows_per_call = max_rows_per_call
        self.fail_exec = set(fail_exec or [])
        self.empty_tables = set(empty_tables or [])
        self.subrow_error_rows = set(subrow_error_rows or [])  # row indexes whose sub-rows return an error
        self.current = None
        self.calls = []

    def __call__(self, svc, params, sid=None, timeout=None, **kwargs):
        self.calls.append((svc, copy.deepcopy(params)))
        if svc == "report/exec_report":
            tid = params["reportTemplateId"]
            if tid in self.fail_exec:
                return {"error": 5}
            self.current = self.tables.get(tid)
            if tid in self.empty_tables or not self.current or not self.current["rows"]:
                return {"reportResult": {"tables": [], "stats": []}}
            table = {"rows": len(self.current["rows"]), "name": f"t{tid}", "total": []}
            if self.include_header:
                table["header"] = list(self.current["header"])
            return {"reportResult": {"tables": [table], "stats": []}}
        if svc == "report/get_result_rows":
            rows = self.current["rows"]
            lo, hi = params["indexFrom"], params["indexTo"]
            page = rows[lo:hi + 1]
            if self.max_rows_per_call is not None:
                page = page[: self.max_rows_per_call]
            return copy.deepcopy(page)
        if svc == "report/get_result_subrows":
            if params["rowIndex"] in self.subrow_error_rows:
                return {"error": 5}
            subs = self.current["subrows"].get(params["rowIndex"], [])
            return copy.deepcopy(subs[params["indexFrom"]:params["indexTo"] + 1])
        if svc == "report/cleanup_result":
            return {}
        raise AssertionError(f"Unexpected Wialon service in fake: {svc}")

    def count(self, svc):
        return sum(1 for s, _ in self.calls if s == svc)


# ─── Non-report fleet fixtures for endpoint-level characterization ─────────────

UNITS = [
    {"id": 101, "nm": "UNIT-A", "sens": {}},
    {"id": 102, "nm": "UNIT-B", "sens": {}},
    {"id": 103, "nm": "UNIT-C", "sens": {}},
]
GROUPS = [
    {"id": 1, "nm": "All Unit", "u": [101, 102, 103]},
    {"id": 7, "nm": "Logistics", "u": [101, 102]},
    {"id": 8, "nm": "Contracts", "u": [103]},
]
CALCULATED = [{"i": 101, "mileage": {"value": 120345.6}}, {"i": 102, "mileage": {"value": 88000.0}}, {"i": 103}]


def live_records():
    statuses = {101: "IDLING", 102: "MOVING", 103: "STOPPED"}
    return [
        {
            "vehicle_id": u["id"], "vehicle_name": u["nm"], "group": "Logistics" if u["id"] != 103 else "Contracts",
            "status": statuses[u["id"]], "connection_status": "STABLE", "is_offline": False,
            "idle_duration": 600 if u["id"] == 101 else 0, "idle_start": None,
            "latitude": 25.2, "longitude": 55.27, "address": f"Street {u['id']}",
            "registration_plate": f"DXB {u['id']}",
        }
        for u in UNITS
    ]


def normalize_for_compare(obj):
    """Sort lists of plain strings (they come from set iteration, which varies between processes)."""
    if isinstance(obj, dict):
        return {k: normalize_for_compare(v) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [normalize_for_compare(v) for v in obj]
        if items and all(isinstance(v, str) for v in items):
            return sorted(items)
        return items
    return obj
