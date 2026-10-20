-- =====================================================================================================
-- REVIEW ONLY — not executed by the app or any script.
-- Proposed schema additions for fuel de-duplication and value classification (ATS Fuel Dashboard, Phase 8).
--
-- Nothing here has been run. Run each section manually, in order, only after review, in a maintenance
-- window, and take a backup / point-in-time-recovery mark first. Section 0 is read-only diagnostics.
-- Order: §0 diagnostics -> §1, §2 columns -> backfill (scripts/backfill_fuel_fingerprints.py, dry run first,
-- then --apply) -> §3 review and (only if you decide) clean up duplicates -> §5 unique index -> set the
-- backend env var FUEL_SCHEMA_V2=1. §5 fails while duplicate fingerprints remain, and the app's
-- on_conflict=fingerprint inserts fail until §5 exists, so do not set the flag before §5 succeeds.
-- No statement in this file deletes data unless you uncomment it (section 3 DELETE is commented out).
-- =====================================================================================================


-- -----------------------------------------------------------------------------------------------------
-- §0  READ-ONLY DIAGNOSTICS (safe; run first and share the results)
-- -----------------------------------------------------------------------------------------------------
-- Column types and the session time zone decide how transaction_date compares (see parse_tx_wallclock).
SELECT column_name, data_type, udt_name
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name IN ('fuel_transactions', 'fuel_imports')
ORDER BY table_name, ordinal_position;

SHOW TimeZone;

SELECT pg_typeof(transaction_date) AS transaction_date_type, transaction_date
FROM public.fuel_transactions
ORDER BY id
LIMIT 5;

-- CHECK constraints (a positive-litres check would reject refund adjustments)
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.fuel_transactions'::regclass AND contype = 'c';

