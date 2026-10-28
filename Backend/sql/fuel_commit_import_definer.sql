-- =====================================================================================================
-- REVIEW ONLY — not executed by the app or any script.
-- Replaces §3 of fuel_import_replacement.sql (run this INSTEAD of §3, after §1 and §2 succeeded).
--
-- Why: fuel_transactions has RLS with SELECT/INSERT policies for 'authenticated' but no UPDATE policy. The §3
-- function is SECURITY INVOKER, so its UPDATEs see no rows, its own count check raises, and every commit rolls
-- back: no monthly summary (or replacing dated import) could ever become visible. This version runs with the
-- owner's rights for exactly two UPDATEs and checks the caller itself, instead of a broad UPDATE policy.
--
-- Authorization enforced inside the function (all must pass, or an error is raised and nothing changes):
--   1. The caller is a signed-in user (auth.uid() is not null).
--   2. Their profiles.role is admin, manager or fleet_manager (the backend's FUEL_IMPORT_ROLES).
--   3. The import p_import_id exists and was created by that user (fuel_imports.imported_by = auth.uid()).
--   4. Every row to supersede: belongs to another import, is visible, lies inside the import's recorded
--      replace_scope period (to the end of its last month, ±1 day for time zones), and is for a vehicle that
--      occurs among this import's own staged rows (same letters/digits key as the backend).
--   5. Exactly p_expected_rows rows of this import are staged (a partial import is never made visible).
-- EXECUTE is granted to 'authenticated' only (not anon/PUBLIC). RLS policies are not changed.
--
-- Before running, check (read-only):
--   * The owner below must own the tables (RLS does not apply to the table owner) and FORCE ROW LEVEL SECURITY
--     must be off:  SELECT relname, relowner::regrole, relforcerowsecurity FROM pg_class
--                   WHERE relname IN ('fuel_transactions', 'fuel_imports');
--   * profiles has columns id (= auth user id) and role; fuel_imports.imported_by is uuid;
--     fuel_transactions.transaction_date is date or timestamptz (not text).
--   * No other overload exists:  SELECT oid::regprocedure FROM pg_proc WHERE proname = 'fuel_commit_import';
-- Rollback: re-run §3 of fuel_import_replacement.sql (invoker version), or DROP FUNCTION (see §6 there).
-- =====================================================================================================

CREATE OR REPLACE FUNCTION public.fuel_commit_import(
    p_import_id      bigint,
    p_supersede_ids  bigint[],
    p_expected_rows  integer
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_uid        uuid := auth.uid();
    v_role       text;
    v_import     public.fuel_imports%ROWTYPE;
    v_from       date;
    v_to         date;
    v_ids        bigint[] := ARRAY(SELECT DISTINCT unnest(coalesce(p_supersede_ids, '{}')));
    v_allowed    integer;
    v_staged     integer;
    v_superseded integer;
    v_committed  integer;
BEGIN
    -- 1-2. Signed-in user with an import role
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'fuel_commit_import: not signed in' USING ERRCODE = '42501';
    END IF;
    SELECT role INTO v_role FROM public.profiles WHERE id = v_uid;
    IF v_role IS NULL OR v_role NOT IN ('admin', 'manager', 'fleet_manager') THEN
        RAISE EXCEPTION 'fuel_commit_import: role % may not import fuel statements', coalesce(v_role, 'none')
            USING ERRCODE = '42501';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('public.fuel_commit_import'));

    -- 3. The caller's own import
    SELECT * INTO v_import FROM public.fuel_imports WHERE id = p_import_id FOR UPDATE;
    IF NOT FOUND OR v_import.imported_by IS DISTINCT FROM v_uid THEN
        RAISE EXCEPTION 'fuel_commit_import: import % is not an import of the caller', p_import_id
            USING ERRCODE = '42501';
    END IF;

    -- 5. Exactly the rows the import wrote are staged
    SELECT count(*) INTO v_staged
    FROM public.fuel_transactions WHERE import_id = p_import_id AND staged IS TRUE;
    IF v_staged <> p_expected_rows THEN
        RAISE EXCEPTION 'fuel_commit_import: % staged row(s) for import %, expected %',
            v_staged, p_import_id, p_expected_rows;
    END IF;

    -- 4. Rows to supersede: inside the recorded scope, for this import's vehicles, still visible
    IF cardinality(v_ids) > 0 THEN
        v_from := (v_import.replace_scope ->> 'period_from')::date;
        v_to   := (v_import.replace_scope ->> 'period_to')::date;
        IF v_from IS NULL OR v_to IS NULL THEN
            RAISE EXCEPTION 'fuel_commit_import: import % has no replacement scope', p_import_id;
        END IF;
        v_to := (date_trunc('month', v_to) + interval '1 month - 1 day')::date;  -- end of its last month

        SELECT count(*) INTO v_allowed
        FROM public.fuel_transactions t
        WHERE t.id = ANY (v_ids)
          AND t.import_id IS DISTINCT FROM p_import_id
          AND t.superseded_by_import_id IS NULL AND t.staged IS NOT TRUE
          AND t.transaction_date >= v_from - 1 AND t.transaction_date < v_to + 2
          AND lower(regexp_replace(coalesce(t.vehicle_identifier, ''), '[^A-Za-z0-9]', '', 'g')) IN (
              SELECT lower(regexp_replace(coalesce(s.vehicle_identifier, ''), '[^A-Za-z0-9]', '', 'g'))
              FROM public.fuel_transactions s WHERE s.import_id = p_import_id AND s.staged IS TRUE);
        IF v_allowed <> cardinality(v_ids) THEN
            RAISE EXCEPTION 'fuel_commit_import: replacement scope changed or out of bounds (% of % rows allowed)',
                v_allowed, cardinality(v_ids);
        END IF;
    END IF;

    UPDATE public.fuel_transactions
    SET superseded_by_import_id = p_import_id, superseded_at = now()
    WHERE id = ANY (v_ids) AND superseded_by_import_id IS NULL AND staged IS NOT TRUE;
    GET DIAGNOSTICS v_superseded = ROW_COUNT;

    UPDATE public.fuel_transactions SET staged = false WHERE import_id = p_import_id AND staged IS TRUE;
    GET DIAGNOSTICS v_committed = ROW_COUNT;

    IF v_superseded <> cardinality(v_ids) OR v_committed <> v_staged THEN
        RAISE EXCEPTION 'fuel_commit_import: updated %/% superseded and %/% staged rows; nothing changed',
            v_superseded, cardinality(v_ids), v_committed, v_staged;
    END IF;

    RETURN jsonb_build_object('superseded', v_superseded, 'committed', v_committed);
END;
$$;

ALTER FUNCTION public.fuel_commit_import(bigint, bigint[], integer) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) TO authenticated;

NOTIFY pgrst, 'reload schema';

-- Verify (read-only): prosecdef = true, owner postgres, EXECUTE for authenticated only
-- SELECT proname, prosecdef, proowner::regrole, proacl FROM pg_proc WHERE proname = 'fuel_commit_import';
