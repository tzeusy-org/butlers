# CI and Test Scope Policy

> **Purpose:** The agent test ladder, gate-verdict rules, CI topology and CI guard predicates.
> **Audience:** Agents and developers choosing test scope, running gates or changing CI.
> **Prerequisites:** [Testing](index.md).

Originally moved from `AGENTS.md` (Test Scope Policy and the CI bullets of "PRs, merge route and CI guards").
`AGENTS.md` keeps a short summary and links here.

## Agent test ladder

**Target:** routine unit and integration lanes should each reach five minutes on
the reference CI runner. This is a staged performance goal, not evidence that
the current suite has met it. Do not meet it by dropping an invariant, wire,
privacy, authorization, retry, idempotency, or migration-outcome test.

1. Start each behavior change with the exact new or affected node, then widen
   to its owning file or package:

   ```bash
   uv run --no-sync pytest path/to/test_file.py::test_name -q --tb=short -n 0
   uv run --no-sync pytest path/to/test_file.py -q --tb=short
   ```

   `-n 0` is intentional for a single node or order-dependent debugging:
   `pyproject.toml` otherwise starts three xdist workers for every command.

2. Before choosing a broader scope, run the planner from the dirty worktree:

   ```bash
   make test-plan BASE=origin/main
   ```

   It prints suggested paths or `ESCALATE` reasons from committed, staged,
   unstaged, and untracked files. It **does not run pytest** and is never test
   evidence or merge-readiness evidence.

3. Include `roster/<butler>/tests/` explicitly for roster work. For a deleted
   or moved test, run collection on its surviving parent scope. For test
   fixtures, imports, module registration, or topology changes, run:

   ```bash
   uv run --no-sync pytest tests/ roster/ --collect-only -q -n 0
   ```

   Collection is additional topology evidence, not a substitute for the
   escalation required below when the change crosses a shared boundary.

4. Migrations, database/shared core, registry/discovery, root `conftest.py`,
   test tooling, `pyproject.toml`, Makefile, CI, and unknown source paths must
   escalate beyond a file-level run. State the relevant real-Postgres,
   contract, API, roster, or CI-shaped lane explicitly; never accept a guessed
   selector result as exhaustive coverage.

5. Do **not** run the broad lanes locally by default. At final merge readiness,
   push the exact head after targeted tests, collection, and hygiene checks,
   then use one terminal hosted CI run as the broad evidence. These
   receipt-producing targets mirror the pytest populations of CI's
   `check-unit (N)` and `check-integration (N)` matrix children when a local reproduction
   is genuinely needed. They retain local covered defaults; required CI runs those
   populations untraced, and the nightly workflow's covered jobs supply full
   eleven-shard coverage as an artifact:

   ```bash
   make test-ci-unit
   make test-ci-integration
   ```

   CI also runs `guards` for static checks, `check-preflight` for the lane-group
   totals, per-lane budgets and smoke/release evidence,
   plus the fail-closed `check` fan-in, so these targets alone are not a full
   hosted Python gate claim. Run only one broad
   Docker-backed lane at a time, and only one owner may run a broad local lane
   for an exact SHA from a **clean** worktree. `test-ci-*` records that SHA and
   refuses dirty state; any edit or rebase invalidates its receipt. Reviewers
   reuse only that matching clean receipt and run focused tests for their
   findings; they do not repeat an already-valid broad gate. A targeted PASS
   proves only its named scope; terminal hosted CI is the broad merge evidence.

   The merge queue's `merge_group` run is that terminal broad gate: it runs
   every shard, `guards`, `frontend` and Playwright `frontend-e2e` (required
   through `check`) against the exact tree about to land (enforced by the `main-merge-queue` ruleset from
   `scripts/setup_main_ruleset.sh`). A PR's own CI run is deliberately
   narrower: the stdlib `route` job classifies the diff fail-closed, and a
   docs/spec-only PR skips the backend shards and frontend jobs on purpose,
   while `push` to `main` skips the shards because the queue already validated
   that tree. On a backend-touching PR `route` consumes the existing conservative
   planner; a clean scoped plan runs only those test paths in
   `check-affected`; any planner uncertainty (escalation trigger, empty plan, or
   a plan reaching into `tests/e2e/`) reports `mode=full` and all eleven children run.
   Measured precision is in `about/craft-and-care/testing-and-verification.md`.
   `check-preflight` enforces one fresh `--collect-only` count per lane against
   the sum of that lane's pytest-split group counts and against the per-lane
   collected-test budget in `scripts/test-budget-baseline.json`: a PR that
   pushes a lane over budget condenses tests in the same PR, or raises the
   budget with `--update-baseline` and states the net test delta
   (`Tests: +a ~b -c`) and why in the PR body. `make check-guards` runs every
   `guards` step locally before you push, except the hosted-only `knip`, `build`, and node
   `--test` tooling contract. When this branch changed `frontend/` since its fork from the base
   it also runs the hosted frontend lint steps (`npm run lint`, `lint:emdash`,
   `lint:query-coercion`) through `scripts/frontend_guards.py`, whose `STEPS`
   list is pinned to `ci.yml` by `tests/contracts/test_frontend_guard_steps.py`. It fails loudly
   ("frontend guard steps not run: ...") when `frontend/node_modules` is missing (`npm ci` in
   `frontend/`), and `scripts/pre_push.py` runs the same script for frontend paths. No frontend
   change on the branch: no cost.

