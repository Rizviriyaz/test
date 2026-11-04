"""
Regression tests for the Drivers page directory, GET /api/drivers (MOCKED / offline).

  - every Wialon driver record of the driver resource is listed, assigned or not
  - the current assignment comes from the record's `bu`: assigned (one driver on a known unit), unassigned (bu 0),
    ambiguous (several drivers on one unit), vehicle_not_found (bu names no discovered unit)
  - vehicles carry their registration plate, name and Wialon groups ("All Unit" excluded, "General Fleet" when none)
  - Driver ID is the custom field jp.ID only; missing, duplicate and other data gaps are flagged, never invented
  - the record id, password, description, position, checksum and other Wialon fields never leave the API
  - Wialon failures give 502 (or the last good result marked stale); results are cached; no token gives 401
  - no driver name, phone or card appears in the logs

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_drivers_directory.py
"""
import io
import json
import logging
import os
import sys
import traceback
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.drivers as drv  # noqa: E402
import app.main as main  # noqa: E402
from app.utils.rate_limit import reset_rate_limits  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "user@example.com", "role": "viewer"}

TRUCK, VAN, BUS, GONE = 600447001, 600447002, 600447003, 600447999
BOUND_AT = 1_793_600_000
SECRETS = ("pwd-secret-1", "description-text-1", "55.2961", "25.2711", "987654321")


def record(rid, name, *, phone=None, card=None, jp=None, bu=0, bt=0):
    # Shaped like the live records: every field present, including the ones that must never be returned
    return {"id": rid, "n": name, "c": card or "", "p": phone or "", "jp": {} if jp is None else jp, "ej": {},
            "pwd": "pwd-secret-1", "ds": "description-text-1", "r": 0, "f": 5, "ck": 987654321, "ct": 1, "mt": 2,
            "bu": bu, "pu": 0, "bt": bt, "bs": 0, "pos": {"x": 55.2961, "y": 25.2711}, "bye": 1}


RECORDS = [
    record(70101, "Aarav Assigned", phone="+971500000101", card="0158BE4201000101", jp={"ID": "100101"}, bu=TRUCK, bt=BOUND_AT),
    record(70102, "Bilal Unassigned", card="0158BE4201000102", jp={"ID": "100102"}),
    record(70103, "Chen NoDriverId", phone="+971500000103"),
    record(70104, "Dina Shared", phone="+971500000104", card="0158BE4201000104", jp={"ID": "100109"}, bu=VAN, bt=BOUND_AT),
    record(70105, "Eli Shared", phone="+971500000105", card="0158BE4201000105", jp={"ID": "100109"}, bu=VAN, bt=BOUND_AT),
    record(70106, "Farah Gone", phone="+971500000106", card="0158BE4201000106", jp='{"ID": "100106"}', bu=GONE, bt=BOUND_AT),
    record(70107, "Gita Ungrouped", phone="+971500000107", card="0158BE4201000107", jp={"ID": " 100107 "}, bu=BUS, bt=0),
]
PERSONAL = [r["n"] for r in RECORDS] + [r["p"] for r in RECORDS if r["p"]] + [r["c"] for r in RECORDS if r["c"]]

VEHICLES = [
    {"id": TRUCK, "nm": " Truck A ", "pflds": {"1": {"n": "registration_plate", "v": "DXB 12345"}}},
    {"id": VAN, "nm": "Van B", "pflds": {}},
    {"id": BUS, "nm": "Bus C"},
]
GROUPS = [
    {"id": 1, "nm": "All Unit", "u": [TRUCK, VAN, BUS]},
    {"id": 2, "nm": "Logistics", "u": [TRUCK]},
    {"id": 3, "nm": "Transport", "u": [TRUCK, VAN]},
]


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


class FakeDriverResource:
    """Wialon stand-in for core/search_item on the driver resource."""

    def __init__(self, records=RECORDS, fail=False):
        self.records, self.fail, self.calls = records, fail, 0

    def __call__(self, svc, params, sid=None, **kwargs):
        assert svc == "core/search_item", svc
        assert params["id"] == drv.DRIVER_RESOURCE_ID == 600441379 and params["flags"] == 0x1 | 0x100, params
        self.calls += 1
        if self.fail:
            return {"error": 4}
        return {"item": {"id": drv.DRIVER_RESOURCE_ID, "nm": "Drivers",
                         "drvrs": {str(r["id"]): r for r in self.records if isinstance(r, dict)} | {"bad": "not-a-record"}}}


def directory(records=RECORDS, vehicles=VEHICLES, groups=GROUPS):
    return drv.build_drivers_directory(records, vehicles, groups, 1_793_700_000)


def by_name(result):
    return {d["name"]: d for d in result["drivers"]}


def keys_of(value):
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def reset():
    main._cache.clear()
    main._failure_backoff.clear()
    reset_rate_limits()


