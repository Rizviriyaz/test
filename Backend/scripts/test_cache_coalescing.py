"""
Offline tests for request coalescing and cache policy (performance batch 2):

  - /api/vehicles: overlapping cold requests share one Wialon read
  - fleet analytics builders: concurrent identical calls run once and share the result or the exception;
    nothing is retained, different arguments never share
  - auth: concurrent validations of one token make one Supabase round trip pair; a rejection is shared, not cached
  - closed report windows (ended before today's UAE midnight) are cached for CLOSED_WINDOW_TTL_SECONDS; windows
    reaching into today keep their short TTL; cache retention covers the longer TTL
  - safety: a partial result keeps its flags and is cached TRANSIENT_PARTIAL_TTL_SECONDS only
  - utilization: unmatched rows (permanent) use the normal TTL, unreadable cells (transient) 30 s, the raw-message
    fallback is never cached; partial flags are unchanged
  - fleet trips: additive `retrieved_at`; existing fields unchanged; closed windows cached longer; hours-N windows
    (no mileage counterpart) run their own report with the normal 180 s TTL
  - Distance (/api/dashboard/mileage) and Trips (/api/fleet/trips) share one Group Trips run per report group and
    window, report the same window and retrieval time, and expire together; a failed run is never cached; a stale
    (last good) mileage result keeps the snapshot's Wialon read time
  - analytics builders share one groups / units / calc_last read within FLEET_DISCOVERY_TTL_SECONDS

Wialon and Supabase are mocked. Every outbound HTTP call is blocked by OfflineGuard.
Run from Backend/:  python scripts/test_cache_coalescing.py
"""
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.auth as auth  # noqa: E402
import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402
from app.utils.datetime_helpers import CLOSED_WINDOW_TTL_SECONDS, UAE_TZ, closed_window_ttl  # noqa: E402


def _reset():
    main._cache.clear()
    main._failure_backoff.clear()
    fa._analytics_cache.clear()
    auth._token_cache.clear()


def _run_concurrently(fn, n=5):
    """Start n threads calling fn() at once; return their results (exceptions are returned, not raised)."""
    results = [None] * n
    start = threading.Barrier(n)

    def worker(i):
        start.wait()
        try:
            results[i] = fn()
        except BaseException as exc:  # noqa: BLE001 - collected for assertions
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results


def _slow(value, delay=0.2):
    def call(*_args, **_kwargs):
        time.sleep(delay)
        return value
    return call


FLEET = [
    {"vehicle_id": 1, "vehicle_name": "UNIT-A", "group": "Logistics", "status": "MOVING"},
    {"vehicle_id": 2, "vehicle_name": "UNIT-B", "group": "Logistics", "status": "STOPPED"},
]
UNITS = [{"id": 1, "nm": "UNIT-A"}, {"id": 2, "nm": "UNIT-B"}]
GROUPS = [{"id": 99, "nm": "All Unit", "u": [1, 2]}]


def _wialon_fleet_patches(module, live_fleet=FLEET):
    return [
        patch.object(module, "login", return_value="sid"),
        patch.object(module, "get_vehicle_groups", return_value=GROUPS),
        patch.object(module, "get_all_vehicles", return_value=UNITS),
        patch.object(module, "get_calculated_data", return_value=[]),
        patch.object(module, "get_vehicle_live_data", return_value=live_fleet),
    ]


class _Patches:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        return [p.start() for p in self.patches]

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


# ── /api/vehicles ─────────────────────────────────────────────────────────────

def test_vehicles_single_flight():
    _reset()
    search = MagicMock(side_effect=_slow(UNITS))
    with _Patches([
        patch.object(main, "login", return_value="sid"),
        patch.object(main, "get_all_vehicles", search),
        patch.object(main, "get_calculated_data", return_value=[]),
        patch.object(main, "get_vehicle_live_data", return_value=FLEET),
    ]):
        results = _run_concurrently(main.get_vehicles)
    assert search.call_count == 1, f"expected one Wialon read, got {search.call_count}"
    # Every caller gets the one shared read, which states when it was read (additive retrieved_at)
    assert all({k: r[k] for k in ("count", "vehicles")} == {"count": 2, "vehicles": FLEET} for r in results), results
    assert len({r["retrieved_at"] for r in results}) == 1 and isinstance(results[0]["retrieved_at"], int), results
    print("[PASS] /api/vehicles: overlapping cold requests share one Wialon read")