## Go bridge CI

`go-bridge` runs `whatsapp-bridge`'s complete Go test population on every
non-doc PR, merge group and main push using the existing backend route. This
conservative predicate also runs Go for other backend/frontend changes; docs-only
PRs explicitly skip it. The required `check` needs its verdict: Go failure,
cancellation, missing result or an unexpected skip cannot read green. The Go job
is separate from the five unit/six integration groups and coverage publication.

The job uses the existing builder's Go1.25.11, a commit-pinned setup-go action,
and a cache keyed by `whatsapp-bridge/go.sum`. It runs all packages with
`-mod=readonly -count=1 -timeout=5m` and refuses tracked go.mod/go.sum changes.
It starts no authenticated bridge, pairing, live session or real database.

Its 15-minute cap is provisional and source-derived: a 600-second command bound,
30-second KILL allowance and 270-second checkout/toolchain/cache/finalization
reserve total900 seconds. The five-minute Go timer applies to each test binary;
the command and job watchdogs also bound compile, dependency and setup phases.
No healthy whole-job observation or current p95 calibration is claimed; the
timeout registry retains that limitation until named hosted evidence exists.

## Scope names are not interchangeable

No local gate command matches CI's scope; check which command produced a number.

| what | actual scope |
| --- | --- |
| `make test-qg` (and `test-qg-serial`, `test-qg-parallel`) | `pytest tests/` minus `test_db.py`, `test_migrations.py`, `tests/e2e` |
| low-context backend gate | `pytest tests/ --ignore=tests/e2e` (~40 min contended, ~21 min alone) |
| CI `check-unit (N)` / `check-integration (N)` | `scripts/ci_lanes.py run --lane {unit,integration} --group N --output DIR` |
| `make lint` | `ruff check src/ tests/ roster/ scripts/`, package-layer contracts, pinned secret/workflow checks and detector self-tests; Ruff omits root `conftest.py` |

- `scripts/check_ci_test_shards.py` and the computed inventory/partition tooling remain only for
  the manual shard-watchdog diagnostic (bu-29wkwj.8); required and nightly lanes use `ci_lanes.py`.
- `tests/` does not collect `roster/`: they are sibling roots, so a `tests/`-rooted "full gate" never
  runs `roster/<butler>/tests/`. Add `roster/` whenever the diff touches a roster butler; `pytest
  tests/ --ignore=tests/e2e` plus `pytest roster/` covers CI's Python scope between them.
- `make test-qg` also skips the DB and migration suites, so it is the wrong gate for migration work.
- `make test-unit` selects only explicitly marked unit tests; it is not the routine fast lane.
- A command-line `-m` replaces pytest's default marker expression. Restate the
  nightly/bench/perf exclusions when using a custom marker expression, as CI does.
- CI's Python jobs run named `run:` steps, not `make check`: adding a target to the Makefile's
  `check` aggregate does not make it run in CI. Verify against `.github/workflows/ci.yml`.
- Skip counts are per scope (the `tests/` vs `tests/ roster/` gap is `tests/e2e`, not `roster/`).
  Exit 0 is the only acceptance criterion; no skip count is pass/fail.
- `-q` prints no test names: confirm new files were collected, not skipped, with `-v` or `-q -rs`.

## Removing or weakening tests