@contextmanager
def wialon(fake, vehicles=VEHICLES, groups=GROUPS):
    with ExitStack() as stack:
        stack.enter_context(patch("app.drivers.wialon_request", fake))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=vehicles))
        stack.enter_context(patch("app.main.get_vehicle_groups", return_value=groups))
        stack.enter_context(patch("app.main.authenticate_access_token", return_value=USER))
        yield


def test_assignments_and_mapping():
    d = by_name(directory())
    check(len(d) == len(RECORDS), "every driver record is listed, assigned or not")
    a = d["Aarav Assigned"]["assignment"]
    check(a["status"] == "assigned" and a["vehicle_id"] == TRUCK and a["vehicle_name"] == "Truck A"
          and a["plate"] == "DXB 12345", "a driver bound to a known unit is assigned to it, with plate and trimmed name")
    check(a["groups"] == ["Logistics", "Transport"], "the vehicle's actual Wialon groups, without All Unit")
    check(a["since"] == BOUND_AT and a["since_iso"].endswith("+04:00"), "the binding time is the assignment start (UAE ISO)")
    b = d["Bilal Unassigned"]["assignment"]
    check(b == {"status": "unassigned", "vehicle_id": None, "vehicle_name": None, "plate": None, "groups": [],
                "since": None, "since_iso": None}, "bu = 0 is unassigned, with no vehicle values invented")
    for name in ("Dina Shared", "Eli Shared"):
        s = d[name]["assignment"]
        check(s["status"] == "ambiguous" and s["vehicle_id"] == VAN and s["groups"] == ["Transport"]
              and s["plate"] is None and "ambiguous" in d[name]["issues"], f"{name}: two drivers on one unit are ambiguous")
    g = d["Farah Gone"]["assignment"]
    check(g["status"] == "vehicle_not_found" and g["vehicle_id"] == GONE and g["vehicle_name"] is None
          and g["groups"] == [] and "vehicle_not_found" in d["Farah Gone"]["issues"], "a bu naming no discovered unit is vehicle_not_found")
    u = d["Gita Ungrouped"]["assignment"]
    check(u["status"] == "assigned" and u["groups"] == ["General Fleet"] and u["since"] is None,
          "a vehicle in no group shows General Fleet; a zero bind time is no start time")


def test_driver_fields_and_issues():
    d = by_name(directory())
    check(d["Aarav Assigned"]["driver_id"] == "100101" and d["Aarav Assigned"]["phone"] == "+971500000101"
          and d["Aarav Assigned"]["card"] == "0158BE4201000101" and d["Aarav Assigned"]["issues"] == [],
          "Driver ID is jp.ID; phone and card are the record's own values")
    check(d["Farah Gone"]["driver_id"] == "100106", "jp given as a JSON string is read")
    check(d["Gita Ungrouped"]["driver_id"] == "100107", "jp.ID is trimmed")
    check(d["Bilal Unassigned"]["phone"] is None and "missing_phone" in d["Bilal Unassigned"]["issues"], "missing phone is None and flagged")
    check(d["Chen NoDriverId"]["card"] is None and "missing_card" in d["Chen NoDriverId"]["issues"], "missing card is None and flagged")
    check(d["Chen NoDriverId"]["driver_id"] is None and "missing_driver_id" in d["Chen NoDriverId"]["issues"],
          "missing jp.ID is None (never the record id, card or index) and flagged")
    check(d["Dina Shared"]["driver_id"] == d["Eli Shared"]["driver_id"] == "100109"
          and all("duplicate_driver_id" in d[n]["issues"] for n in ("Dina Shared", "Eli Shared")),
          "a duplicate jp.ID keeps both drivers, each flagged")


def test_summary_and_groups():
    r = directory()
    check(r["count"] == 7 and r["summary"] == {
        "total": 7, "assigned": 2, "unassigned": 2, "ambiguous": 2, "vehicle_not_found": 1,
        "incomplete_assignments": 3, "missing_phone": 1, "missing_card": 1, "missing_driver_id": 1,
        "duplicate_driver_id": 2,
    }, f"summary counts are computed from the records: {r['summary']}")
    check(r["groups"] == ["General Fleet", "Logistics", "Transport"], "groups list the drivers' vehicle groups")
    check([d["name"] for d in r["drivers"]] == sorted(d["name"] for d in r["drivers"]), "drivers are sorted by name")


def test_no_internal_or_sensitive_fields():
    r = directory()
    text = json.dumps(r)
    check(not any(str(rec["id"]) in text for rec in RECORDS), "no internal record id appears anywhere in the response")
    check(not ({"id", "record_id", "pwd", "ds", "pos", "ck", "jp", "ej", "bu", "pu", "bt", "bs", "bye", "r", "f", "ct", "mt"} & keys_of(r)),
          "no Wialon record field name appears in the response")
    check(not any(s in text for s in SECRETS), "password, description, position and checksum values are never returned")
    check(set(r["drivers"][0]) == {"driver_id", "name", "phone", "card", "assignment", "issues"}, "drivers carry only the approved fields")


