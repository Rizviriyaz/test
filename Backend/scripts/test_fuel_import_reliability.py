"""
Offline regression tests for fuel import reliability (lost batch responses, retries, stuck 'processing' history):

  - a timeout after the database stored a batch is settled by read-back: rows count as imported, never resent
  - only a failure proving the request was never sent (connect timeout / no connection) is retried; retries are
    bounded; a definite 4xx rejection is not retried; failed rows carry their source-row range
  - FUEL_SCHEMA_V2 off: an uncertain outcome (read timeout, 503, delayed commit) is read back once and never
    re-sent; rows not found are reported unverified (not failed), the status is never 'completed', Reconcile
    counts them later; an unreadable read-back is never followed by a blind resend (no duplicates)
  - FUEL_SCHEMA_V2 on: rows not found are re-sent behind the unique index; a late commit is not double counted
  - refund rows: partly stored / ignored-as-duplicate refunds are not counted as failed (both flag states)
  - the time budget starts at request entry (lock wait included), stops new batches, leaves rows 'not
    processed' (not failed) and still writes a terminal status; with every call hanging to its timeout the
    import still ends within the request limit; re-importing the file adds only the missing rows
  - an internal error after the record exists leaves a terminal status from what is actually stored
  - the final status PATCH is retried; if it still fails, Reconcile repairs the record later
  - Reconcile: stuck 'processing' and under-counted records are recounted from stored rows; repeated runs are
    idempotent; an interrupted / incomplete import is never upgraded to 'completed'; a fresh 'processing'
    record is refused (409); the route requires an import role; nothing is inserted or deleted

Supabase is an in-memory fake behind requests.post/patch and the pooled read session; every other outbound
HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_fuel_import_reliability.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# These tests cover the import with FUEL_IMPORT_REPLACE off (the replacement path has its own suites); a local
# .env that enables it must not change them. load_dotenv never overrides a variable that is already set.
os.environ["FUEL_IMPORT_REPLACE"] = ""

from test_fuel_import_integrity import AUTH, FAKE_USER, MAPPING, make_xlsx  # noqa: E402
from test_fuel_scope import run  # noqa: E402

import app.fuel.importer as importer  # noqa: E402
from app.fuel import db as fuel_db  # noqa: E402
from app.fuel.importer import FuelImportError, execute_fuel_import, reconcile_fuel_import  # noqa: E402
from app.main import app  # noqa: E402

os.environ.setdefault("SUPABASE_URL", "https://offline.invalid")
BASE = datetime(2026, 3, 5, 6, 0, 0)


def statement_rows(n):
    """n distinct purchases (one per minute) -> source rows 2..n+1 in the workbook."""
    return [[(BASE + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), "AUH 44120", 40 + i % 9, 3.0,
             round((40 + i % 9) * 3.0, 2), f"INV-{i}", 10000 + i] for i in range(n)]


class Resp:
    def __init__(self, payload, status=200, headers=None, bad_json=False):
        self._payload, self.status_code, self.headers, self._bad = payload, status, headers or {}, bad_json
        self.ok = 200 <= status < 300
        self.text = "" if bad_json else str(payload)

    def json(self):
        if self._bad:
            raise ValueError("unreadable body")
        return self._payload


class FakeClock:
    """time.perf_counter replacement that jumps forward on every transaction POST (and on advance())."""
    def __init__(self, step=0):
        self.t, self.step = time.perf_counter(), step
        self.start = self.t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += max(0.0, seconds)


def pg_numeric(value, places):
    """What PostgreSQL stores for the JSON number `value` in numeric(p, places), as PostgREST returns it (a float)."""
    if value is None:
        return None
    return float(Decimal(repr(value) if isinstance(value, float) else str(value))
                 .quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _timeout_seconds(timeout):
    """Worst case of one call with requests' (connect, read) timeout: both phases run to their limit."""
    return sum(timeout) if isinstance(timeout, tuple) else float(timeout or 0)


