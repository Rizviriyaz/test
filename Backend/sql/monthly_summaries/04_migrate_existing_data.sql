-- =====================================================================================================
-- 04  MIGRATE THE EXISTING FUEL DATA IN PLACE (writes; nothing is deleted). One transaction: every check passes or
--     nothing changes. Every changed existing row is logged in public.fuel_migration_audit (undone by script 99).
--     Skip this script when you use the optional reset (90) and re-import the statements instead.
--
--   A  Imports stored as 'dated' whose rows (2 or more) all carry one identical, time-less, month-end date with a
--      total cost are monthly summaries under the import rule (diagnostics: import 30, 61 rows on 2026-08-31):
--      the import becomes 'monthly_summary' of that month and its rows get date_basis 'reporting_month_end'.
--      An identical date that is NOT a month end is left unchanged and reported: its month needs your decision.
--      Imports you identified as genuine daily transactions are excluded with fuel.keep_dated_imports (below).
--   A2 Monthly-summary rows (including the reclassified ones) that an earlier DATED import superseded become visible
--      again (a dated import never supersedes a monthly summary); supersession by another monthly import is kept.
--   B  Every month-end summary row is COPIED into fuel_monthly_summaries with its import, source row, staged and
--      superseded state, linked by migrated_from_transaction_id. The source rows stay in fuel_transactions; with
--      FUEL_MONTHLY_SUMMARY_TABLE the backend reads them only from the new table, so they are never counted twice.
--   C  The monthly summary takes precedence: visible dated rows of a vehicle-month that has a visible summary (same
--      normalised vehicle key) are marked superseded by the summary's import. History is kept (rows not deleted).
--      With the diagnosed data this replaces import 31's August rows of the vehicles in import 30, so August totals
--      for those vehicles become the monthly-summary totals; other vehicles and months are untouched.
--   V  Verification: the transaction aborts (nothing changes) unless every check passes.
--   B2 On a re-run, the staged / superseded state of each copy follows its source row (changes made since by a
--      backend without the flag), so the script is repeatable and never leaves an outdated summary visible.
-- Other existing supersession is kept as stored (e.g. import 28 rows superseded by a MONTHLY import 29 stay superseded).
-- Run 01-03 (and 02a/02b) first, in a NEW SQL editor tab (01 set its tab read-only). Freeze imports while this runs.
-- =====================================================================================================
BEGIN;
SET LOCAL TimeZone = 'UTC';   -- the wall clock the importer wrote (offset-free timestamps, UTC sessions; 02a/03 check)
-- Imports YOU identified as genuine daily transactions although every row has one date: list their ids here
-- (comma-separated, e.g. '12,57'). Empty = the rule applies to every qualifying import.
SET LOCAL fuel.keep_dated_imports = '';
LOCK TABLE public.fuel_imports, public.fuel_transactions, public.fuel_monthly_summaries IN SHARE ROW EXCLUSIVE MODE;

-- ── A. Same-date 'dated' imports that are month-end monthly summaries ───────────────────────────────
CREATE TEMP TABLE m_same_date ON COMMIT DROP AS
SELECT t.import_id, min(left(t.transaction_date::text, 10))::date AS day
FROM public.fuel_transactions t
JOIN public.fuel_imports i ON i.id = t.import_id
WHERE coalesce(i.import_mode, 'dated') = 'dated'
  AND t.date_basis IS DISTINCT FROM 'reporting_month_end'
  AND t.import_id <> ALL (coalesce(string_to_array(nullif(btrim(current_setting('fuel.keep_dated_imports', true)), ''), ',')::bigint[],
                                   '{}'::bigint[]))
GROUP BY t.import_id
HAVING count(*) >= 2
   AND count(DISTINCT t.transaction_date) = 1
   AND bool_and(substr(t.transaction_date::text, 12, 8) IN ('', '00:00:00'))
   AND bool_and(t.total_cost IS NOT NULL)
   AND min(left(t.transaction_date::text, 10))::date
       = (date_trunc('month', min(left(t.transaction_date::text, 10))::date) + interval '1 month - 1 day')::date;

INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'A_reclassify_import', 'fuel_imports', i.id,
       jsonb_build_object('import_mode', i.import_mode, 'reporting_month', i.reporting_month),
       jsonb_build_object('import_mode', 'monthly_summary', 'reporting_month', date_trunc('month', m.day)::date)
FROM public.fuel_imports i JOIN m_same_date m ON m.import_id = i.id;
UPDATE public.fuel_imports i
SET import_mode = 'monthly_summary', reporting_month = date_trunc('month', m.day)::date
FROM m_same_date m WHERE m.import_id = i.id;

INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'A_reclassify_rows', 'fuel_transactions', t.id,
       jsonb_build_object('date_basis', t.date_basis), jsonb_build_object('date_basis', 'reporting_month_end')
FROM public.fuel_transactions t JOIN m_same_date m ON m.import_id = t.import_id;
UPDATE public.fuel_transactions t SET date_basis = 'reporting_month_end'
FROM m_same_date m WHERE t.import_id = m.import_id;

-- ── A2. A dated import cannot supersede a monthly summary ──────────────────────────────────────────
-- Before this rule, a later DATED import could supersede monthly-summary rows (e.g. import 31 on 2026-08-31
-- superseding rows of import 30 while 30 was treated as dated, or a dated import 29 superseding July rows of 28).
-- Under the precedence rule that never happens to a monthly summary, so every summary row superseded by a dated
-- import becomes visible again; step C then supersedes the dated import's rows of the same vehicle-month instead.
-- Supersession by another monthly import is kept. Logged, undone by 99.
CREATE TEMP TABLE m_unsupersede ON COMMIT DROP AS
SELECT t.id, t.superseded_by_import_id, t.superseded_at
FROM public.fuel_transactions t
JOIN public.fuel_imports si ON si.id = t.superseded_by_import_id
WHERE t.date_basis = 'reporting_month_end'
  AND coalesce(si.import_mode, 'dated') = 'dated';

INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'A2_unsupersede_reclassified_rows', 'fuel_transactions', u.id,
       jsonb_build_object('superseded_by_import_id', u.superseded_by_import_id, 'superseded_at', u.superseded_at),
       jsonb_build_object('superseded_by_import_id', NULL)
FROM m_unsupersede u;
UPDATE public.fuel_transactions t SET superseded_by_import_id = NULL, superseded_at = NULL
FROM m_unsupersede u WHERE t.id = u.id;

-- ── B. Copy every month-end summary row (idempotent) ───────────────────────────────────────────────
INSERT INTO public.fuel_monthly_summaries (
    import_id, reporting_month, vehicle_identifier, fuel_type, litres, unit_price, total_cost, odometer_km,
    station_name, invoice_number, card_number, driver_name, reference_number, notes, source_row_number,
    staged, superseded_by_import_id, superseded_at, migrated_from_transaction_id, migration_note)
SELECT t.import_id,
       date_trunc('month', left(t.transaction_date::text, 10)::date)::date,
       t.vehicle_identifier, t.fuel_type, t.litres, t.unit_price, t.total_cost, t.odometer_km,
       t.station_name, t.invoice_number, t.card_number, t.driver_name, t.reference_number, t.notes,
       t.source_row_number,
       coalesce(t.staged, false), t.superseded_by_import_id,
       CASE WHEN t.superseded_by_import_id IS NOT NULL THEN coalesce(t.superseded_at, now()) END,
       t.id, '04: copied from a legacy month-end fuel_transactions row'
FROM public.fuel_transactions t
WHERE t.date_basis = 'reporting_month_end'
  AND left(t.transaction_date::text, 10)::date
      = (date_trunc('month', left(t.transaction_date::text, 10)::date) + interval '1 month - 1 day')::date
  AND substr(t.transaction_date::text, 12, 8) IN ('', '00:00:00')
