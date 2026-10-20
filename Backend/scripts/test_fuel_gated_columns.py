"""
Offline regression tests for Fuel Phase 8: FUEL_SCHEMA_V2-gated columns, review-only SQL, dry-run backfill.

  - flag off: insert/import payloads keep exactly the pre-existing keys (no unknown columns)
  - flag on: 4 new transaction columns, file_sha256, 409 for an identical file, on_conflict duplicate count
  - backfill script writes nothing without --apply; SQL file is review-only with the DELETE commented out

Nothing touches a database. Run from Backend/:  python scripts/test_fuel_gated_columns.py
"""
import io
import os
import sys
from unittest.mock import MagicMock, patch

import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import run  # noqa: E402

import app.fuel.db as fuel_db  # noqa: E402
from app.fuel.importer import FuelImportError, execute_fuel_import  # noqa: E402

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAPPING = {"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres",
           "unit_price": "Rate", "total_cost": "Amount", "invoice_number": "Invoice"}
ROWS = [["2026-03-05 09:00:00", "AUH 44120", 80, 3.0, 240, "A1"],
        ["2026-03-06 10:00:00", "AUH 44120", -10, None, None, "A2"]]
V2_KEYS = {"fingerprint", "transaction_kind", "cost_derived", "unit_price_derived"}


def xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(MAPPING.values()))
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_import(flag, insert_v2_result=None, sha_hit=None):
    create = MagicMock(return_value=77)
    insert = MagicMock(side_effect=lambda transactions, batch_size=100, bearer_token=None: (len(transactions), []))
    insert_v2 = MagicMock(side_effect=insert_v2_result or (lambda group, batch_size=100, bearer_token=None: (len(group), 0, [])))
    find = MagicMock(return_value=sha_hit)
    env = {"FUEL_SCHEMA_V2": "1"} if flag else {"FUEL_SCHEMA_V2": ""}
    with patch.dict(os.environ, env), \
         patch("app.fuel.importer.create_fuel_import_record", create), \
         patch("app.fuel.importer.update_fuel_import_record", return_value=True), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", insert), \
         patch("app.fuel.importer.insert_fuel_transactions_ignore_duplicates", insert_v2), \
         patch("app.fuel.importer.find_import_by_sha256", find), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", return_value=set()):
        res = execute_fuel_import(xlsx(ROWS), "s.xlsx", MAPPING, "00000000-0000-0000-0000-000000000001")
    return res, create, insert, insert_v2, find


def test_flag_off_payload_unchanged():
    res, create, insert, insert_v2, find = run_import(False)
    keys = set().union(*(set(t) for call in insert.call_args_list for t in call.kwargs["transactions"]))
    assert not (keys & V2_KEYS), keys & V2_KEYS
    assert "file_sha256" not in create.call_args.kwargs and not insert_v2.called and not find.called
    assert res.rows_imported == 2 and res.file_sha256
    with patch.dict(os.environ, {"FUEL_SCHEMA_V2": ""}), patch("app.fuel.db.requests.post") as post:
        post.return_value = MagicMock(ok=True, json=MagicMock(return_value=[{"id": 1}]))
        fuel_db.create_fuel_import_record("f.xlsx", "", 1, file_sha256="abc")
    assert "file_sha256" not in post.call_args.kwargs["json"]
    print("[PASS] FUEL_SCHEMA_V2 unset: insert and import-record payloads keep exactly the existing keys.")


def test_flag_on_columns_hash_and_conflicts():
    res, create, insert, insert_v2, find = run_import(True)
    sent = [t for call in insert_v2.call_args_list for t in call.args[0]]
    assert all(V2_KEYS <= set(t) for t in sent) and not insert.called
    kinds = sorted(t["transaction_kind"] for t in sent)
    assert kinds == ["purchase", "refund_adjustment"]
    assert create.call_args.kwargs["file_sha256"] == res.file_sha256
    try:
        run_import(True, sha_hit=41)
        raise AssertionError("expected 409")
    except FuelImportError as exc:
        assert exc.status_code == 409 and "#41" in exc.message
    print("[PASS] FUEL_SCHEMA_V2=1: 4 new columns written, file hash stored, identical file -> 409.")


