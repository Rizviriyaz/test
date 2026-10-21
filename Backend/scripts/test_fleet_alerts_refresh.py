"""
Offline tests for the /api/fleet/alerts background refresh (refresh-ahead within the 180 s freshness window).

  - expiry: < 120 s is a plain hit; 120-179 s serves the cached entry and starts one background rebuild
    that bypasses the health/safety caches; >= 180 s is never served and triggers a foreground build
  - refresh failure: the last good entry is kept unchanged, the failure is logged, retries back off 30 s
  - concurrency: many hits start one refresh; many cold callers run one build; a cold caller that arrives
    during a refresh waits for it instead of starting a second scan; at most one build runs at a time
  - stale data: an expired entry is never returned, even after a failed refresh; a failed cold build raises
    (502 at the route) and caches nothing; queued cold callers share the failure instead of re-scanning
  - accuracy: a background rebuild produces exactly the same alert list as a foreground build

Health and safety are mocked in app.fleet_analytics. Every outbound HTTP call is blocked by OfflineGuard.
Run from Backend/:  python scripts/test_fleet_alerts_refresh.py
"""
import logging
import os
import sys
import threading
import time
import traceback
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.fleet_analytics as fa  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402
from test_fleet_alerts_concurrency import EXPECTED, HEALTH, SAFETY  # noqa: E402

KEY = fa._ALERTS_CACHE_KEY
OLD = {"total_alerts": 1, "alerts": [{"id": "alt-1", "marker": "old"}]}


def _reset():
    fa._analytics_cache.clear()
    fa._alerts_refresh_failed_at = 0.0
    fa._alerts_last_error = None
    assert not fa._alerts_build_lock.locked(), "a build from a previous test is still running"


def _seed(data, age):
    fa._analytics_cache.set(KEY, {"data": data, "time": time.time() - age}, fa._ANALYTICS_CACHE_RETENTION_SECONDS)


def _wait_for_refresh(timeout=5):
    assert fa._alerts_build_lock.acquire(timeout=timeout), "background refresh did not finish"
    fa._alerts_build_lock.release()


class _Parts:
    """Mock health/safety recording call kwargs, overlap and (optionally) delay or failure."""

    def __init__(self, delay=0.0, fail=False, gate=None):
        self.delay, self.fail, self.gate = delay, fail, gate
        self.calls = []
        self.active = self.max_active = 0
        self._lock = threading.Lock()

    def _run(self, name, value, kwargs):
        with self._lock:
            self.calls.append((name, kwargs))
            if name == "health":
                self.active += 1
                self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            time.sleep(self.delay)
            if self.fail:
                raise WialonAPIError("boom")
            return value
        finally:
            if name == "health":
                with self._lock:
                    self.active -= 1

    def health(self, **kwargs):
        return self._run("health", HEALTH, kwargs)

    def safety(self, hours, **kwargs):
        assert hours == 24, hours
        return self._run("safety", SAFETY, kwargs)

    def patches(self):
        return (patch.object(fa, "get_fleet_health_analytics", side_effect=self.health),
                patch.object(fa, "get_safety_analytics", side_effect=self.safety))

    def builds(self):
        return sum(1 for name, _ in self.calls if name == "health")


def _alert_keys(res):
    return [(a["id"], a["alert_type"], a["severity"]) for a in res["alerts"]]


def test_young_entry_is_plain_hit():
    _reset()
    _seed(OLD, age=60)
    parts = _Parts()
    p1, p2 = parts.patches()
    with p1, p2:
        assert fa.get_fleet_alerts_analytics() is OLD
        assert not fa._alerts_build_lock.locked()
    assert parts.calls == [], parts.calls
    print("[PASS] Entry younger than 120 s is served with no refresh")


