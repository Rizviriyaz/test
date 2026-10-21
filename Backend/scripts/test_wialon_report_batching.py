"""
Offline regression tests for H5: Wialon report serialization, the shared report lock and the per-unit trips
fan-out behind /api/fuel/analytics/efficiency/trend.

The REAL app.wialon.wialon_request/login run against a fake HTTP transport that models Wialon sessions
(token/login issues a session, an unknown session answers error 1) and keeps ONE report result per session,
as Wialon does. A report whose rows are read by a thread other than the one that executed it is recorded as
an interleaving violation.

Run from Backend/:  python scripts/test_wialon_report_batching.py
"""
import contextlib
import io
import json
import os
import sys
import threading
from collections import Counter
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import run  # noqa: E402

import app.fuel.wialon_distance as wd  # noqa: E402
import app.wialon as gateway  # noqa: E402
import app.wialon_reports as wr  # noqa: E402
from app.utils.ttl_cache import TTLCache  # noqa: E402

HEADER = ["Grouping", "Beginning", "Initial location", "End", "Final location",
          "Total time", "Engine hours", "Avg speed", "Max speed", "Mileage"]
BASE_TS = 1785715200  # 2026-08-03 00:00:00 UTC
CLOSED = ("2026-08-03", "2026-08-05")
UNITS = [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008]


def _row(uid, i):
    s = BASE_TS + i * 7200
    return {"c": [str(i), {"t": "s", "v": s}, {"t": "A", "x": 55, "y": 25}, {"t": "e", "v": s + 1800},
                  {"t": "B", "x": 55, "y": 25}, "0:30:00", "0:25:00", "40 km/h", {"t": "80 km/h", "v": 80},
                  f"{uid % 7 + i}.50 km"]}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeWialonTransport:
    """Stands in for app.wialon._wialon_http_session.post."""

    def __init__(self, fail_units=(), expire_after_execs=None, rows_barrier=None):
        self.fail_units = set(fail_units)
        self.expire_after_execs = expire_after_execs
        self.rows_barrier = rows_barrier
        self.calls = Counter()
        self.log = []          # (svc, unit) in arrival order
        self.valid = set()
        self.result = {}       # sid -> (thread id, unit id): Wialon keeps one report result per session
        self.sessions = 0
        self.execs = 0
        self.errors1 = 0
        self.violations = []
        self.executed = set()  # (sid, thread id) with an executed, not yet cleaned-up report
        self.lock = threading.Lock()

    def post(self, url, data=None, timeout=None):
        svc, params, sid = data["svc"], json.loads(data["params"]), data.get("sid")
        if svc == "report/get_result_rows" and self.rows_barrier is not None:
            self.rows_barrier.wait(timeout=5)  # both reports executed before either reads its rows
        with self.lock:
            self.calls[svc] += 1
            if svc == "token/login":
                self.sessions += 1
                new_sid = f"sid-{self.sessions}"
                self.valid.add(new_sid)
                return _Resp({"eid": new_sid})
            if sid not in self.valid:
                self.errors1 += 1
                return _Resp({"error": 1})
            me = threading.get_ident()
            if svc == "report/exec_report":
                self.execs += 1
                uid = params["reportObjectId"]
                self.log.append((svc, uid))
                if self.expire_after_execs and self.execs == self.expire_after_execs:
                    self.valid.discard(sid)  # the session dies right after answering this exec
                if uid in self.fail_units:
                    return _Resp({"error": 5})
                self.result[sid] = (me, uid)
                self.executed.add((sid, me))
                return _Resp({"reportResult": {"tables": [{"rows": 2, "header": HEADER}], "stats": []}})
            if svc == "report/get_result_rows":
                owner, uid = self.result.get(sid, (None, None))
                if (sid, me) in self.executed and owner != me:
                    self.violations.append(sid)  # this thread's result was replaced or cleaned up by another
                if owner is None:
                    return _Resp({"error": 5})
                return _Resp([_row(uid, 1), _row(uid, 2)])
            if svc == "report/cleanup_result":
                self.executed.discard((sid, me))
                self.result.pop(sid, None)
                return _Resp({})
        raise AssertionError(f"Unexpected Wialon service in fake: {svc}")

    def total(self):
        return sum(self.calls.values())


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@contextlib.contextmanager
def wialon(transport, cache=None):
    """Real gateway + report code over the fake transport, with a fresh unit-trips cache and no session."""
    with patch.object(gateway, "SID", None), \
         patch.object(gateway._wialon_http_session, "post", transport.post), \
         patch.object(wd, "_UNIT_TRIPS_CACHE", cache or TTLCache(max_entries=4096)), \
         contextlib.redirect_stdout(io.StringIO()):  # the gateway prints on session refresh
        yield gateway.login()


