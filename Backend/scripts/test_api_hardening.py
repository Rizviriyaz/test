"""
Offline tests for the medium- and low-severity audit items (M1-M16, L1-L10) that change backend behaviour.

  - M1: auth 401 responses carry CORS headers (CORS is the outermost middleware)
  - M2: 500/502 bodies never contain exception text
  - M6: the root logger is configured at LOG_LEVEL; routine PERF timings log at DEBUG, failures stay visible
  - M7: startup survives a Wialon login failure; login failures raise WialonAPIError

Auth, login and Wialon calls are mocked; outbound `requests` calls are blocked. Run from Backend/:
    python scripts/test_api_hardening.py
"""
import logging
import os
import subprocess
import sys
import time
import traceback
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.main as main  # noqa: E402
import app.wialon as wialon  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

AUTH_HEADERS = {"Authorization": "Bearer test-token"}
USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "user@example.com", "role": "viewer"}
ORIGIN = main.allowed_origins[0] if main.allowed_origins else None


def _client():
    # No context manager: lifespan (Wialon login) is exercised separately in test_startup_survives_login_failure.
    return TestClient(main.app, raise_server_exceptions=False)


def test_auth_errors_carry_cors_headers():
    assert ORIGIN, "test needs a configured CORS origin (development defaults or CORS_ORIGINS)"
    client = _client()
    res = client.get("/api/vehicles", headers={"Origin": ORIGIN})
    assert res.status_code == 401, res.text
    assert res.headers.get("access-control-allow-origin") == ORIGIN, dict(res.headers)
    with patch("app.main.authenticate_access_token", side_effect=main.HTTPException(status_code=403, detail="no role")):
        res = client.get("/api/vehicles", headers={"Origin": ORIGIN, **AUTH_HEADERS})
    assert res.status_code == 403 and res.headers.get("access-control-allow-origin") == ORIGIN, dict(res.headers)
    print("[PASS] M1: 401/403 from the auth middleware carry Access-Control-Allow-Origin.")


def test_internal_errors_are_generic():
    client = _client()
    main._cache.clear()
    with patch("app.main.authenticate_access_token", return_value=USER), \
            patch("app.main.login", side_effect=ValueError("secret-internal-detail token=abc")):
        res = client.get("/api/vehicles", headers=AUTH_HEADERS)
    assert res.status_code == 500 and "secret-internal-detail" not in res.text, res.text
    with patch("app.main.authenticate_access_token", return_value=USER), \
            patch("app.main.login", side_effect=WialonAPIError("raw wialon payload {'error': 4}")):
        res = client.get("/api/groups", headers=AUTH_HEADERS)
    assert res.status_code == 502 and "raw wialon payload" not in res.text, res.text
    print("[PASS] M2: 500/502 responses carry a generic detail, not exception text.")


def test_logging_configured():
    root = logging.getLogger()
    assert root.handlers, "root logger has no handler"
    expected = logging.getLevelName(os.getenv("LOG_LEVEL", "INFO").strip().upper())
    expected = expected if isinstance(expected, int) else logging.INFO
    assert root.getEffectiveLevel() == expected, (root.getEffectiveLevel(), expected)
    print(f"[PASS] M6: root logger has a handler at LOG_LEVEL ({logging.getLevelName(expected)}).")


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_log_levels():
    """Routine PERF timings log at DEBUG; request failures and Wialon errors stay visible at default INFO."""
    records = []
    handler = logging.Handler(level=logging.DEBUG)
    handler.emit = records.append
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

    def levels(prefix):
        return [r.levelno for r in records if r.getMessage().startswith(prefix)]

    try:
        for payload, expected in (({"items": []}, logging.DEBUG), ({"error": 7}, logging.WARNING),
                                  ({"error": 1003}, logging.DEBUG)):
            records.clear()
            with patch.object(wialon._wialon_http_session, "post", return_value=_Response(payload)), \
                    patch("app.wialon.time.sleep"):
                assert wialon.wialon_request("report/exec_report", {}, sid="s", max_retries=1) == payload
            assert levels("PERF wialon end") == [expected], (payload, levels("PERF wialon end"))

        client = _client()
        main._cache.clear()
        main.set_cache("fleet-vehicles-live", {"count": 0, "vehicles": []})
        cases = [
            (logging.DEBUG, 200, patch("app.main.login")),
            (logging.INFO, 401, None),
            (logging.WARNING, 502, patch("app.main.login", side_effect=WialonAPIError("raw"))),
        ]
        for expected, status, login_patch in cases:
            if status == 502:
                main._cache.clear()
            records.clear()
            headers = AUTH_HEADERS if login_patch else {}
            with patch("app.main.authenticate_access_token", return_value=USER):
                if login_patch:
                    with login_patch:
                        res = client.get("/api/vehicles", headers=headers)
                else:
                    res = client.get("/api/vehicles", headers=headers)
            assert res.status_code == status, (status, res.status_code, res.text)
            assert levels("PERF request start") == [logging.DEBUG], levels("PERF request start")
            assert levels("PERF request end") == [expected], (status, levels("PERF request end"))
            if status == 200:
                assert levels("PERF calculation") == [logging.DEBUG, logging.DEBUG], levels("PERF calculation")
        main._cache.clear()
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING or previous_level <= logging.DEBUG
    print("[PASS] Log levels: PERF timings at DEBUG; 4xx request end at INFO; 5xx and Wialon errors at WARNING.")