def test_refresh_window_serves_cached_then_replaces():
    _reset()
    _seed(OLD, age=150)
    parts = _Parts(delay=0.5)
    p1, p2 = parts.patches()
    with p1, p2:
        started = time.perf_counter()
        res = fa.get_fleet_alerts_analytics()
        elapsed = time.perf_counter() - started
        assert res is OLD, "request in the refresh window must get the cached entry"
        assert elapsed < 0.2, f"hit blocked on the refresh ({elapsed:.3f}s)"
        _wait_for_refresh()
    assert parts.builds() == 1, parts.calls
    assert sorted(parts.calls, key=lambda c: c[0]) == [("health", {"use_cache": False}), ("safety", {"use_cache": False})], parts.calls
    entry = fa._analytics_cache.get(KEY)
    assert time.time() - entry["time"] < 5, "refreshed entry must be stamped now"
    assert _alert_keys(entry["data"]) == EXPECTED, _alert_keys(entry["data"])
    print(f"[PASS] 120-179 s: cached entry served in {elapsed * 1000:.1f} ms, one cache-bypassing rebuild replaced it")


def test_expired_entry_is_never_served():
    _reset()
    _seed(OLD, age=181)
    parts = _Parts()
    p1, p2 = parts.patches()
    with p1, p2:
        res = fa.get_fleet_alerts_analytics()
    assert res is not OLD and _alert_keys(res) == EXPECTED
    # Foreground build keeps the original call signatures (component caches allowed)
    assert sorted(parts.calls, key=lambda c: c[0]) == [("health", {}), ("safety", {})], parts.calls
    print("[PASS] >= 180 s: expired entry is not served; a foreground build runs as before")


def test_refresh_failure_keeps_last_good_and_logs():
    _reset()
    _seed(OLD, age=150)
    before = fa._analytics_cache.get(KEY)
    parts = _Parts(fail=True)
    p1, p2 = parts.patches()
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logging.getLogger().addHandler(handler)
    try:
        with p1, p2:
            assert fa.get_fleet_alerts_analytics() is OLD
            _wait_for_refresh()
            # Within the 30 s backoff further hits do not start another refresh
            assert fa.get_fleet_alerts_analytics() is OLD
            assert not fa._alerts_build_lock.locked()
            assert parts.builds() == 1, parts.calls
            # After the backoff, the next hit retries
            fa._alerts_refresh_failed_at = time.monotonic() - fa._ALERTS_REFRESH_RETRY_SECONDS - 1
            assert fa.get_fleet_alerts_analytics() is OLD
            _wait_for_refresh()
            assert parts.builds() == 2, parts.calls
    finally:
        logging.getLogger().removeHandler(handler)
    after = fa._analytics_cache.get(KEY)
    assert after is before and after["data"] is OLD, "failed refresh must not touch the cached entry"
    failures = [r for r in records if r.levelno >= logging.ERROR and "background refresh" in r.getMessage()]
    assert len(failures) == 2 and failures[0].exc_info, records
    print("[PASS] Failed refresh keeps the last good entry, logs with traceback, backs off 30 s, then retries")


def test_expired_after_failed_refresh_is_not_served():
    _reset()
    _seed(OLD, age=181)
    parts = _Parts(fail=True)
    p1, p2 = parts.patches()
    with p1, p2:
        try:
            fa.get_fleet_alerts_analytics()
        except WialonAPIError:
            pass
        else:
            raise AssertionError("expired entry or fabricated result returned instead of an error")
    entry = fa._analytics_cache.get(KEY)
    assert entry is not None and entry["data"] is OLD, "failed build must not overwrite or clear the entry"
    assert fa.get_cached(KEY, ttl_seconds=fa._ALERTS_TTL_SECONDS) is None
    print("[PASS] Expired entry is never served; a failed build raises and caches nothing")


