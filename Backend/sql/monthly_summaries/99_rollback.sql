-- =====================================================================================================
-- 99  ROLLBACK to the previous layout (summaries as month-end rows in fuel_transactions). Use only if verification
--     or the application fails. FIRST unset FUEL_MONTHLY_SUMMARY_TABLE on every backend instance and restart it.
--
-- Labelled DESTRUCTIVE for the NEW objects only: it drops fuel_monthly_summaries, fuel_commit_import_v2 and the
-- helper functions fuel_import_accepts_rows / fuel_vehicle_key. Existing fuel_transactions / fuel_imports rows are
-- restored, never deleted:
--   R1  summaries imported AFTER the cutover (only in fuel_monthly_summaries) are copied back as month-end rows,
--       with their import, source row, staged and superseded state, so no imported data is lost;
--   R1b a migrated copy that a later import superseded hides its legacy source row too (else two summaries of one
--       vehicle-month would be visible again);
--   R2  the changes script 04 made (logged in fuel_migration_audit) are undone: dated rows it superseded become
--       visible again, rows it made visible again (A2) get their previous supersession back, and reclassified
--       imports get their previous mode back;
--   R3  the old commit function is granted again; the new objects are dropped (after R1 succeeded).
-- One transaction: it completes fully or changes nothing. fuel_migration_audit is kept as the record.
-- Note: without the flag, the read rule still lets a monthly summary take precedence over dated rows of the same
-- vehicle-month, so R2 restores the earlier stored state without double counting.
-- =====================================================================================================
BEGIN;
SET LOCAL TimeZone = 'UTC';
LOCK TABLE public.fuel_imports, public.fuel_transactions, public.fuel_monthly_summaries IN SHARE ROW EXCLUSIVE MODE;

-- R1. Summaries that exist only in the new table, back as month-end rows (idempotent through the audit log)
WITH moved AS (
    INSERT INTO public.fuel_transactions (
        import_id, transaction_date, vehicle_identifier, fuel_type, litres, unit_price, total_cost, odometer_km,
        station_name, invoice_number, card_number, driver_name, reference_number, notes, source_row_number,
        date_basis, staged, superseded_by_import_id, superseded_at)
    SELECT s.import_id, (s.reporting_month + interval '1 month - 1 day')::date, s.vehicle_identifier, s.fuel_type,
           s.litres, s.unit_price, s.total_cost, s.odometer_km, s.station_name, s.invoice_number, s.card_number,
           s.driver_name, s.reference_number, s.notes, s.source_row_number,
           'reporting_month_end', s.staged, s.superseded_by_import_id, s.superseded_at
    FROM public.fuel_monthly_summaries s
    WHERE s.migrated_from_transaction_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM public.fuel_migration_audit a WHERE a.step = 'R1_reverse_copy' AND a.before ->> 'summary_id' = s.id::text)
    RETURNING id, import_id, source_row_number
)
INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'R1_reverse_copy', 'fuel_transactions', m.id,
       jsonb_build_object('summary_id', s.id), jsonb_build_object('import_id', m.import_id)
FROM moved m
JOIN public.fuel_monthly_summaries s ON s.import_id = m.import_id AND s.source_row_number IS NOT DISTINCT FROM m.source_row_number
                                    AND s.migrated_from_transaction_id IS NULL;

-- R1b. Copies superseded after the cutover (by a newer monthly import through fuel_commit_import_v2) hide their
--      legacy source row too; otherwise the old and the new summary of a vehicle-month would both be visible again
WITH synced AS (
    UPDATE public.fuel_transactions t
    SET superseded_by_import_id = s.superseded_by_import_id, superseded_at = coalesce(s.superseded_at, now())
    FROM public.fuel_monthly_summaries s
    WHERE s.migrated_from_transaction_id = t.id
      AND s.superseded_by_import_id IS NOT NULL AND t.superseded_by_import_id IS NULL
    RETURNING t.id, t.superseded_by_import_id
)
INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'R1b_supersede_source_of_superseded_copy', 'fuel_transactions', id,
       jsonb_build_object('superseded_by_import_id', NULL), jsonb_build_object('superseded_by_import_id', superseded_by_import_id)
FROM synced;

-- R2. Undo script 04 (latest values only where nothing changed them since)
UPDATE public.fuel_transactions t
SET superseded_by_import_id = NULL, superseded_at = NULL
FROM public.fuel_migration_audit a
WHERE a.step = 'C_supersede_dated_by_summary' AND a.row_id = t.id
  AND t.superseded_by_import_id::text = a.after ->> 'superseded_by_import_id';
UPDATE public.fuel_transactions t
SET superseded_by_import_id = (a.before ->> 'superseded_by_import_id')::bigint,
    superseded_at = (a.before ->> 'superseded_at')::timestamptz
FROM public.fuel_migration_audit a
WHERE a.step = 'A2_unsupersede_reclassified_rows' AND a.row_id = t.id AND t.superseded_by_import_id IS NULL;
UPDATE public.fuel_transactions t
SET date_basis = a.before ->> 'date_basis'
FROM public.fuel_migration_audit a
WHERE a.step = 'A_reclassify_rows' AND a.row_id = t.id;
UPDATE public.fuel_imports i
SET import_mode = a.before ->> 'import_mode', reporting_month = (a.before ->> 'reporting_month')::date
FROM public.fuel_migration_audit a
WHERE a.step = 'A_reclassify_import' AND a.row_id = i.id;

-- R3. Old commit function back; new objects removed
GRANT EXECUTE ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) TO authenticated;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.fuel_monthly_summaries s
               WHERE s.migrated_from_transaction_id IS NULL
                 AND NOT EXISTS (SELECT 1 FROM public.fuel_migration_audit a
                                 WHERE a.step = 'R1_reverse_copy' AND a.before ->> 'summary_id' = s.id::text)) THEN
        RAISE EXCEPTION 'Rollback refused: some monthly summaries were not copied back; nothing was changed';
    END IF;
END $$;
DROP FUNCTION IF EXISTS public.fuel_commit_import_v2(bigint, bigint[], bigint[], integer);
DROP TABLE IF EXISTS public.fuel_monthly_summaries;           -- also drops its policies
DROP FUNCTION IF EXISTS public.fuel_import_accepts_rows(bigint);
DROP FUNCTION IF EXISTS public.fuel_vehicle_key(text);
COMMIT;
NOTIFY pgrst, 'reload schema';

SELECT step, count(*) AS rows FROM public.fuel_migration_audit GROUP BY step ORDER BY step;