class FakeSupabase:
    """In-memory fuel_imports + fuel_transactions with scripted transport failures per transaction POST."""

    def __init__(self):
        self.imports, self.tx = {}, []
        self.next_import = 500
        self.tx_actions, self.patch_actions = [], []
        self.readback_failures = 0
        self.tx_posts = []
        self.clock = None
        self.uncommitted, self.commit_after_reads = [], 0  # a commit that becomes visible only after N reads
        self.slow = None  # None, or "writes" / "all": calls hang until their timeout (see test_worst_case_...)
        self.patch_calls = 0
        self.numeric_columns = False  # store litres/total_cost as numeric(12,2) and odometer_km as numeric(12,0)
        self.on_import_created = None  # called after an import record is stored (another instance's concurrent work)
        self.running_reads_fail = False

    # --- writes
    def post(self, url, params=None, json=None, headers=None, timeout=None):
        if url.endswith("/fuel_imports"):
            self.next_import += 1
            rec = {"id": self.next_import, "imported_by": None, "date_from": None, "date_to": None,
                   "error_message": None, "completed_at": None,
                   "created_at": datetime.now(timezone.utc).isoformat(), **json}
            self.imports[rec["id"]] = rec
            if self.on_import_created:
                self.on_import_created(self)
            return Resp([dict(rec)], 201)
        action = self.tx_actions.pop(0) if self.tx_actions else "ok"
        self.tx_posts.append((len(json), action))
        if self.clock:
            self.clock.t += self.clock.step
        if self.slow:
            self.clock.advance(_timeout_seconds(timeout))
            raise requests.exceptions.ReadTimeout("simulated: no response before the read timeout")
        if action == "connect_timeout":
            raise requests.exceptions.ConnectTimeout("simulated: connection never established, nothing sent")
        if action == "timeout_before_write":
            raise requests.exceptions.ReadTimeout("simulated: sent, then no response (nothing committed)")
        if action == "timeout_delayed_commit":
            self.uncommitted = [dict(r) for r in json]
            raise requests.exceptions.ReadTimeout("simulated: sent, commit becomes visible only later")
        if action == "concurrent_duplicate":
            # Another import stores the same transaction as this batch's first row just before this INSERT
            self.tx.append(dict(json[0], id=len(self.tx) + 1, import_id=-1, source_row_number=-1))
            action = "gateway_after_write"
        if action == "http503":
            return Resp({"message": "unavailable"}, 503)
        if action == "http400":
            return Resp({"message": "violates check constraint"}, 400)
        if action == "crash":
            raise KeyError("simulated internal bug")
        stored = self._store(json, on_conflict=(params or {}).get("on_conflict"))
        if action == "timeout_after_write":
            raise requests.Timeout("simulated: response lost after commit")
        if action == "gateway_after_write":
            return Resp({"message": "gateway timeout"}, 504)
        if action == "unreadable_after_write":
            return Resp(None, 201, bad_json=True)
        return Resp(stored, 201)

    def _store(self, rows, on_conflict=None):
        out = []
        for r in rows:
            # The unique index also sees a concurrent, not yet visible commit (the INSERT waits for it)
            if on_conflict and any(t.get("fingerprint") == r.get("fingerprint") for t in self.tx + self.uncommitted):
                continue
            rec = dict(r, id=len(self.tx) + 1)
            if self.numeric_columns:
                rec.update({k: pg_numeric(rec.get(k), places) for k, places in
                            (("litres", 2), ("total_cost", 2), ("odometer_km", 0))})
            self.tx.append(rec)
            out.append(dict(rec))
        return out

    def _reveal_commit(self):
        if self.uncommitted:
            if self.commit_after_reads <= 0:
                pending, self.uncommitted = self.uncommitted, []
                self._store(pending)
            self.commit_after_reads -= 1

    def patch(self, url, params=None, json=None, headers=None, timeout=None):
        self.patch_calls += 1
        if self.slow == "all":
            self.clock.advance(_timeout_seconds(timeout))
            return Resp({}, 503)
        action = self.patch_actions.pop(0) if self.patch_actions else "ok"
        if action == "timeout":
            raise requests.Timeout("simulated status update timeout")
        if action == "http503":
            return Resp({}, 503)
        iid = int(params["id"].split(".", 1)[1])
        self.imports[iid].update(json)
        return Resp([dict(self.imports[iid])], 200)

    # --- reads
    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        if url.endswith("/fuel_imports"):
            rows = list(self.imports.values())
            if "created_at" in params:  # the running-imports read of the cross-instance guard
                if self.running_reads_fail:
                    return Resp({"message": "boom"}, 500)
                since = datetime.fromisoformat(params["created_at"].split(".", 1)[1])
                rows = [r for r in rows if r["status"] == params["status"].split(".", 1)[1]
                        and datetime.fromisoformat(r["created_at"]) >= since]
            if "id" in params:
                rows = [r for r in rows if r["id"] == int(params["id"].split(".", 1)[1])]
            if "file_sha256" in params:
                rows = [r for r in rows if r.get("file_sha256") == params["file_sha256"].split(".", 1)[1]
                        and ("status" not in params or r["status"] == params["status"].split(".", 1)[1])]
            return Resp([dict(r) for r in rows], 200)
        if "import_id" in params:
            self._reveal_commit()
            if self.slow:
                self.clock.advance(_timeout_seconds(timeout))
                return Resp({"message": "gateway timeout"}, 504)
        rows = self.tx
        if "import_id" in params:
            if self.readback_failures > 0:
                self.readback_failures -= 1
                return Resp({"message": "boom"}, 500)
            rows = [r for r in rows if r["import_id"] == int(params["import_id"].split(".", 1)[1])]
        if "source_row_number" in params:
            wanted = {int(x) for x in params["source_row_number"][len("in.("):-1].split(",")}
            rows = [r for r in rows if r["source_row_number"] in wanted]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
        page = rows[offset:offset + limit]
        rng = f"{offset}-{offset + len(page) - 1}/{len(rows)}" if page else f"*/{len(rows)}"
        return Resp([dict(r) for r in page], 200, headers={"content-range": rng})

    # --- checks
    def assert_no_duplicates(self):
        keys = [(t["import_id"], t["source_row_number"]) for t in self.tx]
        assert len(keys) == len(set(keys)), "a source row of an import was stored twice"
        fps = [fuel_db.build_fingerprint(t["vehicle_identifier"], t["transaction_date"], t["litres"],
                                         t["total_cost"], t["invoice_number"], t["odometer_km"]) for t in self.tx]
        assert len(fps) == len(set(fps)), "the same transaction was stored twice"

    def stored_for(self, import_id):
        return sum(1 for t in self.tx if t["import_id"] == import_id)


def run_import(fake, rows, v2=False, budget=None, limit=None, settle="0"):
    env = {"FUEL_SCHEMA_V2": "1" if v2 else "", "FUEL_SUPABASE_WRITE_SETTLE_SECONDS": settle}
    clock_patch = patch.object(fuel_db.time, "perf_counter", fake.clock) if fake.clock else patch.object(fuel_db, "PAGE_SIZE", fuel_db.PAGE_SIZE)
    # With a fake clock, sleeping advances it (backoff and settle delays count against the budget)
    sleep = fake.clock.advance if fake.clock else (lambda _s: None)
    with patch.dict(os.environ, env), clock_patch, \
         patch.object(importer, "IMPORT_TIME_BUDGET_SECONDS", budget or importer.IMPORT_TIME_BUDGET_SECONDS), \
         patch.object(importer, "IMPORT_REQUEST_LIMIT_SECONDS", limit or importer.IMPORT_REQUEST_LIMIT_SECONDS), \
         patch("app.fuel.db.requests.post", side_effect=fake.post), \
         patch("app.fuel.db.requests.patch", side_effect=fake.patch), \
         patch("app.fuel.db._session.get", side_effect=fake.get), \
         patch.object(fuel_db.time, "sleep", side_effect=sleep):
        return execute_fuel_import(make_xlsx(rows), "Transactions_test.xlsx", MAPPING, FAKE_USER["id"],
                                   request_started=fake.clock() if fake.clock else None)