ON CONFLICT (migrated_from_transaction_id) DO NOTHING;

-- ── B2. Re-run: carry later changes of the source rows over to their copies ─────────────────────────
-- A backend running without FUEL_MONTHLY_SUMMARY_TABLE commits through the old function, which may supersede (or
-- make visible) the legacy month-end rows after their first copy. The copies follow, so a re-run never leaves an
-- outdated summary visible and the copy check (V) stays exact.
INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'B2_sync_copy_state', 'fuel_monthly_summaries', s.id,
       jsonb_build_object('staged', s.staged, 'superseded_by_import_id', s.superseded_by_import_id),
       jsonb_build_object('staged', coalesce(t.staged, false), 'superseded_by_import_id', t.superseded_by_import_id)
FROM public.fuel_monthly_summaries s JOIN public.fuel_transactions t ON t.id = s.migrated_from_transaction_id
WHERE s.staged IS DISTINCT FROM coalesce(t.staged, false)
   OR s.superseded_by_import_id IS DISTINCT FROM t.superseded_by_import_id;
UPDATE public.fuel_monthly_summaries s
SET staged = coalesce(t.staged, false),
    superseded_by_import_id = t.superseded_by_import_id,
    superseded_at = CASE WHEN t.superseded_by_import_id IS NOT NULL THEN coalesce(t.superseded_at, now()) END
FROM public.fuel_transactions t
WHERE t.id = s.migrated_from_transaction_id
  AND (s.staged IS DISTINCT FROM coalesce(t.staged, false)
       OR s.superseded_by_import_id IS DISTINCT FROM t.superseded_by_import_id);

-- ── C. Monthly summary takes precedence over dated rows of the same vehicle-month ───────────────────
CREATE TEMP TABLE m_superseded ON COMMIT DROP AS
SELECT t.id, max(s.import_id) AS by_import
FROM public.fuel_transactions t
JOIN public.fuel_monthly_summaries s
  ON s.vehicle_key = public.fuel_vehicle_key(t.vehicle_identifier)
 AND s.reporting_month = date_trunc('month', left(t.transaction_date::text, 10)::date)::date
 AND s.superseded_by_import_id IS NULL AND s.staged IS NOT TRUE
WHERE t.superseded_by_import_id IS NULL AND t.staged IS NOT TRUE
  AND t.date_basis IS DISTINCT FROM 'reporting_month_end'
  AND t.import_id IS DISTINCT FROM s.import_id
GROUP BY t.id;

INSERT INTO public.fuel_migration_audit (step, table_name, row_id, before, after)
SELECT 'C_supersede_dated_by_summary', 'fuel_transactions', m.id,
       jsonb_build_object('superseded_by_import_id', NULL, 'superseded_at', NULL),
       jsonb_build_object('superseded_by_import_id', m.by_import)
FROM m_superseded m;
UPDATE public.fuel_transactions t SET superseded_by_import_id = m.by_import, superseded_at = now()
FROM m_superseded m WHERE t.id = m.id;

-- ── V. Verification: any failure aborts the whole transaction ──────────────────────────────────────
DO $$
DECLARE
    v_source bigint;
    v_copied bigint;
    v_diff   bigint;
    v_multi  bigint;
    v_mixed  bigint;
