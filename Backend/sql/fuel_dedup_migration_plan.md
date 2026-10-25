# Fuel de-duplication migration plan (review only, nothing has been run)

This plan is for whoever applies `Backend/sql/proposed_fuel_dedup.sql` and runs
`Backend/scripts/backfill_fuel_fingerprints.py`. It puts them in order, adds the checks the SQL file lacks, and
says when to stop. **No step in this plan has been executed.** Steps marked WRITE change the database. Run them
only after review, in a maintenance window, with a fresh backup / PITR mark.

`FUEL_SCHEMA_V2` stays **unset** until step 9 passes.

---

## Preconditions (before step 1)

| # | Check | Why |
| --- | --- | --- |
| P1 | The backend containing the Decimal fingerprint fix (`app/fuel/db.py::fingerprint_number`) is deployed, and the backfill runs from that same code. | The backfill must compute exactly what new imports compute. A backfill run with the old float rounding stores different fingerprints for tie values (2.675 → "2.67" instead of "2.68"), so the unique index would not catch re-imports of those rows. |
| P2 | Import #17 has been reconciled (Fuel Performance → import history → Reconcile). This writes only that `fuel_imports` row. | It stops showing `processing` with wrong counts. Its 2,235 stored rows are backfilled like any others. |
| P3 | `SUPABASE_SERVICE_ROLE_KEY` is set in the deployed backend environment. | The cross-instance import guard and the duplicate check read with it, so row-level security cannot hide other users' imports or rows. |
| P4 | No import runs between step 4 and step 9 (announce a freeze). | Rows imported while the flag is off get no fingerprint. They are not protected by the index until backfilled (step 8 re-runs the dry run to catch them). |

---

## Step 1: Read-only diagnostics (run, share results, decide)

Run SQL §0 and these additional queries. All of them are read-only.

```sql
-- 1a. Column types, numeric precision/scale and defaults (§0 lacks scale and defaults)
SELECT table_name, column_name, data_type, udt_name, numeric_precision, numeric_scale,
       datetime_precision, column_default, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name IN ('fuel_transactions', 'fuel_imports')
ORDER BY table_name, ordinal_position;

-- 1b. Session time zone of the API roles (it decides how a naive timestamp is stored in timestamptz)
SELECT rolname, rolconfig FROM pg_roles WHERE rolname IN ('authenticator', 'authenticated', 'service_role', 'anon');
SHOW TimeZone;

-- 1c. Stored values with more decimals than the fingerprint keeps (double-rounding exposure)
SELECT count(*) FILTER (WHERE litres     <> round(litres::numeric, 2))     AS litres_over_2dp,
       count(*) FILTER (WHERE total_cost <> round(total_cost::numeric, 2)) AS cost_over_2dp,
       count(*) FILTER (WHERE odometer_km <> round(odometer_km::numeric, 0)) AS odometer_fractional,
       count(*) FILTER (WHERE transaction_date::text ~ '\.\d') AS dates_with_fraction,
       count(*) AS rows
FROM public.fuel_transactions;

-- 1d. Privileges the import role needs on the new columns (the backend inserts with the user's token)
SELECT has_table_privilege('authenticated', 'public.fuel_transactions', 'INSERT') AS tx_insert,
       has_table_privilege('authenticated', 'public.fuel_transactions', 'SELECT') AS tx_select,
       has_table_privilege('authenticated', 'public.fuel_imports', 'UPDATE')      AS imports_update;
SELECT grantee, privilege_type, column_name FROM information_schema.column_privileges
WHERE table_schema = 'public' AND table_name = 'fuel_transactions' AND grantee IN ('authenticated', 'anon')
ORDER BY grantee, column_name;   -- rows here mean column-level grants are used: new columns need their own GRANT

-- 1e. RLS policies on both tables (INSERT ... WITH CHECK and SELECT policies apply to the new inserts)
SELECT tablename, policyname, cmd, roles, qual, with_check FROM pg_policies
WHERE schemaname = 'public' AND tablename IN ('fuel_transactions', 'fuel_imports');

-- 1f. State of the reported import and of anything else left 'processing'
SELECT id, status, rows_found, rows_imported, rows_duplicate, rows_failed, created_at, completed_at
FROM public.fuel_imports WHERE status = 'processing' OR id = 17 ORDER BY id;
SELECT import_id, count(*) AS rows, count(DISTINCT source_row_number) AS distinct_source_rows
FROM public.fuel_transactions WHERE import_id = 17 GROUP BY import_id;
```

