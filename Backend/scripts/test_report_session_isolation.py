"""
Regression tests for Wialon report session isolation (MOCKED / offline).

Long fleet-wide idle-event reads (/api/idle/fleet-analytics with include_events) run on a dedicated
Wialon session (app.wialon.EVENTS_SESSION) with its own report lock, so Dashboard summary reports on the
shared session never queue behind event pagination.

The REAL app.wialon.wialon_request/login and app.wialon_reports run against a fake HTTP transport that
models Wialon sessions (token/login issues a session id, an unknown id answers error 1) and keeps ONE report
result per session, as Wialon does. A report whose rows are read by a thread other than the one that
executed it on that session is recorded as an interleaving violation. All other HTTP is blocked.

Run from Backend/:  python scripts/test_report_session_isolation.py
"""
import contextlib
import copy
import json
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, UNITS, default_tables, live_records,
)

import app.wialon as gateway  # noqa: E402
import app.wialon_reports as wr  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 3 * 86400 - 1
SUBROWS = "report/get_result_subrows"
WAIT = 5  # seconds; anything near this means a request was blocked


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class SessionWialon:
    """Stands in for app.wialon._wialon_http_session.post with per-session report results."""

    def __init__(self, subrow_gate=None):
        self.tables = default_tables()
        self.subrow_gate = subrow_gate  # threading.Event: sub-row pages wait on it (simulates slow paging)
        self.subrows_waiting = threading.Event()
        self.valid = set()
        self.sessions = 0
        self.result = {}  # sid -> (owner thread, table)
        self.violations = []
        self.calls = []   # (svc, sid)
        self.lock = threading.Lock()

    def expire(self, sid):
        with self.lock:
            self.valid.discard(sid)

    def post(self, url, data=None, timeout=None):
        svc, params, sid = data["svc"], json.loads(data["params"]), data.get("sid")
        if svc == SUBROWS and self.subrow_gate is not None:
            self.subrows_waiting.set()
            self.subrow_gate.wait(timeout=WAIT)
        me = threading.get_ident()
        with self.lock:
            self.calls.append((svc, sid))
            if svc == "token/login":
                self.sessions += 1
                new_sid = f"sid-{self.sessions}"
                self.valid.add(new_sid)
                return _Resp({"eid": new_sid})
            if sid not in self.valid:
                return _Resp({"error": 1})
            if svc == "report/exec_report":
                table = self.tables[params["reportTemplateId"]]
                self.result[sid] = (me, table)
                return _Resp({"reportResult": {"tables": [
                    {"rows": len(table["rows"]), "header": list(table["header"]), "total": []}], "stats": []}})
            owner, table = self.result.get(sid, (None, None))
            if svc in ("report/get_result_rows", SUBROWS):
                if owner != me:
                    self.violations.append((svc, sid))
                if table is None:
                    return _Resp({"error": 5})
                if svc == SUBROWS:
                    subs = table["subrows"].get(params["rowIndex"], [])
                    return _Resp(copy.deepcopy(subs[params["indexFrom"]:params["indexTo"] + 1]))
                return _Resp(copy.deepcopy(table["rows"][params["indexFrom"]:params["indexTo"] + 1]))
            if svc == "report/cleanup_result":
                if owner == me:
                    self.result.pop(sid, None)
                return _Resp({"error": 0})
        raise AssertionError(f"Unexpected Wialon service in fake: {svc}")

    def sids_for(self, svc):
        return [s for c, s in self.calls if c == svc]


@contextlib.contextmanager
def fake_wialon(transport):
    """Real gateway + report code over `transport`, starting with no shared or named sessions."""
    with patch.object(gateway, "SID", None), \
         patch.object(gateway, "_named_sids", {}), \
         patch.object(gateway, "_sid_owner", {}), \
         patch.object(wr, "_session_report_locks", {}), \
         patch.object(gateway._wialon_http_session, "post", transport.post):
        yield


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def run_in_thread(fn, *args, **kwargs):
    box = {}

    def target():
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the caller
            box["error"] = exc

    th = threading.Thread(target=target)
    th.start()
    return th, box


def summary_report(sid):
    """The report work the Dashboard summary/mileage does on the shared session."""
    started = time.perf_counter()
    rows = wr.execute_wialon_group_trips_report(sid, FROM_TS, TO_TS, group_id=1)
    return rows, time.perf_counter() - started


