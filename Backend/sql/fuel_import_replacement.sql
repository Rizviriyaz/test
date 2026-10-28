-- =====================================================================================================
-- REVIEW ONLY — not executed by the app or any script.
-- Latest-import-wins replacement and monthly-summary (undated) fuel imports (ATS Fuel Performance Dashboard).
--
-- Nothing here has been run. Run each section manually, in order, only after review, and take a backup /
-- point-in-time-recovery mark first. §0 is read-only diagnostics.
-- Order: §0 diagnostics -> §1 columns -> §2 indexes -> §3 commit function -> §4 grants (only if §0 asks for them)
-- -> §5 verify -> set the backend env var FUEL_IMPORT_REPLACE=1 and redeploy.
-- Until the flag is set the backend never names these columns or calls the function, so §1-§3 alone change
-- nothing for running code. Do NOT set the flag before §3 succeeds: every read would then filter on unknown
-- columns and fail (PostgREST rejects them), and imports could not commit.
--
-- Safety
--   * Additive only: nullable columns with no default (catalog-only ALTERs, no table rewrite, brief lock).
--   * Existing rows keep NULL everywhere, which means: a dated transaction, visible, never superseded. Nothing is
--     backfilled, updated or deleted by this file.
--   * Rows replaced by a newer import are never deleted: fuel_commit_import only sets superseded_by_import_id /
--     superseded_at on them, and import history (fuel_imports) is kept.
--   * Rows of an import that failed before its commit stay staged (hidden) as an audit trail; they are never
--     removed automatically.
-- =====================================================================================================


-- -----------------------------------------------------------------------------------------------------
-- §0  READ-ONLY DIAGNOSTICS
-- -----------------------------------------------------------------------------------------------------
-- The new columns must not already exist with another type
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name IN ('fuel_transactions', 'fuel_imports')
ORDER BY table_name, ordinal_position;

-- Type of the ids (the function below assumes bigint-compatible ids; adjust its signature if they are uuid)
SELECT table_name, data_type FROM information_schema.columns
WHERE table_schema = 'public' AND table_name IN ('fuel_transactions', 'fuel_imports') AND column_name = 'id';

-- Row-level security: the commit runs with the importing user's token (SECURITY INVOKER). If RLS hides other
-- users' rows from that user, a replacement that touches them is refused (nothing changes); review the policies.
SELECT schemaname, tablename, policyname, cmd, roles, qual, with_check
FROM pg_policies WHERE schemaname = 'public' AND tablename IN ('fuel_transactions', 'fuel_imports');
SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('fuel_transactions', 'fuel_imports');

-- Column-level grants: if rows list per-column privileges for 'authenticated', §4 is needed
SELECT grantee, table_name, privilege_type, column_name
FROM information_schema.column_privileges
WHERE table_schema = 'public' AND table_name IN ('fuel_transactions', 'fuel_imports')
  AND grantee IN ('authenticated', 'anon')
ORDER BY table_name, grantee, column_name;

-- Table-level UPDATE on fuel_transactions for 'authenticated' (the commit updates two columns of existing rows)
SELECT grantee, privilege_type FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND table_name = 'fuel_transactions' AND grantee = 'authenticated';


-- -----------------------------------------------------------------------------------------------------
-- §1  COLUMNS (WRITE; additive, nullable)
-- -----------------------------------------------------------------------------------------------------
BEGIN;
ALTER TABLE public.fuel_transactions
    ADD COLUMN IF NOT EXISTS date_basis              text,
    ADD COLUMN IF NOT EXISTS superseded_by_import_id bigint REFERENCES public.fuel_imports(id),
    ADD COLUMN IF NOT EXISTS superseded_at           timestamptz,
    ADD COLUMN IF NOT EXISTS staged                  boolean;

-- NULL passes a CHECK, so existing rows are unaffected
ALTER TABLE public.fuel_transactions DROP CONSTRAINT IF EXISTS fuel_transactions_date_basis_check;
ALTER TABLE public.fuel_transactions ADD CONSTRAINT fuel_transactions_date_basis_check
    CHECK (date_basis IS NULL OR date_basis IN ('transaction', 'reporting_month_end')) NOT VALID;
ALTER TABLE public.fuel_transactions VALIDATE CONSTRAINT fuel_transactions_date_basis_check;

ALTER TABLE public.fuel_imports
    ADD COLUMN IF NOT EXISTS import_mode     text,
    ADD COLUMN IF NOT EXISTS reporting_month date,
    ADD COLUMN IF NOT EXISTS replace_scope   jsonb,
    ADD COLUMN IF NOT EXISTS rows_replaced   integer,
    ADD COLUMN IF NOT EXISTS rows_unchanged  integer;
