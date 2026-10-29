-- =====================================================================================================
-- 90  OPTIONAL — DESTRUCTIVE — RESET THE FUEL TEST DATA. Not part of the normal migration.
--
-- Deletes every row of fuel_monthly_summaries, fuel_transactions, fuel_migration_audit and fuel_imports, after
-- copying them into schema fuel_backup (tables suffixed with the run time). Tables, columns, functions, policies,
-- grants, users (auth.users, profiles) and every other table are kept. Use it only because the fuel data is
-- disposable test data and you will re-import the statements through the app afterwards.
-- Refuses to run unless the confirmation below is set in the SAME run, and if any other table references the fuel
-- tables (it would otherwise lose rows or fail half-way). One transaction: it completes fully or changes nothing.
--
-- To run: keep the SET line below, run the whole script in a NEW SQL editor tab, after 01-03.
-- =====================================================================================================
SET fuel.reset_confirm = 'DELETE ALL FUEL TEST DATA';

BEGIN;
DO $$
DECLARE
    v_suffix text := to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYYMMDD_HH24MISS');
    v_tables text[] := ARRAY['fuel_monthly_summaries', 'fuel_transactions', 'fuel_migration_audit', 'fuel_imports'];
    v_table  text;
    v_refs   text;
BEGIN
    IF current_setting('fuel.reset_confirm', true) IS DISTINCT FROM 'DELETE ALL FUEL TEST DATA' THEN
        RAISE EXCEPTION 'Reset refused: the confirmation line was not run with the script.';
    END IF;
    SELECT string_agg(DISTINCT conrelid::regclass::text, ', ') INTO v_refs
    FROM pg_constraint
    WHERE contype = 'f'
      AND confrelid IN (SELECT coalesce(to_regclass('public.' || x)::oid, 0::oid) FROM unnest(v_tables) AS x)
      AND conrelid NOT IN (SELECT coalesce(to_regclass('public.' || x)::oid, 0::oid) FROM unnest(v_tables) AS x);
    IF v_refs IS NOT NULL THEN
        RAISE EXCEPTION 'Reset refused: other tables reference the fuel tables (%); review them first.', v_refs;
    END IF;
    IF EXISTS (SELECT 1 FROM public.fuel_imports WHERE status = 'processing' AND created_at > now() - interval '15 minutes') THEN
        RAISE EXCEPTION 'Reset refused: an import is running.';
    END IF;

    CREATE SCHEMA IF NOT EXISTS fuel_backup;
    REVOKE ALL ON SCHEMA fuel_backup FROM anon, authenticated;
    FOREACH v_table IN ARRAY v_tables LOOP
        IF to_regclass('public.' || v_table) IS NOT NULL THEN
            EXECUTE format('CREATE TABLE fuel_backup.%I AS TABLE public.%I', v_table || '_' || v_suffix, v_table);
        END IF;
    END LOOP;
    -- Children first (they reference fuel_imports)
    FOREACH v_table IN ARRAY v_tables LOOP
        IF to_regclass('public.' || v_table) IS NOT NULL THEN
            EXECUTE format('DELETE FROM public.%I', v_table);
        END IF;
    END LOOP;
    RAISE NOTICE 'Fuel test data reset; backups in schema fuel_backup with suffix %', v_suffix;
END $$;
COMMIT;
RESET fuel.reset_confirm;

SELECT (SELECT count(*) FROM public.fuel_transactions) AS fuel_transactions,
       (SELECT count(*) FROM public.fuel_imports) AS fuel_imports,
       (SELECT count(*) FROM public.fuel_monthly_summaries) AS fuel_monthly_summaries,
       (SELECT string_agg(table_name, ', ' ORDER BY table_name) FROM information_schema.tables
        WHERE table_schema = 'fuel_backup') AS backups;
-- Expect 0 / 0 / 0 and the backup table names. Restoring a backup: INSERT INTO public.<table> OVERRIDING SYSTEM VALUE
-- SELECT * FROM fuel_backup.<table>_<suffix>;  (fuel_imports first, then the others)