# ── Analytics builders ────────────────────────────────────────────────────────

def test_builders_are_coalesced():
    for fn in (fa.get_fleet_health_analytics, fa.get_fleet_utilization_analytics, fa.get_trips_mileage_analytics,
               fa.get_safety_analytics, fa.get_fleet_groups_analytics):
        assert hasattr(fn, "__wrapped__"), f"{fn.__name__} is not coalesced"

    calls = []

    @fa._coalesced
    def build(hours, use_cache=True):
        calls.append((hours, use_cache))
        time.sleep(0.2)
        return {"hours": hours, "use_cache": use_cache}

    results = _run_concurrently(lambda: build(24))
    assert calls == [(24, True)], calls
    assert all(r == {"hours": 24, "use_cache": True} for r in results)

    calls.clear()
    results = _run_concurrently(lambda: build(24, use_cache=False), n=3) + [build(48)]
    assert sorted(calls) == [(24, False), (48, True)], calls
    assert build(24) == {"hours": 24, "use_cache": True} and len(calls) == 3, "nothing is retained after a run"
    print("[PASS] Builders: identical concurrent calls run once; other arguments never share; nothing retained")


def test_builder_exception_shared_not_retained():
    attempts = []

    @fa._coalesced
    def failing():
        attempts.append(1)
        time.sleep(0.2)
        raise RuntimeError("wialon down")

    results = _run_concurrently(failing, n=4)
    assert len(attempts) == 1 and all(isinstance(r, RuntimeError) for r in results), (attempts, results)
    try:
        failing()
    except RuntimeError:
        pass
    assert len(attempts) == 2, "a failure is not retained: the next call retries"
    print("[PASS] Builders: a failure is shared by concurrent callers and retried by the next call")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _response(status, body):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = body
    return r


def test_auth_validation_coalesced():
    _reset()
    gets = []

    def fake_get(url, **_kwargs):
        gets.append(url)
        time.sleep(0.2)
        if url.endswith("/auth/v1/user"):
            return _response(200, {"id": "u1", "email": "a@x"})
        return _response(200, [{"id": "u1", "role": "viewer"}])

    env = {"SUPABASE_URL": "https://sb.example", "SUPABASE_ANON_KEY": "anon"}
    with patch.dict(os.environ, env), patch.object(auth._auth_session, "get", side_effect=fake_get):
        results = _run_concurrently(lambda: auth.authenticate_access_token("tok-1"))
        assert len(gets) == 2, f"one user + one profile request expected, got {gets}"
        assert all(r == {"id": "u1", "email": "a@x", "role": "viewer"} for r in results), results
        auth.authenticate_access_token("tok-1")
        assert len(gets) == 2, "a validated token is served from the token cache"
    print("[PASS] Auth: concurrent validations of one token make one Supabase round trip")


def test_auth_rejection_shared_not_cached():
    _reset()
    gets = []

    def fake_get(url, **_kwargs):
        gets.append(url)
        time.sleep(0.2)
        return _response(401, {})

    env = {"SUPABASE_URL": "https://sb.example", "SUPABASE_ANON_KEY": "anon"}
    with patch.dict(os.environ, env), patch.object(auth._auth_session, "get", side_effect=fake_get):
        results = _run_concurrently(lambda: auth.authenticate_access_token("bad"), n=4)
        assert len(gets) == 1, gets
        assert all(getattr(r, "status_code", None) == 401 for r in results), results
        try:
            auth.authenticate_access_token("bad")
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "status_code", None) == 401
        assert len(gets) == 2, "a rejected token is re-checked, never cached as valid"
    print("[PASS] Auth: a rejection is shared by concurrent callers and never cached")


# ── Closed-window TTL ─────────────────────────────────────────────────────────

def test_closed_window_ttl_rule():
    now = datetime(2026, 10, 27, 0, 0, 5, tzinfo=UAE_TZ)  # 5 s after UAE midnight
    today_start = int(now.replace(hour=0, minute=0, second=0).timestamp())
    with patch("app.utils.datetime_helpers.get_uae_now", return_value=now):
        assert closed_window_ttl(today_start - 1, 60) == CLOSED_WINDOW_TTL_SECONDS, "yesterday's end is closed"
        assert closed_window_ttl(today_start, 60) == 60, "a window reaching today stays open"
        assert closed_window_ttl(int(now.timestamp()), 180) == 180
        assert closed_window_ttl(today_start - 1, 4000) == 4000, "never shorter than the open TTL"
    print("[PASS] Closed-window TTL: only windows that ended before today's UAE midnight are cached longer")