**Decision gate. Stop and redesign if any of these hold:**

- `transaction_date` is `date`. Stored rows lose the time, so the fingerprint of a stored row
  (`…|2026-03-05T00:00:00|…`) never equals the import-time fingerprint (`…|2026-03-05T06:15:00|…`). The unique
  index would not stop re-imports.
- `litres` or `total_cost` has a scale other than 2, or `odometer_km` has a scale above 0, **and** 1c shows values
  beyond the fingerprint precision. Example: in `numeric(12,3)`, a statement value of 135.37499999999997 (a float
  artifact from an Excel formula) is stored as 135.375. The stored row then fingerprints as 135.38 while the
  import computes 135.37. The fix is to align the fingerprint precision with the column scale, or to quantize
  payload values to the scale before insert. That is a code change: report it rather than proceed.
- `transaction_date` is `timestamptz` and 1b shows a session time zone that differs between roles, or one that
  changed over time. The wall clock read back would then differ between rows, so check §0's
  `pg_typeof`/sample output for mixed offsets.
- 1f shows `distinct_source_rows < rows` for any import. That is the same source row stored twice: review it
  before backfilling.

If `numeric_scale` is 2 / 2 / 0 (or integer) and `transaction_date` is `timestamp` or `timestamptz` with a single
UTC session zone, the fingerprint is round-trip stable and you can continue.

---

## Step 2 (WRITE): Add columns, SQL §1 and §2 (columns only)

Run §1 and the `ALTER TABLE fuel_imports ADD COLUMN file_sha256` part of §2. Both are additive, nullable and take
brief locks. Existing rows are unchanged and the app (flag off) does not send the new keys.

- **Grants:** table-level grants cover new columns. If 1d showed column-level grants, add them
  (`GRANT INSERT (fingerprint, transaction_kind, cost_derived, unit_price_derived), SELECT (...) ON
  public.fuel_transactions TO authenticated;` and `GRANT INSERT (file_sha256), SELECT (file_sha256) ON
  public.fuel_imports TO authenticated;`). Do not grant anything to `anon`.
- **PostgREST schema reload:** run `NOTIFY pgrst, 'reload schema';`. Until it reloads, any request that names the
  new columns fails with PGRST204.

Validate (read-only):

```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='fuel_transactions'
  AND column_name IN ('fingerprint','transaction_kind','cost_derived','unit_price_derived');  -- expect 4
SELECT conname FROM pg_constraint WHERE conname = 'fuel_transactions_transaction_kind_check';  -- expect 1
```

## Step 3 (WRITE, optional): `fuel_imports_file_sha256_idx`, SQL §2

`CREATE INDEX CONCURRENTLY` must run as a single statement outside a transaction. Run the validity check
afterwards (`indisvalid = true`).

## Step 4: Backfill dry run (read-only)

From `Backend/` with the service-role key set: `python scripts/backfill_fuel_fingerprints.py`. Record:
rows read (it should equal `SELECT count(*) FROM fuel_transactions`), rows needing a fingerprint, and the number
of duplicate groups. Compare the duplicate groups with the §0 duplicate query. Small differences are expected
where the app's wall-clock and vehicle normalization differ from the SQL (offsets dropped, whitespace trimmed).
Explain any large difference before continuing.

## Step 5: Duplicate review, SQL §3 preview (read-only)

