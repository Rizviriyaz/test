-- =====================================================================================================
-- 02  CREATE public.fuel_monthly_summaries (new objects only; no existing row is read or changed). Repeatable.
--
-- fuel_transactions keeps individual dated refuelling transactions. A monthly summary (one statement line: a vehicle's
-- total for a reporting month, without refuelling dates) is stored here with its reporting_month and NO date column.
-- Column types are copied from the live fuel_transactions / fuel_imports columns (nothing is assumed): the table is
-- created by dynamic SQL from their pg_attribute types. Run in a NEW SQL editor tab (01 set its tab read-only).
-- =====================================================================================================
BEGIN;

DO $$
DECLARE
    v_tx  jsonb;
    v_imp text;
    t     text;
BEGIN
    IF to_regclass('public.fuel_monthly_summaries') IS NOT NULL THEN
        RAISE NOTICE 'public.fuel_monthly_summaries already exists: table creation skipped';
        RETURN;
    END IF;
    SELECT jsonb_object_agg(attname, format_type(atttypid, atttypmod)) INTO v_tx
    FROM pg_attribute WHERE attrelid = 'public.fuel_transactions'::regclass AND attnum > 0 AND NOT attisdropped;
    SELECT format_type(atttypid, atttypmod) INTO v_imp
    FROM pg_attribute WHERE attrelid = 'public.fuel_imports'::regclass AND attname = 'id' AND NOT attisdropped;

    t := format($ddl$
        CREATE TABLE public.fuel_monthly_summaries (
            id                           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            import_id                    %1$s NOT NULL REFERENCES public.fuel_imports(id),
            reporting_month              date NOT NULL,
            vehicle_identifier           %2$s NOT NULL,
            vehicle_key                  text GENERATED ALWAYS AS
                                             (regexp_replace(lower(vehicle_identifier), '[^a-z0-9]', '', 'g')) STORED,
            fuel_type                    %3$s,
            litres                       %4$s NOT NULL,
            unit_price                   %5$s,
            total_cost                   %6$s NOT NULL,
            odometer_km                  %7$s,
            station_name                 %8$s,
            invoice_number               %9$s,
            card_number                  %10$s,
            driver_name                  %11$s,
            reference_number             %12$s,
            notes                        %13$s,
            source_row_number            %14$s,
            staged                       boolean NOT NULL DEFAULT true,
            superseded_by_import_id      %1$s REFERENCES public.fuel_imports(id),
            superseded_at                timestamptz,
            migrated_from_transaction_id %15$s UNIQUE,
            migration_note               text,
            created_at                   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fms_reporting_month_first_day CHECK (reporting_month = date_trunc('month', reporting_month)::date),
            CONSTRAINT fms_vehicle_not_blank CHECK (btrim(vehicle_identifier) <> ''),
            CONSTRAINT fms_supersede_pair CHECK ((superseded_by_import_id IS NULL) = (superseded_at IS NULL)),
            CONSTRAINT fms_not_self_superseded CHECK (superseded_by_import_id IS DISTINCT FROM import_id),
            CONSTRAINT fms_import_source_row UNIQUE (import_id, source_row_number)
        )$ddl$,
        v_imp,
        coalesce(v_tx ->> 'vehicle_identifier', 'text'), coalesce(v_tx ->> 'fuel_type', 'text'),
        coalesce(v_tx ->> 'litres', 'numeric'), coalesce(v_tx ->> 'unit_price', 'numeric'),
        coalesce(v_tx ->> 'total_cost', 'numeric'), coalesce(v_tx ->> 'odometer_km', 'numeric'),
        coalesce(v_tx ->> 'station_name', 'text'), coalesce(v_tx ->> 'invoice_number', 'text'),
        coalesce(v_tx ->> 'card_number', 'text'), coalesce(v_tx ->> 'driver_name', 'text'),
        coalesce(v_tx ->> 'reference_number', 'text'), coalesce(v_tx ->> 'notes', 'text'),
        coalesce(v_tx ->> 'source_row_number', 'integer'), coalesce(v_tx ->> 'id', 'bigint'));
    EXECUTE t;
END $$;

-- The table is new and empty: plain CREATE INDEX holds its lock only briefly
CREATE INDEX IF NOT EXISTS fms_visible_month_vehicle ON public.fuel_monthly_summaries (reporting_month, vehicle_key)
    WHERE superseded_by_import_id IS NULL AND staged = false;
CREATE INDEX IF NOT EXISTS fms_import ON public.fuel_monthly_summaries (import_id);
CREATE INDEX IF NOT EXISTS fms_superseded_by ON public.fuel_monthly_summaries (superseded_by_import_id);

COMMENT ON TABLE public.fuel_monthly_summaries IS
    'Monthly fuel totals per vehicle line of a statement without refuelling dates. Never refuelling events.';
COMMENT ON COLUMN public.fuel_monthly_summaries.reporting_month IS 'First day of the month the total covers';
COMMENT ON COLUMN public.fuel_monthly_summaries.staged IS
    'true = written by an import that has not been committed (hidden); set false only by fuel_commit_import_v2';
COMMENT ON COLUMN public.fuel_monthly_summaries.superseded_by_import_id IS
    'Newer import that replaced this row; NULL = visible. Rows are kept, never deleted';
COMMENT ON COLUMN public.fuel_monthly_summaries.migrated_from_transaction_id IS
    'fuel_transactions.id of the legacy month-end row this was copied from (script 04); NULL for rows imported here';

-- Vehicle key of a fuel_transactions row, identical to the generated column and to the backend's
-- replacement_vehicle_key: lower-case first, then keep only a-z and 0-9 (Python: re.sub("[^a-z0-9]", "", s.lower())).
-- Same order as Python, so a letter whose lower-case form is ASCII is kept by both. Owned by the creating role
-- (postgres in the SQL editor); SECURITY INVOKER with a fixed search_path: it reads no table.
CREATE OR REPLACE FUNCTION public.fuel_vehicle_key(p_identifier text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT regexp_replace(lower(coalesce(p_identifier, '')), '[^a-z0-9]', '', 'g') $$;

-- Audit log of every change script 04 makes to existing rows (read by the rollback, script 99)
CREATE TABLE IF NOT EXISTS public.fuel_migration_audit (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    step       text NOT NULL,
    table_name text NOT NULL,
    row_id     bigint NOT NULL,
    before     jsonb,
    after      jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.fuel_migration_audit ENABLE ROW LEVEL SECURITY;   -- no policies: owner / service role only

COMMIT;

-- Verify (read-only): the created column types, which must match fuel_transactions
SELECT column_name, data_type, udt_name, numeric_precision, numeric_scale, is_nullable, is_generated
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'fuel_monthly_summaries'
ORDER BY ordinal_position;
