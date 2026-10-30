"""
Offline regression tests for Wialon session refresh and retries (MOCKED / offline).

  - H1: after an error-1 refresh, later calls that still pass the old session id are sent with the current one
    (no repeated error 1), for the shared session and for named sessions
  - H1: concurrent calls on an expired session log in once (no login storm); each gets at most one error 1
  - H1: a refreshed session that also fails returns Wialon's error (no infinite retry, one login)
  - H1: a named session whose re-login failed never sends its requests with the shared SID (also after _sid_owner
    eviction), keeps its own report lock, and recovers on its own session; the retired-id lookup cannot race
  - L2: a report exec that times out is not re-executed; other services keep their retries; error 1003 gives up
    without a final backoff sleep
  - M3: the fuel report retry forces a new session only for error 1; error 5 retries on the current session; errors a
    rerun cannot fix (4) propagate at once

Run from Backend/:  python scripts/test_wialon_session_refresh.py
"""
import json
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import offline_guard  # noqa: E402,F401

import requests  # noqa: E402

import app.wialon as wialon  # noqa: E402
import app.fuel.wialon_distance as wd  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402


class Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeWialon:
    """Wialon stand-in: token/login issues sid-1, sid-2, ...; a sid in `expired` answers error 1."""

    def __init__(self, expired=(), delay=0.0, expire_new=False, login_fails=False):
        self.expired = set(expired)
        self.login_fails = login_fails  # token/login answers error 8
        self.delay = delay
        self.expire_new = expire_new  # every newly issued sid is already invalid
        self.issued = 0
        self.calls = []  # (svc, sid)
        self._lock = threading.Lock()

    def post(self, url, data=None, timeout=None):
        svc, sid = data["svc"], data.get("sid")
        with self._lock:
            self.calls.append((svc, sid))
        if self.delay:
            time.sleep(self.delay)
        if svc == "token/login" and self.login_fails:
            return Resp({"error": 8})
        if svc == "token/login":
            with self._lock:
                self.issued += 1
                new_sid = f"sid-{self.issued}"
                if self.expire_new:
                    self.expired.add(new_sid)
            return Resp({"eid": new_sid})
        if sid is None or sid in self.expired:
            return Resp({"error": 1})
        return Resp({"svc": svc, "sid": sid, "params": json.loads(data["params"])})

    def count(self, svc=None, sid=None):
        return sum(1 for s, i in self.calls if (svc is None or s == svc) and (sid is None or i == sid))

    def errors_1(self):
        return sum(1 for s, i in self.calls if s != "token/login" and (i is None or i in self.expired))


def _reset(shared=None, named=None):
    wialon.SID = shared
    wialon._named_sids.clear()
    wialon._sid_owner.clear()
    wialon._retired_sids.clear()
    for session, sid in (named or {}).items():
        wialon._named_sids[session] = sid
        wialon._sid_owner[sid] = session


def _env(fake):
    return patch.object(wialon._wialon_http_session, "post", side_effect=fake.post), \
        patch("app.wialon.get_wialon_api", return_value="http://wialon.invalid/ajax"), \
        patch("app.wialon.get_wialon_token", return_value="token")


def test_refresh_then_more_calls_use_current_sid():
    _reset(shared="old")
    fake = FakeWialon(expired={"old"})
    p1, p2, p3 = _env(fake)
    with p1, p2, p3:
        sid = "old"  # what a route got from login() at the start of the request
        results = [wialon.wialon_request("core/search_items", {"i": i}, sid=sid) for i in range(5)]
    assert all(r.get("sid") == "sid-1" for r in results), results
    assert fake.count("token/login") == 1, fake.calls
    assert fake.errors_1() == 1, f"only the first call meets error 1, then the current sid is sent: {fake.calls}"
    assert fake.count("core/search_items", "old") == 1 and fake.count("core/search_items", "sid-1") == 5, fake.calls
    print("[PASS] H1: after a refresh, later calls holding the old sid are sent with the current one (1 error 1, not 5).")


def test_named_session_refresh_then_more_calls():
    _reset(shared="shared-ok", named={wialon.EVENTS_SESSION: "events-old"})
    fake = FakeWialon(expired={"events-old"})
    p1, p2, p3 = _env(fake)
    with p1, p2, p3:
        results = [wialon.wialon_request("report/get_result_rows", {"i": i}, sid="events-old") for i in range(4)]
        shared = wialon.wialon_request("core/search_items", {}, sid="shared-ok")
    assert all(r.get("sid") == "sid-1" for r in results), results
    assert wialon._named_sids[wialon.EVENTS_SESSION] == "sid-1" and wialon.SID == "shared-ok"
    assert wialon.session_name("sid-1") == wialon.EVENTS_SESSION, "the new id still belongs to its named session"
    assert shared.get("sid") == "shared-ok" and fake.errors_1() == 1 and fake.count("token/login") == 1, fake.calls
    print("[PASS] H1: a named session's refresh redirects only that session's old id; the shared SID is untouched.")