def test_malformed_records():
    weird = [
        {"id": 1, "n": "  ", "c": None, "p": ["x"], "jp": "not json", "bu": "abc"},
        {"id": 2, "n": "Valid Twice", "bu": None},
        {"id": 2, "n": "Valid Twice", "bu": None},
        {"n": "No Record Id", "bu": -5},
        {"id": 3, "n": "Float Unit", "bu": 1.5},
    ]
    r = directory(weird)
    d = by_name(r)
    check(r["count"] == 4, "a repeated record id is one driver; a record without an id is still listed")
    nameless = next(x for x in r["drivers"] if x["name"] is None)
    check(nameless["phone"] is None and nameless["card"] is None and nameless["driver_id"] is None
          and "missing_name" in nameless["issues"], "blank or malformed text fields are None, never invented")
    check(nameless["assignment"]["status"] == "vehicle_not_found" and nameless["assignment"]["vehicle_id"] is None,
          "a malformed bu is an incomplete assignment, never a guessed vehicle")
    check(d["Valid Twice"]["assignment"]["status"] == "unassigned", "a missing bu is unassigned")
    check(d["No Record Id"]["assignment"]["status"] == "vehicle_not_found" and d["Float Unit"]["assignment"]["status"] == "vehicle_not_found",
          "negative and fractional bu values are not unit ids")
    check(r["drivers"][-1]["name"] is None, "a nameless driver is listed last")


def test_fetch_records_failure():
    with patch("app.drivers.wialon_request", FakeDriverResource(fail=True)):
        try:
            drv.fetch_driver_records("sid")
        except WialonAPIError as exc:
            check(str(exc) == "Wialon driver records request failed", "a failed records request raises WialonAPIError without the payload")
        else:
            raise AssertionError("expected WialonAPIError")
    with patch("app.drivers.wialon_request", FakeDriverResource()):
        check(len(drv.fetch_driver_records("sid")) == len(RECORDS), "non-dict entries in drvrs are skipped")


def test_endpoint_auth_cache_and_errors():
    client = TestClient(main.app, raise_server_exceptions=False)
    reset()
    check(client.get("/api/drivers").status_code == 401, "no bearer token gives 401")

    fake = FakeDriverResource()
    with wialon(fake):
        first = client.get("/api/drivers", headers=AUTH_HEADERS)
        check(first.status_code == 200 and first.json()["count"] == 7 and fake.calls == 1, "a signed-in viewer gets the directory")
        body = first.json()
        check(set(body) >= {"count", "summary", "drivers", "groups", "retrieved_at", "freshness"},
              "the envelope carries count, summary, drivers, groups, retrieved_at and freshness")
        check(any(d["phone"] for d in body["drivers"]), "phones are returned to every signed-in role, viewer included")
        second = client.get("/api/drivers", headers=AUTH_HEADERS)
        check(second.status_code == 200 and fake.calls == 1, "a second request within the TTL is served from the cache")
        client.get("/api/drivers?refresh=true", headers=AUTH_HEADERS)
        check(fake.calls == 2, "refresh=true re-reads Wialon")
        fake.fail = True
        stale = client.get("/api/drivers?refresh=true", headers=AUTH_HEADERS)
        check(stale.status_code == 200 and stale.json().get("stale") is True and stale.json()["freshness"]["is_stale"],
              "a Wialon failure with a recent good result serves it marked stale")

    reset()
    with wialon(FakeDriverResource(fail=True)):
        res = client.get("/api/drivers", headers=AUTH_HEADERS)
    check(res.status_code == 502 and res.json()["detail"] == "Wialon upstream request failed",
          "a Wialon failure with nothing cached gives 502 with a generic message")
    reset()
    with wialon(FakeDriverResource()), patch("app.main.get_all_vehicles", side_effect=WialonAPIError("units failed")):
        check(client.get("/api/drivers", headers=AUTH_HEADERS).status_code == 502, "a failed unit search gives 502")
    reset()


def test_no_personal_data_in_logs():
    client = TestClient(main.app, raise_server_exceptions=False)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        reset()
        fake = FakeDriverResource()
        with wialon(fake):
            client.get("/api/drivers", headers=AUTH_HEADERS)
            fake.fail = True
            client.get("/api/drivers?refresh=true", headers=AUTH_HEADERS)
        reset()
        with wialon(FakeDriverResource(fail=True)):
            client.get("/api/drivers", headers=AUTH_HEADERS)
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        reset()
    logs = stream.getvalue()
    check("/api/drivers" in logs, "the drivers requests were logged (the capture works)")
    check(not any(p in logs for p in PERSONAL), "no driver name, phone or card appears in the logs")


TESTS = [
    test_assignments_and_mapping,
    test_driver_fields_and_issues,
    test_summary_and_groups,
    test_no_internal_or_sensitive_fields,
    test_malformed_records,
    test_fetch_records_failure,
    test_endpoint_auth_cache_and_errors,
    test_no_personal_data_in_logs,
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
