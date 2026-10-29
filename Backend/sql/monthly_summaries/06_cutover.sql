-- =====================================================================================================
-- 06  CUTOVER CHECK (read-only by default). Run after 05 shows every check OK and AFTER the backend runs with
--     FUEL_MONTHLY_SUMMARY_TABLE=1 (and FUEL_IMPORT_REPLACE=1) and one test import succeeded.
--
-- Nothing here blocks imports. A backend without the flag keeps committing through the old function and writes
-- monthly summaries as month-end rows of fuel_transactions; those are not lost: re-running script 04 copies them
-- into fuel_monthly_summaries (step B is idempotent) and applies the monthly-summary precedence (step C).
--
-- OPTIONAL lock-down (commented out): once EVERY backend instance runs with the flag and imports work, revoking the
-- old function stops a forgotten instance without the flag from committing at all. Do not run it before that: a
-- backend without the flag could then not import anything (its imports would stay hidden as "Not committed").
-- Undo: GRANT EXECUTE ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) TO authenticated;
-- =====================================================================================================
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;

-- REVOKE EXECUTE ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) FROM authenticated;
-- NOTIFY pgrst, 'reload schema';

-- Status: which commit path each recent import used (v2 imports of monthly summaries have rows in the new table)
SELECT has_function_privilege('authenticated', 'public.fuel_commit_import(bigint,bigint[],integer)', 'EXECUTE')
           AS v1_executable,
       has_function_privilege('authenticated', 'public.fuel_commit_import_v2(bigint,bigint[],bigint[],integer)', 'EXECUTE')
           AS v2_executable,
       (SELECT count(*) FROM public.fuel_transactions t
        WHERE t.date_basis = 'reporting_month_end'
          AND NOT EXISTS (SELECT 1 FROM public.fuel_monthly_summaries s WHERE s.migrated_from_transaction_id = t.id))
           AS month_end_rows_not_in_new_table,   -- > 0: a backend without the flag imported summaries; re-run 04
       (SELECT max(id) FROM public.fuel_imports) AS latest_import_id;