def assert_history_matches(fake, res, settled=True):
    rec = fake.imports[res.import_id]
    assert rec["status"] == res.status and rec["status"] != "processing", rec
    for k in ("rows_imported", "rows_duplicate", "rows_failed"):
        assert rec[k] == getattr(res, k), (k, rec[k], getattr(res, k))
    if settled:
        assert rec["rows_imported"] == fake.stored_for(res.import_id), "history must equal what is stored"
    assert res.rows_found == (res.rows_ignored + res.rows_imported + res.rows_duplicate + res.rows_failed
                              + res.rows_unverified + res.rows_not_processed), res
    if res.rows_unverified or res.rows_not_processed:
        assert res.status != "completed" and importer.INCOMPLETE_MARKER in res.error_message, res
    fake.assert_no_duplicates()


# ─── Batch outcomes ────────────────────────────────────────────────────────────

def test_timeout_after_successful_write_is_not_failed_or_resent():
    fake = FakeSupabase()
    fake.tx_actions = ["ok", "timeout_after_write", "ok"]
    res = run_import(fake, statement_rows(250))
    assert res.rows_imported == 250 and res.rows_failed == 0 and res.status == "completed", res
    assert len(fake.tx_posts) == 3, f"batch 2 must not be resent after its read-back found it stored: {fake.tx_posts}"
    assert_history_matches(fake, res)
    print("[PASS] Timeout after a committed batch: read-back counts its 100 rows as imported; nothing resent or failed.")


def test_gateway_error_and_unreadable_body_after_write():
    for action in ("gateway_after_write", "unreadable_after_write"):
        fake = FakeSupabase()
        fake.tx_actions = [action]
        res = run_import(fake, statement_rows(120))
        assert res.rows_imported == 120 and res.rows_failed == 0, (action, res)
        assert len(fake.tx_posts) == 2, (action, fake.tx_posts)
        assert_history_matches(fake, res)
    print("[PASS] 504 after commit and an unreadable 2xx body are settled from the database, not counted as failed.")


def test_partial_success_retry_and_definite_rejection():
    for v2 in (False, True):
        fake = FakeSupabase()
        fake.tx_actions = ["ok", "connect_timeout", "ok", "http400"]
        res = run_import(fake, statement_rows(250), v2=v2)
        # batch 2 never reached the server and was retried; batch 3 (rows 202-251) was rejected outright
        assert res.rows_imported == 200 and res.rows_failed == 50 and res.rows_unverified == 0, (v2, res)
        assert res.status == "completed_with_errors"
        assert [a for _, a in fake.tx_posts] == ["ok", "connect_timeout", "ok", "http400"], fake.tx_posts
        assert "Batch 3 (source rows 202-251) rejected by the database: HTTP 400" in res.error_message, res.error_message
        assert "Batch 2" not in res.error_message, "a batch that succeeded on retry is not an error"
        assert_history_matches(fake, res)
    print("[PASS] Not-sent batch (connect timeout) retried in both flag states; 4xx not retried; failed rows name their source rows.")


def test_retries_are_bounded():
    fake = FakeSupabase()
    fake.tx_actions = ["connect_timeout"] * 5
    res = run_import(fake, statement_rows(5))
    assert len(fake.tx_posts) == 3, f"default FUEL_SUPABASE_WRITE_ATTEMPTS is 3: {fake.tx_posts}"
    assert res.rows_imported == 0 and res.rows_failed == 5 and res.status == "failed", res
    assert "5 row(s) not stored after 3 attempt(s) (ConnectTimeout)" in res.error_message, res.error_message
    assert_history_matches(fake, res)
    print("[PASS] Not-sent failures retried at most 3 times; then the rows are failed (known not stored) with the reason.")


def test_not_sent_classification():
    from urllib3.exceptions import MaxRetryError, NewConnectionError
    refused = requests.ConnectionError(MaxRetryError(None, "/", reason=NewConnectionError(None, "refused")))
    aborted = requests.ConnectionError("('Connection aborted.', RemoteDisconnected())")
    assert fuel_db._not_sent(requests.exceptions.ConnectTimeout("x"))
    assert fuel_db._not_sent(refused)
    for exc in (requests.exceptions.ReadTimeout("x"), aborted, requests.exceptions.ChunkedEncodingError("x")):
        assert not fuel_db._not_sent(exc), exc
    print("[PASS] Only a connect timeout or a failed connection counts as 'not sent'; read timeouts and dropped connections are uncertain.")


def test_uncertain_outcome_flag_off_is_never_resent():
    for action in ("timeout_before_write", "http503"):
        fake = FakeSupabase()
        fake.tx_actions = ["ok", action]
        res = run_import(fake, statement_rows(150))
        assert len(fake.tx_posts) == 2, f"{action}: an uncertain batch must not be re-sent without the unique index"
        assert res.rows_imported == 100 and res.rows_unverified == 50 and res.rows_failed == 0, (action, res)
        assert res.status == "completed_with_errors" and "were not re-sent" in res.error_message, res.error_message
        assert_history_matches(fake, res)
    print("[PASS] FUEL_SCHEMA_V2 off: read timeout / 503 -> one read-back, no re-send; rows unverified (not failed), never 'completed'.")


