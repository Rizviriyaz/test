-- =====================================================================================================
-- 02b  REALIGN THE EXISTING fuel_monthly_summaries WITH THE CURRENT DEFINITION (write; only when 02a says DIFFERS).
--      Safe to run when nothing differs: every step checks first and then does nothing.
--
-- Why: script 02 skips creating the table when it already exists, so a table created by an earlier version of 02
-- keeps that version's vehicle_key expression ("strip, then lower-case") while the backend and fuel_vehicle_key use
-- "lower-case, then strip". The two agree for ASCII identifiers; this makes them identical for every identifier.
--   * vehicle_key: a generated column cannot be altered in place on every server version, so it is dropped and
--     re-added with the current expression (stored values are recomputed from vehicle_identifier; no source data
--     changes). Its partial index is recreated. Nothing else references the column by object dependency
--     (fuel_commit_import_v2 names it inside its body, resolved at call time).
--   * fuel_vehicle_key(text): replaced with the current body (its grants are kept by CREATE OR REPLACE).
-- One transaction: it completes fully or changes nothing. Freeze imports while it runs.
-- =====================================================================================================
BEGIN;

DO $$
DECLARE
    v_expr text;
BEGIN
    IF to_regclass('public.fuel_monthly_summaries') IS NULL THEN
        RAISE EXCEPTION 'public.fuel_monthly_summaries does not exist: run 02 first (nothing was changed)';
    END IF;
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO v_expr
    FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = 'public.fuel_monthly_summaries'::regclass AND a.attname = 'vehicle_key' AND a.attgenerated = 's';

    IF v_expr LIKE 'regexp_replace(lower(vehicle_identifier)%' THEN
        RAISE NOTICE 'vehicle_key already uses the current expression: unchanged';
    ELSE
        LOCK TABLE public.fuel_monthly_summaries IN ACCESS EXCLUSIVE MODE;
        IF v_expr IS NOT NULL OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'public.fuel_monthly_summaries'::regclass
                                                                   AND attname = 'vehicle_key' AND NOT attisdropped) THEN
            ALTER TABLE public.fuel_monthly_summaries DROP COLUMN vehicle_key;   -- also drops fms_visible_month_vehicle
        END IF;
        ALTER TABLE public.fuel_monthly_summaries
            ADD COLUMN vehicle_key text GENERATED ALWAYS AS
                (regexp_replace(lower(vehicle_identifier), '[^a-z0-9]', '', 'g')) STORED;
        RAISE NOTICE 'vehicle_key re-created with the current expression (was: %)', coalesce(v_expr, 'missing');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS fms_visible_month_vehicle ON public.fuel_monthly_summaries (reporting_month, vehicle_key)
    WHERE superseded_by_import_id IS NULL AND staged = false;

CREATE OR REPLACE FUNCTION public.fuel_vehicle_key(p_identifier text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT regexp_replace(lower(coalesce(p_identifier, '')), '[^a-z0-9]', '', 'g') $$;

COMMIT;

-- Verify (read-only): expect the 'regexp_replace(lower(vehicle_identifier) ...' expression and the index
SELECT (SELECT pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        WHERE d.adrelid = 'public.fuel_monthly_summaries'::regclass AND a.attname = 'vehicle_key') AS vehicle_key_expression,
       to_regclass('public.fms_visible_month_vehicle') IS NOT NULL AS index_present,
       (SELECT prosrc FROM pg_proc WHERE oid = to_regprocedure('public.fuel_vehicle_key(text)')) AS fuel_vehicle_key_body;
