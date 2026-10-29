-- =====================================================================================================
-- 01  PREFLIGHT CHECKS (read-only). Run first. Changes nothing.
-- Monthly-summary table (FUEL_MONTHLY_SUMMARY_TABLE) for the ATS Fuel Performance Dashboard.
--
-- Raises an error naming every missing prerequisite; otherwise returns one row 'PREFLIGHT OK'.
-- Prerequisites are the objects of Backend/sql/fuel_import_replacement.sql §1-§2 and
-- Backend/sql/fuel_commit_import_definer.sql (the diagnostics showed them installed), plus no import running.
-- =====================================================================================================
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;

DO $$
DECLARE
    v_missing text[] := '{}';
    v_col     record;
BEGIN
    IF to_regclass('public.fuel_transactions') IS NULL THEN v_missing := v_missing || 'table public.fuel_transactions'; END IF;
    IF to_regclass('public.fuel_imports') IS NULL THEN v_missing := v_missing || 'table public.fuel_imports'; END IF;
    IF to_regclass('public.profiles') IS NULL THEN v_missing := v_missing || 'table public.profiles'; END IF;

    FOR v_col IN
        SELECT e.tbl, e.col
        FROM (VALUES
            ('fuel_transactions', 'id'), ('fuel_transactions', 'import_id'), ('fuel_transactions', 'transaction_date'),
            ('fuel_transactions', 'vehicle_identifier'), ('fuel_transactions', 'fuel_type'), ('fuel_transactions', 'litres'),
            ('fuel_transactions', 'unit_price'), ('fuel_transactions', 'total_cost'), ('fuel_transactions', 'odometer_km'),
            ('fuel_transactions', 'station_name'), ('fuel_transactions', 'invoice_number'), ('fuel_transactions', 'card_number'),
            ('fuel_transactions', 'driver_name'), ('fuel_transactions', 'reference_number'), ('fuel_transactions', 'notes'),
            ('fuel_transactions', 'source_row_number'), ('fuel_transactions', 'date_basis'), ('fuel_transactions', 'staged'),
            ('fuel_transactions', 'superseded_by_import_id'), ('fuel_transactions', 'superseded_at'),
            ('fuel_imports', 'id'), ('fuel_imports', 'status'), ('fuel_imports', 'imported_by'), ('fuel_imports', 'created_at'),
            ('fuel_imports', 'import_mode'), ('fuel_imports', 'reporting_month'), ('fuel_imports', 'replace_scope'),
            ('profiles', 'id'), ('profiles', 'role')) AS e(tbl, col)
        LEFT JOIN information_schema.columns c
               ON c.table_schema = 'public' AND c.table_name = e.tbl AND c.column_name = e.col
        WHERE c.column_name IS NULL
    LOOP
        v_missing := v_missing || format('column public.%s.%s', v_col.tbl, v_col.col);
    END LOOP;

    IF to_regprocedure('public.fuel_commit_import(bigint,bigint[],integer)') IS NULL THEN
        v_missing := v_missing || 'function public.fuel_commit_import(bigint,bigint[],integer)';
    ELSIF NOT (SELECT prosecdef FROM pg_proc WHERE oid = to_regprocedure('public.fuel_commit_import(bigint,bigint[],integer)')) THEN
        v_missing := v_missing || 'fuel_commit_import must be SECURITY DEFINER (fuel_commit_import_definer.sql)';
    END IF;
    IF to_regprocedure('auth.uid()') IS NULL THEN v_missing := v_missing || 'function auth.uid()'; END IF;
    IF (SELECT format_type(atttypid, atttypmod) FROM pg_attribute
        WHERE attrelid = to_regclass('public.fuel_imports') AND attname = 'imported_by' AND NOT attisdropped) IS DISTINCT FROM 'uuid' THEN
        v_missing := v_missing || 'fuel_imports.imported_by must be uuid (compared with auth.uid())';
    END IF;
    IF EXISTS (SELECT 1 FROM public.fuel_imports WHERE status = 'processing' AND created_at > now() - interval '15 minutes') THEN
        v_missing := v_missing || 'an import is running (status processing): wait for it, and freeze imports';
    END IF;

    IF cardinality(v_missing) > 0 THEN
        RAISE EXCEPTION 'PREFLIGHT FAILED: %', array_to_string(v_missing, '; ');
    END IF;
END $$;

SELECT 'PREFLIGHT OK' AS result,
       to_regclass('public.fuel_monthly_summaries') IS NOT NULL AS summary_table_already_exists,
       to_regprocedure('public.fuel_commit_import_v2(bigint,bigint[],bigint[],integer)') IS NOT NULL AS commit_v2_already_exists,
       current_setting('TimeZone') AS session_timezone;
