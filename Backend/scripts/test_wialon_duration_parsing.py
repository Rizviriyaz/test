"""
Regression test for Wialon duration parsing (audit H7): day-prefixed durations such as '1 days 02:03:04'
were parsed as 0 seconds, silently dropping idle and engine-hour totals of a day or more.

Classification: MOCKED / offline. Wialon is replaced by FakeReportEngine and every outbound HTTP call is
blocked by OfflineGuard.

  python scripts/test_wialon_duration_parsing.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, GROUPS, TEMPLATE_GROUP_ENGINE, TEMPLATE_UNIT_ENGINE, FakeReportEngine, default_tables, group_engine_row,
    unit_engine_row,
)

DAY = 86400
FROM_TS, TO_TS = BASE_TS, BASE_TS + 3 * DAY - 1

# (input, parse_duration_to_seconds, parse_duration_strict)
CASES = [
    # Formats already supported before the fix (must not change)
    ("01:30:00", 5400, 5400),
    ("8:00:00", 28800, 28800),
    ("24:00:00", 86400, 86400),
    ("26:03:04", 93784, 93784),
    ("15:30", 930, 930),
    ("0:00:00", 0, 0),
    ("0", 0, 0),
    ("3600", 3600, 3600),
    (" 0:05:00 ", 300, 300),
    (3600, 3600, 3600),
    (90.9, 90, 90),
    (0, 0, 0),
    ({"v": 3600}, 3600, 3600),
    ({"t": "00:05:00"}, 300, 300),
    ({"t": "1 days 00:00:01"}, DAY + 1, DAY + 1),
    # Day-prefixed durations (H7)
    ("1 days 02:03:04", DAY + 7384, DAY + 7384),
    ("1 day 02:03:04", DAY + 7384, DAY + 7384),
    ("2 days 00:00:00", 2 * DAY, 2 * DAY),
    ("10 days 23:59:59", 10 * DAY + 86399, 10 * DAY + 86399),
    ("1 Days 2:03:04", DAY + 7384, DAY + 7384),
    ("1days 02:03:04", DAY + 7384, DAY + 7384),
    ("3 days", 3 * DAY, 3 * DAY),
    ("1 days 03:04", DAY + 184, DAY + 184),
    # Invalid / missing: lenient parser -> 0, strict parser -> None (never a partial day count)
    ("", 0, None),
    ("---", 0, None),
    ("abc", 0, None),
    ("days 02:03:04", 0, None),
    ("1 days xx:03:04", 0, None),
    ("1 weeks 02:03:04", 0, None),
    ({}, 0, None),
    (None, 0, None),
]


def test_parsers():
    from app.wialon_reports import parse_duration_strict, parse_duration_to_minutes, parse_duration_to_seconds

    for value, lenient, strict in CASES:
        got = parse_duration_to_seconds(value)
        assert got == lenient, f"parse_duration_to_seconds({value!r}) = {got}, expected {lenient}"
        got = parse_duration_strict(value)
        assert got == strict, f"parse_duration_strict({value!r}) = {got}, expected {strict}"
    assert parse_duration_to_minutes("1 days 00:30:00") == 1470.0
    print(f"[PASS] {len(CASES)} duration inputs parse as expected (lenient and strict).")


def test_report_totals():
    import app.wialon_reports as wr

    tables = default_tables()
    tables[TEMPLATE_UNIT_ENGINE]["rows"] = [
        unit_engine_row(1, BASE_TS, "2 days 00:00:00", "1 days 02:03:04", "0:30:00", "1 days 01:33:04", 5.0),
    ]
    tables[TEMPLATE_UNIT_ENGINE]["subrows"] = {}
    tables[TEMPLATE_GROUP_ENGINE]["rows"] = [
        group_engine_row("UNIT-A", "2 days 00:00:00", "1 days 02:03:04", "0:30:00", "1 days 01:33:04", 5.0),
    ]
    tables[TEMPLATE_GROUP_ENGINE]["subrows"] = {}
    engine = FakeReportEngine(tables=tables)

    with patch("app.wialon_reports.wialon_request", engine):
        unit = wr.execute_wialon_unit_engine_report("sid", 101, FROM_TS, TO_TS)
        group = wr.execute_wialon_group_engine_idle_report("sid", FROM_TS, TO_TS, include_events=False)

    assert unit["total_engine_seconds"] == DAY + 7384, unit
    assert unit["total_idle_seconds"] == DAY + 5584, unit
    assert unit["total_in_motion_seconds"] == 1800, unit
    assert unit["incomplete_fields"] == [], unit
    print("[PASS] Unit engine report: day-prefixed engine/idle cells summed, not dropped or flagged incomplete.")

    v = group["vehicles"][0]
    assert v["total_time_seconds"] == 2 * DAY, v
    assert v["engine_hours_seconds"] == DAY + 7384, v
    assert v["total_idle_seconds"] == DAY + 5584, v
    assert v["incomplete_fields"] == [], v
    print("[PASS] Group engine report: day-prefixed total/engine/idle cells parsed, row not flagged incomplete.")


if __name__ == "__main__":
    guard = OfflineGuard().start()
    # Report executors discover the account's "All Unit" group; serve the fixture groups offline
    patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS).start()
    try:
        test_parsers()
        test_report_totals()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
