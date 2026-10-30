"""
Offline regression tests for Fuel Performance load fan-out (MOCKED / offline).

  - InFlight: concurrent identical calls share one computation (result or exception); nothing is retained
  - fuel analytics scope: concurrent requests with the same filters share one Supabase read and one Wialon unit
    search and each caller gets its own row copies; the read is kept ANALYTICS_READ_TTL_S and the unit list
    FUEL_UNITS_TTL_S, then read afresh
  - invalidation: any fuel row insert or import commit (applied, refused or unknown) clears the kept reads, and a read
    running while that happens is not kept; failures and an empty unit list are never kept
  - race: rows read before a write and stored in the instant after its clear are never served afterwards

Run from Backend/:  python scripts/test_fuel_shared_reads.py
"""
import asyncio
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import reset_fuel_read_caches, run  # noqa: E402

import app.fuel.db as fuel_db  # noqa: E402
import app.fuel.routes as routes  # noqa: E402
from app.utils.ttl_cache import InFlight  # noqa: E402

# August monthly-summary rows: the fuel reporting source the shared scope classifies
TXS = [
    {"id": 1, "vehicle_identifier": "V-1", "transaction_date": "2026-08-31T00:00:00", "litres": 50.0, "total_cost": 150.0,
     "date_basis": "reporting_month_end"},
    {"id": 2, "vehicle_identifier": "V-2", "transaction_date": "2026-08-31T00:00:00", "litres": 40.0, "total_cost": 120.0,
     "date_basis": "reporting_month_end"},
]
UNITS = [{"id": 1, "nm": "V-1"}, {"id": 2, "nm": "V-2"}]


def _concurrently(n, fn):
    results, errors = [None] * n, [None] * n

    def call(i):
        try:
            results[i] = fn()
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors[i] = exc

    threads = [threading.Thread(target=call, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def test_inflight_shares_and_retains_nothing():
    flight, calls = InFlight(), []

    def slow():
        calls.append(1)
        time.sleep(0.2)
        return len(calls)

    results, _ = _concurrently(4, lambda: flight.run("k", slow))
    assert len(calls) == 1 and results == [1, 1, 1, 1], (calls, results)
    assert flight.run("k", slow) == 2, "a call after the shared one finished must compute afresh"

    def boom():
        calls.append(1)
        time.sleep(0.2)
        raise RuntimeError("upstream down")

    calls.clear()
    _, errors = _concurrently(3, lambda: flight.run("e", boom))
    assert len(calls) == 1 and all(isinstance(e, RuntimeError) for e in errors), (calls, errors)
    assert not flight._calls, "no entry may outlive its computation"
    print("[PASS] InFlight: one computation per concurrent key (value or exception), nothing retained afterwards.")


def test_scope_reads_shared_across_concurrent_requests():
    reads, searches = [], []

    def fetch(filters, token):
        reads.append(dict(filters))
        time.sleep(0.2)
        return [dict(t) for t in TXS]

    def units(sid):
        searches.append(sid)
        time.sleep(0.1)
        return list(UNITS)

    filters = {"date_from": None, "date_to": None, "vehicle_identifier": None, "fuel_type": None}

    async def page_load():
        return await asyncio.gather(*(routes._build_scope(dict(filters), "tok") for _ in range(5)))

    with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "service"}), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", side_effect=fetch), \
         patch("app.fuel.routes.get_all_vehicles", side_effect=units), \
         patch("app.fuel.routes.login", return_value="sid"):
        scopes = asyncio.run(page_load())
        assert len(reads) == 1 and len(searches) == 1, (len(reads), len(searches))
        assert all(len(s.matched) == 2 for s in scopes), "every request classifies the same transactions"
        assert all(a.matched[0] is not b.matched[0] for a, b in zip(scopes, scopes[1:])), "rows are per-request copies"
        asyncio.run(routes._build_scope(dict(filters), "tok"))
        assert len(reads) == 1 and len(searches) == 1, "a later request within the TTLs reuses the kept read and units"

        reads.clear()
        other = dict(filters, date_from="2026-08-04")

        async def mixed():
            return await asyncio.gather(routes._build_scope(dict(filters), "tok"), routes._build_scope(other, "tok"))

        asyncio.run(mixed())
        # The first filter's read is still kept; a different filter never shares it and reads on its own
        assert reads == [other], ("different filters must never share a read", reads)
    print("[PASS] Scope: concurrent same-filter requests share 1 Supabase read + 1 unit search; later and other-filter reads are separate.")