def test_concurrent_refresh_logs_in_once():
    _reset(shared="old")
    fake = FakeWialon(expired={"old"}, delay=0.02)
    p1, p2, p3 = _env(fake)
    results = [None] * 12
    barrier = threading.Barrier(12)

    def call(i):
        barrier.wait()
        results[i] = wialon.wialon_request("core/search_items", {"i": i}, sid="old")

    with p1, p2, p3:
        threads = [threading.Thread(target=call, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # A later call in any of those requests goes straight to the current sid
        later = wialon.wialon_request("core/search_items", {}, sid="old")
    assert all(r and r.get("sid") == "sid-1" for r in results), results
    assert fake.count("token/login") == 1, f"one login for 12 concurrent expired calls, got {fake.count('token/login')}"
    assert fake.count("core/search_items", "old") <= 12 and later.get("sid") == "sid-1", fake.calls
    print(f"[PASS] H1: 12 concurrent calls on an expired session: 1 login, each at most one error 1 "
          f"({fake.count('core/search_items', 'old')} met it), all answered on the new session.")


def test_refreshed_session_also_failing_returns_error_once():
    _reset(shared="old")
    fake = FakeWialon(expired={"old"}, expire_new=True)
    p1, p2, p3 = _env(fake)
    with p1, p2, p3:
        res = wialon.wialon_request("core/search_items", {}, sid="old")
    assert res == {"error": 1}, res
    assert fake.count("token/login") == 1 and fake.count("core/search_items") == 2, fake.calls
    print("[PASS] H1: a refreshed session that also fails returns Wialon's error 1 (one login, one retry, no loop).")


NAMED_SESSIONS = (wialon.EVENTS_SESSION, wialon.FUEL_SESSION, wialon.FUEL_ENGINE_SESSION, wialon.FUEL_TRIPS_SESSION,
                  wialon.VEHICLE_SESSION)


def test_failed_named_login_never_uses_shared_sid():
    for session in NAMED_SESSIONS:
        for evicted in (False, True):
            old = f"{session}-old"
            _reset(shared="shared-ok", named={session: old})
            fake = FakeWialon(expired={old}, login_fails=True)
            p1, p2, p3 = _env(fake)
            with p1, p2, p3:
                try:
                    wialon.login(force=True, session=session)
                    raise AssertionError("the re-login was meant to fail")
                except WialonAPIError:
                    pass
                if evicted:  # the old id has also left _sid_owner (bounded at 64 entries)
                    wialon._sid_owner.clear()
                assert wialon.session_name(old) == session, "its report lock stays the named session's lock"
                try:
                    res = wialon.wialon_request("report/get_result_rows", {}, sid=old)
                except WialonAPIError:
                    res = None
            shared_calls = [c for c in fake.calls if c[0] != "token/login" and c[1] == "shared-ok"]
            assert shared_calls == [], f"{session} (evicted={evicted}) request went through the shared session: {fake.calls}"
            assert res is None or res.get("error") == 1, (session, res)
            assert fake.count("report/get_result_rows", old) == 1, "sent with its own old id, once"
            assert fake.count("token/login") == 2, "the error-1 handling tried that same named session's login again"
            assert wialon.SID == "shared-ok" and session not in wialon._named_sids
    print("[PASS] H1 fix: after a failed events/fuel/fuel_engine/fuel_trips/vehicle re-login, its requests keep their own "
          "old id (error 1, re-login of that session), never the shared SID, also after _sid_owner eviction.")


def test_named_session_recovers_on_its_own_session():
    _reset(shared="shared-ok", named={wialon.VEHICLE_SESSION: "vehicle-old"})
    fake = FakeWialon(expired={"vehicle-old"}, login_fails=True)
    p1, p2, p3 = _env(fake)
    with p1, p2, p3:
        try:
            wialon.login(force=True, session=wialon.VEHICLE_SESSION)
        except WialonAPIError:
            pass
        fake.login_fails = False  # Wialon's login works again
        res = wialon.wialon_request("report/exec_report", {}, sid="vehicle-old")
    assert res.get("sid") == "sid-1" and wialon._named_sids[wialon.VEHICLE_SESSION] == "sid-1", res
    assert wialon.session_name("sid-1") == wialon.VEHICLE_SESSION and wialon.SID == "shared-ok"
    assert not [c for c in fake.calls if c[0] != "token/login" and c[1] == "shared-ok"], fake.calls
    print("[PASS] H1 fix: once login works again, the named session gets its own new id; the shared SID is never used.")


def test_retired_lookup_survives_concurrent_eviction():
    class EvictedBetweenCheckAndRead(dict):
        # Reproduces the race: a membership test still sees the id, then eviction removes it before it is read
        def __contains__(self, key):
            return True

        def __getitem__(self, key):
            raise KeyError(key)

    _reset(shared="shared-ok")
    with patch.object(wialon, "_retired_sids", EvictedBetweenCheckAndRead()):
        assert wialon._live_sid("gone") == "gone", "an id evicted mid-lookup is used as given, never a KeyError"
    print("[PASS] H1 fix: the retired-id lookup is one .get(): eviction between check and read cannot raise KeyError.")


def test_exec_report_timeout_not_reexecuted():
    _reset(shared="ok")
    posts = []

    def timing_out(url, data=None, timeout=None):
        posts.append(data["svc"])
        raise requests.exceptions.ReadTimeout("slow")

    with patch.object(wialon._wialon_http_session, "post", side_effect=timing_out), \
         patch("app.wialon.get_wialon_api", return_value="http://wialon.invalid/ajax"), \
         patch("app.wialon.time.sleep") as sleep:
        for svc in ("report/exec_report", "core/search_items"):
            try:
                wialon.wialon_request(svc, {}, sid="ok", timeout=120)
                raise AssertionError("expected WialonAPIError")
            except WialonAPIError:
                pass
    assert posts.count("report/exec_report") == 1, posts
    assert posts.count("core/search_items") == 3 and sleep.call_count == 2, (posts, sleep.call_args_list)
    print("[PASS] L2: a timed-out report exec is not re-run (1 x 120 s, not 3 x); other services keep 3 attempts.")


def test_1003_gives_up_without_final_sleep():
    _reset(shared="ok")
    with patch.object(wialon._wialon_http_session, "post", side_effect=lambda *a, **k: Resp({"error": 1003})), \
         patch("app.wialon.get_wialon_api", return_value="http://wialon.invalid/ajax"), \
         patch("app.wialon.time.sleep") as sleep:
        res = wialon.wialon_request("core/search_items", {}, sid="ok")
    assert res == {"error": 1003}, res
    assert [c[0][0] for c in sleep.call_args_list] == [0.25, 0.5], sleep.call_args_list
    print("[PASS] L2: error 1003 is retried with backoff (0.25 s, 0.5 s) and returned without a last useless sleep.")


def test_fuel_retry_relogs_in_only_for_session_errors():
    for code, expect_runs, expect_force in ((5, 2, False), (1, 2, True), (4, 1, False), (1004, 1, False)):
        runs, forced = [], []

        def report(sid, *args):
            runs.append(sid)
            raise WialonAPIError("report failed") from WialonAPIError("wialon", code=code)

        with patch.object(wd, "login", side_effect=lambda force=False, session=None: forced.append(force) or "fresh"):
            try:
                wd._run_report_with_retry(report, "sid-a")
                raise AssertionError("expected WialonAPIError")
            except WialonAPIError:
                pass
        assert len(runs) == expect_runs, (code, runs)
        assert (True in forced) == expect_force, (code, forced)
        if code == 5:
            assert runs == ["sid-a", "sid-a"], "a non-session failure is retried on the current session"
    print("[PASS] M3: error 1 forces a new session; error 5 retries on the current one; errors 4 and 1004 are not rerun.")


if __name__ == "__main__":
    tests = [
        test_refresh_then_more_calls_use_current_sid,
        test_named_session_refresh_then_more_calls,
        test_concurrent_refresh_logs_in_once,
        test_refreshed_session_also_failing_returns_error_once,
        test_failed_named_login_never_uses_shared_sid,
        test_named_session_recovers_on_its_own_session,
        test_retired_lookup_survives_concurrent_eviction,
        test_exec_report_timeout_not_reexecuted,
        test_1003_gives_up_without_final_sleep,
        test_fuel_retry_relogs_in_only_for_session_errors,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"[FAIL] {t.__name__}: {exc}")
    _reset()
    sys.exit(1 if failed else 0)