Review is the only deletion gate; there is no inventory, ledger or guard script
(owner decision bu-lm2b4v, option B). A PR that deletes, moves or renames a test,
or weakens one in place (drops an assertion, parameter case, fixture or marker),
must carry the `Tests: +a ~m -d` line in its body and, for every removed check,
either name the executing survivor (`Survivor: <old> -> <new>`, existing at the
PR head) or state that the behavior itself was intentionally removed or changed
in the same PR. Architecture, wire, privacy, authorization, no-send, retry,
idempotency and migration-outcome checks need a named survivor unless their
governing requirement changes in the same PR. Reviewers refuse a missing
`Tests:` line, an invented survivor, or a protected-check loss with neither. The
rule and its rationale live in
[testing doctrine](../../about/craft-and-care/testing-and-verification.md#removing-or-weakening-tests)
and REQ-testing-053; the test budget is unchanged by it.

## Gate verdicts and long runs

- `make test-qg*` run through `scripts/pytest_gate.py`, which leaves a `## pytest-gate exit=N`
  receipt under `.tmp/test-logs/`. Read its `PASS`/`FAILED`/`UNKNOWN` verdict, not the absence of
  `FAILED` in a log. A log with no summary line (a killed run greps clean) is UNKNOWN, never a pass;
  under xdist `--maxfail` an ordinary failure exits 2, which the verdict resolves against the summary.
  Only exit 0 is a pass. The xdist `cannot send (already closed?)` crash cannot be patched from a
  conftest (workers run xdist's module source, and the dead controller writes the summary).
- Launch long runs detached (`pytest_gate.py run --detach`, or `setsid nohup ... </dev/null >LOG
  2>&1 &`): the agent tool's foreground cap signal-kills the process group, which shows as
  `OSError: cannot send (already closed?)` per xdist worker and no summary. Judge a run only on the
  receipt or `.exit` file inside the worktree you are attesting, never on another agent's task output.
- Percent-complete is a poor progress proxy (unit tests front-load); confirm liveness with `pgrep`
  and `tail` before killing a run that sits at 85-90%.
- Testcontainers setup ERRORs with 0 FAILED (`UnixHTTPConnectionPool ... Read timed out`) mean
  Docker contention, not a broken branch: never run two broad Docker-backed gates at once; re-run
  alone before attributing the red to the code.

## Finite execution bounds

- Pytest's default item timeout is 300 seconds. Preserve justified finite overrides. Node's
  `node --test` commands also have a 300-second bound; existing Vitest/Playwright defaults stay.
  A timeout is a named non-pass. Collection, worker startup and session finalization need the
  owning job watchdog, and an incomplete receipt is UNKNOWN.
- Keep every workflow job in `scripts/ci-job-timeouts.json`, including explicitly provisional
  rare-job/setup values. Current p95 and the full two-week long-tail audit require actual named
  receipts; source caps alone are not calibration. Preserve faketime's provisional
  136-minute job and 6360-second ABRT/30-second KILL watchdog until healthy
  measurements justify a change.
- Browser cache hits still run mandatory OS-dependency installation and locked-version launch.
  The shared installer's 720-second budget covers two dependency attempts (TERM150/KILL160,
  two-second cleanup reserves, one ten-second backoff) and three browser attempts
  (TERM110/KILL120, two ten-second backoffs). Privileged dependency cleanup must terminate
  and reap its owned group before a retry. A shell exit must not strand resistant descendants.
  The first dependency attempt keeps the runner's default APT route. Only a confirmed cleaned
  deadline selects the existing second attempt's official-Ubuntu preference; ordinary clean
  failures retry the default route. Supervisor cancellation/EOF is a distinct cleaned,
  non-retryable result, even while the Node parent remains alive. Recovery supports only the
  recognized Ubuntu24 noble deb822/local-mirror layout and fails closed otherwise. Its
  private source overlay preserves trust, third-party sources, packages and APT state; the
  actual APT reader must confirm effective isolation despite normal/binary configuration.
  Preparation and cleanup share the existing owned lifetime. See the
  [installer failure matrix](playwright-installer-failure-matrix.md) for local controls and
  the separate actual-Ubuntu/hosted evidence boundary.

## Frontend CI gate order (knip masks build and test)

The frontend steps in `guards` run lint, em-dash copy gate, query-result coercion gate,
**Import graph (knip)**, then one build. Two `frontend-vitest` children run the tests;
the required `frontend` verdict checks both children and `guards`. A knip failure skips build, so a local
green vitest run is not evidence the job will pass. Run `npm run knip` from `frontend/` before
pushing. Treat "unused export" as a question: it covers both dead code and a helper whose wiring was
forgotten, so check the bead's acceptance criteria before deleting. Never add an import just to
silence knip; `ignoreExportsUsedInFile: true` already exempts in-module use, so the complaint may
name a different symbol. A component with both
`export function Foo` and `export default Foo` where every consumer uses the named import reports
twice; deleting the default clears both.

Frontend tests run locally in a worktree: `scripts/setup_worktree.sh` falls back to a local
`npm install` when the root `frontend/node_modules` is empty, after which every `frontend` CI step
(including `vitest run -u` snapshot regeneration) runs for real. Playwright (`frontend-e2e`) needs a
separate `playwright install --with-deps chromium`.

## Composed local pre-push refusal

`make install-hooks` opts the repository into the composed `.githooks` path.
It preserves the five tracked managed Beads assets, refuses existing custom,
global or worktree-specific configurations, and records the exact prior local
config. `make uninstall-hooks` restores that exact config, refusing intervening
config drift. Installation affects the common Git directory and therefore all
worktrees; ROOT serializes actual fleet installation after review. SOURCE tests
exercise disposable repositories only. No automatic installation occurs.

The pre-push driver requires a clean committed source and fresh actual inventory
when collection inputs change. It calls existing read-only predicates, preserves
complete Git ref-update stdin and managed Beads status, confines guard/collection
and pre-push delegate socket operations, and kills only its owned child groups.
Unsupported tools, sources or hosts refuse. It never regenerates COPY or runs test
bodies. See [composed pre-push](../operations/composed-pre-push.md) for precise
survivors, supported source forms, installation and rollback. CI remains an
independent authority; Git's own `--no-verify` facility is a limitation, not agent
guidance. The required twenty-natural-push p95 below 30 seconds remains unearned.

## Cheap standing pre-push checks

```bash
make check-test-budget        # fresh collect-only count per lane against scripts/test-budget-baseline.json
make check-guards             # every `guards` CI step (dashes, spec, names, frontend lint, ...)
```

Frontend copy extraction is an on-demand report, not a committed artifact or freshness
gate. Run `python3 scripts/extract-frontend-copy.py` for Markdown on stdout and diagnostics
on stderr, or redirect it to an existing ignored destination:

```bash
mkdir -p .tmp
python3 scripts/extract-frontend-copy.py > .tmp/frontend-copy.md
```

The extractor's bounded scope is described in its report. Frontend lint (including owner-time
and draft eligibility), em-dash copy, query coercion, import-graph, build and type checks retain
their existing gates and failure behavior.


