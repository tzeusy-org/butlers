# Agent Instructions

`CLAUDE.md` carries the non-negotiables (repo-root discipline, commands, beads basics, session
completion). This file holds the test policy, the beads/worktree mechanics, and the notes to self.

## Test Scope Policy

### Agent test ladder

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
   receipt-producing targets mirror the pytest and coverage portions of CI's
   `check-unit (N)` and `check-integration (N)` matrix children when a local reproduction
   is genuinely needed:

   ```bash
   make test-ci-unit
   make test-ci-integration
   ```

   CI also runs `guards` for static checks and fresh inventory/budgets, and
   `check-preflight` for complete-identity reconciliation and smoke/release evidence,
   plus the fail-closed `check` fan-in, so these targets alone are not a full
   hosted Python gate claim. Run only one broad
   Docker-backed lane at a time, and only one owner may run a broad local lane
   for an exact SHA from a **clean** worktree. `test-ci-*` records that SHA and
   refuses dirty state; any edit or rebase invalidates its receipt. Reviewers
   reuse only that matching clean receipt and run focused tests for their
   findings; they do not repeat an already-valid broad gate. A targeted PASS
   proves only its named scope; terminal hosted CI is the broad merge evidence.

   The merge queue's `merge_group` run is that terminal broad gate: it runs
   every shard, `guards`, and `frontend` against the exact tree about to land
   (enforced by the `main-merge-queue` ruleset from
   `scripts/setup_main_ruleset.sh`). A PR's own CI run is deliberately
   narrower: the stdlib `route` job classifies the diff fail-closed, and a
   docs/spec-only PR skips the backend shards and frontend jobs on purpose,
   while `push` to `main` skips the shards because the queue already validated
   that tree. On a backend-touching PR `route` consumes the existing conservative
   planner; a clean scoped plan runs only those test paths in
   `check-affected`; any planner uncertainty (escalation trigger, empty plan, or
   a plan reaching into `tests/e2e/`) reports `mode=full` and all eleven children run.
   Measured precision is in `about/craft-and-care/testing-and-verification.md`.
   `guards` enforces the freshly collected inventory against the per-lane
   collected-test budget in `scripts/test-budget-baseline.json`: a PR that
   pushes a lane over budget condenses tests in the same PR, or raises the
   budget with `--update-baseline` and states the net test delta
   (`Tests: +a ~b -c`) and why in the PR body. `make check-guards` runs every
   `guards` step locally before you push.

### Scope names are not interchangeable

No local gate command matches CI's scope; check which command produced a number.

| what | actual scope |
| --- | --- |
| `make test-qg` (and `test-qg-serial`, `test-qg-parallel`) | `pytest tests/` minus `test_db.py`, `test_migrations.py`, `tests/e2e` |
| low-context backend gate | `pytest tests/ --ignore=tests/e2e` (~40 min contended, ~21 min alone) |
| CI `check-unit-N` / `check-integration-N` | `scripts/check_ci_test_shards.py run --lane {unit,integration} --shard N` |
| `make lint` | `ruff check src/ tests/` only; omits `roster/` and `conftest.py` |

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

### Gate verdicts and long runs

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

### Finite execution bounds

- Pytest's default item timeout is 300 seconds. Preserve justified finite overrides. Node's
  `node --test` commands also have a 300-second bound; existing Vitest/Playwright defaults stay.
  A timeout is a named non-pass. Collection, worker startup and session finalization need the
  owning job watchdog, and an incomplete receipt is UNKNOWN.
- Keep every workflow job in `scripts/ci-job-timeouts.json`, including explicitly provisional
  rare-job/setup values. Current p95 and the full two-week long-tail audit require actual named
  receipts; source caps alone are not calibration. Preserve faketime's existing 75-minute job
  and 3600-second ABRT/30-second KILL watchdog until healthy measurements justify a change.
- Browser cache hits still run mandatory OS-dependency installation and locked-version launch.
  The shared installer owns three attempts, TERM110/KILL120 process-group cleanup, two ten-second
  backoffs and the 380-second outer bound. A shell exit must not strand resistant descendants.

