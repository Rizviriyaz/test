"""
Regression test for app.wialon.get_vehicle_history chunking / pagination.

Classification: MOCKED / offline. No network, no .env, no Supabase/Groq.
`app.wialon.wialon_request` is replaced by an in-memory fake Wialon; the app
(`app.main`) is never imported, so no startup login happens.

Run from Backend/:
    python scripts/test_vehicle_history_chunking.py
"""
import copy
import os
import sys
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import app.wialon as wialon  # noqa: E402
from app.wialon import WialonAPIError, get_vehicle_history  # noqa: E402


# app.wialon loads Backend/.env on import; drop any local overrides so the
# assertions below run against the module defaults.
for _key in ("WIALON_HISTORY_CHUNK_SECONDS", "WIALON_HISTORY_PAGE_LIMIT", "WIALON_HISTORY_MAX_REQUESTS"):
    os.environ.pop(_key, None)

DAY = 86400
BASE_T = 1_700_000_000


class FakeWialon:
    """In-memory messages/load_interval honouring timeFrom/timeTo (inclusive) and loadCount."""

    def __init__(self, messages, override=None):
        # Stable ascending order, like Wialon.
        self.messages = sorted(messages, key=lambda m: m["t"])
        self.calls = []
        self.override = override  # optional callable(svc, params, sid) -> response

    def __call__(self, svc, params, sid=None, *args, **kwargs):
        self.calls.append({"svc": svc, "params": copy.deepcopy(params), "sid": sid})
        if self.override is not None:
            return self.override(svc, params, sid)
        assert svc == "messages/load_interval", svc
        in_range = [
            m for m in self.messages
            if params["timeFrom"] <= m["t"] <= params["timeTo"]
        ]
        return {"count": len(in_range), "messages": copy.deepcopy(in_range[: params["loadCount"]])}

    @property
    def windows(self):
        return [(c["params"]["timeFrom"], c["params"]["timeTo"]) for c in self.calls]


def env(**kw):
    return mock.patch.dict(os.environ, {k: str(v) for k, v in kw.items()})


def run(fake, *args, **kwargs):
    with mock.patch("app.wialon.wialon_request", fake):
        return get_vehicle_history("SID", 123, *args, **kwargs)


def make(ts_list):
    return [{"t": t, "i": i} for i, t in enumerate(ts_list)]


class TestSingleCall(unittest.TestCase):
    def test_small_range_one_call_same_param_shape(self):
        src = make(range(BASE_T, BASE_T + 50))
        fake = FakeWialon(src)
        with env(WIALON_HISTORY_PAGE_LIMIT=100):
            out = run(fake, from_time=BASE_T, to_time=BASE_T + 3600)
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["svc"], "messages/load_interval")
        self.assertEqual(call["sid"], "SID")
        self.assertEqual(call["params"], {
            "itemId": 123, "timeFrom": BASE_T, "timeTo": BASE_T + 3600,
            "flags": 1, "flagsMask": 0, "loadCount": 100, "calcSensors": 1,
        })
        self.assertEqual(out, src)
        print("[PASS] small range -> single call, original param shape, output == source")


