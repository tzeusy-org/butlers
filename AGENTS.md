# Agent Instructions

`CLAUDE.md` carries the non-negotiables (repo-root discipline, commands, beads basics, session
completion). This file holds the test-scope summary, the beads/worktree mechanics, and the notes to
self. It is capped at 300 lines by `tests/contracts/test_agent_guidance.py`: detail belongs in the
docs it points to, not here.

## Test Scope Policy

The full ladder, scope table, removal rule, gate-verdict rules and CI topology live in
[CI and test scope policy](docs/testing/ci.md). The rules to hold before reading it:

- **Ladder:** exact node, then its file or package, with `uv run --no-sync pytest <path> -q
  --tb=short` (`-n 0` for one node: `pyproject.toml` otherwise starts three xdist workers).
- **Planner:** `make test-plan BASE=origin/main` prints suggested paths or `ESCALATE` reasons. It does
  **not run pytest** and is never test evidence or merge-readiness evidence.
- **Escalate** beyond a file-level run for migrations, database/shared core, registry/discovery, root
  `conftest.py`, test tooling, `pyproject.toml`, Makefile, CI and unknown source paths. Name the
  real-Postgres, contract, API, roster or CI-shaped lane; a guessed selector is not exhaustive.
- **Roster work** includes `roster/<butler>/tests/` explicitly: `tests/` does not collect `roster/`.
- **Broad lanes** are not run locally by default. At merge readiness push the exact head and use the
  terminal hosted `merge_group` run as the broad evidence; `make test-ci-unit` and
  `make test-ci-integration` reproduce its pytest populations from a **clean** worktree, one broad
  Docker-backed lane at a time.
- **Scope names differ:** `make test-qg` is `tests/` minus the DB and migration suites; `make lint`
  runs four-root Ruff, package-layer checks and pinned secret/workflow checks. State the scope
  beside every number.
- **Verdicts:** `pytest_gate.py` receipts need a positive terminator. A log with no summary line is
  `UNKNOWN`, never a pass; only exit 0 passes. Launch long runs detached.
- **Removing or weakening a test** needs a `Tests: +a ~m -d` PR line and a named survivor
  (`Survivor: <old> -> <new>`) for protected checks; review is the only deletion gate.
- **Test budget:** a PR that pushes a lane over `scripts/test-budget-baseline.json` condenses tests in
  the same PR or states the net delta and why. `make check-guards` runs the hosted `guards` steps
  locally before you push, except hosted-only `knip`, `build`, and node `--test` tooling checks.

<!-- bv-agent-instructions-v1 -->

## Beads Workflow Integration

Issues live on the shared Dolt server; `.beads/` holds only a gitignored export mirror. The backend
facts (server address, no `bd sync`, never create `.beads/issues.jsonl`) are in `CLAUDE.md`
§ Issue Tracking; `bd prime` has the command reference.

- **Repair:** if bd misbehaves (e.g. `database "..." not found`), run `bd doctor --fix --yes` from
  the repo root.
- Coordinator and worker authority differ: under the beads-coordinator protocol only the
  coordinator runs `bd create/update/dep/close`, but a dispatched worker still pushes its own branch
  and opens its PR (the worker report helper requires `Branch-Pushed: yes` for every `completed-*`
  status). A worker that cannot push reports `blocked-awaiting-coordinator` with the failing command.
- Coordinator reconciliation rules and bd quirks:
  [coordinator guardrails](docs/operations/coordinator-guardrails.md).
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

---

## Notes to self

Concise, repo-specific rules. Subsystem contracts and their traps live in their docs; the pointers
below say where. Move a new trap into the owning doc, not here.

### Where subsystem contracts live

Each doc below carries a `## Implementation Notes` section (or an implementation-notes page) with the
traps for that subsystem. Read it before changing the subsystem.

- Runtime: [spawner](docs/runtime/spawner.md), [scheduler](docs/runtime/scheduler-execution.md),
  [session lifecycle](docs/runtime/session-lifecycle.md),
  [runtime implementation notes](docs/runtime/implementation-notes.md) (binding-only session routing,
  approval authority, runtime-attention reissue, QA suppression),
  [butler daemon](docs/architecture/butler-daemon.md), [MCP model](docs/concepts/mcp-model.md),
  [module system](docs/modules/module-system.md).
- Routing and identity: [switchboard routing](docs/concepts/switchboard-routing.md),
  [identity model](docs/concepts/identity-model.md) (the owner lives in `public.entities`),
  [expected signals](docs/concepts/expected-signals.md),
  [receiver-derived routing cutover](docs/operations/receiver-derived-routing-cutover.md).
- Data: [schema topology](docs/data_and_storage/schema-topology.md),
  [migration patterns](docs/data_and_storage/migration-patterns.md) (also the SQL, asyncpg, RLS and
  savepoint traps), [credential store](docs/data_and_storage/credential-store.md).
- Auth: [OAuth flows](docs/identity_and_secrets/oauth-flows.md),
  [CLI runtime auth](docs/identity_and_secrets/cli-runtime-auth.md),
  [dashboard owner auth](docs/identity_and_secrets/dashboard-owner-auth.md).
- Modules: [approvals](docs/modules/approvals.md), [calendar](docs/modules/calendar.md),
  [contacts](docs/modules/contacts.md), [memory](docs/modules/memory.md).
- Butlers: [finance](docs/butlers/finance.md), [home](docs/butlers/home.md),
  [messenger](docs/butlers/messenger.md), [relationship](docs/butlers/relationship.md),
  [chronicler](docs/butlers/chronicler.md).