### Frontend CI gate order (knip masks build and test)

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

### Composed local pre-push refusal

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
bodies. See [composed pre-push](docs/operations/composed-pre-push.md) for precise
survivors, supported source forms, installation and rollback. CI remains an
independent authority; Git's own `--no-verify` facility is a limitation, not agent
guidance. The required twenty-natural-push p95 below 30 seconds remains unearned.

### Cheap standing pre-push checks

```bash
make check-ci-test-shards     # fresh actual pytest inventory, exact whole-file5+6 assignment and unchanged budgets
make check-guards             # every `guards` CI step (dashes, spec, names, frontend-copy inventory, ...)
```


### CI fixed-overhead interfaces

CI environment restoration is advisory: `scripts/ci_environment.py prepare`
always performs finite frozen editable repair and validates the current real
venv/src. Never run it on a linked/shared venv. Shard duration receipts retain
exact membership and lexical UNKNOWN fallback; worker/core experiment options
retain normal defaults until complete comparable evidence selects a change.
[CI shard overhead](docs/operations/ci-shard-overhead.md) defines the actual
receipt scopes and original pending hard targets. Preserve the test ladder,
watchdogs, healthy-workload floors, full merge-group and coverage publisher.

<!-- bv-agent-instructions-v1 -->

## Beads Workflow Integration

Issues live on the shared Dolt server; `.beads/` holds only a gitignored export mirror. The backend
facts (server address, no `bd sync`, never create `.beads/issues.jsonl`) are in `CLAUDE.md`
§ Issue Tracking; `bd prime` has the command reference.

- **Repair:** if bd misbehaves (e.g. `database "..." not found`), run `bd doctor --fix --yes` from
  the repo root.
- Coordinator and worker authority differ: under the beads-coordinator protocol only the
  coordinator runs `bd create/update/dep/close`, but a dispatched worker still pushes its own branch
  and opens its PR (`scripts/emit_worker_report.py` requires `Branch-Pushed: yes` for every
  `completed-*` status). A worker that cannot push reports `blocked-awaiting-coordinator` with the
  failing command.
- Decision beads: format enforced by `make lint-decision-beads`; see
  [docs/operations/decision-beads.md](docs/operations/decision-beads.md).

### Worktrees

- Create worktrees per `CLAUDE.md` (`git worktree add ... origin/main`), then run
  `./scripts/setup_worktree.sh`. It copies the untracked `.beads/metadata.json` pointer (without it,
  bd's git hooks fall back to an 8+ minute re-import of the export mirror that contends the shared
  Dolt server) and wires `frontend/node_modules`: a symlink to the root cache, or a local
  `npm install` when the root cache is empty. It is idempotent.
- bd resolves `core.hooksPath` and `.beads/config.yaml` via `git rev-parse --git-common-dir`, so
  every worktree reads the copies checked out in the main repo root: editing them in a worktree has
  no effect until merged and the root is refreshed.
- `.beads/hooks/post-checkout` and `post-merge` export `BD_IMPORT_AUTO=false` outside the managed
  markers, disabling bd's JSONL-to-Dolt auto-import (5-8 min per checkout). Keep it: the
  `import.auto: false` line in `.beads/config.yaml` is a no-op on bd 1.0.4, and `no-auto-import` is a
  different key.
- A dispatched worker's shell starts in the session's directory (the repo root), not its worktree.
  Prefix commands with an absolute `cd <worktree>`, and before handing back confirm
  `git -C /home/tze/GitHub/butlers status --porcelain` is empty and the root is on `main`.

### Worktree `.venv` must be real, never a symlink

`node_modules` is safe to symlink between worktrees; `.venv` is not. Its editable-install `.pth`
names one absolute `src/`, so a symlinked venv makes `import butlers` resolve to main's code and a
local test run validates the wrong tree. The root `conftest.py` refuses to start pytest when
`butlers` resolves outside `<checkout>/src` (`BUTLERS_ALLOW_EXTERNAL_PACKAGE=1` opts out). Repair
from the worktree with `rm .venv && uv sync --dev`. Never run `uv sync`, `uv pip` or `pip install`
while `.venv` is still a symlink: it rewrites the linked venv and breaks main and every other linked
worktree.