def test_on_conflict_counts_from_returned_rows():
    resp = MagicMock(ok=True, json=MagicMock(return_value=[{"id": 1}]))
    with patch("app.fuel.db.requests.post", return_value=resp) as post:
        inserted, ignored, errors = fuel_db.insert_fuel_transactions_ignore_duplicates([{"a": 1}, {"a": 2}])
    assert (inserted, ignored, errors) == (1, 1, [])
    assert post.call_args.kwargs["params"] == {"on_conflict": "fingerprint"}
    assert "ignore-duplicates" in post.call_args.kwargs["headers"]["Prefer"]
    calls = []
    res, *_ = run_import(True, insert_v2_result=lambda group, batch_size=100, bearer_token=None: (0, len(group), []) if not calls.append(1) and len(calls) == 1 else (len(group), 0, []))
    assert res.rows_duplicate == 1, res.rows_duplicate
    print("[PASS] on_conflict=fingerprint: rows the database skipped are counted as duplicates (from returned rows).")


def test_backfill_dry_run_writes_nothing():
    sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))
    import backfill_fuel_fingerprints as bf
    rows = [{"id": 1, "vehicle_identifier": "A", "transaction_date": "2026-08-01T08:00:00", "litres": 10, "total_cost": 30},
            {"id": 2, "vehicle_identifier": "A", "transaction_date": "2026-08-01T08:00:00", "litres": 10, "total_cost": 30},
            {"id": 3, "vehicle_identifier": "B", "transaction_date": "2026-08-02", "litres": -5, "total_cost": -15, "fingerprint": "x"}]
    patch_fn = MagicMock(return_value=True)
    out = bf.run(apply=False, fetch=lambda: rows, patch=patch_fn, out=lambda *_: None)
    assert patch_fn.call_count == 0 and out["written"] == 0 and out["planned"] == 2 and out["duplicate_groups"] == 1
    out = bf.run(apply=True, fetch=lambda: rows, patch=patch_fn, out=lambda *_: None)
    assert patch_fn.call_count == 2 and out["written"] == 2
    # Dry-run output never prints fingerprint values (they embed plates and invoice numbers)
    printed = []
    secret = [dict(rows[0], vehicle_identifier="DXB-SECRET-77", invoice_number="INV-PRIVATE-9")]
    bf.run(apply=False, fetch=lambda: secret, patch=MagicMock(), out=printed.append)
    text = "\n".join(printed).lower()
    assert "dxbsecret77" not in text and "inv-private-9" not in text and "keeper (plain)" in text, text
    assert patch_fn.call_args_list[0].args[1]["transaction_kind"] == "purchase"
    print("[PASS] Backfill: dry run by default (0 writes); --apply only fills missing fingerprints; never deletes.")


def test_sql_is_review_only():
    sql = io.open(os.path.join(BASE_DIR, "sql", "proposed_fuel_dedup.sql"), encoding="utf-8").read()
    assert "REVIEW ONLY" in sql.splitlines()[1]
    for line in sql.splitlines():
        s = line.strip().upper()
        if s.startswith("--"):
            continue
        assert not s.startswith("DELETE") and "DROP COLUMN" not in s and "DROP CONSTRAINT <" not in s, line
    uidx = sql[sql.index("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS fuel_transactions_fingerprint_uidx"):]
    uidx = uidx[:uidx.index(";")]
    assert "WHERE" not in uidx.upper(), "ON CONFLICT (fingerprint) cannot use a partial index"
    assert sql.index("§5") > sql.index("§3") and "backfill" in " ".join(sql.splitlines()[:10]).lower()
    import app.fuel.importer as imp
    import inspect
    assert "proposed_fuel_dedup" not in inspect.getsource(imp), "the app never executes the SQL"
    print("[PASS] SQL is review-only: destructive statements are commented out; nothing in the app runs it.")


def _bf():
    sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))
    import backfill_fuel_fingerprints as bf
    return bf