- Connectors: [overview](docs/connectors/overview.md) (park-not-crashloop, `connector_registry`
  roles), [attachments](docs/connectors/attachment-handling.md), [gmail](docs/connectors/gmail.md),
  [heartbeat](docs/connectors/heartbeat.md), [owntracks](docs/connectors/owntracks.md),
  [telegram bot](docs/connectors/telegram-bot.md),
  [telegram user client](docs/connectors/telegram-user-client.md),
  [whatsapp](docs/connectors/whatsapp.md).
- Dashboard: [response conventions](docs/api_and_protocols/response-conventions.md),
  [API implementation notes](docs/api_and_protocols/implementation-notes.md) (server-derived actor
  attribution, route enumeration, token validation, secret-file reads, httpx log pin),
  [frontend data access](docs/frontend/data-access-and-refresh.md),
  [frontend implementation notes](docs/frontend/implementation-notes.md) (`tsc -b`, lint gates,
  mocks, Radix, MapLibre).
- Operations and dev: [dev environment](docs/getting_started/dev-environment.md) (live k3s stack,
  local Compose, tailnet paths), [kubernetes deployment](docs/operations/kubernetes-deployment.md),
  [docker deployment](docs/operations/docker-deployment.md),
  [backup and restore](docs/operations/backup-restore.md),
  [decision beads](docs/operations/decision-beads.md),
  [stale branch hygiene](docs/operations/stale-branch-hygiene.md) (custody lock, expected-SHA lease,
  no deletion permission from closed PR names).
- Tests: [CI and test scope](docs/testing/ci.md), [test traps](docs/testing/test-traps.md),
  [markers and fixtures](docs/testing/markers-and-fixtures.md). Long-lived pgvector containers are
  leaks: never write an age-based reaper; use `scripts/reap_orphaned_testcontainers.py`
  ([orphaned testcontainers](docs/testing/orphaned-testcontainers.md)).
- OpenSpec validate/archive traps: the `doctrine` skill's spec-and-spine
  `references/openspec-gotchas.md`.

### Durable queue claims

- Claim through `butlers.core.queue_claim` inside the adapter's transaction. Queue SQL
  fragments are reviewed code constants and values are bound. No-reclaim queues must
  never reclaim expired sending work; runtime attention retains its at-most-once
  transport and existing uncertain-outcome recovery.

### Cross-cutting test rules

The rest of the test traps are in [test traps](docs/testing/test-traps.md). These three apply to every
check you write or read:

- Ask of every check whether it could have failed for the reason its name claims. The commonest
  defect here is a check credited with an answer it was never positioned to give.
- Read reds from a falsification run individually: `assert True is False` against the old code is
  evidence, while `AttributeError: ... does not have the attribute '<new_helper>'` only shows the
  test patches a symbol that does not exist yet.
- A forced-quiet or fail-open green, and an absence assertion, need a control run that goes red:
  neutralise the fix, confirm the suite goes red, restore, and give every absence test a positive
  companion.

### Roster import identity

- Use the canonical roster namespace or `butlers.roster_imports.roster_module()`;
  never add a roster by-path loader. `tests/contracts/test_roster_module_identity.py`
  guards production loading and canonical test names. Deliberately isolated test
  copies use private `_`-prefixed names and never evict canonical models/routers.
  Test-only `roster.<butler>.*` dotted imports still fork identity; their separate
  migration is not a fallback or alias supported by the production loader.

### PRs, merge route and CI guards

- The session-link guard fails CI on any `claude.ai/code/session_...` URL in a PR title, body or
  comment, or in commit text outside the exact terminal `Claude-Session: <url>` trailer. The
  trailer exemption does not extend to PR surfaces: `grep -c "claude.ai" <body-file>` before
  `gh pr create`. The job fetches the PR's current title and body when it runs (the
  "Write PR title and body to files" step in `.github/workflows/ci.yml` calls
  `scripts/fetch_pr_metadata.py`), so after `gh pr edit` a manual rerun sees the corrected body; the
  commit range stays the triggering source head. Enforced by `make check-session-links`.
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
  deletion by the branch. `git patch-id --stable` proves two diffs carry the same net change even
  when their tree SHAs differ.
- During `git merge --no-commit`, check generated-artefact drift with `git diff`, not
  `git status --porcelain`: merged files are already staged. A change to a generator invalidates
  every queued PR carrying its tracked output: resolve by re-running the current generator,
  not by taking a side. Frontend copy extraction is on demand, emits stdout, and has no
  committed output or freshness gate.
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
- CI topology (fan-out, the fail-closed `check` fan-in, `always()` rules) and the narrower-than-named
  guard predicates (`check-no-em-dashes.py`, `spec-overwrite-guard`, `requirement-trace`,
  `cited-requirements-guard`, `duplicate-name-guard`) are in
  [CI and test scope](docs/testing/ci.md#ci-fan-out-and-guard-predicates). About eight concurrent CI
  runs saturate the runners: sequence PRs that append to the same file.

### Git and recovery

- Restore a file deleted by a bad commit with `git checkout <deleting-commit>^ -- <paths>`, never
  from a leftover bead worktree (it is pinned at its branch point, so its copy is older). Confirm
  with an empty `git diff <deleting-commit>^ -- <paths>`.
- `src/butlers/api/app.py` (imported by ~130 test files) and
  `src/butlers/core_tools/_delegation.py` (import failure breaks every daemon) are easy victims of
  an over-broad glob. Review bulk `git rm` in `src/` with `git show --stat` before pushing.

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

### Skills

Project skills are two routers, `butlers-development` and `butlers-tooling`, plus `doctrine`. Add
new skills as subskills under `.claude/skills/<router>/subskills/<name>/` (Claude Code does not
catalogue `subskills/`), and validate with the th-engineering `skill-standards` `audit_skill.py`
(0 errors). Moving a skill deeper breaks repo-relative links, `parents[N]` root computations and
absolute `.claude/skills/...` paths together.