<!-- end-bv-agent-instructions -->

### Coordinator guardrails

- A worker can finish with its branch pushed but its bead still `in_progress`. Detect a branch ahead
  of `main` with no PR; open the PR and mark the bead `blocked` with `pr-review` and `external_ref`.
  Merge-blocker workers likewise leave beads `in_progress` after merging: close them and any related
  `pr-review` or original bead.
- Re-run PR-state normalisation after each coordinator cycle rather than assuming earlier updates
  held. Trust Dolt over any stale export mirror.
- `external_ref` is globally unique: keep `gh-pr:<n>` on the original bead and put PR metadata in
  the review bead's notes.
- To make an original bead wait on its review bead, create the review bead without
  `--deps discovered-from:<original>` (that pre-wires the reverse edge and makes a cycle), then
  `bd dep add <original> <review>`.
- Before creating a "Resolve merge blockers for PR #<n>" bead, reuse any open one for the same PR.
- After `gh pr merge --auto`, a queued PR still reads `OPEN`: confirm with
  `gh pr view <n> --json state,mergedAt` before calling it blocked or merged.
- `bd worktree create` may append paths to `.gitignore`; strip them before committing.
- bd v1.0.4 quirks:
  - `bd lint` wants Acceptance Criteria on tasks, features and bugs (the `--acceptance` field
    counts), Success Criteria on epics, and `## Steps to Reproduce` in a bug's description. A clean
    `bd lint --json` can return `total: 0`, `results: null`: keep the text receipt.
  - `bd create --graph` drops `deps` and `parent`; wire edges afterwards with
    `bd dep <blocker> --blocks <blocked>`. `bd create --parent <epic>` works (dotted child ids)
    but inherits the epic's labels (`--no-inherit-labels`) and does not gate readiness: add
    `bd dep add <child> --blocked-by <gate>`.
  - `bd create ... --json` with literal newlines in `--description` emits unparseable JSON; recover
    the id with `bd list --json`.
- `bd worktree create <path> --branch X` branches from the current HEAD: reset new worktrees to
  `origin/main`, and review worktrees to the PR head.
- `bd close` rejects a bead with an open downstream `blocks`; for an already-finished
  original/review pair pass `--force`.
- Coordinators arm their own CI watcher with a short grace period; workers go dormant during the
  ~25 minute CI run, so finish the merge and cleanup tail yourself if the PR is still open.
- The PR-reviewer worker may move a branch into its own worktree: run `git worktree list` before
  dispatching a corrector "into the worker's worktree".
- A quiet worktree is not a dead worker: `ListAgents` lists peer sessions, not in-process subagents,
  and a clean `git status` only means nothing is committed yet. Check the task list and
  `TaskStop <worker>` before touching its tree; `git reset --hard` on a live worker's worktree
  leaves two agents committing to one branch. When agents did overlap, re-verify the stopped one's
  claims and diff its abandoned artefacts before deleting them.

---

## Notes to self

Concise, repo-specific rules. Subsystem contracts live in their docs; the pointers below say where.

### Where subsystem contracts live

Each doc below carries a `## Implementation Notes` section with the traps for that subsystem. Read
it before changing the subsystem.

- Runtime: [spawner](docs/runtime/spawner.md), [scheduler](docs/runtime/scheduler-execution.md),
  [session lifecycle](docs/runtime/session-lifecycle.md),
  [butler daemon](docs/architecture/butler-daemon.md), [MCP model](docs/concepts/mcp-model.md),
  [module system](docs/modules/module-system.md).
- Routing and identity: [switchboard routing](docs/concepts/switchboard-routing.md),
  [identity model](docs/concepts/identity-model.md) (the owner lives in `public.entities`),
  [expected signals](docs/concepts/expected-signals.md),
  [receiver-derived routing cutover](docs/operations/receiver-derived-routing-cutover.md).