def test_backfill_keeper_gets_plain_fingerprint_others_suffixed():
    bf = _bf()
    same = {"vehicle_identifier": "A", "transaction_date": "2026-08-01T08:00:00", "litres": 10, "total_cost": 30}
    rows = [dict(same, id=7, import_id=1), dict(same, id=3, import_id=1), dict(same, id=9, import_id=2),
            {"id": 4, "import_id": 1, "vehicle_identifier": "B", "transaction_date": "2026-08-02", "litres": -5, "total_cost": -15}]
    updates, dups = bf.plan_backfill(rows)
    by_id = {i: v["fingerprint"] for i, v in updates}
    base = by_id[3]
    assert "|dup:" not in base, "lowest id is the keeper"
    assert by_id[7] == f"{base}|dup:7" and by_id[9] == f"{base}|dup:9"
    assert len(set(by_id.values())) == len(by_id), "every stored fingerprint unique -> SQL §5 index can be built"
    assert bf.base_fingerprint(by_id[9]) == base and "|dup:" not in by_id[4]
    assert dups == {base: [3, 7, 9]}
    # A row that already stores the plain fingerprint (written by a FUEL_SCHEMA_V2 import) stays the keeper.
    held = [dict(same, id=3, import_id=1), dict(same, id=20, import_id=5, fingerprint=base)]
    updates, _ = bf.plan_backfill(held)
    assert updates == [(3, {"fingerprint": f"{base}|dup:3", "transaction_kind": "purchase"})]
    # Re-runs never rewrite stored fingerprints (suffixed or plain).
    rerun = [dict(same, id=3, import_id=1, fingerprint=base), dict(same, id=7, import_id=1, fingerprint=f"{base}|dup:7")]
    assert bf.plan_backfill(rerun)[0] == []
    print("[PASS] Backfill: lowest id (or existing holder) keeps the plain fingerprint; others '|dup:<id>'; all unique.")


def test_backfill_patch_counts_only_one_updated_row():
    bf = _bf()
    rows = [{"id": i, "vehicle_identifier": f"V{i}", "transaction_date": "2026-08-01T08:00:00", "litres": 10, "total_cost": 30}
            for i in (1, 2, 3, 4)]
    bodies = {1: (True, [{"id": 1}]), 2: (True, []), 3: (False, None), 4: (True, [{"id": 4}, {"id": 99}])}
    sent = []

    def fake_patch(url, params=None, json=None, headers=None, timeout=None):
        rid = int(params["id"].split(".")[1])
        sent.append(params)
        ok, body = bodies[rid]
        return MagicMock(ok=ok, json=MagicMock(return_value=body))

    with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "offline-test-key"}), \
         patch("requests.patch", side_effect=fake_patch), \
         patch("app.fuel.db.get_supabase_rest_url", return_value="http://offline.invalid/rest"), \
         patch("app.fuel.db.get_headers", return_value={}), patch("app.fuel.db._read_headers", return_value={}):
        out = bf.run(apply=True, fetch=lambda: rows, out=lambda *_: None)
    assert (out["written"], out["failed"]) == (1, 3), out
    assert all(p.get("fingerprint") == "is.null" for p in sent), "never overwrites an existing fingerprint"
    bad_json = MagicMock(ok=True)
    bad_json.json.side_effect = ValueError("not json")
    assert bf.patch_succeeded(bad_json) is False
    print("[PASS] Backfill PATCH counts as written only when OK with exactly one updated row (200 [] -> failed).")


