"""
Regression tests for the Idle Event Details cards: Driver, Group and Department (MOCKED / offline).

  - the driver is the one bound to the event's vehicle at the event start in Wialon's binding history (resource
    600441379), looked up by driver id; the phone is returned only when that driver's name agrees with the event's
    Idle Report driver name
  - no phone for: no driver name (no Wialon call), no binding, two drivers bound, a driver unbound before the event,
    a driver record missing, a name mismatch; a missing phone is None, never invented
  - a same-named driver with another id never supplies the phone
  - events carry the vehicle's actual Wialon groups (never the "General Fleet" fallback) and the department from the
    unit Profile field vehicle_type (not the template's Department column); missing values are empty / None
  - the fleet idle events response never contains a phone number

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_idle_event_details.py
"""
import os
import sys
import time
import traceback
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402
from test_idle_events_template import FakeIdleReport, GROUPS, UNIT_A, UNIT_B, UNIT_C, live, reset, subrow  # noqa: E402

from fastapi import HTTPException  # noqa: E402

import app.idle_events as ie  # noqa: E402
import app.main as main  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402
from app.wialon_reports import IDLE_REPORT_RESOURCE_ID  # noqa: E402

UNIT = 600446530
AT = 1_793_600_000
PHONE_ASHRAF, PHONE_OTHER = "+971500000001", "+971500000002"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


class FakeDriverWialon:
    """Wialon stand-in for resource/get_driver_bindings and the resource's driver records."""

    def __init__(self, bindings, drivers):
        self.bindings = bindings
        self.drivers = drivers
        self.calls = []

    def __call__(self, svc, params, sid=None, **kwargs):
        self.calls.append((svc, params))
        if svc == "resource/get_driver_bindings":
            assert params["resourceId"] == IDLE_REPORT_RESOURCE_ID and params["unitId"] == UNIT
            assert params["timeTo"] == AT and params["timeFrom"] < AT
            return self.bindings
        if svc == "core/search_item":
            assert params["id"] == IDLE_REPORT_RESOURCE_ID and params["flags"] & 0x100
            return {"item": {"id": IDLE_REPORT_RESOURCE_ID, "drvrs": {str(d["id"]): d for d in self.drivers}}}
        raise AssertionError(f"unexpected Wialon call {svc}")


# Wialon driver records as returned live: `id` is the resource-local record number binding history uses, the Driver ID
# shown in Wialon is the custom field `jp.ID`, and `c` is the driver-card code
ASHRAF = {"id": 54, "n": "Ashraf Kallullathil", "p": PHONE_ASHRAF, "c": "0158BE4201000054", "jp": {"ID": "101183"}}
ANIL = {"id": 41, "n": "Anil Kumar Pudari", "p": PHONE_OTHER, "c": "0158BE4201000041", "jp": {"ID": "142738"}}
SARWAN = {"id": 70, "n": "Sarwan Singh Jaswant Singh", "p": "+971500000070", "c": "0158BE4201000013", "jp": {"ID": "100380"}}


def resolve(fake, name="Ashraf Kallullathil"):
    with patch("app.idle_events.wialon_request", fake), patch("app.main.login", return_value="sid"):
        return main.get_idle_event_driver(vehicle_id=UNIT, start_time=AT, driver_name=name)


def test_driver_resolution():
    # Anil was bound earlier and unbound; Ashraf bound before the event (later entries after AT are ignored)
    bindings = {"41": [{"t": AT - 9000, "u": UNIT}, {"t": AT - 8000, "u": 0}],
                "54": [{"t": AT - 3600, "u": UNIT}, {"t": AT - 60, "u": UNIT}, {"t": AT + 600, "u": 0}]}
    res = resolve(FakeDriverWialon(bindings, [ASHRAF, ANIL]))
    check(res["status"] == "matched" and res["driver_name"] == "Ashraf Kallullathil",
          "the driver bound to the vehicle at the event start is resolved from the binding history")
    check(res["phone"] == PHONE_ASHRAF, "the phone comes from that driver's record")
    check(res["driver_id"] == "101183", "the Driver ID is the matched record's Driver ID custom field, not its record number")

    # A same-named record with another id must never supply the phone: the record is found by the bound driver id
    twin = {"id": 99, "n": "Ashraf Kallullathil", "p": PHONE_OTHER, "jp": {"ID": "999999"}}
    res = resolve(FakeDriverWialon({"54": [{"t": AT - 60, "u": UNIT}]}, [twin, ASHRAF]))
    check(res["phone"] == PHONE_ASHRAF and res["driver_id"] == "101183",
          "the driver record (phone and id) is looked up by the bound driver id, not by name")

    res = resolve(FakeDriverWialon({"54": [{"t": AT - 60, "u": UNIT}]}, [ASHRAF]), name="  ashraf   KALLULLATHIL ")
    check(res["status"] == "matched", "the name check ignores case and spacing")