-- Existing duplicate groups across imports (same values as the app's fingerprint inputs)
SELECT lower(regexp_replace(vehicle_identifier, '[- ]', '', 'g')) AS vehicle_norm,
       transaction_date, round(litres::numeric, 2) AS litres, round(total_cost::numeric, 2) AS total_cost,
       lower(coalesce(invoice_number, '')) AS invoice, round(odometer_km::numeric, 0) AS odometer,
       count(*) AS rows, count(DISTINCT import_id) AS imports, array_agg(id ORDER BY id) AS ids
FROM public.fuel_transactions
GROUP BY 1, 2, 3, 4, 5, 6
HAVING count(DISTINCT import_id) > 1
ORDER BY rows DESC
LIMIT 200;


-- -----------------------------------------------------------------------------------------------------
-- §1  NEW TRANSACTION COLUMNS (additive, nullable; existing rows unchanged)
-- -----------------------------------------------------------------------------------------------------
BEGIN;
ALTER TABLE public.fuel_transactions
    ADD COLUMN IF NOT EXISTS fingerprint        text,
    ADD COLUMN IF NOT EXISTS transaction_kind   text,
    ADD COLUMN IF NOT EXISTS cost_derived       boolean,
    ADD COLUMN IF NOT EXISTS unit_price_derived boolean;

ALTER TABLE public.fuel_transactions
    DROP CONSTRAINT IF EXISTS fuel_transactions_transaction_kind_check;
ALTER TABLE public.fuel_transactions
    ADD CONSTRAINT fuel_transactions_transaction_kind_check
    CHECK (transaction_kind IS NULL OR transaction_kind IN ('purchase', 'refund_adjustment'));
COMMIT;


-- -----------------------------------------------------------------------------------------------------
-- §2  IMPORT FILE HASH (identical re-uploads are rejected by the app with HTTP 409 when FUEL_SCHEMA_V2=1)
-- -----------------------------------------------------------------------------------------------------
BEGIN;
ALTER TABLE public.fuel_imports ADD COLUMN IF NOT EXISTS file_sha256 text;
COMMIT;

-- CREATE INDEX CONCURRENTLY rules (apply to §2 and §5):
--   * It cannot run inside a transaction block. Run it as a single statement on its own; some SQL editors
--     (including the Supabase SQL editor when several statements are selected) wrap statements in one.
--   * If it fails or is cancelled it leaves an INVALID index behind, and a rerun with IF NOT EXISTS then
--     silently does nothing. Always run the validity check below afterwards.
CREATE INDEX CONCURRENTLY IF NOT EXISTS fuel_imports_file_sha256_idx ON public.fuel_imports (file_sha256);

-- Validity check: expect exactly one row with indisvalid = true.
SELECT c.relname AS index_name, i.indisvalid, i.indisready
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'fuel_imports_file_sha256_idx';
-- If indisvalid is false (or the row is missing after an error): drop it, fix the cause, then re-create:
-- DROP INDEX CONCURRENTLY IF EXISTS public.fuel_imports_file_sha256_idx;


-- -----------------------------------------------------------------------------------------------------
-- §3  DUPLICATE CLEAN-UP PREVIEW (run only after the backfill has filled `fingerprint`)
--     The backfill gives the plain fingerprint to one keeper per group and '<fingerprint>|dup:<id>' to
--     the other identical rows, so every stored value is unique and §5 can be built WITHOUT deleting
--     anything. Groups are therefore formed on the BASE fingerprint (suffix removed).
--     Keeper = the same row plan_backfill picks: the row holding the plain fingerprint (no '|dup:'
--     suffix), else the lowest id. So the optional DELETE never removes the plain-fingerprint row that
--     ON CONFLICT (fingerprint) relies on.
--     Suspected duplicates = rows of a group from a different import than the keeper, matching the app's
--     rule. Same-import repeats are only listed. Deleting is optional (below).
-- -----------------------------------------------------------------------------------------------------
WITH ranked AS (
    SELECT id, import_id, fingerprint,
           split_part(fingerprint, '|dup:', 1) AS base_fingerprint,
           first_value(id)        OVER keeper_order AS keeper_id,
           first_value(import_id) OVER keeper_order AS keeper_import_id
    FROM public.fuel_transactions
    WHERE fingerprint IS NOT NULL
    WINDOW keeper_order AS (PARTITION BY split_part(fingerprint, '|dup:', 1)
                            ORDER BY (strpos(fingerprint, '|dup:') > 0), id)
)
SELECT r.*, t.vehicle_identifier, t.transaction_date, t.litres, t.total_cost
FROM ranked r
JOIN public.fuel_transactions t USING (id)
WHERE r.id <> r.keeper_id AND r.import_id IS DISTINCT FROM r.keeper_import_id
ORDER BY r.base_fingerprint, r.id;

-- All groups of identical rows (same-import repeats included), for review:
SELECT split_part(fingerprint, '|dup:', 1) AS base_fingerprint, count(*) AS rows,
       count(DISTINCT import_id) AS imports, array_agg(id ORDER BY id) AS ids
FROM public.fuel_transactions
WHERE fingerprint IS NOT NULL
GROUP BY 1
HAVING count(*) > 1;

-- Must return 0 rows before §5 (each stored fingerprint unique; the backfill guarantees this):
SELECT fingerprint, count(*) FROM public.fuel_transactions
WHERE fingerprint IS NOT NULL GROUP BY fingerprint HAVING count(*) > 1;

-- -----------------------------------------------------------------------------------------------------
-- OPTIONAL, DESTRUCTIVE — never run by the app or any script. Every line is commented out.
-- Only after reviewing the preview above, with a fresh backup / PITR mark. Steps, in ONE session:
--   1. Copy the rows to delete into a backup table and check its count equals the preview count.
--   2. Delete exactly the backed-up ids, check the deleted count, then COMMIT (or ROLLBACK).
--   3. Keep the backup table until the dashboard has been verified; the restore is in §6.
-- The backup holds card numbers, driver names and invoices, so it goes in the private schema fuel_backup,
-- never in public. Supabase grants new public tables to anon/authenticated, and PostgREST serves them to
-- anyone holding the anon key (it ships in the frontend). Do NOT add fuel_backup to the project's
-- API "Exposed schemas". Access is revoked from every API role. RLS with no policies is a second barrier.
-- -----------------------------------------------------------------------------------------------------
-- BEGIN;
-- CREATE SCHEMA IF NOT EXISTS fuel_backup;
-- REVOKE ALL ON SCHEMA fuel_backup FROM PUBLIC, anon, authenticated, service_role;
-- CREATE TABLE fuel_backup.fuel_transactions_dedup_backup AS   -- same columns/order as fuel_transactions
-- SELECT t.*
-- FROM public.fuel_transactions t
-- JOIN (
--     SELECT id FROM (
--         SELECT id, import_id,
--                first_value(id)        OVER keeper_order AS keeper_id,
--                first_value(import_id) OVER keeper_order AS keeper_import_id
--         FROM public.fuel_transactions WHERE fingerprint IS NOT NULL
--         WINDOW keeper_order AS (PARTITION BY split_part(fingerprint, '|dup:', 1)
--                                 ORDER BY (strpos(fingerprint, '|dup:') > 0), id)
--     ) x
--     WHERE x.id <> x.keeper_id AND x.import_id IS DISTINCT FROM x.keeper_import_id
-- ) d ON d.id = t.id;
-- REVOKE ALL ON TABLE fuel_backup.fuel_transactions_dedup_backup FROM PUBLIC, anon, authenticated, service_role;
-- ALTER TABLE fuel_backup.fuel_transactions_dedup_backup ENABLE ROW LEVEL SECURITY;
-- -- Must return 0 rows (no API role can read the backup):
-- SELECT grantee, privilege_type FROM information_schema.role_table_grants
-- WHERE table_schema = 'fuel_backup' AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role');
-- SELECT count(*) FROM fuel_backup.fuel_transactions_dedup_backup;   -- must equal the preview row count
-- DELETE FROM public.fuel_transactions t
-- USING fuel_backup.fuel_transactions_dedup_backup b
-- WHERE t.id = b.id;                                           -- reported count must equal the backup count
-- COMMIT;                                                      -- or ROLLBACK; if any count differs


-- -----------------------------------------------------------------------------------------------------
-- §4  REFUND ADJUSTMENTS: only if §0 shows a CHECK (litres > 0). Commented out on purpose.
-- -----------------------------------------------------------------------------------------------------
-- ALTER TABLE public.fuel_transactions DROP CONSTRAINT <constraint_name_from_section_0>;
-- ALTER TABLE public.fuel_transactions ADD CONSTRAINT fuel_transactions_litres_nonzero CHECK (litres <> 0);


-- -----------------------------------------------------------------------------------------------------
-- §5  UNIQUE FINGERPRINT INDEX (needed for on_conflict=fingerprint inserts; fails if duplicates remain)
--     Deliberately NOT a partial index: PostgREST sends ON CONFLICT (fingerprint) without a WHERE
--     predicate, which PostgreSQL cannot match to a partial index (error 42P10). NULL fingerprints stay
--     allowed because NULLs are distinct in a unique index. CONCURRENTLY cannot run inside a transaction
--     (run it as a single statement; see the rules in §2).
--     Before: the "must return 0 rows" query in §3 returns nothing, and a fresh backfill dry run reports
--     0 rows still needing a fingerprint (imports during the backfill get none while the flag is off).
-- -----------------------------------------------------------------------------------------------------
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS fuel_transactions_fingerprint_uidx
    ON public.fuel_transactions (fingerprint);

-- Validity check: expect exactly one row with indisvalid = true. Do NOT set FUEL_SCHEMA_V2=1 otherwise:
-- an invalid index is not used for ON CONFLICT, so every flagged import would fail (error 42P10).
SELECT c.relname AS index_name, i.indisvalid, i.indisready
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'fuel_transactions_fingerprint_uidx';
-- Recovery if indisvalid is false (e.g. "could not create unique index ... is duplicated", or cancelled):
--   1. DROP INDEX CONCURRENTLY IF EXISTS public.fuel_transactions_fingerprint_uidx;   (single statement)
--   2. Fix the cause (re-run the §3 uniqueness query and the backfill dry run).
--   3. Re-run the CREATE above, then this validity check again.


-- -----------------------------------------------------------------------------------------------------
-- §6  ROLLBACK (unset FUEL_SCHEMA_V2 in the backend first)
-- -----------------------------------------------------------------------------------------------------
-- 6a. Restore rows removed by the optional §3 DELETE (only if it was run), with their original ids.
--     The backup table has exactly the columns of fuel_transactions, in the same order (CREATE TABLE AS
--     SELECT t.*). If §6b already ran, restore BEFORE 6b instead, or the column lists no longer match.
--     If id is a GENERATED ALWAYS identity column, add OVERRIDING SYSTEM VALUE after the table name.
-- BEGIN;
-- INSERT INTO public.fuel_transactions
-- SELECT b.* FROM fuel_backup.fuel_transactions_dedup_backup b
-- WHERE NOT EXISTS (SELECT 1 FROM public.fuel_transactions t WHERE t.id = b.id);
-- -- Note: the keeper (plain fingerprint) was never deleted, so restored rows bring back the duplicate
-- -- fingerprints only as '|dup:<id>' values and §5 stays valid.
-- -- Check: inserted count = backup count. Then COMMIT (or ROLLBACK). Keep the backup table until verified;
-- -- only afterwards: DROP TABLE fuel_backup.fuel_transactions_dedup_backup;
-- --                  DROP SCHEMA fuel_backup;   -- fails (safely) if anything else is still in it
-- COMMIT;
--
-- 6b. Remove the schema additions.
-- DROP INDEX CONCURRENTLY IF EXISTS public.fuel_transactions_fingerprint_uidx;
-- DROP INDEX CONCURRENTLY IF EXISTS public.fuel_imports_file_sha256_idx;
-- BEGIN;
-- ALTER TABLE public.fuel_transactions DROP CONSTRAINT IF EXISTS fuel_transactions_transaction_kind_check;
-- ALTER TABLE public.fuel_transactions
--     DROP COLUMN IF EXISTS fingerprint,
--     DROP COLUMN IF EXISTS transaction_kind,
--     DROP COLUMN IF EXISTS cost_derived,
--     DROP COLUMN IF EXISTS unit_price_derived;
-- ALTER TABLE public.fuel_imports DROP COLUMN IF EXISTS file_sha256;
-- COMMIT;
