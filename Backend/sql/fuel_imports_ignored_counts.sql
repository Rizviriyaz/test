-- =====================================================================================================
-- REVIEW ONLY — not executed by the app or any script.
-- Adds the skipped-row counters to fuel_imports so import history can show ignored and non-fuel rows.
--
-- Independent of FUEL_SCHEMA_V2 / proposed_fuel_dedup.sql; it can be applied before or after it.
-- Order: §0 diagnostics -> §1 columns -> §2 verify -> set the backend env var FUEL_IMPORT_IGNORED_COUNTS=1 and
-- redeploy. Until the flag is set the backend never sends these columns, so applying §1 alone changes nothing
-- for running code. Do NOT set the flag before §1 succeeds: PostgREST rejects unknown columns, and the import
-- record could then not be created (the import would fail before writing any transaction).
--
-- Safety: both columns are nullable with no default, so ADD COLUMN is a catalog-only change (no table rewrite,
-- brief lock only). Existing records keep NULL, meaning "not recorded" — the app and UI treat NULL as unknown,
-- never as 0. Nothing is backfilled: the counts of past imports cannot be derived from stored data (ignored rows
-- were never stored). No statement deletes or updates data.
-- =====================================================================================================


-- -----------------------------------------------------------------------------------------------------
-- §0  READ-ONLY DIAGNOSTICS
-- -----------------------------------------------------------------------------------------------------
-- Columns must not already exist with another type
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'fuel_imports'
ORDER BY ordinal_position;

-- Column-level grants: if rows here list per-column INSERT/UPDATE for 'authenticated', the new columns need
-- the same grants (§1b), because the backend writes fuel_imports with the user's token.
SELECT grantee, privilege_type, column_name
FROM information_schema.column_privileges
WHERE table_schema = 'public' AND table_name = 'fuel_imports' AND grantee IN ('authenticated', 'anon')
ORDER BY grantee, column_name;


-- -----------------------------------------------------------------------------------------------------
-- §1  COLUMNS (WRITE)
-- -----------------------------------------------------------------------------------------------------
BEGIN;
ALTER TABLE public.fuel_imports ADD COLUMN IF NOT EXISTS rows_ignored integer;
ALTER TABLE public.fuel_imports ADD COLUMN IF NOT EXISTS rows_non_fuel integer;
-- NULL passes a CHECK, so existing rows are unaffected
ALTER TABLE public.fuel_imports DROP CONSTRAINT IF EXISTS fuel_imports_ignored_counts_check;
ALTER TABLE public.fuel_imports ADD CONSTRAINT fuel_imports_ignored_counts_check
    CHECK (rows_ignored IS NULL OR rows_ignored >= 0)
    NOT VALID;
ALTER TABLE public.fuel_imports VALIDATE CONSTRAINT fuel_imports_ignored_counts_check;
ALTER TABLE public.fuel_imports DROP CONSTRAINT IF EXISTS fuel_imports_non_fuel_count_check;
ALTER TABLE public.fuel_imports ADD CONSTRAINT fuel_imports_non_fuel_count_check
    CHECK (rows_non_fuel IS NULL OR (rows_non_fuel >= 0 AND (rows_ignored IS NULL OR rows_non_fuel <= rows_ignored)))
    NOT VALID;
ALTER TABLE public.fuel_imports VALIDATE CONSTRAINT fuel_imports_non_fuel_count_check;
COMMENT ON COLUMN public.fuel_imports.rows_ignored IS
    'Rows skipped as blank, summary/total or non-fuel; NULL = not recorded (imports before this column)';
COMMENT ON COLUMN public.fuel_imports.rows_non_fuel IS
    'Part of rows_ignored: statement payments, charges or fees without litres; NULL = not recorded';
COMMIT;

-- §1b  Only if §0 showed column-level grants for 'authenticated':
-- GRANT INSERT (rows_ignored, rows_non_fuel), UPDATE (rows_ignored, rows_non_fuel), SELECT (rows_ignored, rows_non_fuel)
--     ON public.fuel_imports TO authenticated;

-- Make PostgREST see the new columns immediately (otherwise it picks them up on its next schema reload)
NOTIFY pgrst, 'reload schema';


-- -----------------------------------------------------------------------------------------------------
-- §2  VERIFY (read-only)
-- -----------------------------------------------------------------------------------------------------
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'fuel_imports' AND column_name IN ('rows_ignored', 'rows_non_fuel');

-- Expect every existing record to show NULLs (nothing was backfilled)
SELECT count(*) AS records, count(rows_ignored) AS with_ignored, count(rows_non_fuel) AS with_non_fuel
FROM public.fuel_imports;


-- -----------------------------------------------------------------------------------------------------
-- ROLLBACK (only if needed; unset FUEL_IMPORT_IGNORED_COUNTS and redeploy FIRST, then run)
-- -----------------------------------------------------------------------------------------------------
-- ALTER TABLE public.fuel_imports DROP CONSTRAINT IF EXISTS fuel_imports_non_fuel_count_check;
-- ALTER TABLE public.fuel_imports DROP CONSTRAINT IF EXISTS fuel_imports_ignored_counts_check;
-- ALTER TABLE public.fuel_imports DROP COLUMN IF EXISTS rows_non_fuel;
-- ALTER TABLE public.fuel_imports DROP COLUMN IF EXISTS rows_ignored;
-- NOTIFY pgrst, 'reload schema';