class TestWindows(unittest.TestCase):
    def test_multi_day_windows_contiguous_and_clipped(self):
        fake = FakeWialon([])
        t_from, t_to = BASE_T, BASE_T + 3 * DAY + 5000
        with env(WIALON_HISTORY_CHUNK_SECONDS=DAY):
            out = run(fake, from_time=t_from, to_time=t_to)
        self.assertEqual(out, [])
        w = fake.windows
        self.assertEqual(len(w), 4)
        self.assertEqual(w[0][0], t_from)
        self.assertEqual(w[-1][1], t_to)  # last window clipped to `to`
        for (a, b) in w[:-1]:
            self.assertEqual(b - a + 1, DAY)
        for (_, prev_end), (next_start, _) in zip(w, w[1:]):
            self.assertEqual(next_start, prev_end + 1)  # contiguous, no overlap
        covered = sum(b - a + 1 for a, b in w)
        self.assertEqual(covered, t_to - t_from + 1)
        print("[PASS] multi-day range -> contiguous non-overlapping windows covering exactly [from, to]")

    def test_exact_chunk_multiple_no_empty_trailing_window(self):
        fake = FakeWialon([])
        with env(WIALON_HISTORY_CHUNK_SECONDS=DAY):
            run(fake, from_time=BASE_T, to_time=BASE_T + 2 * DAY - 1)
        self.assertEqual(fake.windows, [(BASE_T, BASE_T + DAY - 1), (BASE_T + DAY, BASE_T + 2 * DAY - 1)])
        print("[PASS] range of exactly 2 chunks -> exactly 2 windows")

    def test_boundary_message_not_lost_or_duplicated_across_windows(self):
        edge = BASE_T + DAY - 1
        src = make([BASE_T, edge, edge + 1, BASE_T + 2 * DAY - 1])
        fake = FakeWialon(src)
        with env(WIALON_HISTORY_CHUNK_SECONDS=DAY):
            out = run(fake, from_time=BASE_T, to_time=BASE_T + 2 * DAY - 1)
        self.assertEqual(out, src)
        print("[PASS] messages on window edge seconds kept exactly once")

    def test_min_chunk_floor_is_one_hour(self):
        fake = FakeWialon([])
        with env(WIALON_HISTORY_CHUNK_SECONDS=10):
            run(fake, from_time=BASE_T, to_time=BASE_T + 7200 - 1)
        self.assertEqual(len(fake.calls), 2)
        print("[PASS] chunk size floored at 3600s")