def test_scope_reads_keyed_by_token_without_service_role():
    reads = []

    def fetch(filters, token):
        reads.append(token)
        time.sleep(0.2)
        return [dict(t) for t in TXS]

    filters = {"date_from": None, "date_to": None, "vehicle_identifier": None, "fuel_type": None}

    async def two_users():
        return await asyncio.gather(routes._build_scope(dict(filters), "user-a"), routes._build_scope(dict(filters), "user-b"))

    env = {k: v for k, v in os.environ.items() if k != "SUPABASE_SERVICE_ROLE_KEY"}
    with patch.dict(os.environ, env, clear=True), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", side_effect=fetch), \
         patch("app.fuel.routes.get_all_vehicles", return_value=UNITS), \
         patch("app.fuel.routes.login", return_value="sid"):
        asyncio.run(two_users())
    assert sorted(reads) == ["user-a", "user-b"], reads
    print("[PASS] Scope: reads made with a caller's token (row-level security) are never shared across tokens.")


FILTERS = {"date_from": "2026-08-01", "date_to": "2026-08-31", "vehicle_identifier": None, "fuel_type": None}


class _Clock:
    """A settable monotonic clock for one TTLCache, so expiry is tested without sleeping."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _scope_patches(fetch, units):
    return (patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "service"}),
            patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", side_effect=fetch),
            patch("app.fuel.routes.get_all_vehicles", side_effect=units),
            patch("app.fuel.routes.login", return_value="sid"))


def _scope():
    return asyncio.run(routes._build_scope(dict(FILTERS), "tok"))


def test_kept_reads_expire_after_their_ttls():
    reads, searches = [], []
    read_clock, units_clock = _Clock(), _Clock()
    p_env, p_fetch, p_units, p_login = _scope_patches(lambda f, t: reads.append(1) or [dict(t) for t in TXS],
                                                      lambda sid: searches.append(1) or list(UNITS))
    with p_env, p_fetch, p_units, p_login, \
            patch.object(fuel_db._ANALYTICS_READ_CACHE, "_clock", read_clock), \
            patch.object(routes._UNITS_CACHE, "_clock", units_clock):
        _scope()
        read_clock.now += fuel_db.ANALYTICS_READ_TTL_S - 1
        units_clock.now += fuel_db.ANALYTICS_READ_TTL_S - 1
        assert len(_scope().matched) == 2 and (len(reads), len(searches)) == (1, 1), "reused inside both TTLs"
        read_clock.now += 1  # transactions expire at ANALYTICS_READ_TTL_S; the unit list is still fresh
        _scope()
        assert (len(reads), len(searches)) == (2, 1), (reads, searches)
        units_clock.now += routes.FUEL_UNITS_TTL_S - fuel_db.ANALYTICS_READ_TTL_S + 1  # past FUEL_UNITS_TTL_S
        _scope()
        assert len(searches) == 2, "the unit list is searched again after FUEL_UNITS_TTL_S"
    assert fuel_db.ANALYTICS_READ_TTL_S == 30 and routes.FUEL_UNITS_TTL_S == 60
    print("[PASS] Kept reads: transactions reused for < 30 s then re-read; Wialon units reused for < 60 s then re-searched.")


def test_writes_and_commits_clear_kept_reads():
    reads = []
    p_env, p_fetch, p_units, p_login = _scope_patches(lambda f, t: reads.append(1) or [dict(t) for t in TXS],
                                                      lambda sid: list(UNITS))

    class Ok:
        ok, status_code = True, 200

        def json(self):
            return {"superseded": 0, "committed": 2}

    class Refused:
        ok, status_code, text = False, 400, "refused"

    import requests
    writes = [
        ("row insert", lambda: fuel_db._insert_batches([{"id": 9}], 500, {}, None, None, None, None)),
        ("commit", lambda: fuel_db.commit_fuel_import(7, [], 2)),
        ("refused commit", lambda: fuel_db.commit_fuel_import(7, [], 2)),
        ("commit with unknown outcome", lambda: fuel_db.commit_fuel_import(7, [], 2)),
    ]
    posts = [Ok(), Refused(), requests.ReadTimeout("no answer")]
    with p_env, p_fetch, p_units, p_login, \
            patch("app.fuel.db._insert_chunk", return_value=(1, 0, None)), \
            patch("app.fuel.db.requests.post", side_effect=posts):
        for what, write in writes:
            reset_fuel_read_caches()
            reads.clear()
            _scope()
            _scope()
            assert len(reads) == 1, f"before the {what}: one kept read ({len(reads)} reads)"
            try:
                write()
            except fuel_db.FuelCommitError:
                pass  # the refused commit
            _scope()
            assert len(reads) == 2, f"after the {what} the next request reads afresh"
    print("[PASS] Invalidation: a row insert and a commit (applied, refused or with unknown outcome) clear kept reads.")


def test_read_running_during_a_write_is_not_kept():
    reads = []
    release = threading.Event()

    def fetch(f, t):
        reads.append(1)
        if len(reads) == 1:
            release.wait(5)  # the first read is still running when the import commits
        return [dict(t) for t in TXS]

    p_env, p_fetch, p_units, p_login = _scope_patches(fetch, lambda sid: list(UNITS))
    with p_env, p_fetch, p_units, p_login:
        reader = threading.Thread(target=_scope)
        reader.start()
        while not reads:
            time.sleep(0.01)
        fuel_db.clear_fuel_read_caches()  # what every write does
        release.set()
        reader.join(5)
        _scope()
        assert len(reads) == 2, "a read that overlapped a write must not answer later requests"
        _scope()
        assert len(reads) == 2, "a read that started after the write is kept"
    print("[PASS] A read running while fuel rows were written is not kept; the next read is.")


def test_write_between_ttl_and_store_is_never_served():
    # The exact interleaving: the read finished and its TTL was computed (no write yet), then an import's
    # clear_fuel_read_caches() ran, then TTLCache.set stored the pre-write rows after the clear
    cache, real_set, fired = fuel_db._ANALYTICS_READ_CACHE, fuel_db._ANALYTICS_READ_CACHE.set, []

    def set_after_a_write(key, value, ttl_seconds):
        if not fired:
            fired.append(ttl_seconds)
            fuel_db.clear_fuel_read_caches()
        return real_set(key, value, ttl_seconds)

    with patch.object(cache, "set", side_effect=set_after_a_write):
        first = fuel_db.cached_analytics_read(("k",), lambda: ["PRE-WRITE"])
    assert fired == [fuel_db.ANALYTICS_READ_TTL_S], fired
    assert first == ["PRE-WRITE"], "the request that read before the write gets its own read, as without a cache"
    assert cache.get((("k",), (fuel_db.schema_v2_enabled(), fuel_db.replace_enabled(),
                                 fuel_db.summary_table_enabled()))) is not None, "the race did store the pre-write rows"
    reads = []
    after = fuel_db.cached_analytics_read(("k",), lambda: reads.append(1) or ["POST-WRITE"])
    assert after == ["POST-WRITE"] and len(reads) == 1, "pre-write rows stored after the clear must not be served"
    again = fuel_db.cached_analytics_read(("k",), lambda: reads.append(1) or ["UNUSED"])
    assert again == ["POST-WRITE"] and len(reads) == 1, "the post-write read is kept"

    # The same interleaving through a Fuel Performance scope
    reset_fuel_read_caches()
    fired.clear()
    data = {"rows": TXS[:1]}
    p_env, p_fetch, p_units, p_login = _scope_patches(lambda f, t: [dict(r) for r in data["rows"]], lambda sid: list(UNITS))
    with p_env, p_fetch, p_units, p_login:
        with patch.object(cache, "set", side_effect=set_after_a_write):
            assert len(_scope().matched) == 1
        data["rows"] = TXS  # the import added V-2's transaction
        assert len(_scope().matched) == 2, "the next request after the import sees the imported rows"
    assert cache.max_entries == 16, cache.max_entries
    print("[PASS] Race: pre-write rows stored in the instant after a write's clear are never served; the next request "
          "reads afresh (direct and via the scope). Capacity is 16 entries.")


def test_failures_and_empty_unit_lists_are_not_kept():
    reads, searches = [], []

    def fetch(f, t):
        reads.append(1)
        if len(reads) == 1:
            raise fuel_db.FuelDataFetchError("Supabase down")
        return [dict(t) for t in TXS]

    def units(sid):
        searches.append(1)
        return [] if len(searches) == 1 else list(UNITS)

    p_env, p_fetch, p_units, p_login = _scope_patches(fetch, units)
    with p_env, p_fetch, p_units, p_login, patch.object(fuel_db._ANALYTICS_READ_CACHE, "error_share_seconds", 0):
        try:
            _scope()
        except Exception as exc:  # noqa: BLE001 - the 503 of a failed read
            assert getattr(exc, "status_code", None) == 503, exc
        else:
            raise AssertionError("a failed read must answer 503")
        first = _scope()
        assert len(reads) == 2, "a failed read is not kept: the next request reads again"
        assert not first.matched, "no Wialon units this time: nothing matched"
        second = _scope()
        assert len(searches) == 2 and len(second.matched) == 2, "an empty unit list is not kept"
        _scope()
        assert len(searches) == 2, "a non-empty unit list is kept"
    print("[PASS] A failed read and an empty Wialon unit list are never kept; the next request retries.")


def test_concurrent_requests_after_expiry_share_one_reread():
    reads = []
    clock = _Clock()

    def fetch(f, t):
        reads.append(1)
        time.sleep(0.2)
        return [dict(t) for t in TXS]

    async def page_load():
        return await asyncio.gather(*(routes._build_scope(dict(FILTERS), "tok") for _ in range(6)))

    p_env, p_fetch, p_units, p_login = _scope_patches(fetch, lambda sid: list(UNITS))
    with p_env, p_fetch, p_units, p_login, patch.object(fuel_db._ANALYTICS_READ_CACHE, "_clock", clock):
        asyncio.run(page_load())
        clock.now += fuel_db.ANALYTICS_READ_TTL_S
        scopes = asyncio.run(page_load())
    assert len(reads) == 2, reads
    assert all(len(s.matched) == 2 for s in scopes)
    print("[PASS] Concurrent requests share one read, and after expiry one re-read (no stampede).")


TESTS = [
    test_inflight_shares_and_retains_nothing,
    test_scope_reads_shared_across_concurrent_requests,
    test_scope_reads_keyed_by_token_without_service_role,
    test_kept_reads_expire_after_their_ttls,
    test_writes_and_commits_clear_kept_reads,
    test_read_running_during_a_write_is_not_kept,
    test_write_between_ttl_and_store_is_never_served,
    test_failures_and_empty_unit_lists_are_not_kept,
    test_concurrent_requests_after_expiry_share_one_reread,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PERFORMANCE - SHARED SCOPE READS (OFFLINE)") else 0)