- Data: [schema topology](docs/data_and_storage/schema-topology.md),
  [migration patterns](docs/data_and_storage/migration-patterns.md),
  [credential store](docs/data_and_storage/credential-store.md).
- Auth: [OAuth flows](docs/identity_and_secrets/oauth-flows.md),
  [CLI runtime auth](docs/identity_and_secrets/cli-runtime-auth.md),
  [dashboard owner auth](docs/identity_and_secrets/dashboard-owner-auth.md).
- Modules: [approvals](docs/modules/approvals.md), [calendar](docs/modules/calendar.md),
  [contacts](docs/modules/contacts.md), [memory](docs/modules/memory.md).
- Butlers: [finance](docs/butlers/finance.md), [home](docs/butlers/home.md),
  [messenger](docs/butlers/messenger.md), [relationship](docs/butlers/relationship.md).
- Connectors: [overview](docs/connectors/overview.md) (park-not-crashloop, `connector_registry`
  roles), [attachments](docs/connectors/attachment-handling.md), [gmail](docs/connectors/gmail.md),
  [heartbeat](docs/connectors/heartbeat.md), [owntracks](docs/connectors/owntracks.md),
  [telegram bot](docs/connectors/telegram-bot.md),
  [telegram user client](docs/connectors/telegram-user-client.md),
  [whatsapp](docs/connectors/whatsapp.md).
- Dashboard: [response conventions](docs/api_and_protocols/response-conventions.md),
  [frontend data access](docs/frontend/data-access-and-refresh.md).
- Operations and dev: [dev environment](docs/getting_started/dev-environment.md) (live k3s stack,
  local Compose, tailnet paths), [kubernetes deployment](docs/operations/kubernetes-deployment.md),
  [docker deployment](docs/operations/docker-deployment.md),
  [backup and restore](docs/operations/backup-restore.md),
  [decision beads](docs/operations/decision-beads.md).
- Tests: [markers and fixtures](docs/testing/markers-and-fixtures.md). Long-lived pgvector
  containers are leaks: never write an age-based reaper; use
  `scripts/reap_orphaned_testcontainers.py` ([orphaned testcontainers](docs/testing/orphaned-testcontainers.md)).
- OpenSpec validate/archive traps: the `doctrine` skill's spec-and-spine
  `references/openspec-gotchas.md`.

### PRs, merge route and CI guards

- The session-link guard fails CI on any `claude.ai/code/session_...` URL in a PR title, body or
  comment, or in commit text outside the exact terminal `Claude-Session: <url>` trailer. The
  trailer exemption does not extend to PR surfaces: `grep -c "claude.ai" <body-file>` before
  `gh pr create`. The job reads the event's frozen body, so after `gh pr edit` push a commit or
  close/reopen to re-run it. Enforced by `make check-session-links`.

- All changes go through a PR; never push to `main`. The `main-merge-queue` ruleset
  (`scripts/setup_main_ruleset.sh`, id 22281319) is the sole merge route: `gh pr merge <n> --squash
  --auto` queues a `merge_group` that runs `check`, `guards` and `frontend` on the exact tree about
  to land. Do not reintroduce a manual merge route or a between-merges health poll. Source bead
  closure keys off the completed squash merge.
- Required checks are non-strict: rebase only for a real conflict or on reviewer request. Probe
  mergeability without moving HEAD with `git merge-tree --write-tree --name-only origin/main
  <branch>`; a clean probe proves textual, not semantic, compatibility.
- Remove the worktree before `gh pr merge ... --delete-branch`. While a worktree holds the branch,
  the local delete fails and aborts cleanup, leaving the remote branch behind even though the error
  names only the local one. If it happens, `git push origin --delete <branch>`.
- Review a branch with three dots (`git diff --stat origin/main...HEAD`) or `git show --stat`. A
  two-dot `git diff origin/main` shows every commit that landed on `main` since the fork as a
  deletion by the branch.