Review the cross-import duplicate preview. **Nothing has to be deleted for the index to build:** the backfill
gives each duplicate after the first the suffix `|dup:<id>`. Deleting is a separate business decision. If you
decide to delete, use only the commented §3 procedure (backup into the private `fuel_backup` schema, count checks,
single transaction).

## Step 6 (WRITE): Backfill apply

Run `python scripts/backfill_fuel_fingerprints.py --apply`. It patches only rows whose `fingerprint IS NULL`, so it
can be re-run safely, and it exits 1 if any write failed (re-run to retry).

Validate (read-only):

```sql
SELECT count(*) FILTER (WHERE fingerprint IS NULL) AS missing, count(*) AS rows FROM public.fuel_transactions;  -- missing = 0
SELECT fingerprint, count(*) FROM public.fuel_transactions
WHERE fingerprint IS NOT NULL GROUP BY 1 HAVING count(*) > 1;                                                 -- 0 rows
SELECT transaction_kind, count(*) FROM public.fuel_transactions GROUP BY 1;                                    -- no NULL
```

## Step 7 (WRITE): Unique index, SQL §5

Run `CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS fuel_transactions_fingerprint_uidx ON
public.fuel_transactions (fingerprint);` as a single statement. Then run the §5 validity check: you need exactly
one row with `indisvalid = true`. If it is invalid, follow the §5 recovery steps (drop concurrently, fix, re-create).
Afterwards run `NOTIFY pgrst, 'reload schema';`.

## Step 8: Final pre-flag checks (read-only)

- Re-run the backfill **dry run**. It must report 0 rows needing a fingerprint. Rows imported during the window
  would show up here: if any do, run `--apply` again, then repeat the duplicate query from step 6.
- Confirm the index is valid and unique:
  `SELECT indisunique, indisvalid FROM pg_index WHERE indexrelid = 'public.fuel_transactions_fingerprint_uidx'::regclass;`

## Step 9: Enable `FUEL_SCHEMA_V2=1` and verify on staging first

Set the flag on a staging/preview deployment against the migrated database. Import a small known statement
twice. The first import stores its rows. The second returns HTTP 409 (identical file), or, after a one-byte change
to the file, reports every row as a duplicate. Then enable the flag in production and lift the freeze.

---

## Behaviour notes the reviewer should know

- **Rounding:** fingerprints round half away from zero on the decimal value (Python `Decimal`, `ROUND_HALF_UP`).
  That is what `numeric(p, s)` storage and `round(numeric, s)` do, so §0's `round(...)` grouping and the app agree.
- **Timestamps:** fingerprints use the wall clock exactly as written, and any offset PostgREST returns is dropped,
  not converted. Midnight and date-only values are the same. The app's own pre-insert check also treats a
  date-only row as matching any time that day. The unique index does not: it is exact.
- **Concurrency:** with the flag off, the app now claims each import in `fuel_imports` before its duplicate check,
  so two instances cannot both insert. A `processing` record younger than `FUEL_IMPORT_STALE_SECONDS` (900 s)
  blocks new imports with 409. With the flag on, the unique index is the authoritative guard.

## Rollback and recovery

- **Before step 7:** the flag is still off, so the app does not use the new columns. Drop them with §6b if you
  want to.
- **After step 7, before the flag:** `DROP INDEX CONCURRENTLY public.fuel_transactions_fingerprint_uidx;` (single
  statement), then reload the PostgREST schema.
- **After the flag:** unset `FUEL_SCHEMA_V2` and redeploy **first**. With the flag on and the index gone, every
  import fails with 42P10. Then apply §6b, then `NOTIFY pgrst, 'reload schema';`. Dropping the columns while the
  flag is on breaks imports with PGRST204.
- **If rows were deleted in §3:** restore with §6a **before** §6b (the column lists must still match).
- **A backfill that was interrupted** leaves some rows NULL. That is harmless, and re-running `--apply` completes it.
- **Interrupted imports during any step** leave `processing` records. Reconcile them after 15 minutes. Reconcile
  writes only the `fuel_imports` row.
