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

   CI also runs `guards` for static checks, `smoke` (beside the lane groups)
   for the fresh lane counts, per-lane budgets and smoke/release evidence, `check-totals`
   for the lane-group totals,
   plus the fail-closed `alls-green` fan-in, so these targets alone are not a full
   hosted Python gate claim. Run only one broad
   Docker-backed lane at a time, and only one owner may run a broad local lane
   for an exact SHA from a **clean** worktree. `test-ci-*` records that SHA and
   refuses dirty state; any edit or rebase invalidates its receipt. Reviewers
   reuse only that matching clean receipt and run focused tests for their
   findings; they do not repeat an already-valid broad gate. A targeted PASS
   proves only its named scope; terminal hosted CI is the broad merge evidence.

   The merge queue's `merge_group` run is that terminal broad gate against the
   exact tree about to land (enforced by the `main-merge-queue` ruleset from
   `scripts/setup_main_ruleset.sh`). Every event (pull request, merge group and
   push to `main`) runs the same complete pipeline: every shard, `guards`,
   `smoke`, `check-totals`, both Vitest children, `frontend-assurance`, Playwright
   `frontend-e2e` and `go-bridge`. There is no path routing and no scoped mode
   (bu-29wkwj.3). The single required check, `alls-green`, needs every one of
   those jobs and fails unless each reports success; the ruleset requires it alone.
   `smoke` takes one fresh `--collect-only` count per lane while the groups run;
   `check-totals` then requires the sum of that lane's pytest-split group counts to match
   it. There is no collected-test cap (owner decision 2026-11-03); test growth is governed by
   the CI duration targets in the
   [testing doctrine](../../about/craft-and-care/testing-and-verification.md#five-minute-routine-lane-target).
   `make check-guards` runs every
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
PR, merge group and main push. The required `alls-green` needs its verdict: Go failure,
cancellation, missing result or a skip cannot read green. The Go job
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

- The computed inventory/partition tooling and the manual shard-watchdog diagnostic are retired
  (bu-29wkwj.8); required and nightly lanes use `ci_lanes.py`.
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
  The CI lane groups pass `-rs`, so every skip reason (for example a real-PG node's
  "Docker not available") appears in the group log; the group's junit evidence records it too.
- The integration lane guarantees Docker, so `scripts/ci_lanes.py` sets `BUTLERS_REQUIRE_DOCKER=1`
  for it and the root `conftest.py` turns a "Docker not available" skip into a failure there.
  Local runs leave the flag unset, so Docker-less hosts keep skipping real-PG tests.

### Session-host native proof

The existing unit groups install Bubblewrap, static C build prerequisites and OpenSSL,
and set `BUTLERS_REQUIRE_SESSION_HOST_NATIVE=1`. The dark session-host tests compile
synthetic static payloads and execute real filesystem/PID namespace isolation and
cleanup proofs; their local TLS tests use generated synthetic certificates.
Missing Bubblewrap or a denied namespace preflight fails under hosted `CI=true` or
that strict flag. A local non-strict skip is setup information, never native evidence.
The unit jobs pin Ubuntu24.04 and run a bounded CI-host-only AppArmor experiment.
The global userns restriction stays1. The helper verifies the published Noble
`apparmor-profiles` package and purpose-built ABI4 Bubblewrap member by pinned
SHA256, extracts only that member, and refuses preloaded/conflicting or local
policy overrides. It loads the `/usr/bin/bwrap` policy with its inseparable
child-capability-deny companion, never a generic unconfined profile. The exact
unprivileged synthetic preflight must change from an attributed namespace refusal
to payload exit31 while generic map-root `unshare` remains denied. Cleanup runs
with `always()` and removal is observed. An unproven/partial load is quarantined
as NONPASS rather than removing policy whose ownership is uncertain.
No global sysctl relaxation, disabled AppArmor/seccomp, privileged fallback or
hosted skip is permitted. Package/profile verification, setup or cleanup failure
fails the job; no production policy is installed. Native fixture diagnostics expose
fixed failure categories and a stderr digest. The CI prerequisite additionally logs
at most the first 512 stderr bytes from its fixed synthetic preflight, preserving
printable ASCII and replacing control/non-ASCII bytes with question marks. That
command receives no invocation or provider material. The primary sources and separate
integration disk-exhaustion limitation are recorded in the session-host page.
The unit JUnit evidence must include passing native and TLS cases; no new CI job or
second broad lane is introduced. The private API and remaining production gates are in
[session host](../runtime/session-host.md).

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
and REQ-testing-053.

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
`frontend-assurance` reconciles both children's populations, and `alls-green` requires all three plus `guards`. A knip failure skips build, so a local
green vitest run is not evidence the job will pass. Run `npm run knip` from `frontend/` before
pushing. Treat "unused export" as a question: it covers both dead code and a helper whose wiring was
forgotten, so check the bead's acceptance criteria before deleting. Never add an import just to
silence knip; `ignoreExportsUsedInFile: true` already exempts in-module use, so the complaint may
name a different symbol. A component with both
`export function Foo` and `export default Foo` where every consumer uses the named import reports
twice; deleting the default clears both.

A nonzero Vitest execution remains `complete=false`, `wrapper_exit=2` and nonpassing even
when its private reporter file contains useful observations. The wrapper retains only closed
error counters, outcome states and opaque module/item identities after validating the fresh
file's closed schema, mandatory configuration predicates, tracked source paths and unchanged
source/run/attempt identity. An observed successful independent reference additionally checks
membership and matching runtime configuration. Without it, safe reported observations survive,
but reference, membership and configuration match stay `UNKNOWN`. These partial diagnostics
never prove complete population or readiness. Missing, stale, malformed or mismatched evidence
is explicitly `UNKNOWN`; a cancelled reference cannot provide membership authority. Raw test
names, parameters, provider
strings, exception messages and subprocess stdout/stderr remain outside artifacts and wrapper
output. Transport controls establish this information boundary, not an installed Vitest failure
cause, performance result or hosted check success.

Frontend tests run locally in a worktree: `scripts/setup_worktree.sh` falls back to a local
`npm install` when the root `frontend/node_modules` is empty, after which every frontend CI step
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

- CI fan-out: five unit/six integration pytest-split groups beside `smoke`
  (fresh lane counts, budgets and dedicated smoke/release evidence)→lane totals in
  `check-totals`, then the `alls-green` fan-in. It runs under `always()`, needs every
  workload job directly, and its one `jq` step fails unless `NEEDS_JSON` holds exactly
  the expected number of jobs and every result is `success`: a failure, cancellation,
  skip or missing job fails it. `tests/contracts/test_required_checks.py` executes that
  step and fails when a job leaves `needs`; actionlint owns workflow syntax.
  Lock, lint, format and SQL safety run in guards with their original scopes;
  the fresh `--collect-only` lane counts, budgets and smoke/release evidence run in smoke
  beside the groups, and `check-totals` compares each lane's pytest-split group counts with
  those counts once the groups finish. Lane groups
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
    A modified heading keeps its existing canonical ID, including an already-synced rename;
    otherwise same-delta `RENAMED` FROM/TO chains resolve its canonical ancestor. A newly minted
    replacement ID fails, and an unanchored rename cycle fails rather than looping.
  - `cited-requirements-guard` reads qualified `REQ-<capability>-<NNN>` ids from test files only.
  - `duplicate-name-guard` (`make check-duplicate-names`) exists because ruff F811 misses a
    duplicate top-level name when the first definition is used in between, which is exactly what a
    merge of two branches adding the same helper produces.

## Route and OpenAPI snapshot

`tests/api/test_router_registry.py` compares the live FastAPI app with
`tests/api/route_snapshot.txt`: one sorted line per route, OpenAPI operation and component
schema (short content hash), plus the middleware stack. Adding a route adds lines, so
independent PRs merge cleanly; unintended drift fails with a line diff. Route table order is
not pinned; instead the test asserts the frontend mount is last and that no API route is
shadowed by an earlier route with a different path.

Refresh after an intentional route or schema change, then commit the snapshot:

```bash
BUTLERS_UPDATE_ROUTE_SNAPSHOT=1 uv run pytest tests/api/test_router_registry.py -n 0 -q
```

On a merge conflict in the snapshot, rerun the refresh instead of hand-editing hashes.