def test_startup_survives_login_failure():
    with patch("app.main.login", side_effect=WialonAPIError("Wialon login failed")):
        with TestClient(main.app) as client:
            res = client.get("/api/health")
    assert res.status_code == 200, res.text
    print("[PASS] M7: app starts and /api/health answers while Wialon login fails.")

    with patch("app.wialon.wialon_request", return_value={"error": 8}), patch.object(wialon, "SID", None):
        try:
            wialon.login(force=True)
        except WialonAPIError:
            pass
        else:
            raise AssertionError("login() did not raise on a Wialon error response")
    print("[PASS] M7: a failed Wialon login raises WialonAPIError (mapped to 502).")


def test_caches_are_bounded():
    import app.auth as auth
    import app.fleet_analytics as fa
    for cache, setter in (
        (main._cache, lambda i: main.set_cache(f"vehicle-trips-v3-1-exact-{i}-{i + 1}-uae", {"i": i})),
        (fa._analytics_cache, lambda i: fa.set_cached(f"fleet_utilization_{i}", {"i": i})),
    ):
        cache.clear()
        for i in range(5000):
            setter(i)
        assert len(cache._data) <= cache.max_entries, len(cache._data)
    main._cache.clear()
    fa._analytics_cache.clear()
    assert main.cached("missing") is None
    main.set_cache("k", {"v": 1})
    assert main.cached("k", ttl_seconds=60) == {"v": 1}
    assert auth._token_cache.max_entries > 0
    print("[PASS] M3: response, analytics and token caches stay bounded under 5000 distinct keys.")


