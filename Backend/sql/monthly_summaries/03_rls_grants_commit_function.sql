-- =====================================================================================================
-- 03  ROW-LEVEL SECURITY, GRANTS AND THE ATOMIC COMMIT FUNCTION fuel_commit_import_v2. Repeatable.
-- New objects only; no existing row and no existing policy or grant is changed. Run after 02, in a NEW SQL editor
-- tab (not the read-only tab of 01). The whole script is ONE transaction: if any statement fails, nothing of it
-- remains (no half-created policy, function or grant).
--
-- How the backend calls the database (Backend/app/fuel/db.py):
--   * staged rows are inserted with the importing user's JWT (get_headers(bearer_token)): role 'authenticated',
--     row-level security applies, auth.uid() is the importing user;
--   * the commit RPC is called with the same user JWT: fuel_commit_import_v2 checks auth.uid(), the user's
--     profiles.role (the backend's FUEL_IMPORT_ROLES: admin, manager, fleet_manager) and that the import is theirs.
--     Called with the service-role key (no user) auth.uid() is NULL and the function refuses: it fails closed;
--   * reads use the service-role key when it is configured, else the user's JWT.
-- Existing tables keep their policies; fuel_transactions has no UPDATE policy, which is why the commit functions
-- are SECURITY DEFINER (owner postgres, like the installed fuel_commit_import). Narrowing the existing tables'
-- broad default grants is optional hardening, listed at the end, commented out.
-- =====================================================================================================
BEGIN;

-- -----------------------------------------------------------------------------------------------------
-- §0  Preconditions (abort before anything is created).
--   a) Time zone. The function inherits the API session's TimeZone, so a stored timestamptz is read as the same
--      wall-clock day the backend read through PostgREST; scripts 04/05 use UTC. The API session's zone is the
--      server default unless a setting for the database, for the login role (authenticator), or for a role in this
--      database overrides it. This checks every such setting in pg_db_role_setting (any role, all databases or this
--      one) and this session's own zone: run from the SQL editor (role postgres), with no override found, this
--      session's zone is the server default. NOT covered (not visible from SQL): a PostgREST pre-request function
--      or a per-request 'Prefer: timezone' header; the backend sends no such header (Backend/app/fuel/db.py), and
--      02a reports a configured pre-request function.
--   b) vehicle_key of the existing table uses the current expression (02a / 02b).
-- -----------------------------------------------------------------------------------------------------
DO $$
DECLARE
    v_utc  text[] := ARRAY['utc', 'etc/utc', 'gmt', 'etc/gmt', 'z', 'uct', 'zulu', 'universal', 'etc/universal'];
    v_bad  text;
    v_expr text;
BEGIN
    SELECT string_agg(coalesce(r.rolname, '(every role)')
                      || CASE WHEN d.setdatabase = 0 THEN '' ELSE ' in this database' END || ': ' || s, ', ') INTO v_bad
    FROM pg_db_role_setting d
    LEFT JOIN pg_roles r ON r.oid = d.setrole
    CROSS JOIN LATERAL unnest(d.setconfig) AS s
    WHERE (d.setdatabase = 0 OR d.setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database()))
      AND s ILIKE 'timezone=%'
      AND lower(split_part(s, '=', 2)) <> ALL (v_utc);
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'A non-UTC time zone is configured (%); the date rules of scripts 03-05 assume UTC. Nothing was changed.', v_bad;
    END IF;
    IF lower(current_setting('TimeZone')) <> ALL (v_utc) THEN
        RAISE EXCEPTION 'This session''s time zone is % (server default), not UTC. Nothing was changed.', current_setting('TimeZone');
    END IF;

    SELECT pg_get_expr(d.adbin, d.adrelid) INTO v_expr
    FROM pg_attrdef d JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = 'public.fuel_monthly_summaries'::regclass AND a.attname = 'vehicle_key' AND a.attgenerated = 's';
    IF v_expr IS NULL OR v_expr NOT LIKE 'regexp_replace(lower(vehicle_identifier)%' THEN
        RAISE EXCEPTION 'fuel_monthly_summaries.vehicle_key is % (expected the current expression): run 02b first. Nothing was changed.',
            coalesce(v_expr, 'missing');
    END IF;