def test_repeated_trend_served_from_cache():
    t = FakeWialonTransport()
    with wialon(t) as sid:
        first = wd.get_units_trips_for_range(UNITS, *CLOSED, sid)
        cold = dict(t.calls)
        again = wd.get_units_trips_for_range(list(reversed(UNITS)) + UNITS[:2], *CLOSED, sid)  # dupes, other order
        subset = wd.get_units_trips_for_range(UNITS[:3], *CLOSED, sid)                         # single-vehicle view
    assert cold["report/exec_report"] == cold["report/get_result_rows"] == cold["report/cleanup_result"] == len(UNITS)
    assert dict(t.calls) == cold, "repeat / reordered / subset requests must not reach Wialon"
    assert again == first and subset == {u: first[u] for u in UNITS[:3]}
    assert all(r["status"] == "complete" and len(r["trips"]) == 2 for r in first.values())
    print(f"[PASS] Cold trend: 3 Wialon calls per unit ({len(UNITS)} units); repeat, reordered and subset requests: 0 calls.")


def test_cache_ttl_closed_live_and_failed():
    clock = FakeClock()
    t = FakeWialonTransport()
    with wialon(t, TTLCache(max_entries=4096, clock=clock)) as sid:
        wd.get_units_trips_for_range(UNITS, *CLOSED, sid)
        clock.now += wd.TELEMATICS_TTL_CLOSED_S - 1
        wd.get_units_trips_for_range(UNITS, *CLOSED, sid)
        assert t.calls["report/exec_report"] == len(UNITS), "closed range is cached for the closed TTL"
        clock.now += 2
        wd.get_units_trips_for_range(UNITS, *CLOSED, sid)
        assert t.calls["report/exec_report"] == 2 * len(UNITS), "expired entries are refetched exactly once each"

    clock = FakeClock()
    t = FakeWialonTransport()
    live = ("2026-08-03", "2099-12-31")  # ends today or later -> live TTL
    with wialon(t, TTLCache(max_entries=4096, clock=clock)) as sid:
        wd.get_units_trips_for_range(UNITS[:2], *live, sid)
        clock.now += wd.TELEMATICS_TTL_LIVE_S - 1
        wd.get_units_trips_for_range(UNITS[:2], *live, sid)
        assert t.calls["report/exec_report"] == 2
        clock.now += 2
        wd.get_units_trips_for_range(UNITS[:2], *live, sid)
        assert t.calls["report/exec_report"] == 4

    clock = FakeClock()
    t = FakeWialonTransport(fail_units={UNITS[0]})
    with wialon(t, TTLCache(max_entries=4096, clock=clock)) as sid:
        r = wd.get_units_trips_for_range(UNITS[:1], *CLOSED, sid)
        assert r[UNITS[0]]["status"] == "failed" and t.calls["report/exec_report"] == 2  # one re-login retry
        wd.get_units_trips_for_range(UNITS[:1], *CLOSED, sid)
        assert t.calls["report/exec_report"] == 2, "a failed unit is not re-requested within the partial TTL"
        clock.now += wd.TELEMATICS_TTL_PARTIAL_S + 1
        wd.get_units_trips_for_range(UNITS[:1], *CLOSED, sid)
        assert t.calls["report/exec_report"] == 4, "and is retried once that short TTL expires"
    print("[PASS] TTLs: closed range 1 h, live range 2 min, failed unit 30 s; each expiry refetches once.")