- `git patch-id --stable` proves two diffs carry the same net change even when tree SHAs differ.
- During `git merge --no-commit`, check generated-artefact drift with `git diff`, not
  `git status --porcelain`: merged files are already staged.
- A change to a generator invalidates every queued PR carrying its output (e.g.
  `frontend/COPY_INVENTORY.md`). Resolve such a conflict by re-running the
  current generator, not by taking a side.
- A pre-merge union gate must prove the merge applied: hard-fail on fetch failure or on a merge
  that changes nothing, and print the SHAs merged.
- Reading PR state:
  - `gh pr checks` can show a stale `fail` over an in-flight rerun (confirm with
    `gh run view <id> --json status,conclusion`), exits 8 while checks are pending, and `gh` can
    return a transient `HTTP 401`. Poll loops must tolerate all three.
  - Poll the `gh pr checks` state column, not `statusCheckRollup`: an in-flight check's
    `.conclusion` is `""`, not null, so `(.conclusion // .status)` and a `grep null` guard both
    report done early.
  - A CONFLICTING PR runs no CI, so its rollup stays frozen green. Never treat a non-`MERGEABLE`
    PR as settled.
  - The job set varies by design (`route` explicitly skips shards or frontend jobs as neutral).
    Judge completeness by the required contexts (`check`, `guards`, `frontend`), not a job count.
- CI fan-out: route→guards→five unit/six integration matrix children→complete-identity `check-preflight`, then
  the `check` fan-in, which uses `always()` and reads every declared job through `NEEDS_JSON`.
  `always()` alone does not enforce prerequisites: route and guards verdicts
  must succeed, and preflight is checked independently of the heavy-shard consistency state.
  Preflight never counts as a heavy shard; a scoped PR requires preflight and check-affected
  success with every heavy shard skipped. Docs-only PR and main-push skips require the exact
  classifier/event/ref policy; full PR and merge_group require all heavy shards. New needed jobs
  accept success and deny skips until an explicit policy is added. Malformed or missing results,
  classifier outputs or planner paths fail closed.
  Lock, lint, format, SQL safety and fresh inventory/budgets run in guards with their original scopes;
  exact-once and smoke/release evidence remain in preflight. `make check-guards` includes the same
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
  - `cited-requirements-guard` reads qualified `REQ-<capability>-<NNN>` ids from test files only.
  - `duplicate-name-guard` (`make check-duplicate-names`) exists because ruff F811 misses a
    duplicate top-level name when the first definition is used in between, which is exactly what a
    merge of two branches adding the same helper produces.

### Database and migrations

- Public-table GRANTs are not an authority boundary: `scripts/init-db.sql` deliberately re-widens
  them. Tables such as `public.cost_claims` rely on enabled and forced RLS keyed to the runtime role,
  so migration tests must replay bootstrap and write under real `SET ROLE` identities.
- In partitioned-index migrations a blocking `pg_advisory_lock()` waiter can hold a snapshot that
  `CREATE INDEX CONCURRENTLY` waits on. Poll `pg_try_advisory_lock()` in autocommit with the waits
  outside PostgreSQL (see `core_231`).
- Core-chain migrations must tolerate core-only databases: guard cross-schema
  `ALTER/UPDATE/GRANT` with `to_regclass(...)` or `information_schema` checks.
- At runtime under `SET ROLE`, `to_regclass('other_schema.tbl')` raises
  `InsufficientPrivilegeError` instead of returning NULL. Probe reachability first with
  `SELECT COALESCE((SELECT has_schema_privilege(oid,'USAGE') FROM pg_namespace WHERE nspname='<s>'), false)`
  (a subquery: Postgres does not guarantee `AND` short-circuits).
- `src/butlers/db.py` parses `sslmode` from `DATABASE_URL` / `POSTGRES_SSLMODE` for both daemon and
  API pools. With it unset, a STARTTLS `unexpected connection_lost()` retries once with
  `ssl="disable"`.