def test_unreliable_matches_return_no_phone():
    res = resolve(FakeDriverWialon({"54": [{"t": AT - 60, "u": UNIT}]}, [ASHRAF]), name="Anil Kumar Pudari")
    check(res["status"] == "name_mismatch" and res["phone"] is None and res["driver_name"] is None and res["driver_id"] is None,
          "a binding that disagrees with the report's driver name returns no phone")

    fake = FakeDriverWialon({}, [ASHRAF])
    res = resolve(fake, name="")
    check(res["status"] == "no_driver_name" and res["phone"] is None and not fake.calls,
          "an event without a driver name returns nothing and makes no Wialon call")

    res = resolve(FakeDriverWialon({"54": [], "41": [{"t": AT - 100, "u": 0}]}, [ASHRAF]))
    check(res["status"] == "no_binding" and res["phone"] is None and res["driver_id"] is None,
          "no binding at the event start returns no phone or driver id")

    res = resolve(FakeDriverWialon({"54": [{"t": AT - 500, "u": UNIT}, {"t": AT - 10, "u": 0}]}, [ASHRAF]))
    check(res["status"] == "no_binding", "a driver unbound before the event is not its driver")

    res = resolve(FakeDriverWialon({"54": [{"t": AT - 60, "u": UNIT}], "41": [{"t": AT - 30, "u": UNIT}]}, [ASHRAF, ANIL]))
    check(res["status"] == "ambiguous" and res["phone"] is None and res["driver_id"] is None,
          "two drivers bound at once is ambiguous: no phone or driver id")

    res = resolve(FakeDriverWialon({"54": [{"t": AT - 60, "u": UNIT}]}, [ANIL]))
    check(res["status"] == "driver_not_found" and res["phone"] is None and res["driver_id"] is None,
          "a bound driver without a record returns no phone or driver id")

    res = resolve(FakeDriverWialon({"54": [{"t": AT - 60, "u": UNIT}]}, [dict(ASHRAF, p="  ")]))
    check(res["status"] == "matched" and res["phone"] is None and res["driver_id"] == "101183",
          "a matched driver without a phone keeps its driver id; phone None (not invented)")


def test_driver_ids_are_wialon_driver_ids():
    # Sarwan Singh: binding history names record 70; the Driver ID shown in Wialon is 100380
    res = resolve(FakeDriverWialon({"70": [{"t": AT - 60, "u": UNIT}]}, [ASHRAF, ANIL, SARWAN]), name="Sarwan Singh Jaswant Singh")
    check(res["status"] == "matched" and res["driver_id"] == "100380" and res["driver_id"] != "70",
          "Sarwan Singh's Driver ID is 100380 (custom field), never 70 (record number)")
    # Several drivers: each gets its own record's Driver ID; binding keys never equal the Driver IDs
    for rec in (ASHRAF, ANIL, SARWAN):
        res = resolve(FakeDriverWialon({str(rec["id"]): [{"t": AT - 60, "u": UNIT}]}, [ASHRAF, ANIL, SARWAN]), name=rec["n"])
        check(res["driver_id"] == rec["jp"]["ID"] and res["driver_id"] not in (str(rec["id"]), rec["c"], str(UNIT)),
              f"{rec['n']}: Driver ID {rec['jp']['ID']} (not record {rec['id']}, card code or vehicle id)")
    # The custom fields can arrive as a JSON string
    res = resolve(FakeDriverWialon({"70": [{"t": AT - 60, "u": UNIT}]}, [dict(SARWAN, jp='{"ID": " 100380 "}')]),
                  name="Sarwan Singh Jaswant Singh")
    check(res["driver_id"] == "100380", "a JSON-string custom field is read and trimmed")
    # A record without the Driver ID field: matched, phone kept, Driver ID unknown (never the record number)
    for jp in ({}, None, {"ID": "  "}, "not json"):
        res = resolve(FakeDriverWialon({"70": [{"t": AT - 60, "u": UNIT}]}, [dict(SARWAN, jp=jp)]), name="Sarwan Singh Jaswant Singh")
        check(res["status"] == "matched" and res["driver_id"] is None and res["phone"] == "+971500000070",
              f"no Driver ID custom field ({jp!r}): Driver ID None, never the record number")
    # A mismatched name returns neither Driver ID nor phone
    res = resolve(FakeDriverWialon({"70": [{"t": AT - 60, "u": UNIT}]}, [SARWAN]), name="Ashraf Kallullathil")
    check(res["driver_id"] is None and res["phone"] is None, "a name mismatch returns no Driver ID")