def test_sessions_are_distinct_and_reused():
    t = SessionWialon()
    with fake_wialon(t):
        shared = gateway.login()
        events = gateway.login(session=gateway.EVENTS_SESSION)
        check(shared != events, "events session has its own Wialon session id")
        check(gateway.login() == shared and gateway.login(session=gateway.EVENTS_SESSION) == events,
              "both sessions are reused across calls (one login each)")
        check(gateway.session_name(events) == gateway.EVENTS_SESSION and gateway.session_name(shared) is None,
              "session ids map to their session (events vs shared)")
        check(wr._report_lock_for(shared) is wr._report_lock and wr._report_lock_for(events) is not wr._report_lock,
              "each session has its own report lock")
    check(len(t.sids_for("token/login")) == 2, "exactly two token/login calls")


def test_summary_not_blocked_by_event_paging():
    gate = threading.Event()
    t = SessionWialon(subrow_gate=gate)
    with fake_wialon(t):
        shared = gateway.login()
        events = gateway.login(session=gateway.EVENTS_SESSION)
        ev_thread, ev_box = run_in_thread(wr.execute_wialon_group_engine_idle_report, events, FROM_TS, TO_TS, group_id=1)
        check(t.subrows_waiting.wait(WAIT), "event report is mid sub-row paging (holding its report lock)")
        rows, elapsed = summary_report(shared)
        still_paging = ev_thread.is_alive()
        gate.set()
        ev_thread.join(WAIT * 2)
    check(still_paging, "summary report finished while event paging was still in progress")
    check(elapsed < 1.0 and [r["vehicle_name"] for r in rows] == ["UNIT-A", "UNIT-B", "UNIT-C"],
          f"summary report completed without waiting ({elapsed * 1000:.0f} ms) with all rows")
    check("error" not in ev_box and len(ev_box["result"]["events"]) == 4 and ev_box["result"]["events_incomplete"] == [],
          "event report then completed with every idle event (3 sub-row events + 1 single interval)")
    check(t.violations == [], "no report result was read across threads on either session")
    check(set(t.sids_for(SUBROWS)) == {events} and set(t.sids_for("report/get_result_rows")) == {shared, events},
          "sub-rows paged only on the events session; summary rows read on the shared session")


def test_negative_control_shared_session_blocks():
    """Without the dedicated session the summary queues behind event paging (what the change removes)."""
    gate = threading.Event()
    t = SessionWialon(subrow_gate=gate)
    with fake_wialon(t):
        shared = gateway.login()
        ev_thread, _ = run_in_thread(wr.execute_wialon_group_engine_idle_report, shared, FROM_TS, TO_TS, group_id=1)
        t.subrows_waiting.wait(WAIT)
        sum_thread, sum_box = run_in_thread(summary_report, shared)
        sum_thread.join(0.5)
        blocked = sum_thread.is_alive()
        gate.set()
        ev_thread.join(WAIT * 2)
        sum_thread.join(WAIT * 2)
    check(blocked, "control: on one shared session the summary waits behind event paging")
    check("error" not in sum_box and t.violations == [], "control: lock still prevents interleaving on that session")


def test_events_session_reports_still_serialized():
    gate = threading.Event()
    t = SessionWialon(subrow_gate=gate)
    with fake_wialon(t):
        events = gateway.login(session=gateway.EVENTS_SESSION)
        first, b1 = run_in_thread(wr.execute_wialon_group_engine_idle_report, events, FROM_TS, TO_TS, group_id=1)
        t.subrows_waiting.wait(WAIT)
        second, b2 = run_in_thread(wr.execute_wialon_group_engine_idle_report, events, FROM_TS, TO_TS, group_id=1)
        second.join(0.3)
        queued = second.is_alive()
        gate.set()
        first.join(WAIT * 2)
        second.join(WAIT * 2)
    check(queued, "a second event report on the events session waits for the first (one result per session)")
    check(t.violations == [] and b1["result"] == b2["result"], "both event reports complete identically without interleaving")


