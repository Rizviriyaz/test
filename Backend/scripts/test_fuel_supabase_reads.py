"""
Offline regression tests for the Supabase fuel transaction read (MOCKED / offline).

  - every page goes through the one pooled session (connection reuse), with (connect, read) timeouts
  - transient failures (read timeout, connection reset, gateway 502/503/504/544) are retried a bounded number of
    times with jittered backoff, and the read completes with every row
  - persistent transient failures, non-retryable errors (HTTP 500) and the overall read deadline end in
    FuelDataFetchError: never partial rows
  - concurrent identical analytics requests share one read, so a retrying read is not multiplied (no retry storm)
  - an exhausted read is a 503 with a clear, retryable detail, never zero-valued KPIs

Run from Backend/:  python scripts/test_fuel_supabase_reads.py
"""
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import offline_guard  # noqa: E402,F401

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.fuel.db as fuel_db  # noqa: E402
from app.fuel.db import FuelDataFetchError, fetch_fuel_transactions_for_analytics  # noqa: E402
from app.main import app  # noqa: E402

FAKE_USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "t@local", "role": "admin"}
AUTH = {"Authorization": "Bearer test"}
_real_sleep = time.sleep  # tests patch time.sleep (process-wide) to skip backoff; the fake table still really waits
ROWS = [{"id": i + 1, "transaction_date": "2026-08-10T10:00:00", "vehicle_identifier": "V", "litres": 1,
         "total_cost": 3} for i in range(2335)]


class Resp:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload = payload if payload is not None else []
        self.status_code = status
        self.ok = 200 <= status < 300
        self.headers = headers or {}

    def json(self):
        return self._payload


class Table:
    """Paged PostgREST stand-in; `script` maps call number -> exception or status to return instead of a page."""

    def __init__(self, rows=ROWS, script=None, delay=0.0):
        self.rows, self.script, self.delay = rows, dict(script or {}), delay
        self.calls, self.timeouts = [], []
        self._lock = threading.Lock()

    def get(self, url, params=None, headers=None, timeout=None):
        with self._lock:
            self.calls.append(dict(params or {}))
            self.timeouts.append(timeout)
            n = len(self.calls)
        if self.delay:
            _real_sleep(self.delay)
        action = self.script.get(n)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, int):
            return Resp(status=action)
        off, lim = int(params.get("offset", 0)), int(params.get("limit", 1000))
        page = self.rows[off:off + lim]
        span = f"{off}-{off + len(page) - 1}" if page else "*"
        return Resp(page, status=206, headers={"content-range": f"{span}/{len(self.rows)}"})


def _read(table, **env):
    with patch.object(fuel_db._session, "get", side_effect=table.get), \
         patch("app.fuel.db.time.sleep") as sleep, \
         patch.dict(os.environ, {k: str(v) for k, v in env.items()}):
        try:
            return fetch_fuel_transactions_for_analytics({}), sleep
        except FuelDataFetchError as exc:
            return exc, sleep


def test_pages_use_pooled_session_with_timeouts():
    table = Table()
    rows, sleep = _read(table)
    assert rows == ROWS and len(table.calls) == 3, (len(rows), len(table.calls))
    assert [c["offset"] for c in table.calls] == ["0", "1000", "2000"]
    assert all(t == (5.0, 10.0) for t in table.timeouts), table.timeouts
    assert not sleep.called
    adapter = fuel_db._session.get_adapter("https://example.supabase.co/rest/v1/x")
    assert adapter._pool_maxsize >= 8, "concurrent reads must not discard pooled connections"
    print("[PASS] Every page uses the pooled session with (connect, read) timeouts; 2335 rows in 3 pages.")


def test_transient_failures_are_retried_and_complete():
    for failure in (requests.ReadTimeout("stalled"), requests.ConnectionError("reset"),
                    requests.exceptions.ChunkedEncodingError("cut"), 502, 503, 504, 544):
        table = Table(script={2: failure})
        rows, sleep = _read(table)
        assert rows == ROWS, (failure, rows)
        assert len(table.calls) == 4 and table.calls[1]["offset"] == table.calls[2]["offset"] == "1000", table.calls
        assert sleep.call_count == 1 and 0.25 <= sleep.call_args[0][0] <= 0.5, sleep.call_args
    print("[PASS] A transient page failure (timeout, reset, truncated body, 502/503/504/544) is retried; all rows returned.")