def test_retention_covers_long_ttl():
    _reset()
    main.set_cache("k-closed", {"v": 1}, ttl_seconds=CLOSED_WINDOW_TTL_SECONDS)
    fa.set_cached("k-closed", {"v": 1}, ttl_seconds=CLOSED_WINDOW_TTL_SECONDS)
    later = time.time() + 1000  # past the 600 s default retention, inside the closed TTL
    with patch("time.time", return_value=later):
        assert main.cached("k-closed", ttl_seconds=CLOSED_WINDOW_TTL_SECONDS) == {"v": 1}
        assert main.cached("k-closed", ttl_seconds=60) is None, "a caller's shorter TTL still applies"
        assert fa.get_cached("k-closed", ttl_seconds=CLOSED_WINDOW_TTL_SECONDS) == {"v": 1}
    for cache in (main._cache, fa._analytics_cache):
        expires_at, _ = cache._data["k-closed"]
        assert expires_at - cache._now() > 1700, "retained at least as long as the entry's own TTL"
    print("[PASS] Cache retention covers a closed window's longer TTL")


def _mileage_patches(report):
    return [
        patch.object(main, "login", return_value="sid"),
        patch.object(main, "get_vehicle_groups", return_value=GROUPS),
        patch.object(main, "get_all_vehicles", return_value=UNITS),
        patch.object(main, "get_calculated_data", return_value=[]),
        patch.object(main, "resolve_all_units_group_id", return_value=99),
        patch.object(main, "execute_wialon_group_trips_report", report),
    ]


def test_mileage_closed_window_cached_longer():
    rows = [{"vehicle_name": "UNIT-A", "mileage_km": 12.5}, {"vehicle_name": "UNIT-B", "mileage_km": 0.0}]
    for timeframe, expected_reads in (("yesterday", 1), ("today", 2)):
        _reset()
        report = MagicMock(return_value=rows)
        with _Patches(_mileage_patches(report)):
            first = main.get_dashboard_mileage(timeframe=timeframe)
            with patch("time.time", return_value=time.time() + 600):  # past the 60 s mileage TTL
                second = main.get_dashboard_mileage(timeframe=timeframe)
        assert report.call_count == expected_reads, (timeframe, report.call_count)
        assert first["total_km"] == second["total_km"] == 12.5
        assert first["stale"] is False and first["incomplete_units"] == []
    print("[PASS] Mileage: yesterday is served from cache after 10 min; today is re-read after its 60 s TTL")


# ── Partial results ───────────────────────────────────────────────────────────

def test_safety_partial_cached_briefly_with_flags():
    _reset()
    reads = []

    def history(_sid, vid, hours=None):
        reads.append(vid)
        if vid == 2:
            raise RuntimeError("history unavailable")
        return []

    with _Patches(_wialon_fleet_patches(fa) + [patch.object(fa, "get_vehicle_history", side_effect=history)]):
        first = fa.get_safety_analytics(hours=24)
        second = fa.get_safety_analytics(hours=24)
    assert first["partial"] is True and first["failed_vehicles"] == ["UNIT-B"], first
    assert second is first and sorted(reads) == [1, 2], "the partial result is reused, not rescanned"
    assert fa._analytics_cache.get("safety_analytics_24")["ttl"] == fa.TRANSIENT_PARTIAL_TTL_SECONDS
    with patch("time.time", return_value=time.time() + fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1):
        assert fa.get_cached("safety_analytics_24", ttl_seconds=180) is None, "re-read after 30 s"
    print("[PASS] Safety: a partial result keeps its flags and is cached for 30 s only")


def _engine_row(name, motion=3600, idle=600, engine=4200, incomplete=None):
    row = {"vehicle_name": name, "in_motion_seconds": motion, "total_idle_seconds": idle,
           "engine_hours_seconds": engine}
    if incomplete:
        row["incomplete_fields"] = incomplete
    return row