def _jwt(exp):
    import base64
    import json as _json
    body = base64.urlsafe_b64encode(_json.dumps({"sub": "u", "exp": exp}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{body}.sig"


def test_expired_or_stale_tokens_rejected():
    import time as _time
    import app.auth as auth
    from fastapi import HTTPException
    now = _time.time()
    user = {"id": "u", "email": None, "role": "viewer"}

    expired = _jwt(now - 5)
    auth._token_cache.set(expired, {"user": user, "timestamp": now}, auth.TOKEN_STALE_SECONDS)
    try:
        auth.authenticate_access_token(expired)
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("an expired JWT was accepted from the cache")

    # Supabase unreachable: a recently validated token is reused, an old validation is not.
    live = _jwt(now + 3600)
    with patch.dict(os.environ, {"SUPABASE_URL": "https://sb.example", "SUPABASE_ANON_KEY": "anon"}), \
            patch.object(auth._auth_session, "get", side_effect=auth.requests.ConnectionError("down")):
        auth._token_cache.set(live, {"user": user, "timestamp": now - auth.TOKEN_CACHE_SECONDS - 1}, 3600)
        assert auth.authenticate_access_token(live) == user
        auth._token_cache.set(live, {"user": user, "timestamp": now - auth.TOKEN_STALE_SECONDS - 1}, 3600)
        try:
            auth.authenticate_access_token(live)
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("a validation older than the stale window was reused during an outage")
    auth._token_cache.clear()
    assert auth._token_expiry("not-a-jwt") is None
    print(f"[PASS] M14: expired JWTs rejected locally; outage reuse capped at {auth.TOKEN_STALE_SECONDS}s.")


def test_time_windows_bounded():
    client = _client()
    with patch("app.main.authenticate_access_token", return_value=USER), \
            patch("app.main.login", side_effect=AssertionError("Wialon must not be reached")):
        for url in (
            "/api/vehicles/1/history?hours=100000",
            "/api/vehicles/1/efficiency?hours=0",
            "/api/fleet/safety?hours=0",
            "/api/fleet/utilization?hours=100000",
            "/api/vehicles/1/route?hours=100000",
            "/api/vehicles/1/analytics?hours=100000",
            "/api/dashboard/summary?hours=100000",
            "/api/idle/fleet-analytics?hours=100000",
            "/api/fleet/trips?hours=100000",
        ):
            res = client.get(url, headers=AUTH_HEADERS)
            assert res.status_code == 422, (url, res.status_code, res.text)
        for url in (
            "/api/idle/fleet-analytics?timeframe=all",
            "/api/vehicles/1/idle?timeframe=all",
            "/api/fleet/trips?timeframe=all",
            "/api/vehicles/1/trips?from_time=0&to_time=1800000000",
            "/api/vehicles/1/history?from_time=1700000000&to_time=1800000000",
            "/api/dashboard/mileage?from_time=0&to_time=1800000000",
        ):
            res = client.get(url, headers=AUTH_HEADERS)
            assert res.status_code == 400 and "too long" in res.text, (url, res.status_code, res.text)
        # Safety loads raw history per unit: at most 168 h, refused with a clear 400 naming the limit
        for hours in (169, 744, 100000):
            res = client.get(f"/api/fleet/safety?hours={hours}", headers=AUTH_HEADERS)
            assert res.status_code == 400 and "at most 168 hours" in res.text, (hours, res.status_code, res.text)
    print("[PASS] M4: oversized hours -> 422/400 and oversized ranges -> 400, before any Wialon call.")


def test_ui_windows_still_accepted():
    # The longest presets the UI sends must stay valid (Idle "Last 30 days" = 720 h, report "year").
    from fastapi import HTTPException
    from app.utils.datetime_helpers import get_timeframe_timestamps
    main._require_window_within(*get_timeframe_timestamps(hours=720), main.MAX_HISTORY_HOURS)
    for tf in ("today", "yesterday", "week", "month", "year"):
        main._require_window_within(*get_timeframe_timestamps(timeframe=tf), main.MAX_REPORT_HOURS)
    try:
        main._require_window_within(0, 10 ** 9, main.MAX_REPORT_HOURS)
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("epoch-0 window accepted")
    print("[PASS] M4: UI presets (720 h, today..year) remain within the bounds.")


def test_summary_uses_one_window():
    client = _client()
    main._cache.clear()
    calls = {}

    def rec(name, payload):
        def fn(**kwargs):
            calls[name] = kwargs
            return payload
        return fn

    with patch("app.main.authenticate_access_token", return_value=USER), \
            patch("app.main.login", return_value="sid"), \
            patch("app.main.get_all_vehicles", return_value=[]), \
            patch("app.main.get_calculated_data", return_value=[]), \
            patch("app.main.get_vehicle_live_data", return_value=[]), \
            patch("app.fleet_analytics.get_trips_mileage_analytics", side_effect=rec("trips", {"summary": {}})), \
            patch("app.fleet_analytics.get_safety_analytics", side_effect=rec("safety", {"summary": {}})), \
            patch("app.fleet_analytics.get_fleet_utilization_analytics", side_effect=rec("util", {})):
        res = client.get("/api/dashboard/summary?timeframe=7d&hours=24", headers=AUTH_HEADERS)
        assert res.status_code == 200, res.text
        hours = calls["trips"].get("hours")
        assert calls["trips"] == {"hours": hours} and calls["safety"] == {"hours": hours} \
            and calls["util"] == {"hours": hours}, calls
        assert res.json()["hours"] == hours == 7 * 24, res.json()
        # Calendar timeframes cannot be expressed as a rolling window, so they are rejected
        calls.clear()
        for tf in ("today", "week", "month", "year", "all", "bogus"):
            bad = client.get(f"/api/dashboard/summary?timeframe={tf}", headers=AUTH_HEADERS)
            assert bad.status_code == 400, (tf, bad.status_code, bad.text)
        assert not calls, calls
    main._cache.clear()
    print(f"[PASS] M15: summary passes one rolling window (7d -> hours={hours}) to trips, idle and safety; "
          "calendar timeframes return 400.")


def test_rolling_timeframe_aliases():
    from app.utils import datetime_helpers as dh
    for tf, days in (("7d", 7), ("last_1_week", 7), ("1w", 7), ("30d", 30), ("last_1_month", 30),
                     ("1m", 30), ("365d", 365), ("last_1_year", 365), ("1y", 365)):
        f, t = dh.get_timeframe_timestamps(timeframe=tf)
        assert t - f == days * 86400, (tf, t - f)
    f, t = dh.get_timeframe_timestamps(timeframe="week")
    assert t - f <= 7 * 86400 and dh.datetime.fromtimestamp(f, tz=dh.UAE_TZ).weekday() == 0
    print("[PASS] M16: 7d/30d/365d-style aliases are rolling; 'week' stays calendar (Monday).")


def test_report_ids_from_env():
    import importlib
    import app.wialon_reports as wr
    assert wr.WIALON_RESOURCE_ID == 601814494 and wr.GROUP_ID_ALL_UNITS == 0  # group discovered by default
    with patch.dict(os.environ, {"WIALON_REPORT_RESOURCE_ID": "123", "WIALON_GROUP_ALL_UNITS": "456",
                                 "WIALON_TEMPLATE_GROUP_TRIPS": "9"}):
        importlib.reload(wr)
        assert (wr.WIALON_RESOURCE_ID, wr.GROUP_ID_ALL_UNITS, wr.TEMPLATE_ID_GROUP_TRIPS) == (123, 456, 9)
    importlib.reload(wr)
    assert wr.WIALON_RESOURCE_ID == 601814494
    print("[PASS] M5: report resource/template/group IDs are overridable via environment.")


def test_all_units_group_discovered_from_account():
    import app.wialon_reports as wr
    groups = [{"id": 11, "nm": "Delivery", "u": [1, 2]}, {"id": 22, "nm": " All Unit ", "u": [1, 2, 3]}]
    with patch.object(wr, "GROUP_ID_ALL_UNITS", 0):
        assert wr.resolve_all_units_group_id("sid", groups) == 22
        with patch.object(wr, "get_vehicle_groups", return_value=groups) as fetch:
            assert wr.resolve_all_units_group_id("sid") == 22
            fetch.assert_called_once_with("sid")
        try:
            wr.resolve_all_units_group_id("sid", [{"id": 11, "nm": "Delivery", "u": [1]}])
            raise AssertionError("missing 'All Unit' group must raise")
        except wr.WialonAPIError:
            pass
    with patch.object(wr, "GROUP_ID_ALL_UNITS", 456):
        assert wr.resolve_all_units_group_id("sid", groups) == 456
    print("[PASS] M5b: 'All Unit' report group is discovered from the account unless overridden.")


def _idle_msgs(start, end, step=60):
    # speed 0 with ignition on (io_11) -> IDLING
    return [{"tp": "ud", "t": t, "pos": {"s": 0}, "p": {"io_11": 1}} for t in range(start, end + 1, step)]


def test_idle_seed_widens_past_two_hours():
    import app.vehicles as vehicles
    now = 1_800_000_000
    idle_since = now - 5 * 3600  # idling for 5 h; a 2 h window alone would report ~2 h
    calls = []

    def fake_history(sid, vid, hours=6, from_time=None, to_time=None):
        calls.append(to_time - from_time)
        msgs = [{"tp": "ud", "t": idle_since - 600, "pos": {"s": 40}, "p": {"io_11": 1}}] + _idle_msgs(idle_since, now)
        return [m for m in msgs if from_time <= m["t"] <= to_time]

    vehicle = {"id": 7, "nm": "T7", "sens": {}, "pos": {"s": 0, "t": now}, "lmsg": {"t": now, "p": {"io_11": 1}}}
    vehicles.idle_start_times.pop(7, None)
    with patch("app.vehicles.get_vehicle_history", side_effect=fake_history), \
            patch("app.vehicles.now_timestamp", return_value=now),             patch("app.vehicles.calculate_status", return_value="IDLING"):
        fleet = vehicles.get_vehicle_live_data("sid", [vehicle], [], groups=[])
    v = fleet[0]
    assert v["status"] == "IDLING", v["status"]
    assert calls == [2 * 3600, 24 * 3600], calls
    assert abs(v["idle_duration"] - 5 * 3600) <= 60, v["idle_duration"]

    # A short idle period is found in the first window: no second history call.
    calls.clear()
    vehicles.idle_start_times.pop(7, None)
    idle_since = now - 1800
    with patch("app.vehicles.get_vehicle_history", side_effect=fake_history), \
            patch("app.vehicles.now_timestamp", return_value=now),             patch("app.vehicles.calculate_status", return_value="IDLING"):
        v = vehicles.get_vehicle_live_data("sid", [vehicle], [], groups=[])[0]
    assert calls == [2 * 3600] and abs(v["idle_duration"] - 1800) <= 60, (calls, v["idle_duration"])
    vehicles.idle_start_times.pop(7, None)
    print("[PASS] M9: a 5 h idle after restart reads as 5 h (24 h lookback only when the 2 h window is all idle).")


def test_report_failures_are_502_not_fallback():
    client = _client()
    main._cache.clear()
    with patch("app.main.authenticate_access_token", return_value=USER), \
            patch("app.main.login", return_value="sid"), \
            patch("app.main.get_vehicle_history", side_effect=AssertionError("no raw-history fallback")), \
            patch("app.main.execute_wialon_unit_trips_report", side_effect=WialonAPIError("x")), \
            patch("app.main.execute_wialon_unit_engine_report", side_effect=WialonAPIError("x")):
        assert client.get("/api/vehicles/1/trips", headers=AUTH_HEADERS).status_code == 502
        assert client.get("/api/vehicles/1/idle", headers=AUTH_HEADERS).status_code == 502
    main._cache.clear()
    print("[PASS] M12: unit trips/idle report failures return 502 (dead fallback branches removed).")


def test_null_lmsg_does_not_crash_health():
    from app.fleet_analytics import evaluate_vehicle_health
    vehicle = {"vehicle_id": 9, "vehicle_name": "N9", "location": {}}
    health = evaluate_vehicle_health(vehicle, raw_unit={"id": 9, "nm": "N9", "lmsg": None}, calc_data={})
    assert isinstance(health, dict), health
    print("[PASS] L1: a unit with \"lmsg\": null is evaluated instead of raising AttributeError.")


def test_brake_pedal_is_not_harsh_braking():
    import app.fleet_analytics as fa
    now = int(time.time())
    units = [
        {"id": 1, "nm": "PEDAL", "sens": {"1": {"n": "Brake pedal", "p": "io_9"}}},
        {"id": 2, "nm": "EVENT", "sens": {"1": {"n": "Harsh braking event", "p": "io_10"}}},
    ]
    fleet = [{"vehicle_id": 1, "vehicle_name": "PEDAL", "group": "G"}, {"vehicle_id": 2, "vehicle_name": "EVENT", "group": "G"}]

    def fake_history(sid, vid, hours=24):
        return [{"t": now - 60, "p": {}, "pos": {"s": 30}, "sensors": {"1": {"value": 1}}}]

    fa._analytics_cache.clear()
    with patch("app.fleet_analytics.login", return_value="sid"), \
            patch("app.fleet_analytics.get_vehicle_groups", return_value=[]), \
            patch("app.fleet_analytics.get_all_vehicles", return_value=units), \
            patch("app.fleet_analytics.get_calculated_data", return_value=[]), \
            patch("app.fleet_analytics.get_vehicle_live_data", return_value=fleet), \
            patch("app.fleet_analytics.get_vehicle_history", side_effect=fake_history):
        res = fa.get_safety_analytics(hours=23)
    fa._analytics_cache.clear()
    braking = [e["vehicle_name"] for e in res["events"] if e["event_type"] == "Harsh Braking"]
    assert braking == ["EVENT"], braking
    print("[PASS] L5: a plain brake-pedal sensor is not counted as harsh braking; a braking-event sensor is.")


def test_docs_disabled_in_production():
    code = (
        "import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'scripts');"
        "from offline_guard import OfflineGuard; g = OfflineGuard().start();"
        "from fastapi.testclient import TestClient; import app.main as m;"
        "c = TestClient(m.app);"
        "print([c.get(p).status_code for p in ('/docs', '/redoc', '/openapi.json')]); g.assert_no_live_calls()"
    )
    backend = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    # ENVIRONMENT="" stands for "unset": load_dotenv never overrides a variable that already exists
    for env_value, expected in (("production", "[404, 404, 404]"), ("", "[404, 404, 404]"),
                                ("staging", "[404, 404, 404]"), ("development", "[200, 200, 200]")):
        env = dict(os.environ, ENVIRONMENT=env_value, CORS_ORIGINS="https://fleet.example.com")
        out = subprocess.run([sys.executable, "-c", code], cwd=backend, env=env, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0 and expected in out.stdout.splitlines(), (env_value, out.stdout, out.stderr[-2000:])
    print("[PASS] L3: /docs, /redoc and /openapi.json are served only when ENVIRONMENT=development (404 when unset).")


def test_duplicate_unit_names_not_attributed():
    client = _client()
    main._cache.clear()
    units = [{"id": 1, "nm": "TWIN"}, {"id": 2, "nm": "TWIN"}, {"id": 3, "nm": "SOLO"}]
    rows = [{"vehicle_name": "TWIN", "mileage_km": 10.0}, {"vehicle_name": "SOLO", "mileage_km": 5.0}]
    with patch("app.main.authenticate_access_token", return_value=USER), \
            patch("app.main.login", return_value="sid"), \
            patch("app.main.get_vehicle_groups", return_value=[{"id": 1, "nm": "All Unit", "u": []}]), \
            patch("app.main.get_all_vehicles", return_value=units), \
            patch("app.main.get_calculated_data", return_value=[]), \
            patch("app.main.execute_wialon_group_trips_report", return_value=rows):
        res = client.get("/api/dashboard/mileage?timeframe=today", headers=AUTH_HEADERS)
    main._cache.clear()
    assert res.status_code == 200, res.text
    body = res.json()
    units_out = sorted((u["vehicle_id"], u["vehicle_name"], u["mileage"]) for u in body["all_units"])
    # Each same-named unit is listed by id with unknown mileage; the TWIN row's 10 km goes to neither
    assert units_out == [(1, "TWIN", None), (2, "TWIN", None), (3, "SOLO", 5.0)], body
    assert body["ambiguous_vehicle_names"] == ["TWIN"] and body["incomplete_units"] == ["TWIN", "TWIN"], body
    assert body["total_km"] == 5.0, body
    print("[PASS] L4: a report row whose name is shared by two units is not attributed to either (flagged instead).")


def test_ambiguous_idle_is_unknown_not_zero():
    from wialon_report_fixtures import BASE_TS, CALCULATED, GROUPS, UNITS, FakeReportEngine, live_records
    # Unit 102 shares unit 101's name, so neither can be attributed a report row
    twin_units = [dict(u, nm="UNIT-A") if u["id"] == 102 else u for u in UNITS]
    twin_live = [dict(r, vehicle_name="UNIT-A") if r["vehicle_id"] == 102 else r for r in live_records()]
    with patch("app.wialon_reports.wialon_request", FakeReportEngine()), \
            patch("app.main.login", return_value="sid"), \
            patch("app.main.get_vehicle_groups", return_value=GROUPS), \
            patch("app.main.get_all_vehicles", return_value=twin_units), \
            patch("app.main.get_calculated_data", return_value=CALCULATED), \
            patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: twin_live):
        res = main.get_fleet_idle_analytics(from_time=BASE_TS, to_time=BASE_TS + 86400 - 1,
                                            include_events=True, skip_cache=True)
    by_vehicle = {v["vehicle_id"]: v for v in res["by_vehicle"]}
    for vid in (101, 102):
        v = by_vehicle[vid]
        assert (v["total_idle_seconds"], v["idle_minutes"], v["idle_events"]) == (None, None, None), v
    assert by_vehicle[103]["total_idle_seconds"] is not None, by_vehicle[103]
    groups = {g["group_name"]: g for g in res["by_group"]}
    assert groups["Logistics"]["total_idle_seconds"] is None and groups["Logistics"]["average_idle_minutes"] is None, groups
    assert groups["Contracts"]["total_idle_seconds"] == by_vehicle[103]["total_idle_seconds"], groups
    assert res["kpis"]["total_idle_partial"] is True and res["ambiguous_vehicle_names"] == ["UNIT-A"], res["kpis"]
    assert res["kpis"]["total_idle_seconds"] == by_vehicle[103]["total_idle_seconds"], res["kpis"]
    assert [v["vehicle_id"] for v in res["by_vehicle"]][-2:] in ([101, 102], [102, 101]), "unknown totals sort last"
    print("[PASS] Idle analytics: units sharing a name report unknown (None) idle, not 0; totals are flagged partial.")


def test_incomplete_reports_not_cached():
    main._cache.clear()
    calls = {"trips": 0, "engine": 0, "group": 0}

    def unit_trips(*a, **k):
        calls["trips"] += 1
        return {"trips": [{"distance_km": 0.0, "incomplete_fields": ["distance_km"]}],
                "incomplete_fields": ["distance_km"], "column_warnings": []}

    def unit_engine(*a, **k):
        calls["engine"] += 1
        return {"idle_events": [], "total_idle_seconds": 0, "total_engine_seconds": 3600,
                "incomplete_fields": ["idle_seconds"], "column_warnings": []}

    def group_trips(*a, **k):
        calls["group"] += 1
        return [{"vehicle_name": "SOLO", "mileage_km": 0.0, "incomplete_fields": ["mileage_km"]},
                {"vehicle_name": "OTHER", "mileage_km": 12.5}]

    units = [{"id": 3, "nm": "SOLO"}, {"id": 4, "nm": "OTHER"}]
    with patch("app.main.login", return_value="sid"), \
            patch("app.main.get_vehicle_groups", return_value=[{"id": 1, "nm": "All Unit", "u": []}]), \
            patch("app.main.get_all_vehicles", return_value=units), \
            patch("app.main.get_calculated_data", return_value=[]), \
            patch("app.main.execute_wialon_unit_trips_report", side_effect=unit_trips), \
            patch("app.main.execute_wialon_unit_engine_report", side_effect=unit_engine), \
            patch("app.main.execute_wialon_group_trips_report", side_effect=group_trips):
        for _ in range(2):
            trips = main.get_vehicle_trips(1, from_time=1000, to_time=5000)
            idle = main.get_vehicle_idle(1, from_time=1000, to_time=5000)
            mileage = main.get_dashboard_mileage(timeframe="custom", from_time=1000, to_time=5000)
        # Dashboard mileage (report-result cache policy): an unreadable cell is transient, so the partial result
        # is served for TRANSIENT_PARTIAL_TTL_SECONDS only, then re-read
        # Ages the mileage result and the Group Trips snapshot it shares with /api/fleet/trips
        from wialon_report_fixtures import age_report_caches
        age_report_caches(main.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        mileage = main.get_dashboard_mileage(timeframe="custom", from_time=1000, to_time=5000)
    main._cache.clear()
    assert calls == {"trips": 2, "engine": 2, "group": 2}, f"incomplete results were cached: {calls}"
    assert trips["incomplete_fields"] == ["distance_km"], trips
    assert idle["summary"]["total_idle_seconds"] is None and idle["summary"]["total_engine_seconds"] == 3600, idle
    assert idle["incomplete_fields"] == ["idle_seconds"], idle
    by_name = {u["vehicle_name"]: u["mileage"] for u in mileage["all_units"]}
    assert by_name == {"SOLO": None, "OTHER": 12.5} and mileage["total_km"] == 12.5, mileage
    assert mileage["incomplete_units"] == ["SOLO"], mileage
    print("[PASS] Unit trips/idle and dashboard mileage pass incomplete report cells on as unknown; unit routes do not "
          "cache them, dashboard mileage only for the short transient TTL.")


def test_fuel_summary_sums_like_excluded_scope():
    from app.fuel.analytics import calculate_fuel_summary
    from app.fuel.scope import _totals
    # Float round() gives 1.0 / 2.67 here; Decimal half-up (as in excluded_scope) gives 1.01 / 2.68
    rows = [{"litres": 1.005, "total_cost": 2.675, "fuel_type": "DIESEL", "vehicle_identifier": "A"}]
    summary = calculate_fuel_summary(rows)
    totals = _totals(rows)
    assert (summary.total_litres, summary.total_cost) == (totals["litres"], totals["cost"]) == (1.01, 2.68), (summary, totals)
    print("[PASS] L7: fuel summary totals use Decimal and match excluded_scope rounding (1.01 L / 2.68).")


def test_concurrent_imports_serialized():
    import app.fuel.importer as importer
    assert importer._IMPORT_LOCK.acquire(timeout=1)
    try:
        with patch.object(importer, "IMPORT_LOCK_WAIT_SECONDS", 0.05), \
                patch.object(importer, "_execute_fuel_import_locked", side_effect=AssertionError("must not run")):
            try:
                importer.execute_fuel_import(b"", "s.xlsx", {}, USER["id"])
                raise AssertionError("expected FuelImportError while another import holds the lock")
            except importer.FuelImportError as exc:
                assert exc.status_code == 409, exc.status_code
    finally:
        importer._IMPORT_LOCK.release()

    # Two imports started together must run one after the other, never overlapping.
    import threading
    active, max_active, finished = [0], [0], []
    counter_lock = threading.Lock()

    def fake_locked_import(file_bytes, file_name, *args, **kwargs):
        with counter_lock:
            active[0] += 1
            max_active[0] = max(max_active[0], active[0])
        time.sleep(0.2)
        with counter_lock:
            active[0] -= 1
            finished.append(file_name)
        return {"file": file_name}

    start = threading.Barrier(2)

    def run_import(name):
        start.wait()
        importer.execute_fuel_import(b"", name, {}, USER["id"])

    with patch.object(importer, "IMPORT_LOCK_WAIT_SECONDS", 5), \
            patch.object(importer, "_execute_fuel_import_locked", side_effect=fake_locked_import):
        threads = [threading.Thread(target=run_import, args=(n,)) for n in ("a.xlsx", "b.xlsx")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    assert sorted(finished) == ["a.xlsx", "b.xlsx"], finished
    assert max_active[0] == 1, f"imports overlapped ({max_active[0]} ran at once)"
    print("[PASS] L10: an import waiting on a running import (same process) gets 409 instead of racing it; "
          "two concurrent imports run one after the other.")


TESTS = [
    test_idle_seed_widens_past_two_hours,
    test_report_failures_are_502_not_fallback,
    test_auth_errors_carry_cors_headers,
    test_internal_errors_are_generic,
    test_logging_configured,
    test_log_levels,
    test_startup_survives_login_failure,
    test_caches_are_bounded,
    test_expired_or_stale_tokens_rejected,
    test_time_windows_bounded,
    test_ui_windows_still_accepted,
    test_summary_uses_one_window,
    test_rolling_timeframe_aliases,
    test_report_ids_from_env,
    test_all_units_group_discovered_from_account,
    test_null_lmsg_does_not_crash_health,
    test_brake_pedal_is_not_harsh_braking,
    test_docs_disabled_in_production,
    test_duplicate_unit_names_not_attributed,
    test_ambiguous_idle_is_unknown_not_zero,
    test_incomplete_reports_not_cached,
    test_fuel_summary_sums_like_excluded_scope,
    test_concurrent_imports_serialized,
]


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failures = 0
    try:
        for test in TESTS:
            try:
                test()
            except Exception:  # any error fails this test but lets the remaining tests run
                failures += 1
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
        guard.assert_no_live_calls()
    finally:
        guard.stop()
    if failures:
        sys.exit(1)
    print("\nAll API hardening tests passed.")
