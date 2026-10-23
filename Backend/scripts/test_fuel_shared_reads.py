"""
Offline regression tests for Fuel Performance load fan-out (MOCKED / offline).

  - InFlight: concurrent identical calls share one computation (result or exception); nothing is retained
  - fuel analytics scope: concurrent requests with the same filters share one Supabase read and one Wialon unit
    search, each caller gets its own row copies, and a later request reads afresh (no staleness window)

Run from Backend/:  python scripts/test_fuel_shared_reads.py
"""
import asyncio
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import run  # noqa: E402

import app.fuel.routes as routes  # noqa: E402
from app.utils.ttl_cache import InFlight  # noqa: E402

TXS = [
    {"id": 1, "vehicle_identifier": "V-1", "transaction_date": "2026-08-03T10:00:00", "litres": 50.0, "total_cost": 150.0},
    {"id": 2, "vehicle_identifier": "V-2", "transaction_date": "2026-08-04T10:00:00", "litres": 40.0, "total_cost": 120.0},
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
        assert len(reads) == 2 and len(searches) == 2, "a later request must read afresh"

        reads.clear()
        other = dict(filters, date_from="2026-08-04")

        async def mixed():
            return await asyncio.gather(routes._build_scope(dict(filters), "tok"), routes._build_scope(other, "tok"))

        asyncio.run(mixed())
        assert len(reads) == 2, "different filters must never share a read"
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


TESTS = [
    test_inflight_shares_and_retains_nothing,
    test_scope_reads_shared_across_concurrent_requests,
    test_scope_reads_keyed_by_token_without_service_role,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PERFORMANCE - SHARED SCOPE READS (OFFLINE)") else 0)