def test_negative_control_unlocked_events_session_interleaves():
    """Proves the fake detects interleaving, so the passes above are meaningful."""
    class NoLock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    gate = threading.Event()
    t = SessionWialon(subrow_gate=gate)
    with fake_wialon(t), patch.object(wr, "_report_lock_for", lambda sid: NoLock()):
        events = gateway.login(session=gateway.EVENTS_SESSION)
        first, _ = run_in_thread(wr.execute_wialon_group_engine_idle_report, events, FROM_TS, TO_TS, group_id=1)
        t.subrows_waiting.wait(WAIT)
        second, _ = run_in_thread(wr.execute_wialon_group_trips_report, events, FROM_TS, TO_TS, group_id=1)
        second.join(0.5)
        gate.set()
        first.join(WAIT * 2)
        second.join(WAIT * 2)
    check(t.violations, "control: without a per-session lock two reports on one session interleave")


def test_expired_session_refreshes_only_itself():
    t = SessionWialon()
    with fake_wialon(t):
        shared = gateway.login()
        events = gateway.login(session=gateway.EVENTS_SESSION)

        t.expire(events)
        rep = wr.execute_wialon_group_engine_idle_report(events, FROM_TS, TO_TS, group_id=1, include_events=False)
        new_events = gateway.login(session=gateway.EVENTS_SESSION)
        check(new_events not in (events, shared) and gateway.SID == shared,
              "expired events session is re-logged as the events session; shared SID untouched")
        check(gateway.session_name(new_events) == gateway.EVENTS_SESSION and len(rep["vehicles"]) == 3,
              "refreshed events session keeps its own lock mapping and the report completes")

        t.expire(shared)
        wr.execute_wialon_group_trips_report(shared, FROM_TS, TO_TS, group_id=1)
        check(gateway.SID not in (shared, new_events) and gateway.login(session=gateway.EVENTS_SESSION) == new_events,
              "expired shared session is refreshed alone; events session untouched")

        # A stale events id from before the refresh resolves to the current events session, never the shared SID
        resolved = gateway.wialon_request("report/cleanup_result", {}, sid=events)
        check(resolved == {"error": 0} and t.calls[-1] == ("report/cleanup_result", new_events),
              "a stale events session id is redirected to the current events session")


def run_idle_endpoint(include_events):
    import app.main as main

    seen = []
    real_report = wr.execute_wialon_group_engine_idle_report

    def spy(sid, *args, **kwargs):
        seen.append(sid)
        return real_report(sid, *args, **kwargs)

    patches = [
        patch("app.main.get_vehicle_groups", return_value=GROUPS),
        patch("app.main.get_all_vehicles", return_value=UNITS),
        patch("app.main.get_calculated_data", return_value=CALCULATED),
        patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()),
        patch("app.main.execute_wialon_group_engine_idle_report", side_effect=spy),
    ]
    for p in patches:
        p.start()
    try:
        main._cache.clear()
        res = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, skip_cache=True,
                                            include_events=include_events)
    finally:
        for p in reversed(patches):
            p.stop()
    return seen, json.loads(json.dumps(res, default=str))


def test_idle_endpoint_uses_events_session():
    t = SessionWialon()
    with fake_wialon(t):
        shared = gateway.login()
        full_sids, full = run_idle_endpoint(True)
        summary_sids, summary = run_idle_endpoint(False)
        events = gateway.login(session=gateway.EVENTS_SESSION)
    check(full_sids == [events] and events != shared, "include_events=true runs the report on the events session")
    check(summary_sids == [shared], "include_events=false (Dashboard idle tile) stays on the shared session")
    check(full["kpis"]["total_idle_seconds"] == summary["kpis"]["total_idle_seconds"] == 5400 + 4530,
          "idle totals unchanged (9930 s) whichever session runs the report")
    check(len(full["events"]) == 4 and summary["events"] == [], "event output unchanged (4 events / none for summary)")


if __name__ == "__main__":
    guard = OfflineGuard().start()
    try:
        # The guard blocks requests.Session.request; the fake replaces .post on the gateway session itself.
        test_sessions_are_distinct_and_reused()
        test_summary_not_blocked_by_event_paging()
        test_negative_control_shared_session_blocks()
        test_events_session_reports_still_serialized()
        test_negative_control_unlocked_events_session_interleaves()
        test_expired_session_refreshes_only_itself()
        test_idle_endpoint_uses_events_session()
    finally:
        guard.stop()
    if guard.attempts:
        raise AssertionError(f"Live calls attempted: {guard.attempts}")
    print("\nAll report session isolation tests passed.")