def test_delayed_commit_flag_off():
    # The commit becomes visible only after the read-back and the re-count: the rows stay unverified, are never
    # re-sent (that would store them twice), and Reconcile counts them later without upgrading to 'completed'.
    fake = FakeSupabase()
    fake.tx_actions = ["timeout_delayed_commit"]
    fake.commit_after_reads = 2
    res = run_import(fake, statement_rows(4))
    assert len(fake.tx_posts) == 1 and res.rows_imported == 0 and res.rows_unverified == 4, res
    assert res.status == "completed_with_errors", "rows may be stored: not 'failed', and never 'completed'"
    assert_history_matches(fake, res)
    rec = _reconcile(fake, res.import_id)
    assert fake.stored_for(res.import_id) == 4 and rec["rows_imported"] == 4 and rec["rows_failed"] == 0, rec
    assert rec["status"] == "completed_with_errors" and importer.INCOMPLETE_MARKER in rec["error_message"], rec
    fake.assert_no_duplicates()

    # Visible by the time of the re-count: the import itself settles the rows as stored
    fake = FakeSupabase()
    fake.tx_actions = ["timeout_delayed_commit"]
    fake.commit_after_reads = 1
    res = run_import(fake, statement_rows(4))
    assert len(fake.tx_posts) == 1 and res.rows_imported == 4 and res.rows_unverified == 0, res
    assert_history_matches(fake, res)
    print("[PASS] FUEL_SCHEMA_V2 off, delayed commit: never re-sent, no duplicate; unverified until counted (re-count or Reconcile).")


def test_flag_on_uncertain_outcomes_resend_safely():
    fake = FakeSupabase()
    fake.tx_actions = ["timeout_before_write"]
    res = run_import(fake, statement_rows(4), v2=True)
    assert len(fake.tx_posts) == 2 and res.rows_imported == 4 and res.rows_unverified == 0, res
    assert_history_matches(fake, res)

    # Late commit: the read-back misses it, the re-send is skipped by the unique index, the re-count finds the
    # rows under this import, so they are imported (not duplicates) and nothing is stored twice.
    fake = FakeSupabase()
    fake.tx_actions = ["timeout_delayed_commit"]
    fake.commit_after_reads = 1
    res = run_import(fake, statement_rows(4), v2=True)
    assert len(fake.tx_posts) == 2, fake.tx_posts
    assert res.rows_imported == 4 and res.rows_duplicate == 0 and res.rows_unverified == 0, res
    assert_history_matches(fake, res)
    print("[PASS] FUEL_SCHEMA_V2 on: uncertain batches re-sent behind the unique index; a late commit is counted once, as imported.")


def test_unreadable_readback_never_resends():
    fake = FakeSupabase()
    fake.tx_actions = ["timeout_after_write"]
    fake.readback_failures = 2  # the read-back and the final re-count both fail (HTTP 500 is not retried)
    res = run_import(fake, statement_rows(3))
    assert fake.readback_failures == 0
    assert len(fake.tx_posts) == 1, "outcome unknown and unreadable: resending could duplicate rows"
    assert fake.stored_for(res.import_id) == 3
    assert res.rows_imported == 0 and res.rows_failed == 0 and res.rows_unverified == 3, res
    assert res.status == "completed_with_errors", "rows may be stored: neither 'failed' nor 'completed'"
    assert "not retried to avoid duplicates" in res.error_message, res.error_message
    assert any("Reconcile" in w for w in res.warnings), res.warnings
    assert_history_matches(fake, res, settled=False)
    # Reconcile later corrects the counts from the stored rows, but the import stays marked incomplete
    rec = _reconcile(fake, res.import_id)
    assert rec["rows_imported"] == 3 and rec["rows_failed"] == 0 and rec["status"] == "completed_with_errors", rec
    assert "not retried to avoid duplicates" in rec["error_message"], "the original error context is kept"
    print("[PASS] Unknown outcome + failed read-back: no blind resend; rows unverified (not failed); Reconcile fixes counts, never upgrades.")


def test_v2_on_conflict_readback_counts_inserted_not_duplicate():
    fake = FakeSupabase()
    fake.tx_actions = ["timeout_after_write"]
    res = run_import(fake, statement_rows(4), v2=True)
    assert res.rows_imported == 4 and res.rows_duplicate == 0 and res.rows_failed == 0, res
    assert len(fake.tx_posts) == 1
    assert_history_matches(fake, res)
    with patch("app.fuel.db._session.get", side_effect=fake.get) as get:
        assert fuel_db.find_import_by_sha256("abc") is None
    assert get.call_args.kwargs["params"]["status"] == "eq.completed", "only a completed import blocks the same file"
    print("[PASS] FUEL_SCHEMA_V2: read-back rows count as inserted; only a completed import blocks the identical file.")


# ─── Refund adjustment counts ──────────────────────────────────────────────────