END $$;

-- -----------------------------------------------------------------------------------------------------
-- §1  ROW-LEVEL SECURITY AND GRANTS for the new table
--     API users may read summaries and insert staged rows of their own running import; they can never update or
--     delete. staged / superseded change only inside fuel_commit_import_v2 (SECURITY DEFINER).
-- -----------------------------------------------------------------------------------------------------
ALTER TABLE public.fuel_monthly_summaries ENABLE ROW LEVEL SECURITY;

-- Whether the signed-in user may stage rows for an import: their own, still 'processing'. SECURITY DEFINER so the
-- answer does not depend on fuel_imports' own row-level security (which might hide the row from the user).
CREATE OR REPLACE FUNCTION public.fuel_import_accepts_rows(p_import_id bigint) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (SELECT 1 FROM public.fuel_imports i
                   WHERE i.id = p_import_id AND i.imported_by = auth.uid() AND i.status = 'processing')
$$;
ALTER FUNCTION public.fuel_import_accepts_rows(bigint) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.fuel_import_accepts_rows(bigint) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fuel_import_accepts_rows(bigint) TO authenticated;

DROP POLICY IF EXISTS fms_select_authenticated ON public.fuel_monthly_summaries;
CREATE POLICY fms_select_authenticated ON public.fuel_monthly_summaries
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS fms_insert_own_import ON public.fuel_monthly_summaries;
CREATE POLICY fms_insert_own_import ON public.fuel_monthly_summaries
    FOR INSERT TO authenticated
    WITH CHECK (
        staged IS TRUE AND superseded_by_import_id IS NULL AND superseded_at IS NULL
        AND migrated_from_transaction_id IS NULL AND migration_note IS NULL
        AND public.fuel_import_accepts_rows(import_id)
    );

-- Supabase's default privileges grant new public tables to anon and authenticated: narrow them explicitly.
-- authenticated: SELECT (PostgREST returns inserted rows: Prefer return=representation) and INSERT only.
-- service_role: SELECT (backend reads and write read-backs). The identity sequence gets USAGE so an insert can draw
-- an id whatever the server's rule for identity columns.
REVOKE ALL ON TABLE public.fuel_monthly_summaries FROM anon, authenticated, service_role;
GRANT SELECT, INSERT ON TABLE public.fuel_monthly_summaries TO authenticated;
GRANT SELECT ON TABLE public.fuel_monthly_summaries TO service_role;
DO $$
DECLARE
    v_seq text := pg_get_serial_sequence('public.fuel_monthly_summaries', 'id');   -- already quoted / qualified
BEGIN
    IF v_seq IS NULL OR to_regclass(v_seq) IS NULL THEN
        RAISE EXCEPTION 'fuel_monthly_summaries.id has no identity sequence (see 02a item 60); nothing was changed';
    END IF;
    EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM anon', v_seq);
    EXECUTE format('GRANT USAGE ON SEQUENCE %s TO authenticated', v_seq);
END $$;