- asyncpg type traps: bind `datetime.date`/`datetime` objects, never ISO strings, even with a `::date`
  cast (the codec encodes before the cast runs); pass explicit bound values rather than
  `$2 ± interval '1 day'`, which asyncpg can infer as `interval`.
- `ON CONFLICT (cols) DO UPDATE` against a partial unique index must repeat the index's `WHERE`
  predicate.
- Core revisions replay per schema against shared `public.*` data; see migration-patterns for the
  cumulative-CHECK rule.
- The asyncpg JSONB codec encodes write parameters: pass dicts/lists straight to `$N::jsonb`.
  `json.dumps(...)` double-encodes into a JSONB string, and under
  `COALESCE(col, '{}'::jsonb) || $N::jsonb` that corrupts the row into an array. Reads need no
  `json.loads` either.
- Never add a second advisory lock inside `dispatch_outcomes` producers: the `ON CONFLICT DO NOTHING`
  insert already waits on the conflicting transaction.
- Converge a stored-function literal by re-running its installer (the `finalize_interface`
  pattern), never by backfilling rows.
- Never string-match `"does not exist"` to detect a missing table: a missing column matches too.
  Catch `asyncpg.exceptions.UndefinedTableError` for the silent path and log everything else.
- JSONB `||` is right-biased: for creation-wins merges write `new || existing`, not the reverse.
- Any raised exception aborts the whole Postgres transaction, so "catch and continue" inside one
  needs a savepoint (a nested `connection.transaction()` in asyncpg). Absorb one error class only
  (e.g. `InsufficientPrivilegeError` from a best-effort call beside the primary write), and prove the
  commit by reading back from a separate pool acquisition. Do not wrap a gated call that is the
  transaction's purpose (`_routing.py` durable dashboard acceptance).
- A savepoint does not make DDL durable: a partition created inside a transaction is dropped by its
  rollback. Call ensure-partition functions on the pool, outside the transaction (see
  `roster/switchboard/tools/ingestion/ingest.py`), and test by forcing the rollback then checking
  from a separate acquisition.

### API and security contracts

- Actor attribution is server-derived, never caller-asserted. Any route that persists or audits an
  actor takes it from `authenticated_principal()` (`src/butlers/api/audit_emit.py`); a request
  field such as `actor: str = "dashboard"` is a forgery hole. To retire such a field on an
  `extra="forbid"` model without 422ing old clients, base the model on `IgnoresCallerAssertedActor`
  (same module). `tests/api/test_actor_attribution_sweep.py` guards every route; extend its
  allow-lists only with a recorded justification. `X-Butlers-Decision-Actor` on approvals is
  server-verified and is not this defect.
- Enumerating API routes: FastAPI mounts included routers lazily, so `create_app().routes` holds
  `_IncludedRouter` objects. Recurse into `route.original_router.routes`; the body model is
  `route.body_field.field_info.annotation`.
- A dashboard disconnect watcher inside nested `BaseHTTPMiddleware` needs AnyIO cancellation: a
  one-shot `Task.cancel()` then awaiting the watcher can deadlock response delivery.
  `IngestionReadBudgetRoute` uses a watcher-owned `CancelScope`.
- OAuth token refresh exists more than once per provider under look-alike names (Spotify has both
  `SpotifyClient._refresh_access_token` and `SpotifyConnector._refresh_access_token`). Before
  closing a token fix, grep the tree for every `expires_in` / `access_token` extraction site.
- A 200 from a token endpoint is not a validated payload: `int(data.get("expires_in", 3600))`
  accepts strings, bools, floats and negatives. Validate before assigning, as
  `spotify_credentials.parse_spotify_token_response` does.
- Infrastructure identity-version provenance is opt-in: producers pass
  `Observation(identity_version=...)` and, on the first higher-version successor, the explicit
  `predecessor_fingerprint`. Every resolution reason lives in top-level
  `metadata.resolution_reason`; producers may not set `RESOLUTION_METADATA_KEYS`. Never infer
  lineage from fingerprints or rewrite historic rows.
