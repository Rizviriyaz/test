-- =====================================================================================================
-- 02a  READ-ONLY DIAGNOSTIC of the EXISTING fuel_monthly_summaries table and of the effective time zones.
--      Run after 02 (which skips creation when the table exists) and before 02b/03. Changes nothing.
-- One result table: item, status, detail. 'DIFFERS' / 'MISSING' rows need script 02b or a review before 03.
-- =====================================================================================================
SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;

WITH
tbl AS (SELECT to_regclass('public.fuel_monthly_summaries') AS oid),
expected_constraints(name) AS (VALUES ('fms_reporting_month_first_day'), ('fms_vehicle_not_blank'),
    ('fms_supersede_pair'), ('fms_not_self_superseded'), ('fms_import_source_row')),
expected_indexes(name) AS (VALUES ('fms_visible_month_vehicle'), ('fms_import'), ('fms_superseded_by')),
utc(name) AS (VALUES ('utc'), ('etc/utc'), ('gmt'), ('etc/gmt'), ('z'), ('uct'), ('zulu'), ('universal'), ('etc/universal')),
tz_overrides AS (
    SELECT coalesce(r.rolname, '(every role)') AS role,
           CASE WHEN d.setdatabase = 0 THEN 'all databases' ELSE 'this database' END AS scope,
           s AS setting
    FROM pg_db_role_setting d
    LEFT JOIN pg_roles r ON r.oid = d.setrole
    CROSS JOIN LATERAL unnest(d.setconfig) AS s
    WHERE (d.setdatabase = 0 OR d.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database()))
      AND s ILIKE 'timezone=%'
),
checks AS (
    SELECT 10 AS ord, 'vehicle_key generated expression' AS item,
           CASE WHEN x.expr IS NULL THEN 'MISSING'
                WHEN x.expr LIKE 'regexp_replace(lower(vehicle_identifier)%' THEN 'OK (lower-case first, as the backend)'
                ELSE 'DIFFERS: run 02b' END AS status,
           to_jsonb(x.expr) AS detail
    FROM (SELECT (SELECT pg_get_expr(d.adbin, d.adrelid)
                  FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                  WHERE d.adrelid = (SELECT oid FROM tbl) AND a.attname = 'vehicle_key' AND a.attgenerated = 's') AS expr) x

    UNION ALL
    SELECT 20, 'fuel_vehicle_key(text) body and settings',
           CASE WHEN f.src IS NULL THEN 'MISSING'
                WHEN f.src LIKE '%regexp_replace(lower(coalesce(p_identifier%' THEN 'OK'
                ELSE 'DIFFERS: run 02b' END,
           jsonb_build_object('body', f.src, 'config', f.cfg, 'owner', f.own, 'volatility', f.vol)
    FROM (SELECT (SELECT prosrc FROM pg_proc WHERE oid = to_regprocedure('public.fuel_vehicle_key(text)')) AS src,
                 (SELECT proconfig FROM pg_proc WHERE oid = to_regprocedure('public.fuel_vehicle_key(text)')) AS cfg,
                 (SELECT proowner::regrole::text FROM pg_proc WHERE oid = to_regprocedure('public.fuel_vehicle_key(text)')) AS own,
                 (SELECT provolatile::text FROM pg_proc WHERE oid = to_regprocedure('public.fuel_vehicle_key(text)')) AS vol) f

    UNION ALL
    SELECT 30, 'Columns (name, type, nullable, identity, generated)', 'INFO',
           coalesce((SELECT jsonb_agg(jsonb_build_object('column', a.attname, 'type', format_type(a.atttypid, a.atttypmod),
                                                         'not_null', a.attnotnull, 'identity', a.attidentity::text,
                                                         'generated', a.attgenerated::text) ORDER BY a.attnum)
                     FROM pg_attribute a WHERE a.attrelid = (SELECT oid FROM tbl) AND a.attnum > 0 AND NOT a.attisdropped),
                    '[]'::jsonb)

    UNION ALL
    SELECT 40, 'Constraints',
           CASE WHEN (SELECT count(*) FROM expected_constraints e
                      WHERE NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = (SELECT oid FROM tbl)
                                                                        AND c.conname = e.name)) = 0
                THEN 'OK' ELSE 'MISSING' END,
           jsonb_build_object(
               'missing', (SELECT coalesce(jsonb_agg(e.name), '[]'::jsonb) FROM expected_constraints e
                           WHERE NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conrelid = (SELECT oid FROM tbl)
                                                                             AND c.conname = e.name)),
               'all', (SELECT coalesce(jsonb_agg(jsonb_build_object('name', c.conname, 'definition', pg_get_constraintdef(c.oid))
                                                 ORDER BY c.conname), '[]'::jsonb)
                       FROM pg_constraint c WHERE c.conrelid = (SELECT oid FROM tbl)))

    UNION ALL
    SELECT 50, 'Indexes',
           CASE WHEN (SELECT count(*) FROM expected_indexes e WHERE to_regclass('public.' || e.name) IS NULL) = 0
                THEN 'OK' ELSE 'MISSING' END,
           jsonb_build_object(
               'missing', (SELECT coalesce(jsonb_agg(e.name), '[]'::jsonb) FROM expected_indexes e
                           WHERE to_regclass('public.' || e.name) IS NULL),
               'all', (SELECT coalesce(jsonb_agg(jsonb_build_object('index', i.indexrelid::regclass::text, 'valid', i.indisvalid,
                                                                    'definition', pg_get_indexdef(i.indexrelid))), '[]'::jsonb)
                       FROM pg_index i WHERE i.indrelid = (SELECT oid FROM tbl)))

    UNION ALL
    SELECT 60, 'Identity sequence of id',
           CASE WHEN q.seq IS NOT NULL AND to_regclass(q.seq) IS NOT NULL THEN 'OK' ELSE 'MISSING' END,
           jsonb_build_object('sequence', q.seq, 'exists', to_regclass(q.seq) IS NOT NULL,
                              'owner', (SELECT relowner::regrole::text FROM pg_class WHERE oid = to_regclass(q.seq)),
                              'acl', (SELECT relacl::text FROM pg_class WHERE oid = to_regclass(q.seq)))
    FROM (SELECT CASE WHEN (SELECT oid FROM tbl) IS NULL THEN NULL
                      ELSE pg_get_serial_sequence('public.fuel_monthly_summaries', 'id') END AS seq) q

    UNION ALL
    SELECT 70, 'Rows already stored (02b rewrites the generated column; 04 not run yet if 0 copies)', 'INFO',
           jsonb_build_object(
               'summary_rows', (SELECT count(*) FROM public.fuel_monthly_summaries),   -- needs the table (02 ran)
               'copied_rows', (SELECT count(*) FROM public.fuel_monthly_summaries WHERE migrated_from_transaction_id IS NOT NULL),
               'audit_table_exists', to_regclass('public.fuel_migration_audit') IS NOT NULL,
               'policies', (SELECT coalesce(jsonb_agg(policyname::text), '[]'::jsonb) FROM pg_policies
                            WHERE schemaname = 'public' AND tablename = 'fuel_monthly_summaries'),
               'commit_v2_exists', to_regprocedure('public.fuel_commit_import_v2(bigint,bigint[],bigint[],integer)') IS NOT NULL)

    UNION ALL
    SELECT 80, 'Time zone of THIS session and the server default',
           CASE WHEN lower(current_setting('TimeZone')) IN (SELECT name FROM utc) THEN 'OK' ELSE 'REVIEW' END,
           (SELECT jsonb_build_object('session', current_setting('TimeZone'), 'source', s.source,
                                      'reset_value', s.reset_val, 'built_in_default', s.boot_val)
            FROM pg_settings s WHERE s.name = 'TimeZone')

    UNION ALL
    SELECT 90, 'Time-zone overrides for the database or any role (the API connects as authenticator)',
           CASE WHEN EXISTS (SELECT 1 FROM tz_overrides
                             WHERE lower(split_part(setting, '=', 2)) NOT IN (SELECT name FROM utc)) THEN 'REVIEW' ELSE 'OK' END,
           (SELECT coalesce(jsonb_agg(jsonb_build_object('role', role, 'scope', scope, 'setting', setting)), '[]'::jsonb)
            FROM tz_overrides)

    UNION ALL
    SELECT 100, 'PostgREST pre-request hook (could change the time zone per request)',
           CASE WHEN EXISTS (SELECT 1 FROM pg_db_role_setting d JOIN pg_roles r ON r.oid = d.setrole
                             CROSS JOIN LATERAL unnest(d.setconfig) AS s
                             WHERE r.rolname = 'authenticator' AND s ILIKE 'pgrst.db_pre_request=%')
                THEN 'REVIEW' ELSE 'OK' END,
           (SELECT coalesce(jsonb_agg(s), '[]'::jsonb) FROM pg_db_role_setting d JOIN pg_roles r ON r.oid = d.setrole
            CROSS JOIN LATERAL unnest(d.setconfig) AS s
            WHERE r.rolname = 'authenticator' AND s ILIKE 'pgrst.db_pre_request=%')
)
SELECT item, status, detail FROM checks ORDER BY ord;