def refund_rows(n):
    """n distinct refunds (negative litres and cost), an hour after the purchases of statement_rows."""
    return [[(BASE + timedelta(hours=1, minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), "AUH 44120", -(20 + i), 3.0,
             -round((20 + i) * 3.0, 2), f"RF-{i}", None] for i in range(n)]


def test_negative_litres_rejected_before_insert_both_flags():
    # Litres must be >= 0: negative rows fail validation and are never sent, with either schema flag
    rows = statement_rows(2) + refund_rows(3)  # purchases: source rows 2-3; negative rows: 4-6
    for v2 in (False, True):
        fake = FakeSupabase()
        res = run_import(fake, rows, v2=v2)
        assert (res.rows_imported, res.rows_refund, res.rows_failed, res.rows_unverified) == (2, 0, 3, 0), (v2, res)
        assert sorted(d["source_row"] for d in res.failed_row_details) == [4, 5, 6], res.failed_row_details
        assert all("cannot be negative" in d["reason"] for d in res.failed_row_details)
        assert all(t["litres"] > 0 for t in fake.tx), "no negative row reached the database"
        assert_history_matches(fake, res)
    print("[PASS] Negative litres are rejected during validation (both flags); purchases stored; nothing negative sent.")


# ─── Terminal status and resume ────────────────────────────────────────────────

def test_time_budget_terminal_status_then_resume_without_duplicates():
    fake = FakeSupabase()
    fake.clock = FakeClock(step=600)  # each batch "takes" 600 s against a 1000 s budget
    rows = statement_rows(250)
    first = run_import(fake, rows, budget=1000, limit=2000)
    # Rows never sent are 'not processed', not failed: nothing was attempted for them
    assert first.rows_imported == 200 and first.rows_failed == 0 and first.rows_not_processed == 50, first
    assert first.status == "completed_with_errors" and "Time limit reached: 50 row(s)" in first.error_message
    assert first.error_message.startswith(importer.INCOMPLETE_MARKER), first.error_message
    assert_history_matches(fake, first)
    rec = _reconcile(fake, first.import_id)
    assert rec["status"] == "completed_with_errors" and rec["rows_imported"] == 200, "never upgraded to completed"

    fake.clock = None
    second = run_import(fake, rows)
    assert second.rows_imported == 50 and second.rows_duplicate == 200 and second.rows_failed == 0, second
    assert len(fake.tx) == 250, "re-importing the file must add only the missing rows"
    assert_history_matches(fake, second)
    print("[PASS] Time budget: remaining batches 'not processed' (not failed), terminal status written; Reconcile keeps it "
          "incomplete; re-import adds only the 50 missing rows.")


def test_time_budget_starts_at_request_entry_and_bounds_lock_wait():
    import threading
    deadlines = importer.ImportDeadlines(100.0)
    assert deadlines.status == 100.0 + importer.IMPORT_REQUEST_LIMIT_SECONDS - importer.IMPORT_RESPONSE_MARGIN_SECONDS
    assert deadlines.writes <= deadlines.recount - importer.IMPORT_RECOUNT_RESERVE_SECONDS
    assert deadlines.recount <= deadlines.status - importer.IMPORT_STATUS_RESERVE_SECONDS
    assert deadlines.status - 100.0 < 300, "everything must end before the 300 s Vercel maxDuration"

    never = patch.object(importer, "_execute_fuel_import_locked", side_effect=AssertionError("must not run"))
    # The request already used its budget (e.g. a slow upload): refused at once, nothing written
    with never:
        try:
            execute_fuel_import(b"", "s.xlsx", {}, FAKE_USER["id"], request_started=time.perf_counter() - 400)
            raise AssertionError("expected 409")
        except FuelImportError as exc:
            assert exc.status_code == 409

    # Another import holds the lock: the wait is cut to the request's remaining budget, not FUEL_IMPORT_LOCK_WAIT_SECONDS
    holder = threading.Lock()
    holder.acquire()
    started = time.perf_counter()
    entry = started - (importer.ImportDeadlines(started).writes - started - importer.IMPORT_MIN_WORK_SECONDS - 0.3)
    with patch.object(importer, "_IMPORT_LOCK", holder), patch.object(importer, "IMPORT_LOCK_WAIT_SECONDS", 300), never:
        try:
            execute_fuel_import(b"", "s.xlsx", {}, FAKE_USER["id"], request_started=entry)
            raise AssertionError("expected 409")
        except FuelImportError as exc:
            assert exc.status_code == 409
    waited = time.perf_counter() - started
    assert waited < 5, f"lock wait must be bounded by the remaining budget, waited {waited:.1f}s"

    # The route hands the middleware's request-entry time to the importer
    seen = {}

    def capture(*args):
        seen["started"] = args[5]
        raise FuelImportError("stop", 400)
    client = TestClient(app)
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.routes.execute_fuel_import", side_effect=capture):
        before = time.perf_counter()
        client.post("/api/fuel/import", headers=AUTH, data={"mapping": '{"a": "b"}'},
                    files={"file": ("s.xlsx", make_xlsx(statement_rows(1)))})
    assert isinstance(seen.get("started"), float) and seen["started"] >= before, seen
    print("[PASS] Time budget anchored at request entry (route -> importer); lock wait bounded by it; ends < 300 s.")


def test_worst_case_every_call_hangs_still_ends_within_request_limit():
    # Every insert, read-back, re-count and status update hangs until its (clipped) timeout and then fails.
    # The import must still return before the request limit, with an honest, non-'completed' result.
    limit = importer.IMPORT_REQUEST_LIMIT_SECONDS
    for slow in ("writes", "all"):
        fake = FakeSupabase()
        fake.clock = FakeClock()
        fake.slow = slow
        res = run_import(fake, statement_rows(1000), settle="2")
        elapsed = fake.clock() - fake.clock.start
        assert elapsed <= limit - importer.IMPORT_RESPONSE_MARGIN_SECONDS, f"{slow}: {elapsed:.0f}s of a {limit}s limit"
        assert res.rows_imported == 0 and res.rows_failed == 0, res
        assert res.rows_unverified + res.rows_not_processed == 1000 and res.status != "completed", res
        assert res.rows_not_processed > 0, "batches must stop being started before the limit"
        rec = fake.imports[res.import_id]
        if slow == "writes":
            assert rec["status"] == res.status == "completed_with_errors", rec
        else:
            assert rec["status"] == "processing" and any("could not be updated" in w for w in res.warnings), res.warnings
    print(f"[PASS] Worst case (every call hangs to its timeout): import ends within {limit - importer.IMPORT_RESPONSE_MARGIN_SECONDS}s "
          f"of request entry (limit {limit}s); rows unverified / not processed, never 'completed'.")


def test_internal_error_mid_import_leaves_terminal_status():
    fake = FakeSupabase()
    fake.tx_actions = ["ok", "crash"]
    try:
        run_import(fake, statement_rows(250))
        raise AssertionError("expected FuelImportError")
    except FuelImportError as exc:
        assert exc.status_code == 500 and "100 row(s) were stored" in exc.message, exc.message
    (rec,) = fake.imports.values()
    assert rec["status"] == "completed_with_errors" and rec["rows_imported"] == 100 and rec["completed_at"], rec
    assert rec["date_from"] == "2026-03-05", rec
    fake.assert_no_duplicates()
    print("[PASS] Internal error after the record exists: history gets a terminal status with the stored count.")


def test_status_update_retried_then_reconcile_when_it_keeps_failing():
    fake = FakeSupabase()
    fake.patch_actions = ["timeout", "http503"]  # third attempt succeeds
    res = run_import(fake, statement_rows(3))
    assert not any("could not be updated" in w for w in res.warnings), res.warnings
    assert_history_matches(fake, res)

    fake = FakeSupabase()
    fake.patch_actions = ["timeout"] * 3
    res = run_import(fake, statement_rows(3))
    assert any(f"#{res.import_id}" in w and "processing" in w for w in res.warnings), res.warnings
    rec = fake.imports[res.import_id]
    assert rec["status"] == "processing" and fake.stored_for(res.import_id) == 3
    with patch("app.fuel.db.requests.patch", side_effect=fake.patch), patch("app.fuel.db._session.get", side_effect=fake.get):
        try:
            reconcile_fuel_import(res.import_id)
            raise AssertionError("a fresh 'processing' record may still be running")
        except FuelImportError as exc:
            assert exc.status_code == 409
        rec["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        fixed = reconcile_fuel_import(res.import_id)
    assert fixed["status"] == "completed_with_errors" and fixed["rows_imported"] == 3, fixed
    assert fake.imports[res.import_id]["status"] == "completed_with_errors"
    print("[PASS] Status PATCH retried (3 attempts); if all fail, Reconcile (after the stale window) repairs the record.")


# ─── Fingerprint rounding, accounting and concurrency across instances ─────────

def test_fingerprint_rounding_matches_postgres_numeric():
    # (statement value, places, what PostgreSQL numeric(p, places) stores: ties round half away from zero)
    cases = [(2.675, 2, "2.68"), (1.005, 2, "1.01"), (45.125, 2, "45.13"), (-2.675, 2, "-2.68"), (-0.001, 2, "0.00"),
             (0.1 + 0.2, 2, "0.30"), (50, 2, "50.00"), ("12.5", 2, "12.50"), (12344.5, 0, "12345"),
             (-12344.5, 0, "-12345"), (120000.49, 0, "120000"), (None, 2, "")]
    for value, places, stored in cases:
        assert fuel_db.fingerprint_number(value, places) == stored, (value, places)
        if value is not None:
            assert pg_numeric(value, places) == float(stored), (value, places)
            # The stored value as PostgREST returns it (a JSON number) gives the same text
            assert fuel_db.fingerprint_number(pg_numeric(value, places), places) == stored, (value, places)
    assert f"{2.675:.2f}" == "2.67" and f"{12344.5:.0f}" == "12344", "binary float formatting (the old rule) differs"
    a = fuel_db.build_fingerprint("AUH 44120", "2026-03-05 06:00:00", 45.125, 140.885, "INV-1", 12344.5)
    b = fuel_db.build_fingerprint("AUH-44120", "2026-03-05T06:00:00+00:00", pg_numeric(45.125, 2),
                                  pg_numeric(140.885, 2), "inv-1", pg_numeric(12344.5, 0))
    assert a == b == "auh44120|2026-03-05T06:00:00|45.13|140.89|inv-1|12345", (a, b)
    print("[PASS] Fingerprints: statement values and numeric-stored values give one string (Decimal, half away from zero).")


def test_reimport_with_rounded_stored_values_is_all_duplicates():
    fake = FakeSupabase()
    fake.numeric_columns = True
    rows = [["2026-03-05 06:00:00", "AUH 44120", 45.125, 3.0, 135.375, "INV-1", 12344.5],
            ["2026-03-05 07:00:00", "AUH 44120", 2.675, 3.0, 8.025, "INV-2", 12400.5]]
    first = run_import(fake, rows)
    assert first.rows_imported == 2 and fake.tx[0]["litres"] == 45.13 and fake.tx[0]["odometer_km"] == 12345, fake.tx[0]
    assert_history_matches(fake, first)
    again = run_import(fake, rows)
    assert again.rows_imported == 0 and again.rows_duplicate == 2 and len(fake.tx) == 2, again
    assert_history_matches(fake, again)
    print("[PASS] Re-import against values rounded by numeric columns: every row detected as a duplicate, nothing stored twice.")


def test_ignored_rows_are_counted_and_totals_add_up():
    fake = FakeSupabase()
    rows = statement_rows(5)
    rows.insert(2, [None] * 7)
    rows.append(["Total", None, None, None, 999.0, None, None])
    res = run_import(fake, rows)
    assert res.rows_found == 7 and res.rows_ignored == 2 and res.rows_imported == 5 and res.status == "completed", res
    assert_history_matches(fake, res)
    print("[PASS] Blank and total rows are reported as rows_ignored; all buckets add up to rows_found.")


def test_concurrent_import_on_another_instance():
    rows = statement_rows(30)
    now = datetime.now(timezone.utc).isoformat()

    def refused(fake):
        try:
            run_import(fake, rows)
        except FuelImportError as exc:
            mine = fake.imports[max(fake.imports)]
            assert mine["status"] == "failed" and mine["rows_imported"] == 0 and mine["completed_at"], mine
            assert not fake.tx_posts, "nothing may be written by a refused import"
            return exc
        raise AssertionError("expected FuelImportError")

    # Another instance's import is still running: this one stops before its duplicate check and writes nothing
    fake = FakeSupabase()
    _seed(fake, 41, 30, status="processing", rows_found=30, created_at=now)
    exc = refused(fake)
    assert exc.status_code == 409 and "#41" in exc.message, exc.message
    # It finishes; the retry sees its rows as duplicates and stores nothing twice
    fake.imports[41]["status"] = "completed"
    res = run_import(fake, rows)
    assert res.rows_imported == 0 and res.rows_duplicate == 30 and len(fake.tx) == 30, res
    fake.assert_no_duplicates()

    # The other instance creates its record between this import's create and its check: this one stops
    fake = FakeSupabase()

    def other_instance_starts(f):
        f.on_import_created = None
        f.imports[42] = {"id": 42, "status": "processing", "created_at": datetime.now(timezone.utc).isoformat()}
    fake.on_import_created = other_instance_starts
    assert refused(fake).status_code == 409

    # A stale 'processing' record (an interrupted import such as #17) does not block new imports
    fake = FakeSupabase()
    _seed(fake, 17, 0, status="processing")
    res = run_import(fake, rows)
    assert res.rows_imported == 30, res
    assert_history_matches(fake, res)

    # The guard cannot read fuel_imports: refused with 503, nothing written (fails closed)
    fake = FakeSupabase()
    fake.running_reads_fail = True
    assert refused(fake).status_code == 503
    print("[PASS] Concurrent imports across instances: a running import blocks (409, nothing written), retry adds no "
          "duplicates; stale records do not block; an unreadable check fails closed (503).")


def test_reported_incident_reconcile_then_reimport():
    # Shape of import #17: 2,341 sheet rows (6 invalid), 2,235 stored, record left 'processing' by the interrupted run
    fake = FakeSupabase()
    good = statement_rows(2335)
    # Invalid rows: negative litres (zero litres are a genuine 0 and would be imported)
    bad = [[(BASE + timedelta(days=5, minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), "AUH 44120", -1, 3.0, 0, f"BAD-{i}", 1]
           for i in range(6)]
    _seed(fake, 17, 2235, status="processing", rows_found=2341)
    rec = _reconcile(fake, 17)
    assert rec["status"] == "completed_with_errors" and rec["rows_imported"] == 2235, rec
    assert importer.INCOMPLETE_MARKER in rec["error_message"] and _reconcile(fake, 17) == rec
    res = run_import(fake, good + bad)
    assert (res.rows_imported, res.rows_duplicate, res.rows_failed) == (100, 2235, 6), res
    assert res.status == "completed_with_errors" and len(fake.tx) == 2335, res
    assert_history_matches(fake, res)
    print("[PASS] Incident replay: Reconcile sets #17 to 2,235 stored (kept incomplete, idempotent); re-import adds only "
          "the 100 missing rows, 6 invalid rows failed, no duplicates.")


# ─── Reconcile of existing records ─────────────────────────────────────────────

def _seed(fake, import_id, n, **record):
    fake.imports[import_id] = {"id": import_id, "file_name": "Transactions_09-02-26_09-09.xlsx", "imported_by": None,
                               "rows_found": n, "rows_imported": 0, "rows_duplicate": 0, "rows_failed": 0,
                               "date_from": None, "date_to": None, "error_message": None, "completed_at": None,
                               "created_at": "2026-02-09T09:09:41.52+00:00", **record}
    for i, r in enumerate(statement_rows(n)):
        fake.tx.append({"id": len(fake.tx) + 1, "import_id": import_id, "source_row_number": i + 2,
                        "transaction_date": r[0].replace(" ", "T"), "vehicle_identifier": r[1], "litres": r[2],
                        "total_cost": r[4], "invoice_number": r[5], "odometer_km": r[6]})


def _reconcile(fake, import_id):
    with patch("app.fuel.db.requests.patch", side_effect=fake.patch), patch("app.fuel.db._session.get", side_effect=fake.get):
        return reconcile_fuel_import(import_id)


def test_reconcile_stuck_processing_import():
    fake = FakeSupabase()
    _seed(fake, 7, 120, status="processing", rows_found=250)
    before = len(fake.tx)
    rec = _reconcile(fake, 7)
    assert rec["status"] == "completed_with_errors" and rec["rows_imported"] == 120, rec
    assert rec["date_from"] == "2026-03-05" and rec["completed_at"], rec
    assert "interrupted" in rec["error_message"] and "Re-import the same file" in rec["error_message"]
    assert len(fake.tx) == before, "reconcile must not insert or delete transactions"
    print("[PASS] Reconcile: a stale 'processing' import becomes completed_with_errors with its stored count.")


def test_reconcile_undercounted_import_is_idempotent():
    fake = FakeSupabase()
    # Like the reported import: two 'network failure' batches were actually stored
    _seed(fake, 8, 250, status="completed_with_errors", rows_imported=150, rows_failed=106,
          error_message="Batch 2 network failure.; Batch 3 network failure.", completed_at="2026-02-09T09:12:00+00:00")
    rec = _reconcile(fake, 8)
    assert rec["rows_imported"] == 250 and rec["rows_failed"] == 6 and rec["status"] == "completed_with_errors", rec
    assert rec["error_message"].startswith("Batch 2 network failure.") and "history showed 150" in rec["error_message"]
    again = _reconcile(fake, 8)
    assert again["rows_imported"] == 250 and again["rows_failed"] == 6, again
    assert again["error_message"].count("Reconciled:") == 1, again["error_message"]
    print("[PASS] Reconcile: under-counted batches recounted (150 -> 250 imported, 106 -> 6 failed); repeat is idempotent.")


def test_reconcile_repeated_is_idempotent_and_never_upgrades():
    fake = FakeSupabase()
    _seed(fake, 21, 120, status="processing", rows_found=250)
    runs = [_reconcile(fake, 21) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2], "repeated reconciliation must give the same record"
    rec = runs[-1]
    assert rec["status"] == "completed_with_errors" and rec["rows_imported"] == 120, rec
    assert "interrupted before it finished" in rec["error_message"], "the interrupted context must survive repeats"
    assert rec["error_message"].count(importer.INCOMPLETE_MARKER) == 1 and rec["error_message"].count("Reconciled:") == 1

    # Even when every row of the file is stored, an interrupted import is not upgraded to 'completed'
    fake = FakeSupabase()
    _seed(fake, 22, 5, status="processing", rows_found=5)
    assert [_reconcile(fake, 22)["status"] for _ in range(2)] == ["completed_with_errors"] * 2

    # Records written before the marker existed: time limit / unknown outcome wording also means incomplete
    for i, legacy in enumerate(("Time limit reached: 50 row(s) (source rows 202-251) were not sent.",
                                "Batch 2 (source rows 102-201): outcome unknown after Timeout; rows cannot be read back.")):
        fake = FakeSupabase()
        _seed(fake, 30 + i, 3, status="completed_with_errors", rows_imported=3, rows_found=3, error_message=legacy,
              completed_at="2026-02-09T09:12:00+00:00")
        first, again = _reconcile(fake, 30 + i), _reconcile(fake, 30 + i)
        assert first == again and first["status"] == "completed_with_errors", first
        assert first["error_message"].startswith(legacy), "original error text kept for re-import"
    print("[PASS] Reconcile repeated 3x: identical record; interrupted/incomplete (incl. legacy wording) never upgraded to completed.")


def test_finalize_interrupted_then_reconcile():
    for count_fails in (False, True):
        fake = FakeSupabase()
        fake.tx_actions = ["ok", "crash"]
        if count_fails:
            fake.readback_failures = 1  # _finalize_interrupted_import cannot count the stored rows
        try:
            run_import(fake, statement_rows(250))
            raise AssertionError("expected FuelImportError")
        except FuelImportError as exc:
            assert exc.status_code == 500
        (rec,) = fake.imports.values()
        assert rec["status"] == ("failed" if count_fails else "completed_with_errors"), rec
        assert importer.INCOMPLETE_MARKER in rec["error_message"] and "interrupted by an internal error" in rec["error_message"]
        original = rec["error_message"]
        first, again = _reconcile(fake, rec["id"]), _reconcile(fake, rec["id"])
        assert first == again, "repeat must not change the record"
        assert first["status"] == "completed_with_errors" and first["rows_imported"] == 100, first
        assert first["error_message"].startswith(original), "the interrupted error context is kept"
        assert "Re-import the same file" in first["error_message"]
        fake.assert_no_duplicates()
    print("[PASS] _finalize_interrupted_import then Reconcile (x2): stays completed_with_errors with the stored count; "
          "interrupted context kept; also when the first count failed.")


def test_reconcile_reports_extra_copies_without_deleting():
    fake = FakeSupabase()
    _seed(fake, 9, 3, status="completed", rows_imported=3)
    fake.tx.append(dict(fake.tx[0], id=99))
    rec = _reconcile(fake, 9)
    assert rec["rows_imported"] == 3 and rec["status"] == "completed_with_errors", rec
    assert "1 extra stored copy(ies) of source row(s) 2" in rec["error_message"], rec["error_message"]
    assert len(fake.tx) == 4, "nothing is deleted"
    print("[PASS] Reconcile: extra stored copies are reported for review, never deleted.")


def test_reconcile_route_requires_import_role():
    fake = FakeSupabase()
    _seed(fake, 11, 2, status="processing")
    client = TestClient(app)
    with patch("app.fuel.db.requests.patch", side_effect=fake.patch), patch("app.fuel.db._session.get", side_effect=fake.get):
        with patch("app.main.authenticate_access_token", return_value={**FAKE_USER, "role": "viewer"}):
            denied = client.post("/api/fuel/imports/11/reconcile", headers=AUTH)
        assert fake.imports[11]["status"] == "processing"
        with patch("app.main.authenticate_access_token", return_value=FAKE_USER):
            ok = client.post("/api/fuel/imports/11/reconcile", headers=AUTH)
            missing = client.post("/api/fuel/imports/404/reconcile", headers=AUTH)
    assert denied.status_code == 403, denied.text
    assert ok.status_code == 200 and ok.json()["status"] == "completed_with_errors" and ok.json()["rows_imported"] == 2, ok.text
    assert missing.status_code == 404, missing.text
    print("[PASS] POST /api/fuel/imports/{id}/reconcile: 403 for viewer, 200 for import roles, 404 for unknown ids.")


TESTS = [
    test_timeout_after_successful_write_is_not_failed_or_resent,
    test_gateway_error_and_unreadable_body_after_write,
    test_partial_success_retry_and_definite_rejection,
    test_retries_are_bounded,
    test_not_sent_classification,
    test_uncertain_outcome_flag_off_is_never_resent,
    test_delayed_commit_flag_off,
    test_flag_on_uncertain_outcomes_resend_safely,
    test_unreadable_readback_never_resends,
    test_v2_on_conflict_readback_counts_inserted_not_duplicate,
    test_negative_litres_rejected_before_insert_both_flags,
    test_time_budget_terminal_status_then_resume_without_duplicates,
    test_time_budget_starts_at_request_entry_and_bounds_lock_wait,
    test_worst_case_every_call_hangs_still_ends_within_request_limit,
    test_internal_error_mid_import_leaves_terminal_status,
    test_status_update_retried_then_reconcile_when_it_keeps_failing,
    test_fingerprint_rounding_matches_postgres_numeric,
    test_reimport_with_rounded_stored_values_is_all_duplicates,
    test_ignored_rows_are_counted_and_totals_add_up,
    test_concurrent_import_on_another_instance,
    test_reported_incident_reconcile_then_reimport,
    test_reconcile_stuck_processing_import,
    test_reconcile_undercounted_import_is_idempotent,
    test_reconcile_repeated_is_idempotent_and_never_upgrades,
    test_finalize_interrupted_then_reconcile,
    test_reconcile_reports_extra_copies_without_deleting,
    test_reconcile_route_requires_import_role,
]


if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL IMPORT RELIABILITY (OFFLINE)") else 0)