- Read secret files with `os.open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)` then `fstat` +
  `S_ISREG`: without `O_NONBLOCK` a FIFO planted at the mount path blocks forever (a hung pytest
  run, not a failure). Mode `0400` is not isolation from a same-identity child process; say so
  wherever it is checked.
- `src/butlers/core/logging.py` pins `httpx` and `httpcore` to `WARNING`: at INFO they log full
  request URLs, which carry Telegram bot tokens and OAuth query parameters. Keep that pin on any new
  logging setup.

### Test traps

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

### Git and recovery

- Restore a file deleted by a bad commit with `git checkout <deleting-commit>^ -- <paths>`, never
  from a leftover bead worktree (it is pinned at its branch point, so its copy is older). Confirm
  with an empty `git diff <deleting-commit>^ -- <paths>`.
- `src/butlers/api/app.py` (imported by ~130 test files) and
  `src/butlers/core_tools/_delegation.py` (import failure breaks every daemon) are easy victims of
  an over-broad glob. Review bulk `git rm` in `src/` with `git show --stat` before pushing.

### Frontend traps

- Type-check with `npx tsc -b` (what `npm run build` runs); `tsc --noEmit -p .` skips project
  references and misses errors in test files.
- `react-hooks/set-state-in-effect` is a hard lint error: keep form fields query-backed until a local
  draft exists instead of mirroring query data into state in `useEffect`.
- Test mocks: type a mocked `localStorage` store as `Record<string, string | null>`, and cast
  partial TanStack Query results `as unknown as ReturnType<typeof useFoo>` (a direct
  `UseQueryResult` cast fails on the discriminated union).
- Radix `Dialog` renders through a portal: jsdom tests query `document`, not the mounted container,
  and drive controlled inputs with the native value setter plus an `input` event.
- Recharts `Tooltip` formatters take `value: string | number | undefined`; narrower signatures fail
  the build.
- MapLibre: call `map.addSource` / `addLayer` only once `map.isStyleLoaded()` (or on `load`), or the
  map throws `Style is not done loading` and renders its failure fallback.
- `npm run lint:query-coercion` (CI `frontend` job) holds a per-file baseline: any net-new `?? []` /
  `?? 0` on a query's `.data` / `.meta` fails. Prefer an already-guarded value, then an `isError`
  guard; bump `query-coercion-baseline.json` only with justification. Run it before pushing.
- Serialise racing writes on one row with a TanStack mutation `scope: { id }` (last `mutate()`
  applies last). Test races with per-call deferred promises resolved at response time.
