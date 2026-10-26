"""
Offline regression tests for audit finding H3: fuel identifiers matched to a Wialon unit by registration number
only must never merge different vehicles.

  - prefix/emirate tokens ('13', 'AUH', 'T') never match a unit
  - an exact plate or unit name beats a number-only match; exact plate vs exact name of different units is ambiguous
  - '13-AUH-30561' and '1-DXB-30561' against a unit named '30561': both ambiguous (plate_code_conflict), excluded
    from KPIs and reported; the reconciliation invariant holds
  - the conflict is checked against every imported identifier, so it does not depend on the selected period
  - compatible spellings ('7-AUH-55943' / '7 AUH 55943', a bare '30561') are not conflicts
  - the identifier registry is read only when a number-only match occurs; a failing read is 503
  - vehicle detail / distance for a conflicted identifier are 409

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:  python scripts/test_fuel_plate_matching.py
"""
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_scope import Api, tx  # noqa: E402

from app.fuel.db import FuelDataFetchError  # noqa: E402
from app.fuel.scope import (  # noqa: E402
    AMBIGUITY_PLATE_CODE_CONFLICT, UnitResolver, build_excluded_scope, partition_transactions,
)
from app.fuel.wialon_distance import (  # noqa: E402
    MATCH_EXACT_NAME, MATCH_NUMBER_ONLY, match_fuel_vehicle_to_wialon, match_fuel_vehicle_with_basis,
)

Q = "date_from=2026-08-03&date_to=2026-08-05"
UNIT_30561 = {"id": 30, "nm": "30561"}
UNIT_FULL = {"id": 55, "nm": "7AUH55943"}
UNITS = [UNIT_30561, UNIT_FULL]

ROWS = [
    tx(1, 1, "13-AUH-30561", "2026-08-03T08:00:00", 100.0, 300.0),
    tx(2, 1, "1-DXB-30561", "2026-08-04T09:00:00", 60.0, 180.0),
    tx(3, 1, "7-AUH-55943", "2026-08-04T10:00:00", 40.0, 120.0),
]


def _plate(unit_id, name, plate):
    return {"id": unit_id, "nm": name, "pflds": {"1": {"n": "registration_plate", "v": plate}}}


def test_prefix_tokens_never_match():
    units = [{"id": 1, "nm": "13"}, {"id": 2, "nm": "AUH"}, {"id": 3, "nm": "T"}, {"id": 4, "nm": "DXB"}]
    for ident in ("13-AUH-30561", "T-DXB-15770"):
        assert match_fuel_vehicle_to_wialon(ident, units) is None, ident
    print("[PASS] H3: prefix/emirate tokens ('13', 'AUH', 'T', 'DXB') never match a unit.")


def test_exact_beats_number_only_and_exact_conflict_is_ambiguous():
    units = [_plate(1, "Truck A", "30561"), {"id": 2, "nm": "13-AUH-30561"}]
    m, basis = match_fuel_vehicle_with_basis("13-AUH-30561", units)
    assert m["id"] == 2 and basis == MATCH_EXACT_NAME, (m, basis)
    # Today's tier order returned unit 1 (plate token) here; a number-only match only applies without an exact one
    m, basis = match_fuel_vehicle_with_basis("1-DXB-30561", units)
    assert m["id"] == 1 and basis == MATCH_NUMBER_ONLY, (m, basis)
    clash = [_plate(1, "Truck A", "7-AUH-55943"), {"id": 2, "nm": "7-AUH-55943"}]
    m, _ = match_fuel_vehicle_with_basis("7-AUH-55943", clash)
    assert m.get("ambiguous") and {c["id"] for c in m["candidates"]} == {1, 2}, m
    print("[PASS] H3: exact name beats a number-only plate; exact plate vs exact name of two units is ambiguous.")


def test_conflicting_plate_codes_are_ambiguous_and_excluded():
    resolver = UnitResolver(UNITS)
    part = partition_transactions(ROWS, resolver)
    amb = {r["vehicle_identifier"]: r for r in part.ambiguous}
    assert set(amb) == {"13-AUH-30561", "1-DXB-30561"}, amb
    assert all(r["_ambiguity"] == AMBIGUITY_PLATE_CODE_CONFLICT and r["_candidates"] == ["30561"] for r in amb.values())
    assert [r["vehicle_identifier"] for r in part.matched] == ["7-AUH-55943"], part.matched
    scope = build_excluded_scope(part)
    assert scope["merged_units"] == [], scope["merged_units"]  # never combined against one unit's distance
    assert scope["kpi"]["litres"] + scope["ambiguous"]["litres"] + scope["unmatched"]["litres"] \
        + scope["suspected_duplicates"]["litres"] == scope["all_litres"] == 200.0, scope
    print("[PASS] H3: '13-AUH-30561' + '1-DXB-30561' on unit '30561' -> both ambiguous (plate_code_conflict), "
          "excluded, reconciled.")