def test_sql_index_validity_and_backup_restore():
    sql = io.open(os.path.join(BASE_DIR, "sql", "proposed_fuel_dedup.sql"), encoding="utf-8").read()
    code = [ln for ln in sql.splitlines() if ln.strip() and not ln.strip().startswith("--")]
    code_text = "\n".join(code).upper()
    # 2. invalid index detection runs (uncommented) for both concurrent indexes, recovery documented
    assert code_text.count("INDISVALID") >= 2
    assert "'FUEL_TRANSACTIONS_FINGERPRINT_UIDX'" in code_text and "'FUEL_IMPORTS_FILE_SHA256_IDX'" in code_text
    assert "-- DROP INDEX CONCURRENTLY IF EXISTS public.fuel_transactions_fingerprint_uidx" in sql
    assert "cannot run inside a transaction block" in sql
    # 1. preview groups by the base fingerprint
    assert "SPLIT_PART(FINGERPRINT, '|DUP:', 1)" in code_text
    # 4. backup + restore exist but are commented; DELETE / DROP TABLE never executable
    for needle in ("CREATE TABLE fuel_backup.fuel_transactions_dedup_backup", "DELETE FROM public.fuel_transactions",
                   "INSERT INTO public.fuel_transactions", "DROP TABLE fuel_backup.fuel_transactions_dedup_backup",
                   "CREATE SCHEMA IF NOT EXISTS fuel_backup", "DROP SCHEMA fuel_backup"):
        assert needle in sql, needle
        assert needle.upper() not in code_text, f"must stay commented out: {needle}"
    assert sql.index("CREATE TABLE fuel_backup.fuel_transactions_dedup_backup") < sql.index("DELETE FROM public.fuel_transactions")
    for forbidden in ("DELETE ", "DROP TABLE", "DROP COLUMN", "DROP SCHEMA", "TRUNCATE", "INSERT INTO", "UPDATE PUBLIC.",
                      "CREATE SCHEMA", "CREATE TABLE", "REVOKE "):
        assert forbidden not in code_text, forbidden
    print("[PASS] SQL: index validity checks + recovery; base-fingerprint preview; backup/restore commented; no DELETE runs.")


def test_sql_backup_in_private_schema():
    sql = io.open(os.path.join(BASE_DIR, "sql", "proposed_fuel_dedup.sql"), encoding="utf-8").read()
    assert "public.fuel_transactions_dedup_backup" not in sql, "backup must never be created in the API-exposed public schema"
    lines = [ln.strip() for ln in sql.splitlines()]
    expected = [
        "-- REVOKE ALL ON SCHEMA fuel_backup FROM PUBLIC, anon, authenticated, service_role;",
        "-- REVOKE ALL ON TABLE fuel_backup.fuel_transactions_dedup_backup FROM PUBLIC, anon, authenticated, service_role;",
        "-- ALTER TABLE fuel_backup.fuel_transactions_dedup_backup ENABLE ROW LEVEL SECURITY;",
        "-- USING fuel_backup.fuel_transactions_dedup_backup b",                      # DELETE uses the backup ids
        "-- SELECT b.* FROM fuel_backup.fuel_transactions_dedup_backup b",            # restore reads the same table
    ]
    for line in expected:
        assert line in lines, line
    order = [sql.index(s) for s in ("CREATE SCHEMA IF NOT EXISTS fuel_backup", "CREATE TABLE fuel_backup.",
                                    "REVOKE ALL ON TABLE fuel_backup.", "ENABLE ROW LEVEL SECURITY",
                                    "DELETE FROM public.fuel_transactions")]
    assert order == sorted(order), "schema -> table -> revoke -> RLS -> delete, in that order"
    assert "table_schema = 'fuel_backup'" in sql and "Exposed schemas" in sql
    print("[PASS] SQL backup: private fuel_backup schema, API roles revoked, RLS on; delete and restore use the same table.")


KEEPER_ORDER = "ORDER BY (strpos(fingerprint, '|dup:') > 0), id)"


