"""
Regression test: /api/idle/fleet-analytics cache key across UAE midnight (MOCKED / offline).

A calendar timeframe ("today", "yesterday", "week", ...) starts at a UAE calendar boundary, so its cache key carries
that start (eff_from): a result cached before midnight is never served after it as the new day. A rolling timeframe
("24h", "7d", ...) keeps its key without eff_from, as before; exact, date and hours windows are unchanged.
The UAE clock is frozen via datetime_helpers.get_uae_now, the compute step is replaced by a recorder (no Wialon), and
every outbound HTTP call is blocked by OfflineGuard.

  python scripts/test_idle_cache_key_midnight.py
"""

import os
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.main as main  # noqa: E402
from app.utils.datetime_helpers import UAE_TZ  # noqa: E402

# Sunday 25 -> Monday 26 Oct 2026 (UAE), so the week boundary passes at the same midnight as the day boundary
BEFORE_MIDNIGHT = datetime(2026, 10, 25, 23, 59, 30, tzinfo=UAE_TZ)
AFTER_MIDNIGHT = datetime(2026, 10, 26, 0, 0, 30, tzinfo=UAE_TZ)
LATER_SAME_DAY = datetime(2026, 10, 26, 0, 4, 0, tzinfo=UAE_TZ)
MIDNIGHT_24 = int(datetime(2026, 10, 24, tzinfo=UAE_TZ).timestamp())
MIDNIGHT_25 = int(datetime(2026, 10, 25, tzinfo=UAE_TZ).timestamp())
MIDNIGHT_26 = int(datetime(2026, 10, 26, tzinfo=UAE_TZ).timestamp())


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def idle_key(at, include_events=False, **params):
    """(cache_key, eff_from) the endpoint uses at UAE time `at`; the cache is bypassed and nothing is computed."""
    seen = {}

    def record(eff_from, eff_to, hours, timeframe, group_id, vehicle_id, include_events, cache_key):
        seen.update(key=cache_key, eff_from=eff_from)
        return {}

    with patch("app.utils.datetime_helpers.get_uae_now", return_value=at), \
            patch("app.main.cached", return_value=None), \
            patch("app.main._compute_fleet_idle_analytics", side_effect=record):
        main.get_fleet_idle_analytics(include_events=include_events, **params)
    return seen["key"], seen["eff_from"]


def main_test():
    guard = OfflineGuard().start()
    try:
        before, before_from = idle_key(BEFORE_MIDNIGHT, timeframe="today")
        after, after_from = idle_key(AFTER_MIDNIGHT, timeframe="today")
        check(before_from == MIDNIGHT_25 and after_from == MIDNIGHT_26, "today starts at UAE midnight on each side")
        check(before != after, "today: keys differ across UAE midnight")
        check(before == f"fleet-idle-v10-tf-today-{MIDNIGHT_25}-grp-None-veh-None-summary-uae", before)
        check(after == f"fleet-idle-v10-tf-today-{MIDNIGHT_26}-grp-None-veh-None-summary-uae", after)
        check(idle_key(LATER_SAME_DAY, timeframe="today")[0] == after, "today: key is stable within the same UAE day")

        y_before, y_before_from = idle_key(BEFORE_MIDNIGHT, timeframe="yesterday")
        y_after, y_after_from = idle_key(AFTER_MIDNIGHT, timeframe="yesterday")
        check(y_before_from == MIDNIGHT_24 and y_after_from == MIDNIGHT_25, "yesterday moves one UAE day at midnight")
        check(y_before != y_after and y_after.endswith(f"tf-yesterday-{MIDNIGHT_25}-grp-None-veh-None-summary-uae"),
              "yesterday: keys differ across UAE midnight and carry the new start")
        check(y_after != after, "today and yesterday after midnight do not share a key")

        # Monday 26 Oct 2026: the week's start moves at this midnight too
        w_before, w_after = idle_key(BEFORE_MIDNIGHT, timeframe="week")[0], idle_key(AFTER_MIDNIGHT, timeframe="week")[0]
        check(w_before != w_after and w_after.endswith(f"tf-week-{MIDNIGHT_26}-grp-None-veh-None-summary-uae"),
              "week: key changes when the week boundary passes")

        check(idle_key(AFTER_MIDNIGHT, timeframe=" Today ")[0] == after, "timeframe is normalized (case, spaces)")
        full = idle_key(AFTER_MIDNIGHT, timeframe="today", include_events=True, group_id=7, vehicle_id=9)[0]
        check(full == f"fleet-idle-v10-tf-today-{MIDNIGHT_26}-grp-7-veh-9-full-uae", full)

        for tf in ("24h", "last_24_hours", "7d"):
            k_before, k_after = idle_key(BEFORE_MIDNIGHT, timeframe=tf)[0], idle_key(AFTER_MIDNIGHT, timeframe=tf)[0]
            check(k_before == k_after == f"fleet-idle-v10-tf-{tf}-grp-None-veh-None-summary-uae",
                  f"rolling {tf}: key has no eff_from and is unchanged across midnight")

        exact = idle_key(AFTER_MIDNIGHT, from_time=MIDNIGHT_25, to_time=MIDNIGHT_26 - 1)[0]
        check(exact == f"fleet-idle-v10-exact-{MIDNIGHT_25}-{MIDNIGHT_26 - 1}-grp-None-veh-None-summary-uae", exact)
        dates = idle_key(AFTER_MIDNIGHT, from_date="2026-10-25")[0]
        check(dates == "fleet-idle-v10-dates-2026-10-25-2026-10-25-grp-None-veh-None-summary-uae", dates)
        hours = idle_key(AFTER_MIDNIGHT, hours=12)[0]
        check(hours == "fleet-idle-v10-hours-12-grp-None-veh-None-summary-uae", hours)

        check(not guard.attempts, "no live network calls were attempted")
    finally:
        guard.stop()


if __name__ == "__main__":
    main_test()