## CI fixed-overhead interfaces

CI environment restoration is advisory: `scripts/ci_environment.py prepare`
always performs finite frozen editable repair and validates the current real
venv/src. Never run it on a linked/shared venv. Current backend groups publish
collected counts and use `.test_durations` only for balancing. The computed-runner
receipts and lexical UNKNOWN fallback in
[CI shard overhead](../operations/ci-shard-overhead.md) are historical or belong
to the retained manual watchdog diagnostic, not required/nightly lane evidence. Preserve
the test ladder, watchdogs, healthy-workload floors, full merge-group and coverage publisher.

## CI fan-out and guard predicates

- CI fan-out: route→five unit/six integration pytest-split groups→lane totals, budgets and dedicated
  smoke/release evidence in `check-preflight`, then
  the `check` fan-in, which uses `always()` and reads every declared job through `NEEDS_JSON`.
  `always()` alone does not enforce prerequisites: route and guards verdicts
  must succeed, and preflight is checked independently of the heavy-shard consistency state.
  Preflight never counts as a heavy shard; a scoped PR requires preflight and check-affected
  success with every heavy shard skipped. Docs-only PR and main-push skips require the exact
  classifier/event/ref policy; full PR and merge_group require all heavy shards. New needed jobs
  accept success and deny skips until an explicit policy is added. Malformed or missing results,
  classifier outputs or planner paths fail closed.
  Lock, lint, format and SQL safety run in guards with their original scopes;
  the lane-group totals (each lane's pytest-split group counts against one fresh
  `--collect-only` count), budgets and smoke/release evidence run in preflight. Lane groups
  balance on the committed `.test_durations`; the nightly `durations-refresh` job opens a PR
  to update it (bu-29wkwj.2). `make check-guards` includes the same
  nonmutating static checks (`check-lock`, `lint`, `check-format`, `check-for-update-joins`).
  Never add `!cancelled()` to it (a skipped check can read green to branch protection). Never use
  `--cov-append` across jobs, and give artifact uploads `overwrite: true`.
- About eight concurrent CI runs saturate the runners. Sequence PRs that append to the same file
  instead of racing them.
- CI guards have narrower predicates than their names suggest:
  - `check-no-em-dashes.py` scans only `about/{heart-and-soul,lay-and-land,craft-and-care}/**` and
    `roster/*/{MANIFESTO,AGENTS}.md`.
  - `spec-overwrite-guard` reads only `## MODIFIED` blocks in `openspec/changes/`: it is blind to
    direct baseline edits and to `## REMOVED` blocks.
  - `requirement-trace` (`make check-requirement-trace`) reads `ADDED`/`MODIFIED` blocks in
    `openspec/changes/` only and needs `ID:`/`Source:`/`Scope:` lines unless frozen in its baseline.
  - `cited-requirements-guard` reads qualified `REQ-<capability>-<NNN>` ids from test files only.
  - `duplicate-name-guard` (`make check-duplicate-names`) exists because ruff F811 misses a
    duplicate top-level name when the first definition is used in between, which is exactly what a
    merge of two branches adding the same helper produces.