def test_concurrent_hits_start_one_refresh():
    _reset()
    _seed(OLD, age=150)
    gate = threading.Event()
    parts = _Parts(gate=gate)
    p1, p2 = parts.patches()
    results = []
    with p1, p2:
        threads = [threading.Thread(target=lambda: results.append(fa.get_fleet_alerts_analytics())) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        assert len(results) == 12 and all(r is OLD for r in results)
        gate.set()
        _wait_for_refresh()
    assert parts.builds() == 1, parts.calls
    print("[PASS] 12 concurrent hits in the refresh window start exactly one refresh")


def test_concurrent_cold_callers_share_one_build():
    _reset()
    parts = _Parts(delay=0.3)
    p1, p2 = parts.patches()
    results = []
    with p1, p2:
        threads = [threading.Thread(target=lambda: results.append(fa.get_fleet_alerts_analytics())) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert len(results) == 8 and all(r is results[0] for r in results)
    assert parts.builds() == 1 and parts.max_active == 1, (parts.builds(), parts.max_active)
    print("[PASS] 8 concurrent cold callers run a single build and share its result")


def test_cold_caller_waits_for_running_refresh():
    _reset()
    _seed(OLD, age=170)
    gate = threading.Event()
    parts = _Parts(gate=gate)
    p1, p2 = parts.patches()
    cold_result = []
    with p1, p2:
        assert fa.get_fleet_alerts_analytics() is OLD  # starts the refresh, which blocks on the gate
        _seed(OLD, age=181)  # entry expires while the refresh is still running
        cold = threading.Thread(target=lambda: cold_result.append(fa.get_fleet_alerts_analytics()))
        cold.start()
        time.sleep(0.2)
        assert cold.is_alive(), "cold caller must wait for the running refresh"
        gate.set()
        cold.join(5)
    assert parts.builds() == 1 and parts.max_active == 1, (parts.builds(), parts.max_active)
    assert cold_result and _alert_keys(cold_result[0]) == EXPECTED
    print("[PASS] A cold caller during a refresh waits for it; no second scan, never overlapping")


def test_queued_cold_callers_share_failure():
    _reset()
    gate = threading.Event()
    parts = _Parts(gate=gate, fail=True)
    p1, p2 = parts.patches()
    errors = []

    def call():
        try:
            fa.get_fleet_alerts_analytics()
        except WialonAPIError as exc:
            errors.append(exc)

    with p1, p2:
        threads = [threading.Thread(target=call) for _ in range(5)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        gate.set()
        for t in threads:
            t.join(5)
        assert len(errors) == 5 and parts.builds() == 1, (len(errors), parts.builds())
        # A new caller after the failure is not handed the old error: it builds again
        parts.fail = False
        assert _alert_keys(fa.get_fleet_alerts_analytics()) == EXPECTED
    assert parts.builds() == 2
    print("[PASS] Callers queued behind a failed build share its error; the next caller retries")


def test_use_cache_false_bypasses_component_caches():
    _reset()
    sentinel = RuntimeError("bypassed cache")
    fa.set_cached("fleet_health_analytics", {"marker": "health"})
    fa.set_cached("safety_analytics_24", {"marker": "safety"})
    try:
        assert fa.get_fleet_health_analytics() == {"marker": "health"}
        assert fa.get_safety_analytics(24) == {"marker": "safety"}
        with patch.object(fa, "login", side_effect=sentinel):
            for fn in (lambda: fa.get_fleet_health_analytics(use_cache=False),
                       lambda: fa.get_safety_analytics(24, use_cache=False)):
                try:
                    fn()
                except RuntimeError as exc:
                    assert exc is sentinel
                else:
                    raise AssertionError("use_cache=False returned the cached value")
    finally:
        fa._analytics_cache.clear()
    print("[PASS] use_cache=False skips the health/safety caches; the default still uses them")


def main() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_young_entry_is_plain_hit,
        test_refresh_window_serves_cached_then_replaces,
        test_expired_entry_is_never_served,
        test_refresh_failure_keeps_last_good_and_logs,
        test_expired_after_failed_refresh_is_not_served,
        test_concurrent_hits_start_one_refresh,
        test_concurrent_cold_callers_share_one_build,
        test_cold_caller_waits_for_running_refresh,
        test_queued_cold_callers_share_failure,
        test_use_cache_false_bypasses_component_caches,
    ]
    passed = 0
    try:
        for test in tests:
            try:
                test()
                passed += 1
            except Exception:
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
                _wait_for_refresh(10)
    finally:
        guard.stop()
        fa._analytics_cache.clear()
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
