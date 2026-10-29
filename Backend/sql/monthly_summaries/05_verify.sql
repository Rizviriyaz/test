-- =====================================================================================================
-- 05  VERIFY (read-only). Run after 02-04 (or after 02-03 + reset 90 + re-import). Changes nothing.
-- One result table: check, status (OK / REVIEW / INFO), result. Every row should be OK before the cutover (06).
-- =====================================================================================================
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;
SET TimeZone = 'UTC';

WITH
s AS (SELECT * FROM public.fuel_monthly_summaries),
t AS (SELECT id, import_id, vehicle_identifier, transaction_date, litres, total_cost, date_basis,
             superseded_by_import_id, staged,
             public.fuel_vehicle_key(vehicle_identifier) AS vkey,
             date_trunc('month', left(transaction_date::text, 10)::date)::date AS month
      FROM public.fuel_transactions),
vs AS (SELECT * FROM s WHERE superseded_by_import_id IS NULL AND staged IS NOT TRUE),
vd AS (SELECT * FROM t WHERE superseded_by_import_id IS NULL AND staged IS NOT TRUE
                         AND date_basis IS DISTINCT FROM 'reporting_month_end'),
checks AS (
    SELECT 10 AS ord, 'Objects installed' AS check_name,
           CASE WHEN to_regprocedure('public.fuel_commit_import_v2(bigint,bigint[],bigint[],integer)') IS NOT NULL
                     AND (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.fuel_monthly_summaries'::regclass)
                THEN 'OK' ELSE 'REVIEW' END AS status,
           jsonb_build_object(
               'commit_v2_security_definer', (SELECT prosecdef FROM pg_proc
                    WHERE oid = to_regprocedure('public.fuel_commit_import_v2(bigint,bigint[],bigint[],integer)')),
               'summary_table_rls', (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.fuel_monthly_summaries'::regclass),
               'summary_policies', (SELECT coalesce(jsonb_agg(policyname::text ORDER BY policyname), '[]'::jsonb)
                                    FROM pg_policies WHERE tablename = 'fuel_monthly_summaries'),
               'v1_executable_by_authenticated', has_function_privilege('authenticated',
                    'public.fuel_commit_import(bigint,bigint[],integer)', 'EXECUTE')) AS result

    UNION ALL
    SELECT 20, 'Row counts', 'INFO',
           jsonb_build_object(
               'summary_rows', (SELECT count(*) FROM s),
               'visible_summary_rows', (SELECT count(*) FROM vs),
               'summary_rows_copied_from_legacy', (SELECT count(*) FROM s WHERE migrated_from_transaction_id IS NOT NULL),
               'legacy_month_end_rows_in_fuel_transactions', (SELECT count(*) FROM t WHERE date_basis = 'reporting_month_end'),
               'visible_dated_rows', (SELECT count(*) FROM vd),
               'superseded_dated_rows', (SELECT count(*) FROM t WHERE superseded_by_import_id IS NOT NULL
                                                                AND date_basis IS DISTINCT FROM 'reporting_month_end'))

    UNION ALL
    SELECT 30, 'Every legacy month-end row has an identical copy',
           CASE WHEN (SELECT count(*) FROM t WHERE date_basis = 'reporting_month_end'
                                               AND NOT EXISTS (SELECT 1 FROM s WHERE s.migrated_from_transaction_id = t.id)) = 0
                THEN 'OK' ELSE 'REVIEW' END,
           jsonb_build_object('legacy_rows_without_copy',
               (SELECT count(*) FROM t WHERE date_basis = 'reporting_month_end'
                                         AND NOT EXISTS (SELECT 1 FROM s WHERE s.migrated_from_transaction_id = t.id)))

    UNION ALL
    SELECT 35, 'No monthly summary is hidden by a dated import (the precedence rule would show it)',
           CASE WHEN count(*) = 0 THEN 'OK' ELSE 'REVIEW' END,
           jsonb_build_object('rows', count(*),
                              'by_import', coalesce(jsonb_agg(DISTINCT s.superseded_by_import_id), '[]'::jsonb))
    FROM s JOIN public.fuel_imports i ON i.id = s.superseded_by_import_id
    WHERE coalesce(i.import_mode, 'dated') = 'dated'

    UNION ALL
    SELECT 40, 'No vehicle-month counts a monthly summary together with dated rows',
           CASE WHEN count(*) = 0 THEN 'OK' ELSE 'REVIEW' END,
           jsonb_build_object('vehicle_months_with_both', count(*))
    FROM (SELECT DISTINCT vs.vehicle_key, vs.reporting_month FROM vs
          JOIN vd ON vd.vkey = vs.vehicle_key AND vd.month = vs.reporting_month) x

    UNION ALL
    SELECT 50, 'At most one import supplies the visible summary of a vehicle-month',
           CASE WHEN count(*) = 0 THEN 'OK' ELSE 'REVIEW' END,
           jsonb_build_object('vehicle_months_with_several_summary_imports', count(*))
    FROM (SELECT vehicle_key, reporting_month FROM vs GROUP BY 1, 2 HAVING count(DISTINCT import_id) > 1) x

    UNION ALL
    SELECT 60, 'Monthly-summary imports have a reporting month and rows only in their month',
           CASE WHEN count(*) = 0 THEN 'OK' ELSE 'REVIEW' END,
           jsonb_build_object('inconsistent_imports', coalesce(jsonb_agg(i.id ORDER BY i.id), '[]'::jsonb))
    FROM public.fuel_imports i
    WHERE i.import_mode = 'monthly_summary'
      AND (i.reporting_month IS NULL
           OR EXISTS (SELECT 1 FROM s WHERE s.import_id = i.id AND s.reporting_month <> i.reporting_month))

    UNION ALL
    SELECT 70, 'Visible totals per month (before Wialon matching): what the dashboard can count', 'INFO',
           coalesce(jsonb_agg(jsonb_build_object('month', m.month, 'summary_rows', m.sn, 'summary_litres', m.sl,
                                                 'summary_cost', m.sc, 'dated_rows', m.dn, 'dated_litres', m.dl,
                                                 'dated_cost', m.dc) ORDER BY m.month), '[]'::jsonb)
    FROM (SELECT coalesce(a.month, b.month) AS month, a.n AS sn, a.l AS sl, a.c AS sc, b.n AS dn, b.l AS dl, b.c AS dc
          FROM (SELECT reporting_month AS month, count(*) AS n, sum(litres) AS l, sum(total_cost) AS c FROM vs GROUP BY 1) a
          FULL JOIN (SELECT month, count(*) AS n, sum(litres) AS l, sum(total_cost) AS c FROM vd GROUP BY 1) b
            ON b.month = a.month) m

    UNION ALL
    SELECT 80, 'Changes logged by the migration (04)', 'INFO',
           coalesce((SELECT jsonb_object_agg(step, n) FROM (SELECT step, count(*) AS n FROM public.fuel_migration_audit
                                                            GROUP BY step) a), '{}'::jsonb)
)
SELECT check_name, status, result FROM checks ORDER BY ord;