ALTER TABLE public.fuel_imports DROP CONSTRAINT IF EXISTS fuel_imports_import_mode_check;
ALTER TABLE public.fuel_imports ADD CONSTRAINT fuel_imports_import_mode_check
    CHECK (import_mode IS NULL OR import_mode IN ('dated', 'monthly_summary')) NOT VALID;
ALTER TABLE public.fuel_imports VALIDATE CONSTRAINT fuel_imports_import_mode_check;

COMMENT ON COLUMN public.fuel_transactions.date_basis IS
    'reporting_month_end: monthly-summary row dated on the last day of its reporting month (not a refuel date); '
    'transaction or NULL: dated transaction';
COMMENT ON COLUMN public.fuel_transactions.superseded_by_import_id IS
    'Newer import that replaced this row (latest import wins); NULL = visible. The row is kept, never deleted';
COMMENT ON COLUMN public.fuel_transactions.staged IS
    'true = written by an import that has not been committed (hidden); NULL/false = visible';
COMMENT ON COLUMN public.fuel_imports.reporting_month IS 'First day of the reporting month of a monthly summary';
COMMENT ON COLUMN public.fuel_imports.replace_scope IS 'Period the import replaces for the vehicles in its file';
COMMIT;


-- -----------------------------------------------------------------------------------------------------
-- §2  INDEXES (each CREATE INDEX CONCURRENTLY must run as a single statement, outside a transaction block;
--     some SQL editors, including the Supabase SQL editor when several statements are selected, wrap them in one.
--     A failed or cancelled build leaves an INVALID index: run the validity check after each.)
-- -----------------------------------------------------------------------------------------------------
CREATE INDEX CONCURRENTLY IF NOT EXISTS fuel_transactions_superseded_by_idx
    ON public.fuel_transactions (superseded_by_import_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS fuel_transactions_staged_import_idx
    ON public.fuel_transactions (import_id) WHERE staged IS TRUE;

-- Validity check: expect two rows with indisvalid = true. If not: DROP INDEX CONCURRENTLY IF EXISTS <name>; re-create.
SELECT c.relname AS index_name, i.indisvalid, i.indisready
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname IN ('fuel_transactions_superseded_by_idx', 'fuel_transactions_staged_import_idx');


-- -----------------------------------------------------------------------------------------------------
-- §3  COMMIT FUNCTION (called by the backend through PostgREST: POST /rest/v1/rpc/fuel_commit_import)
--     One transaction: either every check passes and both updates apply, or an error is raised and nothing
--     changes. A transaction-scoped advisory lock serializes commits across backend instances.
-- -----------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fuel_commit_import(
    p_import_id      bigint,
    p_supersede_ids  bigint[],
    p_expected_rows  integer
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_ids        bigint[] := coalesce(p_supersede_ids, '{}');
    v_visible    integer;
    v_staged     integer;
    v_superseded integer;
    v_committed  integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('public.fuel_commit_import'));

    IF EXISTS (SELECT 1 FROM public.fuel_transactions WHERE id = ANY (v_ids) AND import_id = p_import_id) THEN
        RAISE EXCEPTION 'fuel_commit_import: import % cannot supersede its own rows', p_import_id;
    END IF;

    -- Every row to replace must still be visible (no other import replaced it since the plan was made)
    SELECT count(*) INTO v_visible
    FROM public.fuel_transactions
    WHERE id = ANY (v_ids) AND superseded_by_import_id IS NULL AND staged IS NOT TRUE;
    IF v_visible <> cardinality(ARRAY(SELECT DISTINCT unnest(v_ids))) THEN
        RAISE EXCEPTION 'fuel_commit_import: replacement scope changed (% of % rows still visible)',
            v_visible, cardinality(v_ids);
    END IF;

    -- Exactly the rows the import wrote must be staged (a partial import is never made visible)
    SELECT count(*) INTO v_staged
    FROM public.fuel_transactions WHERE import_id = p_import_id AND staged IS TRUE;
    IF v_staged <> p_expected_rows THEN
        RAISE EXCEPTION 'fuel_commit_import: % staged row(s) for import %, expected %',
            v_staged, p_import_id, p_expected_rows;
    END IF;

    UPDATE public.fuel_transactions
    SET superseded_by_import_id = p_import_id, superseded_at = now()
    WHERE id = ANY (v_ids) AND superseded_by_import_id IS NULL AND staged IS NOT TRUE;
    GET DIAGNOSTICS v_superseded = ROW_COUNT;

    UPDATE public.fuel_transactions SET staged = false WHERE import_id = p_import_id AND staged IS TRUE;
    GET DIAGNOSTICS v_committed = ROW_COUNT;

    -- Row-level security can make UPDATE skip rows the SELECTs above counted: then roll everything back
    IF v_superseded <> v_visible OR v_committed <> v_staged THEN
        RAISE EXCEPTION 'fuel_commit_import: updated %/% superseded and %/% staged rows; nothing changed',
            v_superseded, v_visible, v_committed, v_staged;
    END IF;

    RETURN jsonb_build_object('superseded', v_superseded, 'committed', v_committed);
END;
$$;

REVOKE ALL ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) TO authenticated;