BEGIN
    SELECT count(*) INTO v_source FROM public.fuel_transactions t
    WHERE t.date_basis = 'reporting_month_end'
      AND left(t.transaction_date::text, 10)::date
          = (date_trunc('month', left(t.transaction_date::text, 10)::date) + interval '1 month - 1 day')::date
      AND substr(t.transaction_date::text, 12, 8) IN ('', '00:00:00');
    SELECT count(*) INTO v_copied FROM public.fuel_monthly_summaries WHERE migrated_from_transaction_id IS NOT NULL;
    IF v_source <> v_copied THEN
        RAISE EXCEPTION '04: % month-end source rows but % copies; nothing was changed', v_source, v_copied;
    END IF;

    SELECT count(*) INTO v_diff
    FROM public.fuel_transactions t JOIN public.fuel_monthly_summaries s ON s.migrated_from_transaction_id = t.id
    WHERE s.import_id IS DISTINCT FROM t.import_id
       OR s.vehicle_identifier IS DISTINCT FROM t.vehicle_identifier
       OR s.litres IS DISTINCT FROM t.litres
       OR s.total_cost IS DISTINCT FROM t.total_cost
       OR s.unit_price IS DISTINCT FROM t.unit_price
       OR s.fuel_type IS DISTINCT FROM t.fuel_type
       OR s.source_row_number IS DISTINCT FROM t.source_row_number
       OR s.staged IS DISTINCT FROM coalesce(t.staged, false)
       OR s.superseded_by_import_id IS DISTINCT FROM t.superseded_by_import_id
       OR s.reporting_month <> date_trunc('month', left(t.transaction_date::text, 10)::date)::date;
    IF v_diff > 0 THEN
        RAISE EXCEPTION '04: % copied row(s) differ from their source; nothing was changed', v_diff;
    END IF;

    SELECT count(*) INTO v_multi FROM (
        SELECT vehicle_key, reporting_month FROM public.fuel_monthly_summaries
        WHERE superseded_by_import_id IS NULL AND staged IS NOT TRUE
        GROUP BY 1, 2 HAVING count(DISTINCT import_id) > 1) x;
    IF v_multi > 0 THEN
        RAISE EXCEPTION '04: % vehicle-month(s) have visible summaries of several imports; decide which import is '
                        'current before migrating; nothing was changed', v_multi;
    END IF;

    SELECT count(*) INTO v_mixed FROM public.fuel_transactions t
    WHERE t.superseded_by_import_id IS NULL AND t.staged IS NOT TRUE
      AND t.date_basis IS DISTINCT FROM 'reporting_month_end'
      AND EXISTS (SELECT 1 FROM public.fuel_monthly_summaries s
                  WHERE s.vehicle_key = public.fuel_vehicle_key(t.vehicle_identifier)
                    AND s.reporting_month = date_trunc('month', left(t.transaction_date::text, 10)::date)::date
                    AND s.superseded_by_import_id IS NULL AND s.staged IS NOT TRUE);
    IF v_mixed > 0 THEN
        RAISE EXCEPTION '04: % visible dated row(s) still share a vehicle-month with a visible summary; nothing was changed',
            v_mixed;
    END IF;
END $$;

COMMIT;

-- Report (read-only): what this script changed, same-date imports left for your decision (not a month end), and
-- monthly summaries an earlier DATED import had superseded (kept hidden as stored; the precedence rule would show
-- them instead: decide, e.g. re-import that monthly statement)
SELECT 'changed: ' || step AS item, table_name AS detail, count(*) AS rows
FROM public.fuel_migration_audit GROUP BY 1, 2
UNION ALL
SELECT 'review: summaries hidden by dated import #' || s.superseded_by_import_id,
       string_agg(DISTINCT to_char(s.reporting_month, 'YYYY-MM'), ', '), count(*)
FROM public.fuel_monthly_summaries s JOIN public.fuel_imports i ON i.id = s.superseded_by_import_id
WHERE coalesce(i.import_mode, 'dated') = 'dated'
GROUP BY s.superseded_by_import_id
UNION ALL
SELECT 'needs decision: same-date import #' || t.import_id, min(left(t.transaction_date::text, 10)), count(*)
FROM public.fuel_transactions t JOIN public.fuel_imports i ON i.id = t.import_id
WHERE coalesce(i.import_mode, 'dated') = 'dated' AND t.date_basis IS DISTINCT FROM 'reporting_month_end'
GROUP BY t.import_id
HAVING count(*) >= 2 AND count(DISTINCT t.transaction_date) = 1
ORDER BY 1;