def test_utilization_partial_cache_policy():
    cases = [
        ("permanent", {"vehicles": [_engine_row("UNIT-A"), _engine_row("UNIT-B"), _engine_row("GHOST")]}, None),
        ("transient", {"vehicles": [_engine_row("UNIT-A", incomplete=["total_idle_seconds"]), _engine_row("UNIT-B")]},
         fa.TRANSIENT_PARTIAL_TTL_SECONDS),
        ("complete", {"vehicles": [_engine_row("UNIT-A"), _engine_row("UNIT-B")]}, None),
    ]
    for label, report, expected_ttl in cases:
        _reset()
        with _Patches(_wialon_fleet_patches(fa) + [
            patch.object(fa, "execute_wialon_group_engine_idle_report", return_value=report),
        ]):
            res = fa.get_fleet_utilization_analytics(hours=24)
        entry = fa._analytics_cache.get("fleet_utilization_24")
        assert entry is not None, f"{label}: report result is cached"
        assert entry["ttl"] == expected_ttl, (label, entry["ttl"])
        assert res["source"] == "wialon_group_engine_report"
        assert res["partial"] is (label != "complete"), (label, res["partial"])
        if label == "transient":
            assert res["idle_unknown_vehicles"] == ["UNIT-A"] and res["total_idle_hours"] == 0.2, res

    _reset()
    with _Patches(_wialon_fleet_patches(fa) + [
        patch.object(fa, "execute_wialon_group_engine_idle_report", side_effect=RuntimeError("report failed")),
        patch.object(fa, "get_vehicle_history", return_value=[]),
    ]):
        res = fa.get_fleet_utilization_analytics(hours=24)
    assert res["source"] == "raw_message_fallback" and res["partial"] is True
    assert fa._analytics_cache.get("fleet_utilization_24") is None, "the raw-message fallback is never cached"
    print("[PASS] Utilization: permanent gaps use the normal TTL, transient 30 s, fallback never cached")


# ── Fleet trips ───────────────────────────────────────────────────────────────