-- Make PostgREST see the new columns and function immediately
NOTIFY pgrst, 'reload schema';


-- -----------------------------------------------------------------------------------------------------
-- §4  GRANTS — only if §0 showed column-level grants for 'authenticated'
-- -----------------------------------------------------------------------------------------------------
-- GRANT SELECT (date_basis, superseded_by_import_id, superseded_at, staged),
--       INSERT (date_basis, staged),
--       UPDATE (superseded_by_import_id, superseded_at, staged)
--     ON public.fuel_transactions TO authenticated;
-- GRANT SELECT (import_mode, reporting_month, replace_scope, rows_replaced, rows_unchanged),
--       INSERT (import_mode, reporting_month, replace_scope),
--       UPDATE (rows_replaced, rows_unchanged)
--     ON public.fuel_imports TO authenticated;


-- -----------------------------------------------------------------------------------------------------
-- §5  VERIFY (read-only)
-- -----------------------------------------------------------------------------------------------------
-- Expect every existing row to show NULLs (nothing was backfilled) and therefore stay visible
SELECT count(*) AS rows, count(date_basis) AS with_basis, count(superseded_by_import_id) AS superseded,
       count(*) FILTER (WHERE staged IS TRUE) AS staged
FROM public.fuel_transactions;

SELECT proname, prosecdef FROM pg_proc WHERE proname = 'fuel_commit_import';   -- prosecdef must be false (invoker)

-- After imports with the flag on: rows replaced per import, and rows left hidden by uncommitted imports
-- SELECT superseded_by_import_id, count(*) FROM public.fuel_transactions
--   WHERE superseded_by_import_id IS NOT NULL GROUP BY 1 ORDER BY 1;
-- SELECT import_id, count(*) FROM public.fuel_transactions WHERE staged IS TRUE GROUP BY 1 ORDER BY 1;


-- -----------------------------------------------------------------------------------------------------
-- §6  ROLLBACK — unset FUEL_IMPORT_REPLACE and redeploy FIRST
-- -----------------------------------------------------------------------------------------------------
-- WARNING: once any import has superseded rows, dropping the columns makes the old and the new rows visible
-- together again (double counting), and staged rows of uncommitted imports become visible too. Before 6b,
-- decide per import: keep the columns (the backend ignores them while the flag is unset, but then also shows the
-- superseded and staged rows), or move superseded/staged rows out of the table in a reviewed, backed-up step.
-- This file does not do that for you.
--
-- 6a. Review what the flag created
-- SELECT count(*) FILTER (WHERE superseded_by_import_id IS NOT NULL) AS superseded,
--        count(*) FILTER (WHERE staged IS TRUE) AS staged,
--        count(*) FILTER (WHERE date_basis = 'reporting_month_end') AS monthly_summary
-- FROM public.fuel_transactions;
--
-- 6b. Remove the schema additions (only after the review above)
-- DROP FUNCTION IF EXISTS public.fuel_commit_import(bigint, bigint[], integer);
-- DROP INDEX CONCURRENTLY IF EXISTS public.fuel_transactions_superseded_by_idx;
-- DROP INDEX CONCURRENTLY IF EXISTS public.fuel_transactions_staged_import_idx;
-- BEGIN;
-- ALTER TABLE public.fuel_transactions DROP CONSTRAINT IF EXISTS fuel_transactions_date_basis_check;
-- ALTER TABLE public.fuel_transactions
--     DROP COLUMN IF EXISTS date_basis,
--     DROP COLUMN IF EXISTS superseded_by_import_id,
--     DROP COLUMN IF EXISTS superseded_at,
--     DROP COLUMN IF EXISTS staged;
-- ALTER TABLE public.fuel_imports DROP CONSTRAINT IF EXISTS fuel_imports_import_mode_check;
-- ALTER TABLE public.fuel_imports
--     DROP COLUMN IF EXISTS import_mode,
--     DROP COLUMN IF EXISTS reporting_month,
--     DROP COLUMN IF EXISTS replace_scope,
--     DROP COLUMN IF EXISTS rows_replaced,
--     DROP COLUMN IF EXISTS rows_unchanged;
-- COMMIT;
-- NOTIFY pgrst, 'reload schema';