def test_retries_are_bounded_and_jittered():
    table = Table(script={1: requests.ReadTimeout("x"), 2: requests.ReadTimeout("x"), 3: requests.ReadTimeout("x"),
                          4: requests.ReadTimeout("x")})
    exc, sleep = _read(table)
    assert isinstance(exc, FuelDataFetchError) and "ReadTimeout after 3 attempt(s)" in str(exc), exc
    assert len(table.calls) == 3, "3 attempts per page by default, then give up"
    delays = [c[0][0] for c in sleep.call_args_list]
    assert len(delays) == 2 and 0.25 <= delays[0] <= 0.5 and 0.5 <= delays[1] <= 1.0, delays

    table = Table(script={1: 503, 2: 503})
    exc, _ = _read(table, FUEL_SUPABASE_READ_ATTEMPTS=2)
    assert isinstance(exc, FuelDataFetchError) and len(table.calls) == 2, (exc, table.calls)
    print("[PASS] Retries are bounded (FUEL_SUPABASE_READ_ATTEMPTS, default 3) with jittered, growing backoff.")


def test_non_retryable_errors_fail_at_once():
    for failure in (500, 400, 401, requests.exceptions.InvalidURL("bad")):
        table = Table(script={2: failure})
        exc, sleep = _read(table)
        assert isinstance(exc, FuelDataFetchError), (failure, exc)
        assert len(table.calls) == 2 and not sleep.called, (failure, table.calls)
    print("[PASS] Non-transient errors (HTTP 500/4xx, invalid request) fail at once: no retry, no partial rows.")


def test_read_deadline_stops_retries():
    table = Table(script={1: requests.ReadTimeout("x")})
    clock = iter([0.0, 0.0])  # read start, attempt start; after the failure the clock reads 30 s

    with patch("app.fuel.db.time.perf_counter", side_effect=lambda: next(clock, 30.0)):
        exc, sleep = _read(table, FUEL_SUPABASE_READ_DEADLINE_SECONDS=30)
    assert isinstance(exc, FuelDataFetchError) and len(table.calls) == 1 and not sleep.called, (exc, table.calls)
    print("[PASS] No retry starts past the overall read deadline (FUEL_SUPABASE_READ_DEADLINE_SECONDS).")


def test_concurrent_requests_share_one_retrying_read():
    # 6 concurrent analytics requests, first attempt of page 1 times out: one read, one retry, all succeed.
    table = Table(rows=ROWS[:10], script={1: requests.ReadTimeout("x")}, delay=0.1)
    results = []
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch.object(fuel_db._session, "get", side_effect=table.get), \
         patch("app.fuel.db.time.sleep"), \
         patch("app.fuel.routes.login", return_value="sid"), \
         patch("app.fuel.routes.get_all_vehicles", return_value=[{"id": 1, "nm": "V"}]):
        client = TestClient(app)

        def call():
            results.append(client.get("/api/fuel/analytics/summary?date_from=2026-08-01&date_to=2026-08-31",
                                      headers=AUTH).status_code)

        threads = [threading.Thread(target=call) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert results == [200] * 6, results
    assert len(table.calls) == 2, f"one shared read (1 timeout + 1 retry), not one per request: {len(table.calls)}"
    print("[PASS] Concurrent identical requests share one read and its retries (no retry storm).")


def test_exhausted_read_is_clear_503_not_zeros():
    table = Table(script={i: 503 for i in range(1, 10)})
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch.object(fuel_db._session, "get", side_effect=table.get), \
         patch("app.fuel.db.time.sleep"):
        r = TestClient(app).get("/api/fuel/analytics/summary", headers=AUTH)
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert "withheld" in detail and "try again" in detail and "total" not in r.json(), r.text
    assert len(table.calls) == 3
    print("[PASS] An exhausted read is a 503 with a clear, retryable detail; no zero-valued KPIs are returned.")


if __name__ == "__main__":
    test_pages_use_pooled_session_with_timeouts()
    test_transient_failures_are_retried_and_complete()
    test_retries_are_bounded_and_jittered()
    test_non_retryable_errors_fail_at_once()
    test_read_deadline_stops_retries()
    test_concurrent_requests_share_one_retrying_read()
    test_exhausted_read_is_clear_503_not_zeros()
    print("\nAll Supabase read tests passed.")