def test_trips_retrieved_at_and_closed_ttl():
    rows = [{"vehicle_name": "UNIT-A", "trip_count": 3, "mileage_km": 40.0, "avg_speed": "40 km/h", "max_speed": "90 km/h"},
            {"vehicle_name": "UNIT-B", "trip_count": 0, "mileage_km": 0.0, "avg_speed": "0 km/h", "max_speed": "0 km/h"}]
    _reset()
    report = MagicMock(return_value=rows)
    with _Patches(_wialon_fleet_patches(fa) + [
        patch.object(fa, "execute_wialon_group_trips_report", report),
        patch.object(fa, "resolve_all_units_group_id", return_value=99),
    ]):
        before = int(time.time())
        res = fa.get_trips_mileage_analytics(hours=24)
        assert isinstance(res["retrieved_at"], int) and res["retrieved_at"] >= before
        assert res["summary"]["total_trips"] == 3 and res["summary"]["total_distance_km"] == 40.0
        assert {"period_hours", "from_time", "to_time", "source", "summary", "by_group", "vehicles"} <= set(res)
        # hours-N has no Dashboard mileage counterpart: its own report run (as before), no snapshot, normal TTL
        assert report.call_args.kwargs["group_id"] == fa.GROUP_ID_ALL_UNITS
        assert not any(str(k).startswith("group_trips_snapshot") for k in fa._analytics_cache._data)
        assert fa._analytics_cache.get("trips_mileage_v3_hours-24_uae")["ttl"] == 180, "normal trips TTL"
        with patch("time.time", return_value=time.time() + fa.GROUP_TRIPS_SNAPSHOT_TTL_SECONDS + 1):
            fa.get_trips_mileage_analytics(hours=24)
        assert report.call_count == 1, "still cached after the 60 s snapshot TTL"

        y = fa.get_trips_mileage_analytics(timeframe="yesterday")
    key = next(k for k in fa._analytics_cache._data if k.startswith("trips_mileage_v3_tf-yesterday"))
    assert fa._analytics_cache.get(key)["ttl"] > CLOSED_WINDOW_TTL_SECONDS - 5, "closed window: the long TTL"
    assert y["to_time"] < int(datetime.now(UAE_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    print("[PASS] Fleet trips: additive retrieved_at; hours-N keeps its own run and 180 s TTL; yesterday cached for "
          "the closed-window TTL")


# ── Batch 3: shared Group Trips snapshot and fleet discovery ─────────────────

def test_distance_and_trips_share_one_report_run():
    rows = [{"vehicle_name": "UNIT-A", "trip_count": 3, "mileage_km": 40.0, "avg_speed": "40 km/h", "max_speed": "90 km/h"},
            {"vehicle_name": "UNIT-B", "trip_count": 0, "mileage_km": 0.0, "avg_speed": "0 km/h", "max_speed": "0 km/h"}]
    _reset()
    report = MagicMock(side_effect=_slow(rows, delay=0.05))
    with _Patches(_mileage_patches(report) + _wialon_fleet_patches(fa) + [
        patch.object(fa, "execute_wialon_group_trips_report", report),
        patch.object(fa, "resolve_all_units_group_id", return_value=99),
    ]):
        mileage = main.get_dashboard_mileage(timeframe="today")
        trips = fa.get_trips_mileage_analytics(timeframe="today")
        assert report.call_count == 1, f"one Group Trips run for Distance and Trips, got {report.call_count}"
        assert (mileage["effective_from"], mileage["effective_to"]) == (trips["from_time"], trips["to_time"])
        assert mileage["retrieved_at"] == trips["retrieved_at"], "same report run"
        assert mileage["total_km"] == trips["summary"]["total_distance_km"] == 40.0
        assert trips["summary"]["total_trips"] == 3
        # Both results expire with the snapshot, so the next poll reads one new run for both
        with patch("time.time", return_value=time.time() + fa.GROUP_TRIPS_SNAPSHOT_TTL_SECONDS + 1):
            main.get_dashboard_mileage(timeframe="today")
            fa.get_trips_mileage_analytics(timeframe="today")
        assert report.call_count == 2, report.call_count

        # A different report group (the Dashboard group filter) or window never shares
        main.get_dashboard_mileage(timeframe="today", group_id=7)
        fa.get_trips_mileage_analytics(timeframe="yesterday")
        assert report.call_count == 4, report.call_count
    print("[PASS] Distance and Trips: one Group Trips run per window, same window and retrieval time, expiring together")


def test_snapshot_failure_not_cached():
    _reset()
    report = MagicMock(side_effect=main.WialonAPIError("report failed"))
    with _Patches(_mileage_patches(report)):
        for _ in range(2):
            try:
                main.get_dashboard_mileage(timeframe="today")
                raise AssertionError("expected an error without a last good result")
            except main.HTTPException as exc:
                assert exc.status_code == 502
            main._failure_backoff.clear()
    assert report.call_count == 2, "a failed report is never cached; the next request re-runs it"
    assert not any(str(k).startswith("group_trips_snapshot") for k in fa._analytics_cache._data)
    print("[PASS] Snapshot: a failed Group Trips report is not cached and is reported, never as zero")


def test_stale_mileage_keeps_snapshot_read_time():
    rows = [{"vehicle_name": "UNIT-A", "mileage_km": 12.5}, {"vehicle_name": "UNIT-B", "mileage_km": 0.0}]
    _reset()
    report = MagicMock(return_value=rows)
    read_at = int(time.time()) - 50  # the snapshot was read before the mileage result was cached
    with _Patches(_mileage_patches(report) + [patch.object(fa, "now_timestamp", return_value=read_at)]):
        good = main.get_dashboard_mileage(timeframe="today")
        assert good["retrieved_at"] == read_at and good["stale"] is False
        report.side_effect = main.WialonAPIError("report failed")
        with patch("time.time", return_value=time.time() + fa.GROUP_TRIPS_SNAPSHOT_TTL_SECONDS + 1):
            stale = main.get_dashboard_mileage(timeframe="today")
    assert report.call_count == 2 and stale["stale"] is True and stale["total_km"] == good["total_km"]
    assert stale["retrieved_at"] == read_at, f"the Wialon read time, not when it was cached: {stale['retrieved_at']}"
    print("[PASS] Stale mileage: the last good result keeps its snapshot's Wialon read time")


def test_fleet_discovery_shared_between_builders():
    _reset()
    groups = MagicMock(return_value=GROUPS)
    units = MagicMock(return_value=UNITS)
    calc = MagicMock(return_value=[])
    with _Patches([
        patch.object(fa, "login", return_value="sid"),
        patch.object(fa, "get_vehicle_groups", groups),
        patch.object(fa, "get_all_vehicles", units),
        patch.object(fa, "get_calculated_data", calc),
        patch.object(fa, "get_vehicle_live_data", return_value=FLEET),
        patch.object(fa, "get_vehicle_history", return_value=[]),
        patch.object(fa, "execute_wialon_group_engine_idle_report",
                     return_value={"vehicles": [_engine_row("UNIT-A"), _engine_row("UNIT-B")]}),
    ]):
        fa.get_fleet_health_analytics()
        fa.get_safety_analytics(hours=24)
        fa.get_fleet_utilization_analytics(hours=24)
        assert (groups.call_count, units.call_count, calc.call_count) == (1, 1, 1), "one discovery for three builders"
        with patch("time.time", return_value=time.time() + fa.FLEET_DISCOVERY_TTL_SECONDS + 1):
            fa.get_fleet_health_analytics(use_cache=False)
        assert units.call_count == 2, "re-read after FLEET_DISCOVERY_TTL_SECONDS"
    print("[PASS] Fleet discovery: one groups/units/calc_last read shared by builders within 5 s")


def test_ttlcache_refresh_is_single_flight_with_get_or_compute():
    from app.utils.ttl_cache import TTLCache

    clock = [100.0]
    cache = TTLCache(clock=lambda: clock[0])
    cache.set("k", "OLD", 10)
    computes, release = [], threading.Event()

    def slow(value):
        def compute():
            computes.append(value)
            release.wait(5)
            return value
        return compute

    refresher = threading.Thread(target=lambda: cache.refresh("k", slow("NEW"), lambda v: 10))
    refresher.start()
    while not computes:
        time.sleep(0.01)
    assert cache.get("k") == "OLD", "the current entry is served while it refreshes"
    clock[0] += 11  # the entry expires during the refresh
    results = [None] * 4

    def miss(i):
        results[i] = cache.get_or_compute("k", slow("DUPLICATE"), lambda v: 10)

    misses = [threading.Thread(target=miss, args=(i,)) for i in range(4)]
    for t in misses:
        t.start()
    time.sleep(0.2)
    release.set()
    refresher.join(5)
    for t in misses:
        t.join(5)
    assert computes == ["NEW"], ("misses during a refresh join it instead of computing", computes)
    assert results == ["NEW"] * 4 and cache.get("k") == "NEW", results
    assert cache._states == {}, "per-key state dropped once nobody waits"
    print("[PASS] TTLCache.refresh: the old entry is served meanwhile; 4 misses after it expired join the refresh (1 compute)")


def test_ttlcache_refresh_failure_keeps_entry_and_is_not_cached():
    from app.utils.ttl_cache import TTLCache

    clock = [100.0]
    cache = TTLCache(clock=lambda: clock[0])
    cache.set("k", "OLD", 10)

    def failing():
        raise RuntimeError("Wialon down")

    try:
        cache.refresh("k", failing, lambda v: 10)
    except RuntimeError:
        pass
    else:
        raise AssertionError("a failed refresh must raise")
    assert cache.get("k") == "OLD", "a failed refresh leaves the current entry in place"
    clock[0] += 11
    assert cache.get("k") is None, "and it still expires on its own TTL"
    assert cache.get_or_compute("k", lambda: "FRESH", lambda v: 10) == "FRESH", "the next miss computes again"
    assert cache.refresh("k", lambda: "ZERO-TTL", lambda v: 0) == "ZERO-TTL" and cache.get("k") == "FRESH", \
        "a refresh whose TTL is 0 stores nothing, as set() does"
    print("[PASS] TTLCache.refresh: a failure is raised, not cached, keeps the old entry until its own expiry")


def main_entry():
    guard = OfflineGuard().start()
    tests = [
        test_vehicles_single_flight,
        test_builders_are_coalesced,
        test_builder_exception_shared_not_retained,
        test_auth_validation_coalesced,
        test_auth_rejection_shared_not_cached,
        test_closed_window_ttl_rule,
        test_retention_covers_long_ttl,
        test_mileage_closed_window_cached_longer,
        test_safety_partial_cached_briefly_with_flags,
        test_utilization_partial_cache_policy,
        test_trips_retrieved_at_and_closed_ttl,
        test_distance_and_trips_share_one_report_run,
        test_snapshot_failure_not_cached,
        test_stale_mileage_keeps_snapshot_read_time,
        test_fleet_discovery_shared_between_builders,
        test_ttlcache_refresh_is_single_flight_with_get_or_compute,
        test_ttlcache_refresh_failure_keeps_entry_and_is_not_cached,
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
    finally:
        guard.stop()
        _reset()
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main_entry())