def test_single_number_only_identifier_still_matches():
    resolver = UnitResolver(UNITS, registry_fn=lambda: ["13-AUH-30561", "7-AUH-55943"])
    res = resolver.resolve("13-AUH-30561")
    assert res["status"] == "matched" and res["unit_id"] == 30 and res["basis"] == MATCH_NUMBER_ONLY, res
    print("[PASS] H3: a number-only match with no conflicting plate code is kept (existing behaviour).")


def test_conflict_is_period_independent():
    # The period holds only 13-AUH-30561 rows; 1-DXB-30561 was imported for another period
    registry = MagicMock(return_value=["13-AUH-30561", "1-DXB-30561", "7-AUH-55943"])
    part = partition_transactions(ROWS[:1], UnitResolver(UNITS, registry_fn=registry))
    assert not part.matched and part.ambiguous[0]["_ambiguity"] == AMBIGUITY_PLATE_CODE_CONFLICT, part
    assert registry.call_count == 1
    print("[PASS] H3: the conflict is found from the all-time identifier registry, whatever the selected period.")


def test_compatible_spellings_are_not_conflicts():
    resolver = UnitResolver(UNITS + [{"id": 9, "nm": "55943"}],
                            registry_fn=lambda: ["30561", "13-AUH-30561", "7-AUH-55943", "7 AUH 55943", "8AUH-55943"])
    assert resolver.resolve("13-AUH-30561")["status"] == "matched"  # bare '30561' carries no code
    assert resolver.resolve("30561")["unit_id"] == 30
    # '7-AUH-55943' and '7 AUH 55943' resolve exactly to unit 55; '8AUH-55943' alone reaches unit 9 by number
    assert resolver.resolve("7 AUH 55943")["unit_id"] == 55 and resolver.resolve("8AUH-55943")["unit_id"] == 9
    print("[PASS] H3: a bare number and equal normalized spellings are compatible, not conflicts.")


def test_registry_read_only_when_needed():
    registry = MagicMock(return_value=[])
    resolver = UnitResolver(UNITS, registry_fn=registry)
    assert resolver.resolve("7-AUH-55943")["status"] == "matched"
    assert resolver.resolve("ZZZ-999")["status"] == "unmatched"
    assert registry.call_count == 0, "exact and unmatched identifiers must not read the registry"
    resolver.resolve("13-AUH-30561")
    resolver.resolve("13-AUH-30561")
    assert registry.call_count == 1
    print("[PASS] H3: the identifier registry is read once, and only when a number-only match occurs.")


def test_routes_use_registry_and_fail_loudly():
    registry = MagicMock(return_value=["13-AUH-30561", "1-DXB-30561", "7-AUH-55943"])
    with Api(rows=ROWS, units=UNITS) as api, patch("app.fuel.routes.get_fuel_vehicle_identifiers", registry):
        s = api.get(f"/api/fuel/analytics/summary?{Q}")["excluded_scope"]
        assert s["ambiguous"]["transaction_count"] == 2 and s["kpi"]["transaction_count"] == 1, s
        groups = api.get(f"/api/fuel/analytics/excluded?{Q}")["data"]
        assert {g["vehicle_identifier"]: g["ambiguity_reason"] for g in groups} == {
            "13-AUH-30561": AMBIGUITY_PLATE_CODE_CONFLICT, "1-DXB-30561": AMBIGUITY_PLATE_CODE_CONFLICT}, groups
        for path in (f"/api/fuel/analytics/vehicle-detail?vehicle_identifier=13-AUH-30561&{Q}",
                     f"/api/fuel/analytics/distance?vehicle_identifier=1-DXB-30561&{Q}"):
            r = api.client.get(path, headers={"Authorization": "Bearer offline-test-token"})
            assert r.status_code == 409 and "plate codes share that number" in r.json()["detail"], (path, r.text)
    with Api(rows=ROWS, units=UNITS) as api, \
            patch("app.fuel.routes.get_fuel_vehicle_identifiers", side_effect=FuelDataFetchError("down")):
        r = api.client.get(f"/api/fuel/analytics/summary?{Q}", headers={"Authorization": "Bearer offline-test-token"})
        assert r.status_code == 503, r.text
    print("[PASS] H3: KPI routes exclude conflicted identifiers, the excluded list names the reason, detail/distance "
          "are 409, and a failed registry read is 503.")


TESTS = [
    test_prefix_tokens_never_match,
    test_exact_beats_number_only_and_exact_conflict_is_ambiguous,
    test_conflicting_plate_codes_are_ambiguous_and_excluded,
    test_single_number_only_identifier_still_matches,
    test_conflict_is_period_independent,
    test_compatible_spellings_are_not_conflicts,
    test_registry_read_only_when_needed,
    test_routes_use_registry_and_fail_loudly,
]


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in TESTS:
            try:
                t()
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
    print(f"{len(TESTS) - failures}/{len(TESTS)} test functions passed")
    sys.exit(1 if failures else 0)
