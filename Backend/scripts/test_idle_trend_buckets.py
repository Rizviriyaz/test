"""
Regression test for /api/idle/fleet-analytics trend buckets on custom ranges (MOCKED / offline).

A custom range over 48 h that starts mid-day used to be split into 24 h buckets stepped from the start time,
so a bucket labelled "Oct 20" covered Oct 20 14:00 -> Oct 21 14:00 and events were attributed to the wrong date.
Daily buckets must now follow UAE calendar days:
  - the first bucket starts at the selected from_time, later ones at UAE midnight
  - every in-range event is counted exactly once, in the bucket of its UAE start date
  - events outside [from, to] are ignored; ranges <= 48 h keep hourly buckets stepped from from_time
  - ranges that already start at UAE midnight produce the same buckets as before
Wialon is replaced by FakeReportEngine and every outbound HTTP call is blocked by OfflineGuard.

  python scripts/test_idle_trend_buckets.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import BASE_TS, FakeReportEngine  # noqa: E402
from test_idle_completeness import fleet_idle  # noqa: E402

import app.main as main  # noqa: E402
from app.utils.datetime_helpers import UAE_TZ  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def uae(y, mo, d, h=0, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=UAE_TZ).timestamp())


def ev(ts, dur=600):
    return {"start_time": ts, "duration": dur}


def test_mid_day_daily_buckets():
    frm, to = uae(2026, 10, 20, 14, 30), uae(2026, 10, 23, 9, 15)
    events = [
        ev(frm),                              # exactly at the selected start
        ev(uae(2026, 10, 20, 23, 59, 59)),    # last second of the first day
        ev(uae(2026, 10, 21, 0, 0, 0)),       # UAE midnight -> next day (old code put it in "Oct 20")
        ev(uae(2026, 10, 21, 13, 0)),         # before 14:30 on day 2 (old code put it in "Oct 20")
        ev(uae(2026, 10, 22, 18, 0), 1200),
        ev(to, 300),                          # exactly at the selected end (inclusive)
        ev(frm - 1),                          # before the range
        ev(to + 1),                           # after the range
    ]
    trend = main._build_idle_trend(events, frm, to)

    check([p["label"] for p in trend] == ["Oct 20", "Oct 21", "Oct 22", "Oct 23"],
          "mid-day 3-day range: one bucket per UAE calendar day, Oct 20..Oct 23")
    check(trend[0]["timestamp"] == frm,
          "mid-day range: first bucket starts at the selected from_time, not before it")
    check([p["timestamp"] for p in trend[1:]] == [uae(2026, 10, 21), uae(2026, 10, 22), uae(2026, 10, 23)],
          "mid-day range: later buckets start at UAE midnight")
    check(all(p["timestamp"] <= to for p in trend),
          "mid-day range: no bucket starts after the selected to_time")
    check([p["event_count"] for p in trend] == [2, 2, 1, 1],
          "mid-day range: events land in the bucket of their UAE start date (boundaries inclusive)")
    check(sum(p["event_count"] for p in trend) == 6 and sum(p["idle_seconds"] for p in trend) == 600 * 4 + 1200 + 300,
          "mid-day range: every in-range event counted once, out-of-range events ignored")
    check(trend[2]["idle_minutes"] == 20.0 and set(trend[0]) == {"timestamp", "label", "idle_seconds",
                                                                  "idle_minutes", "event_count"},
          "mid-day range: point shape unchanged (timestamp, label, idle_seconds, idle_minutes, event_count)")


def test_midnight_aligned_unchanged():
    frm = uae(2026, 10, 1)
    to = frm + 7 * 86400 - 1
    trend = main._build_idle_trend([ev(frm + 86400 * 3 + 5), ev(to)], frm, to)
    check([p["timestamp"] for p in trend] == [frm + i * 86400 for i in range(7)],
          "midnight-aligned 7-day range: same 7 daily buckets as the previous stepping")
    check([p["event_count"] for p in trend] == [0, 0, 0, 1, 0, 0, 1],
          "midnight-aligned 7-day range: events counted in the same buckets as before")


def test_utc_midnight_start_not_uae_midnight():
    # 20:00 UTC is UAE midnight; a range starting at 00:00 UTC is 04:00 UAE (the old code's typical mislabel)
    frm = uae(2026, 10, 20, 4)
    to = frm + 3 * 86400 - 1
    trend = main._build_idle_trend([ev(uae(2026, 10, 21, 2))], frm, to)
    check([p["label"] for p in trend] == ["Oct 20", "Oct 21", "Oct 22", "Oct 23"],
          "04:00 UAE start: the trailing partial UAE day (Oct 23 00:00-03:59) gets its own bucket")
    check([p["event_count"] for p in trend] == [0, 1, 0, 0],
          "04:00 UAE start: an event at Oct 21 02:00 UAE counts on Oct 21, not Oct 20")


def test_hourly_unchanged():
    frm = uae(2026, 10, 20, 14, 30)
    to = frm + 48 * 3600  # exactly 48 h stays hourly
    trend = main._build_idle_trend([ev(frm + 3599), ev(frm + 3600)], frm, to)
    check(len(trend) == 49 and trend[0]["timestamp"] == frm and trend[1]["timestamp"] == frm + 3600,
          "48 h range: hourly buckets still stepped from from_time (49 buckets incl. the end)")
    check(trend[0]["label"] == "14:30" and [p["event_count"] for p in trend[:2]] == [1, 1],
          "48 h range: hourly labels and event placement unchanged")


def test_endpoint_trend():
    res = fleet_idle(FakeReportEngine())
    trend = res["trend"]
    frm = res["timeframe"]["from_time"]
    check(frm == BASE_TS and trend[0]["timestamp"] == frm,
          "endpoint: first trend bucket starts at the selected from_time")
    check(all(datetime.fromtimestamp(p["timestamp"], tz=UAE_TZ).strftime("%H:%M:%S") == "00:00:00"
              for p in trend[1:]),
          "endpoint: later trend buckets are aligned to UAE midnight")
    check(sum(p["event_count"] for p in trend) == res["kpis"]["idle_event_count"] == 4,
          "endpoint: trend event total still equals the KPI idle event count (4)")


def main_entry():
    guard = OfflineGuard().start()
    try:
        test_mid_day_daily_buckets()
        test_midnight_aligned_unchanged()
        test_utc_midnight_start_not_uae_midnight()
        test_hourly_unchanged()
        test_endpoint_trend()
    finally:
        guard.stop()
    check(not guard.attempts, "no live network calls were attempted")
    print("\nAll idle trend bucket checks passed.")


if __name__ == "__main__":
    main_entry()
