# Test Traps

> **Purpose:** Hard-won traps for writing, auditing and running tests in this repository.
> **Audience:** Anyone writing or reviewing tests, or interpreting a red or green gate.
> **Prerequisites:** [Testing](index.md), [CI and test scope policy](ci.md).

Moved verbatim from `AGENTS.md` (Test traps). `AGENTS.md` keeps the three cross-cutting rules.

- A test needing another butler's table builds it from `src/butlers/testing/schema_standins.py`
  (e.g. `CONNECTOR_REGISTRY.ddl(schema="switchboard")`), never a local `CREATE TABLE`: a hand-copied
  column list goes stale and the route degrades far from the cause.
  `tests/config/test_schema_standin_parity.py` enforces it (`# schema-standin-exempt: <why>` opts a
  non-query fixture out).
- Test counts: always state the scope beside the number (`tests/` and `tests/ roster/` differ by
  thousands). Anchor the grep as `^[[:space:]]*(async[[:space:]]+)?def[[:space:]]+test_` to exclude
  commented-out and in-string matches.

- Ask of every check whether it could have failed for the reason its name claims. The commonest
  defect here is a check credited with an answer it was never positioned to give: a retry gated on
  a status code its tolerated errors never carry, a fixture laxer than the table it stands in for, a
  snapshot recorded against mock data.
- Read reds from a falsification run individually: `assert True is False` against the old code is
  evidence, while `AttributeError: ... does not have the attribute '<new_helper>'` only shows the
  test patches a symbol that does not exist yet.
- A forced-quiet or fail-open green needs a control run that goes red. Example: without
  `public.approvals_policy`, `get_approvals_policy_quiet_hours` returns `None` and quiet hours never
  fire, so a widened-window green proves nothing without a red run inside the window.
- `scripts/init-db.sql` (fresh bootstrap) and `alembic/versions/core/` (incremental) drift;
  `public.audit_log` exists only in alembic. Several tests hand-roll `audit_log` DDL: when mirroring
  a migration into one, copy the CHECK constraint too. Fixtures that model pre-migration schemas
  (the audit_log backfill/repair migration tests) are deliberate.
- `app.dependency_overrides[get_x]` intercepts only `Depends(get_x)`; a wrapper that calls `get_x()`
  directly ignores the override.
- If a SQL predicate is what excludes wrong rows (`WHERE 'owner' = ANY(roles)`), a canned-row mock
  cannot test it: read the columns and filter in Python so a mock can return a wrong row and the
  code still rejects it.
- A PR that remaps top-level frontend routes must grep `frontend/tests/e2e/` for pinned `goto(` and
  URL regexes; only the CI e2e job catches them.
- Initialise `time.monotonic()` freshness markers to `float("-inf")`, not `0.0`: fresh CI runners
  have small monotonic clocks, so `0.0` reads as fresh-but-empty.
- Holding the serialised DB slot is structural, not path-based: `tests/api/` mixes mocked tests with
  real-Postgres `*_db.py` integration tests that run (Docker is on PATH). Screen by what a file
  calls, not imports:
  `grep -nE 'docker|create_migrated_test_db|mark\.integration|asyncpg\.(connect|create_pool)'`,
  then pass pytest an explicit path list; an `--ignore` list built from belief is not DB-free.
- An absence assertion passes when nothing was planted, the query returned nothing, or the fix is
  not wired. Neutralise the fix, confirm the suite goes red, restore, and give every absence test a
  positive companion (a planted sentinel it does find). Some tests correctly survive the mutation:
  check what each pins.
- When auditing a leak, read how composed display fields are built: `audit_grouping.py` builds
  `Issue.type` from a slug of `error_message`, so the text rides out in the type label.
- Never run a gate against a worktree a live worker owns: a half-written module fails for unrelated
  reasons. `git status --porcelain` must be empty before and after the run.
- When a producer's failure leaves its artefact untouched, freshness cannot report the failure: have
  each run write a receipt from its `EXIT` trap, keep "no receipt" as its own `unknown` value, and
  store it where it survives what it reports (the backup `last_run.json` pattern).
- Testcontainers setup ERRORs (0 FAILED / N ERROR, `Read timed out` at fixture setup) mean Docker
  contention from concurrent gates, not a broken branch. Record the red, then re-run alone before
  attributing it to the code.
- A flaky test whose cause cannot be removed yet goes in `tests/quarantine.json` with a bead
  ([quarantine](quarantine.md)), never behind a rerun, a raised timeout or a looser
  assertion. The registry leaves the test file untouched, so removing the entry restores the test
  exactly; a `nightly` marker edits the test and is a weakening under the [test-removal rule](ci.md#removing-or-weakening-tests).
- Logging state is process-global and survives across tests in an xdist worker: a test that calls
  `set_butler_context`, `configure_logging` or `lifecycle.run_startup` must restore the context var
  (`reset_butler_context(token)`), root handlers, filters and level. A leaked `ButlerDBLogHandler`
  plus a leaked butler context made an unrelated caplog assertion see an extra WARNING ("no running
  event loop; dropping ... log line") depending on which file ran first on the worker (bu-w4c1iw).
- A signal handler that raises can land inside `ThreadPoolExecutor.submit()` after the worker
  thread started but before the pool registered it; the `with` exit then never joins that worker.
  While pool workers are live, have the handler only set a cancellation event and raise after the
  join (found in the since-retired condensation proof producer, bu-w4c1iw).