class TestPagination(unittest.TestCase):
    def test_full_window_paginates_without_dropping_newest(self):
        # 3 messages per second, 400 msgs; page=100 is not a multiple of 3, so
        # page boundaries split a second.
        ts = [BASE_T + k // 3 for k in range(400)]
        src = make(ts)
        fake = FakeWialon(src)
        with env(WIALON_HISTORY_PAGE_LIMIT=100, WIALON_HISTORY_CHUNK_SECONDS=DAY):
            out = run(fake, from_time=BASE_T, to_time=BASE_T + 3600)
        self.assertGreater(len(fake.calls), 1)
        self.assertEqual(len(out), 400)
        self.assertEqual(sorted(m["i"] for m in out), list(range(400)))  # none dropped/duplicated
        self.assertEqual(out[-1]["t"], max(ts))  # newest preserved
        self.assertEqual([m["t"] for m in out], sorted(m["t"] for m in out))
        self.assertEqual(len({m["i"] for m in out}), len(out))
        # Each follow-up page resumes at the previous page's last timestamp (inclusive).
        starts = [c["params"]["timeFrom"] for c in fake.calls]
        self.assertEqual(starts[0], BASE_T)
        self.assertTrue(all(b > a for a, b in zip(starts, starts[1:])))
        print("[PASS] full page paginates; all %d msgs returned, chronological, no duplicates" % len(out))

    def test_exact_multiple_of_page_limit_terminates(self):
        src = make(range(BASE_T, BASE_T + 200))  # exactly 2 full pages worth
        fake = FakeWialon(src)
        with env(WIALON_HISTORY_PAGE_LIMIT=100):
            out = run(fake, from_time=BASE_T, to_time=BASE_T + 3000)
        self.assertEqual(out, src)
        print("[PASS] message count == k * page limit terminates and returns all")

    def test_pathological_more_than_page_in_one_second_terminates(self):
        t0 = BASE_T + 10
        src = make([t0] * 250 + [t0 + 5])
        for window_start in (BASE_T, t0):  # burst strictly after / exactly at page_from
            fake = FakeWialon(src)
            with env(WIALON_HISTORY_PAGE_LIMIT=100, WIALON_HISTORY_MAX_REQUESTS=1000):
                with self.assertLogs(level="WARNING"):
                    out = run(fake, from_time=window_start, to_time=t0 + 100)
            self.assertLess(len(fake.calls), 50)  # no infinite loop
            self.assertTrue(len(fake.calls) >= 2)
            self.assertEqual([m["t"] for m in out], sorted(m["t"] for m in out))
            self.assertEqual(out[-1]["t"], t0 + 5)  # later messages still reached
        print("[PASS] >page_limit messages in one second terminates and advances")


class TestRequestCap(unittest.TestCase):
    def test_cap_stops_returns_sorted_partial_and_warns(self):
        src = make(range(BASE_T, BASE_T + 1000))
        fake = FakeWialon(src)
        with env(WIALON_HISTORY_PAGE_LIMIT=100, WIALON_HISTORY_MAX_REQUESTS=3):
            with self.assertLogs(level="WARNING") as cm:
                out = run(fake, from_time=BASE_T, to_time=BASE_T + 5000)
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(any("incomplete" in line or "stopped" in line for line in cm.output))
        self.assertGreater(len(out), 0)
        self.assertLess(len(out), 1000)
        ts = [m["t"] for m in out]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual(len(set(ts)), len(ts))
        self.assertEqual(ts[0], BASE_T)
        print("[PASS] request cap -> stops at 3 calls, sorted partial data (%d msgs), warning logged" % len(out))


class TestMerge(unittest.TestCase):
    def test_sorts_out_of_order(self):
        msgs = [{"t": 5, "i": 0}, {"t": 1, "i": 1}, {"t": 3, "i": 2}]
        self.assertEqual([m["t"] for m in wialon._merge_history_messages(msgs)], [1, 3, 5])

    def test_drops_exact_duplicates_only(self):
        a = {"t": 10, "pos": {"x": 1}}
        b = {"t": 10, "pos": {"x": 2}}
        msgs = [a, copy.deepcopy(a), b, copy.deepcopy(b), {"t": 11}]
        out = wialon._merge_history_messages(msgs)
        self.assertEqual(out, [a, b, {"t": 11}])

    def test_same_t_distinct_messages_keep_stable_order(self):
        msgs = [{"t": 7, "n": k} for k in (3, 1, 2)] + [{"t": 2, "n": 9}]
        out = wialon._merge_history_messages(msgs)
        self.assertEqual([m["n"] for m in out], [9, 3, 1, 2])

    def test_empty_and_untimed(self):
        self.assertEqual(wialon._merge_history_messages([]), [])
        out = wialon._merge_history_messages([{"t": 4}, {"x": 1}])
        self.assertEqual(len(out), 2)

    def test_all(self):
        self.test_sorts_out_of_order()
        self.test_drops_exact_duplicates_only()
        self.test_same_t_distinct_messages_keep_stable_order()
        self.test_empty_and_untimed()
        print("[PASS] merge: sorted, exact dups dropped, distinct same-t kept in stable order")


class TestErrorsAndInputs(unittest.TestCase):
    def test_error_dict_raises(self):
        fake = FakeWialon([], override=lambda *a: {"error": 7, "reason": "access denied"})
        with self.assertRaises(WialonAPIError):
            run(fake, from_time=BASE_T, to_time=BASE_T + 10)
        fake = FakeWialon([], override=lambda *a: {"error": 4})
        with self.assertRaises(WialonAPIError):
            run(fake, from_time=BASE_T, to_time=BASE_T + 10)
        print("[PASS] Wialon error dict -> WialonAPIError")

    def test_error_on_later_page_raises(self):
        src = make(range(BASE_T, BASE_T + 300))
        base = FakeWialon(src)
        state = {"n": 0}

        def override(svc, params, sid):
            state["n"] += 1
            if state["n"] == 2:
                return {"error": 1003}
            return {
                "messages": [m for m in base.messages if params["timeFrom"] <= m["t"] <= params["timeTo"]][: params["loadCount"]]
            }

        fake = FakeWialon([], override=override)
        with env(WIALON_HISTORY_PAGE_LIMIT=100):
            with self.assertRaises(WialonAPIError):
                run(fake, from_time=BASE_T, to_time=BASE_T + 1000)
        print("[PASS] error on a follow-up page raises (no silent partial data)")

    def test_non_dict_or_missing_messages_returns_empty(self):
        for resp in ([], None, "oops", 5, {}, {"count": 0}, {"messages": None}):
            fake = FakeWialon([], override=lambda *a, r=resp: r)
            self.assertEqual(run(fake, from_time=BASE_T, to_time=BASE_T + 10), [], msg=repr(resp))
        print("[PASS] non-dict / missing messages -> []")

    def test_hours_only_uses_now_timestamp(self):
        fake = FakeWialon([])
        with mock.patch("app.wialon.now_timestamp", return_value=1_000_000):
            run(fake, hours=2)
        self.assertEqual(fake.windows, [(1_000_000 - 7200, 1_000_000)])
        fake = FakeWialon([])
        with mock.patch("app.wialon.now_timestamp", return_value=1_000_000):
            run(fake)  # default 6h
        self.assertEqual(fake.windows, [(1_000_000 - 6 * 3600, 1_000_000)])
        print("[PASS] hours-only call derives range from now_timestamp()")

    def test_only_one_of_from_to_falls_back_to_hours(self):
        fake = FakeWialon([])
        with mock.patch("app.wialon.now_timestamp", return_value=1_000_000):
            run(fake, hours=1, from_time=5)
        self.assertEqual(fake.windows, [(1_000_000 - 3600, 1_000_000)])
        print("[PASS] only one of from/to supplied -> hours/now range")

    def test_inverted_range_returns_empty_no_calls(self):
        fake = FakeWialon(make([BASE_T]))
        self.assertEqual(run(fake, from_time=BASE_T + 10, to_time=BASE_T), [])
        self.assertEqual(fake.calls, [])
        print("[PASS] to < from -> [] with no Wialon calls")

    def test_zero_length_range_single_call(self):
        src = make([BASE_T])
        fake = FakeWialon(src)
        self.assertEqual(run(fake, from_time=BASE_T, to_time=BASE_T), src)
        self.assertEqual(fake.windows, [(BASE_T, BASE_T)])
        print("[PASS] from == to -> one inclusive single-second request")


class TestOriginalBugRegression(unittest.TestCase):
    def test_three_day_custom_range_not_capped(self):
        per_day = 8640  # one fix every 10 s
        total = 3 * per_day
        t_from = BASE_T
        t_to = BASE_T + 3 * DAY - 1
        src = make(t_from + k * 10 for k in range(total))
        fake = FakeWialon(src)
        out = run(fake, from_time=t_from, to_time=t_to)  # default env / limits
        self.assertEqual(len(out), total)
        self.assertEqual(out, src)
        # The old bug requested a hours-derived small loadCount (<= 5000) for custom ranges.
        self.assertTrue(all(c["params"]["loadCount"] >= 50000 for c in fake.calls))
        self.assertEqual(len(fake.calls), 1)  # fits one 7-day window and one page
        self.assertEqual(out[-1]["t"], t_to - 9)  # newest message present
        print("[PASS] 3-day custom range returns all %d msgs (not capped at old 5000)" % total)

    def test_three_day_range_with_small_pages_still_complete(self):
        # Same shape at reduced scale: forces pagination inside every window.
        src = make(BASE_T + k * 10 for k in range(3 * 864))
        fake = FakeWialon(src)
        with env(WIALON_HISTORY_PAGE_LIMIT=500, WIALON_HISTORY_CHUNK_SECONDS=DAY):
            out = run(fake, from_time=BASE_T, to_time=BASE_T + 3 * DAY - 1)
        self.assertEqual(out, src)
        self.assertGreater(len(fake.calls), 3)
        print("[PASS] 3-day range with paginated windows returns every message")


if __name__ == "__main__":
    print("Contains MOCKED / offline tests only (no live Wialon).")
    result = unittest.main(exit=False, verbosity=0).result
    if result.wasSuccessful():
        print("[PASS] all vehicle history chunking tests")
        sys.exit(0)
    sys.exit(1)