- A `vi.mocked(hook).mockImplementation(() => ({...}))` that builds a fresh object per call
  deterministically hangs vitest when a component effect depends on that value by reference (as
  `ChatPanel`'s `useConversationTurn` does): the effect re-fires every render and `act()` spins
  forever without yielding, so the per-test timeout never fires and the hang lands on a later test.
  Build one stable result object per branch outside the callback, as
  `mockHooksForConversationRefetchGap()` in `ChatPanel.test.tsx` does.

### Butler-specific rules without a docs page

- Chronicler adapters whose evidence is mirrored across schemas derive `source_ref` from the
  upstream identifier (`calendar:{origin_instance_ref}`), so the `(source_name, source_ref)` upsert
  dedupes the fan-out. `health.steps` and `health.heart_rate` are point-event sources with no lane
  mapping. `SpotifySessionAdapter` falls back to `track_names` when both context fields are NULL.
- Chronicler editorial endpoints (`/api/chronicler/briefing|attention|kpi`) already accept any
  `?date=&tz=` deterministically via `editorial.compose_briefing_payload`; date navigation is a
  frontend concern.

### Shell traps

A check that saw nothing must not read as a pass; each trap below does exactly that.

- Redirection is left to right: `cmd > file 2>&1` captures stderr, `cmd 2>&1 > file` does not.
  Assert a captured output is non-empty before trusting a comparison of it.
- `out=$(cmd | head -1); rc=$?` captures `head`'s status, not `cmd`'s.
- `jq -e` on empty stdin exits 0, and in-flight GitHub checks carry `conclusion: ""` (not null), so
  `select(.conclusion != null)` counts them as done. Count the array first and treat zero as
  not-settled; print a visible marker when a poll tick sees no data.
- The shell is zsh: an unquoted `$VAR` is not word-split, so `for p in $PRS` iterates once. Use a
  literal list or an array.
- Put each step of a backgrounded gate on its own line or separate with `;`: a one-line `eval` can
  swallow `wait $PID; echo EXIT=$?` as arguments to an earlier `echo`.
- `find` is bfs: a GNU relative `-newermt '-30 minutes'` errors to stderr and exits 0, printing
  nothing. Use `date -Iseconds -d '30 minutes ago'`; for liveness prefer `ls -lt` or `ps`.
- `#!/bin/sh` has no `pipefail`; under `set -o pipefail`, `grep ... | wc -l || echo 0` in a command
  substitution yields `0\n0` on no match.
- Never `pkill -f` a pattern that also appears in your own shell's command line (exit 144); kill by
  PID or listening port.

### Runtime and agent-tooling rules

- `ContextVar.reset()` does not revoke values inherited by an `asyncio.create_task()` child.
  Executor-scoped approval authority must also bind the authorising task, tool name and canonical
  argument digest; Home treats inherited-child, wrong-tool and wrong-argument context as unapproved.
- Runtime-attention manual reissue creates a new episode, never a replay:
  `public.reissue_runtime_attention_episode(uuid)` admits only an uncertain original after the
  delivery lease is inactive and never mutates the original, calls transport or writes breaker
  provenance. The Models/Spend attention reads sit behind `require_dashboard_owner_control` (503
  without `DASHBOARD_API_KEY`, 401 on a missing or wrong key). Stored-function and audit
  contracts live in [`docs/operations/runtime-attention.md`](docs/operations/runtime-attention.md).
- Process-fenced supervised-job health (bu-c6wjr) covers Dashboard lifespan loops, not
  Switchboard's runtime-attention delivery worker, whose truth is its attention condition and
  outbox evidence.
- QA `suppressed` is not synonymous with synthetic: the patrol uses it for filtered findings and the
  dashboard for a synthetic placeholder. Assurance reads durable origin, the enabled-source snapshot
  and all-source completion; a newer non-qualifying row does not erase a still-fresh qualifying
  patrol.
- Project skills are two routers, `butlers-development` and `butlers-tooling`, plus `doctrine`. Add
  new skills as subskills under `.claude/skills/<router>/subskills/<name>/` (Claude Code does not
  catalogue `subskills/`), and validate with the th-engineering `skill-standards` `audit_skill.py`
  (0 errors). Moving a skill deeper breaks repo-relative links, `parents[N]` root computations and
  absolute `.claude/skills/...` paths together.


### Stale branches, cache custody and live PR metadata

- Use [stale branch hygiene](docs/operations/stale-branch-hygiene.md) for dry-run,
  independently verified object recovery and concrete owner-approved retirement.
  Closed PR names, backdated commits, missing workers and an under-100 target are
  not deletion permission. Cache eviction is irreversible and rebuildable.
- Trusted coordinator claims/worktree setup and native QA/healing creation and
  cleanup share `<common-git-dir>/ci-branch-custody.lock`; do not nest an external
  acquisition around a native operation that already acquires it. Remote deletion
  still uses an expected-SHA lease. Retain dirty/live/foreign workers and receipts.
- PR guards fetch validated current title/body at execution, so a manual body-fix
  rerun sees live metadata. The commit range remains the triggering source head.
  API failure refuses; public diagnostics omit matched session text. No token
  widening, `pull_request_target`, public forbidden canary or mass old-head push.
- A source merge does not complete live branch/cache/setting application, an
  actual body-edit rerun, or both seven AND fourteen-day push observation windows.
  Preserve separate receipts and claim no wall-clock gain without its evidence.