def test_sql_keeper_matches_plan_backfill():
    sql = io.open(os.path.join(BASE_DIR, "sql", "proposed_fuel_dedup.sql"), encoding="utf-8").read()
    # Both the preview (executable) and the commented backup selection use the plain-first keeper order.
    assert sql.count(KEEPER_ORDER) == 2, sql.count(KEEPER_ORDER)
    code = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
    assert KEEPER_ORDER in code, "the preview query itself uses the aligned keeper rule"
    assert "ORDER BY id) AS keeper" not in sql, "old lowest-id-only keeper rule removed"

    # Emulate the SQL rule on the values plan_backfill stores, including a FUEL_SCHEMA_V2 row (id 20) that
    # already holds the plain fingerprint while older identical rows (3, 9) are still NULL.
    bf = _bf()
    same = {"vehicle_identifier": "A", "transaction_date": "2026-08-01T08:00:00", "litres": 10, "total_cost": 30}
    rows = [dict(same, id=3, import_id=1), dict(same, id=9, import_id=2), dict(same, id=20, import_id=5)]
    base =bf.plan_backfill([dict(same, id=1)])[0][0][1]["fingerprint"]
    rows[2]["fingerprint"] = base
    updates, _ = bf.plan_backfill(rows)
    stored = {r["id"]: r.get("fingerprint") for r in rows}
    stored.update({i: v["fingerprint"] for i, v in updates})
    sql_keeper = min(stored, key=lambda i: ("|dup:" in stored[i], i))      # = ORDER BY (strpos(..) > 0), id
    plain_holders = [i for i, fp in stored.items() if fp == base]
    assert plain_holders == [20] and sql_keeper == 20, (plain_holders, sql_keeper)
    deleted = [r["id"] for r in rows if r["id"] != sql_keeper and r["import_id"] != 5]
    assert 20 not in deleted and all("|dup:" in stored[i] for i in deleted), "DELETE never removes the plain row"
    # Without a holder, both rules fall back to the lowest id.
    fresh = [dict(same, id=7, import_id=1), dict(same, id=4, import_id=2)]
    stored = {i: v["fingerprint"] for i, v in bf.plan_backfill(fresh)[0]}
    assert min(stored, key=lambda i: ("|dup:" in stored[i], i)) == 4 and "|dup:" not in stored[4]
    print("[PASS] SQL keeper rule = plan_backfill: existing plain-fingerprint row first, else lowest id.")


def test_backfill_requires_service_role_key():
    bf = _bf()
    for apply in (False, True):
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": "  "}), \
             patch("requests.get") as get, patch("requests.patch") as patch_req:
            try:
                bf.run(apply=apply, out=lambda *_: None)
                raise AssertionError("expected refusal without SUPABASE_SERVICE_ROLE_KEY")
            except SystemExit as exc:
                assert "SUPABASE_SERVICE_ROLE_KEY" in str(exc.code)
            assert not get.called and not patch_req.called, "refuses before any request"
    # Injected offline callables need no key.
    with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": ""}):
        out = bf.run(apply=False, fetch=lambda: [], patch=MagicMock(), out=lambda *_: None)
    assert out["rows"] == 0
    print("[PASS] Backfill refuses to run (dry run and --apply) without SUPABASE_SERVICE_ROLE_KEY, before any request.")


def test_backfill_exit_status():
    bf = _bf()
    base = {"rows": 2, "planned": 2, "duplicate_groups": 0}
    cases = [(False, {"written": 0, "failed": 0}, 0), (True, {"written": 2, "failed": 0}, 0),
             (True, {"written": 1, "failed": 1}, 1)]
    for apply, counts, expected in cases:
        fake_run = MagicMock(return_value=dict(base, **counts))
        with patch.object(bf, "run", fake_run), patch("dotenv.load_dotenv"):
            code = bf.main(["--apply"] if apply else [])
        assert code == expected, (apply, counts, code)
        assert fake_run.call_args.kwargs["apply"] is apply
    print("[PASS] Backfill exit status: 0 on success or dry run, 1 when any write failed.")


def test_flag_stays_off():
    assert os.getenv("FUEL_SCHEMA_V2", "").strip() != "1", "FUEL_SCHEMA_V2 must stay off"
    assert fuel_db.schema_v2_enabled() is False
    print("[PASS] FUEL_SCHEMA_V2 is off in this environment.")


TESTS = [
    test_backfill_keeper_gets_plain_fingerprint_others_suffixed,
    test_backfill_patch_counts_only_one_updated_row,
    test_sql_index_validity_and_backup_restore,
    test_sql_backup_in_private_schema,
    test_sql_keeper_matches_plan_backfill,
    test_backfill_requires_service_role_key,
    test_backfill_exit_status,
    test_flag_stays_off,
    test_flag_off_payload_unchanged,
    test_flag_on_columns_hash_and_conflicts,
    test_on_conflict_counts_from_returned_rows,
    test_backfill_dry_run_writes_nothing,
    test_sql_is_review_only,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PHASE 8 - GATED SCHEMA COLUMNS (OFFLINE)") else 0)