def test_driver_errors_and_validation():
    try:
        resolve(FakeDriverWialon({"error": 7}, [ASHRAF]))
        raised = False
    except WialonAPIError:
        raised = True
    check(raised, "a Wialon bindings error is an upstream failure, never 'no driver'")
    for kwargs in ({"vehicle_id": 0, "start_time": AT}, {"vehicle_id": UNIT, "start_time": 0},
                   {"vehicle_id": UNIT, "start_time": int(time.time()) + 3600}):
        try:
            main.get_idle_event_driver(driver_name="x", **kwargs)
            status = None
        except HTTPException as exc:
            status = exc.status_code
        check(status == 400, f"invalid driver lookup parameters are rejected: {kwargs}")


def pfld(name, value):
    return {"1": {"id": 1, "n": "registration_plate", "v": "P 1"}, "4": {"id": 4, "n": name, "v": value}}


UNITS = [
    {"id": UNIT_A, "nm": "58523", "sens": {"15": {"n": "Idling"}}, "pflds": pfld("vehicle_type", "Transport Dept")},
    {"id": UNIT_B, "nm": "11111", "sens": {"15": {"n": "Idling"}}, "pflds": pfld("vehicle_type", "   ")},
    {"id": UNIT_C, "nm": "22222", "sens": {"15": {"n": "Idling"}}, "pflds": pfld("brand", "Transport Dept")},
]
# UNIT_C is in no group except All Unit; give it messages today so it has events
GROUPS_WITH_UNGROUPED = [g for g in GROUPS]


def fleet(units=UNITS):
    reset()
    now = int(time.time())
    engine = FakeIdleReport({uid: [subrow(uid, now - 900, now - 600, driver="Ashraf Kallullathil")] for uid in (UNIT_A, UNIT_B, UNIT_C)})
    rows = live()
    rows[2] = dict(rows[2], seconds_since_update=30, last_message_timestamp=now - 30, status="STOPPED")
    with ExitStack() as stack:
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        stack.enter_context(patch("app.idle_events.wialon_request", engine))
        stack.enter_context(patch("app.idle_events.login", side_effect=lambda session=None: f"sid-{session}"))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_vehicle_groups", return_value=GROUPS_WITH_UNGROUPED))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=units))
        stack.enter_context(patch("app.main.get_calculated_data", return_value=[]))
        stack.enter_context(patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: rows))
        return main.get_idle_events_analytics(timeframe="today"), now


def test_group_and_department():
    res, now = fleet()
    ev = {e["vehicle_id"]: e for e in res["events"]}
    check(ev[UNIT_A]["vehicle_groups"] == ["Transport"], "an event carries its vehicle's actual Wialon group")
    check(ev[UNIT_C]["vehicle_groups"] == [] and ev[UNIT_C]["group_name"] == "General Fleet",
          "an ungrouped vehicle has no Wialon group (the General Fleet fallback is not one); group_name is unchanged")
    check(ev[UNIT_A]["vehicle_department"] == "Transport Dept", "the department is the unit Profile field vehicle_type")
    check(ev[UNIT_B]["vehicle_department"] is None, "an empty vehicle_type is no department")
    check(ev[UNIT_C]["vehicle_department"] is None, "a unit without vehicle_type has no department (other fields never used)")
    check(ev[UNIT_A]["department"] == "Transport Dept" and ev[UNIT_A]["start_time"] == now - 900
          and ev[UNIT_A]["end_time"] == now - 600 and ev[UNIT_A]["duration"] == 300,
          "the template's own fields and timestamps are unchanged")
    check(all("phone" not in e and "p" not in e for e in res["events"]) and "phone" not in str(res["by_vehicle"]),
          "the fleet idle events response carries no phone number")


def test_department_helper():
    check(ie.vehicle_department({"pflds": pfld("vehicle_type", " Finance ")}) == "Finance", "department value is trimmed")
    check(ie.vehicle_department({"pflds": [{"n": "vehicle_type", "v": "IT"}]}) == "IT", "list-shaped profile fields are read")
    check(ie.vehicle_department(None) is None and ie.vehicle_department({}) is None, "a missing unit or field is no department")


TESTS = [
    test_driver_resolution,
    test_unreliable_matches_return_no_phone,
    test_driver_ids_are_wialon_driver_ids,
    test_driver_errors_and_validation,
    test_group_and_department,
    test_department_helper,
]

if __name__ == "__main__":
    guard = OfflineGuard().start()
    failed = 0
    try:
        for test in TESTS:
            try:
                test()
            except Exception:
                failed += 1
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    check(not guard.attempts, "no live network call was attempted")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} test groups passed")
    sys.exit(1 if failed else 0)