REVOKE ALL ON FUNCTION public.fuel_vehicle_key(text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.fuel_vehicle_key(text) TO authenticated, service_role;
REVOKE ALL ON TABLE public.fuel_migration_audit FROM anon, authenticated;


-- -----------------------------------------------------------------------------------------------------
-- §2  COMMIT FUNCTION v2 (called by the backend through PostgREST: POST /rest/v1/rpc/fuel_commit_import_v2)
--     Replaces fuel_commit_import for every import once FUEL_MONTHLY_SUMMARY_TABLE is set. One transaction: either
--     every check passes and all updates apply, or an exception is raised and nothing changes. It takes the SAME
--     advisory lock as fuel_commit_import, so commits through v1 and v2 never interleave.
--
--     Checks (all must pass):
--       1. The caller is signed in with role admin, manager or fleet_manager (the backend's FUEL_IMPORT_ROLES).
--       2. The import exists and was created by the caller.
--       3. Its staged rows are in its own table only (monthly_summary -> fuel_monthly_summaries, else
--          fuel_transactions), and exactly p_expected_rows rows are staged. The backend sends the number of rows it
--          stored (skipped, unchanged and failed rows are not stored) and calls only when every row to store was
--          stored, so a partial import never becomes visible.
--       4. Every row to supersede belongs to another import, is visible, lies in the import's replace_scope period
--          (period_from to the end of period_to's month, the window the backend read; ±1 day as in
--          fuel_commit_import) and is for one of the file's vehicles (replace_scope.vehicle_keys, written by the
--          backend with the same key rule as fuel_vehicle_key; else the vehicles of the staged rows). Transactions
--          superseded are dated rows only: summaries are superseded in fuel_monthly_summaries. A dated import may
--          not supersede monthly summaries.
--       5. Afterwards, for every vehicle-month in which the import has visible rows: monthly summaries of at most
--          one import are visible, and no visible dated transaction exists next to a visible monthly summary.
--          (Legacy month-end rows still in fuel_transactions are not dated rows.) Vehicle identity here is the
--          normalised key; identifiers with different keys that the dashboard resolves to one Wialon unit are
--          resolved at read time (the monthly summary takes precedence), since Wialon units are not in the database.
--     Days: transaction_date::date in the API session's time zone, the wall-clock day the backend read.
-- -----------------------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fuel_commit_import_v2(
    p_import_id               bigint,
    p_supersede_tx_ids        bigint[],
    p_supersede_summary_ids   bigint[],
    p_expected_rows           integer
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_uid        uuid := auth.uid();
    v_role       text;
    v_import     public.fuel_imports%ROWTYPE;
    v_monthly    boolean;
    v_from       date;
    v_to         date;
    v_keys       text[];
    v_tx_ids     bigint[] := ARRAY(SELECT DISTINCT unnest(coalesce(p_supersede_tx_ids, '{}')));
    v_sum_ids    bigint[] := ARRAY(SELECT DISTINCT unnest(coalesce(p_supersede_summary_ids, '{}')));
    v_staged_tx  integer;
    v_staged_sum integer;
    v_allowed    integer;
    v_sup_tx     integer;
    v_sup_sum    integer;
    v_com_tx     integer;
    v_com_sum    integer;
    v_conflict   text;
BEGIN
    -- 1. Signed-in user with an import role
    IF v_uid IS NULL THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: not signed in' USING ERRCODE = '42501';
    END IF;
    SELECT role INTO v_role FROM public.profiles WHERE id = v_uid;
    IF v_role IS NULL OR v_role NOT IN ('admin', 'manager', 'fleet_manager') THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: role % may not import fuel statements', coalesce(v_role, 'none')
            USING ERRCODE = '42501';
    END IF;
    IF p_expected_rows IS NULL OR p_expected_rows < 0 THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: invalid expected row count %', p_expected_rows;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('public.fuel_commit_import'));

    -- 2. The caller's own import
    SELECT * INTO v_import FROM public.fuel_imports WHERE id = p_import_id FOR UPDATE;
    IF NOT FOUND OR v_import.imported_by IS DISTINCT FROM v_uid THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: import % is not an import of the caller', p_import_id
            USING ERRCODE = '42501';
    END IF;
    v_monthly := v_import.import_mode = 'monthly_summary';

    -- 3. Staged rows in the import's own table, exactly as many as written
    SELECT count(*) INTO v_staged_tx FROM public.fuel_transactions WHERE import_id = p_import_id AND staged IS TRUE;
    SELECT count(*) INTO v_staged_sum FROM public.fuel_monthly_summaries WHERE import_id = p_import_id AND staged IS TRUE;
    IF (v_monthly AND v_staged_tx <> 0) OR (NOT v_monthly AND v_staged_sum <> 0) THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: import % has staged rows in the wrong table', p_import_id;
    END IF;
    IF v_staged_tx + v_staged_sum <> p_expected_rows THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: % staged row(s) for import %, expected %',
            v_staged_tx + v_staged_sum, p_import_id, p_expected_rows;
    END IF;
    IF NOT v_monthly AND cardinality(v_sum_ids) > 0 THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: a dated import cannot supersede monthly summaries';
    END IF;

    -- 4. Rows to supersede: inside the recorded scope, for the file's vehicles, still visible
    IF cardinality(v_tx_ids) + cardinality(v_sum_ids) > 0 THEN
        v_from := (v_import.replace_scope ->> 'period_from')::date;
        v_to   := (v_import.replace_scope ->> 'period_to')::date;
        IF v_from IS NULL OR v_to IS NULL THEN
            RAISE EXCEPTION 'fuel_commit_import_v2: import % has no replacement scope', p_import_id;
        END IF;
        v_to := (date_trunc('month', v_to) + interval '1 month - 1 day')::date;  -- end of its last month
        IF jsonb_typeof(v_import.replace_scope -> 'vehicle_keys') = 'array' THEN
            v_keys := ARRAY(SELECT jsonb_array_elements_text(v_import.replace_scope -> 'vehicle_keys'));
        ELSE
            v_keys := ARRAY(
                SELECT public.fuel_vehicle_key(vehicle_identifier) FROM public.fuel_transactions
                WHERE import_id = p_import_id AND staged IS TRUE
                UNION
                SELECT vehicle_key FROM public.fuel_monthly_summaries WHERE import_id = p_import_id AND staged IS TRUE);
        END IF;

        SELECT count(*) INTO v_allowed
        FROM public.fuel_transactions t
        WHERE t.id = ANY (v_tx_ids)
          AND t.import_id IS DISTINCT FROM p_import_id
          AND t.superseded_by_import_id IS NULL AND t.staged IS NOT TRUE
          AND t.date_basis IS DISTINCT FROM 'reporting_month_end'
          AND t.transaction_date::date >= v_from - 1
          AND t.transaction_date::date < v_to + 2
          AND public.fuel_vehicle_key(t.vehicle_identifier) = ANY (v_keys);
        IF v_allowed <> cardinality(v_tx_ids) THEN
            RAISE EXCEPTION 'fuel_commit_import_v2: transaction scope changed or out of bounds (% of % rows allowed)',
                v_allowed, cardinality(v_tx_ids);
        END IF;

        SELECT count(*) INTO v_allowed
        FROM public.fuel_monthly_summaries s
        WHERE s.id = ANY (v_sum_ids)
          AND s.import_id IS DISTINCT FROM p_import_id
          AND s.superseded_by_import_id IS NULL AND s.staged IS NOT TRUE
          AND s.reporting_month BETWEEN date_trunc('month', v_from)::date AND v_to
          AND s.vehicle_key = ANY (v_keys);
        IF v_allowed <> cardinality(v_sum_ids) THEN
            RAISE EXCEPTION 'fuel_commit_import_v2: summary scope changed or out of bounds (% of % rows allowed)',
                v_allowed, cardinality(v_sum_ids);
        END IF;
    END IF;

    UPDATE public.fuel_transactions
    SET superseded_by_import_id = p_import_id, superseded_at = now()
    WHERE id = ANY (v_tx_ids) AND superseded_by_import_id IS NULL AND staged IS NOT TRUE;
    GET DIAGNOSTICS v_sup_tx = ROW_COUNT;

    UPDATE public.fuel_monthly_summaries
    SET superseded_by_import_id = p_import_id, superseded_at = now()
    WHERE id = ANY (v_sum_ids) AND superseded_by_import_id IS NULL AND staged IS NOT TRUE;
    GET DIAGNOSTICS v_sup_sum = ROW_COUNT;

    UPDATE public.fuel_transactions SET staged = false WHERE import_id = p_import_id AND staged IS TRUE;
    GET DIAGNOSTICS v_com_tx = ROW_COUNT;

    UPDATE public.fuel_monthly_summaries SET staged = false WHERE import_id = p_import_id AND staged IS TRUE;
    GET DIAGNOSTICS v_com_sum = ROW_COUNT;

    IF v_sup_tx <> cardinality(v_tx_ids) OR v_sup_sum <> cardinality(v_sum_ids)
       OR v_com_tx + v_com_sum <> v_staged_tx + v_staged_sum THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: updated %/% + %/% superseded and %/% staged rows; nothing changed',
            v_sup_tx, cardinality(v_tx_ids), v_sup_sum, cardinality(v_sum_ids), v_com_tx + v_com_sum,
            v_staged_tx + v_staged_sum;
    END IF;

    -- 5. No vehicle-month this import has visible rows in may count monthly and daily fuel together, or two summaries
    WITH vm AS (
        SELECT DISTINCT public.fuel_vehicle_key(t.vehicle_identifier) AS k,
               date_trunc('month', t.transaction_date::date)::date AS m
        FROM public.fuel_transactions t
        WHERE t.import_id = p_import_id AND t.superseded_by_import_id IS NULL AND t.staged IS NOT TRUE
        UNION
        SELECT DISTINCT s.vehicle_key, s.reporting_month
        FROM public.fuel_monthly_summaries s
        WHERE s.import_id = p_import_id AND s.superseded_by_import_id IS NULL AND s.staged IS NOT TRUE
    )
    SELECT vm.k || ' ' || to_char(vm.m, 'YYYY-MM') INTO v_conflict
    FROM vm
    WHERE (SELECT count(DISTINCT s.import_id) FROM public.fuel_monthly_summaries s
           WHERE s.vehicle_key = vm.k AND s.reporting_month = vm.m
             AND s.superseded_by_import_id IS NULL AND s.staged IS NOT TRUE) > 1
       OR (EXISTS (SELECT 1 FROM public.fuel_monthly_summaries s
                   WHERE s.vehicle_key = vm.k AND s.reporting_month = vm.m
                     AND s.superseded_by_import_id IS NULL AND s.staged IS NOT TRUE)
           AND EXISTS (SELECT 1 FROM public.fuel_transactions t
                       WHERE public.fuel_vehicle_key(t.vehicle_identifier) = vm.k
                         AND t.transaction_date::date >= vm.m
                         AND t.transaction_date::date < (vm.m + interval '1 month')::date
                         AND t.superseded_by_import_id IS NULL AND t.staged IS NOT TRUE
                         AND t.date_basis IS DISTINCT FROM 'reporting_month_end'))
    LIMIT 1;
    IF v_conflict IS NOT NULL THEN
        RAISE EXCEPTION 'fuel_commit_import_v2: vehicle-month % would count a monthly summary with dated rows or '
            'with another summary; nothing changed', v_conflict;
    END IF;

    RETURN jsonb_build_object('superseded', v_sup_tx + v_sup_sum, 'committed', v_com_tx + v_com_sum);
END;
$$;

ALTER FUNCTION public.fuel_commit_import_v2(bigint, bigint[], bigint[], integer) OWNER TO postgres;
REVOKE ALL ON FUNCTION public.fuel_commit_import_v2(bigint, bigint[], bigint[], integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.fuel_commit_import_v2(bigint, bigint[], bigint[], integer) TO authenticated;

NOTIFY pgrst, 'reload schema';   -- delivered when the transaction commits

COMMIT;


-- -----------------------------------------------------------------------------------------------------
-- Verify (read-only; after COMMIT). Expect four functions (fuel_commit_import, fuel_commit_import_v2,
-- fuel_import_accepts_rows, fuel_vehicle_key); the last three owned by postgres, v2 and fuel_import_accepts_rows
-- SECURITY DEFINER with EXECUTE for authenticated only; two policies on fuel_monthly_summaries. The SQL editor shows
-- only the last result (the policies); run the first SELECT on its own to see the functions.
-- -----------------------------------------------------------------------------------------------------
SELECT p.oid::regprocedure AS signature, p.prosecdef AS security_definer, p.proowner::regrole AS owner,
       p.proacl::text AS acl, p.proconfig AS config
FROM pg_proc p
WHERE p.oid IN (to_regprocedure('public.fuel_commit_import_v2(bigint,bigint[],bigint[],integer)'),
                to_regprocedure('public.fuel_commit_import(bigint,bigint[],integer)'),
                to_regprocedure('public.fuel_import_accepts_rows(bigint)'),
                to_regprocedure('public.fuel_vehicle_key(text)'));
SELECT policyname, cmd, roles::text, qual, with_check FROM pg_policies WHERE tablename = 'fuel_monthly_summaries';


-- -----------------------------------------------------------------------------------------------------
-- OPTIONAL hardening of the EXISTING tables (not needed for this feature; review diagnostics C2 first). The app
-- never uses anon for fuel data, so removing anon's privileges has no application impact:
-- REVOKE ALL ON TABLE public.fuel_transactions, public.fuel_imports FROM anon;
-- -----------------------------------------------------------------------------------------------------