def test_concurrent_identical_requests_single_flight():
    t = FakeWialonTransport()
    results, errors = [], []
    with wialon(t) as sid:
        start = threading.Barrier(4)

        def worker():
            try:
                start.wait(timeout=5)
                results.append(wd.get_units_trips_for_range(UNITS, *CLOSED, sid))
            except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
    assert not errors, errors
    assert len(results) == 4 and all(r == results[0] for r in results)
    assert t.calls["report/exec_report"] == len(UNITS), t.calls
    assert t.violations == []
    print(f"[PASS] 4 concurrent identical trend requests: {len(UNITS)} reports total (one per unit), no interleaving.")


def test_session_refreshed_mid_batch_is_reused():
    t = FakeWialonTransport(expire_after_execs=3)
    with wialon(t) as sid:
        res = wd.get_units_trips_for_range(UNITS, *CLOSED, sid)
    assert all(r["status"] == "complete" for r in res.values())
    # Unit 3's rows hit the dead session (error 1 -> refresh -> no result there -> one forced re-login retry).
    # Every later unit must use the refreshed session, not first fail on the stale one.
    assert t.errors1 <= 2, f"stale session re-sent {t.errors1} times"
    assert t.calls["report/exec_report"] == len(UNITS) + 1, t.calls
    assert t.violations == []
    print(f"[PASS] Session dies mid-batch: {t.errors1} error-1 round trips (was 3 per remaining unit), all units complete.")


def test_report_lock_serializes_reports_on_shared_session():
    t = FakeWialonTransport()
    with wialon(t) as sid:
        threads = [
            threading.Thread(target=wd.get_units_trips_for_range, args=(UNITS, *CLOSED, sid)),
            threading.Thread(target=wd.get_units_trips_for_range, args=(UNITS, "2026-07-01", "2026-07-31", sid)),
            threading.Thread(target=wr.execute_wialon_unit_trips_report, args=(sid, 77, BASE_TS, BASE_TS + 86400)),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
    assert t.calls["report/exec_report"] == 2 * len(UNITS) + 1 and t.violations == []

    # Negative control: without the lock two reports on one session overwrite each other's result.
    class NoLock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def locked(self):
            return False

    t = FakeWialonTransport(rows_barrier=threading.Barrier(2))
    with wialon(t) as sid, patch.object(wr, "_report_lock", NoLock()):
        threads = [threading.Thread(target=wr.execute_wialon_unit_trips_report, args=(sid, u, BASE_TS, BASE_TS + 86400))
                   for u in (1, 2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=30)
    assert t.violations, "the fake must detect interleaved reports, otherwise the check above proves nothing"
    print("[PASS] Report lock: concurrent batches + a single report never interleave on the shared session "
          "(and the same scenario without the lock does).")


def test_lock_held_per_report_not_per_batch():
    held_during_cache_writes = []

    class SpyCache(TTLCache):
        def set(self, key, value, ttl_seconds):
            held_during_cache_writes.append(wr._report_lock.locked())
            super().set(key, value, ttl_seconds)

    t = FakeWialonTransport()
    with wialon(t, SpyCache(max_entries=4096)) as sid:
        wd.get_units_trips_for_range(UNITS, *CLOSED, sid)
        assert not wr._report_lock.locked()
    assert held_during_cache_writes == [False] * len(UNITS), held_during_cache_writes
    print("[PASS] The report lock is released after every unit, so other report users can run between units.")


TESTS = [
    test_repeated_trend_served_from_cache,
    test_cache_ttl_closed_live_and_failed,
    test_concurrent_identical_requests_single_flight,
    test_session_refreshed_mid_batch_is_reused,
    test_report_lock_serializes_reports_on_shared_session,
    test_lock_held_per_report_not_per_batch,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "H5 - WIALON REPORT SERIALIZATION, CACHING AND LOCK (OFFLINE)") else 0)
