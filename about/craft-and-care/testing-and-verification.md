# Testing and Verification

This file defines the evidence standard for changes in Butlers.

## Core Rules

- **New features start with a failing test** when the behavior is practical to
  exercise.
- **Bug fixes start with a reproducer** that fails before the fix.
- **Verification depth scales with risk.** Do not default to the full suite for
  every edit, and do not stop at a smoke check for risky changes.
- **Completion claims require evidence.** "It looks right" is not enough.

## Verification by Change Type

### Bug Fix

- Reproduce the bug with a focused test when feasible.
- Verify the fix with the narrowest relevant scope first.
- Expand scope if the fix touches shared paths, async orchestration, or schema
  contracts.

### New Feature

- Add tests for the promised behavior before or alongside implementation.
- Verify the feature at the layer where the behavior is defined:
  unit, integration, API, or UI.
- If the feature is spec-driven, verify the scenarios the spec actually
  promises.

### Refactor

- Protect existing behavior with regression tests before moving code.
- Remove dead paths rather than keeping parallel implementations alive.
- Expand verification when shared utilities, migration machinery, or daemon
  lifecycle code move.

### Documentation or Standards Change

- Verify links, reading order, and cross-references.
- If the doc changes behavior expectations, ensure the implementation and other
  docs agree.

## Test Scope Policy

Butlers intentionally uses graduated verification:

1. Start with targeted pytest scope during active development.
2. Expand to broader file or subsystem coverage when the risk surface widens.
3. Run the full repo gate for final merge-readiness checks.

The local hygiene gate commands (lint, format, `make test-qg`) are listed in
[`CLAUDE.md`](../../CLAUDE.md) § Commands.

`make test-qg` is valuable regression evidence, but it is not CI-equivalent: it
does not cover `roster/`, root DB/migration suites, or the CI marker selection.
For a final backend claim, use the CI-shaped unit and integration targets in
[`docs/testing/ci.md`](../../docs/testing/ci.md) only when local reproduction is needed; otherwise push the exact
head after focused evidence and use terminal hosted CI as the one broad result.

That one broad result is the merge queue's `merge_group` run, which validates
the exact tree about to land. A pull request's own CI run is narrower on
purpose: a docs/spec-only diff skips the backend shards and frontend jobs, and
a push to `main` skips the shards because the queue already ran them. The
suite also has a size budget: `scripts/check_test_budget.py` fails
`check-preflight` when a lane collects more tests than
`scripts/test-budget-baseline.json` allows, so a change that grows the suite
past its headroom condenses tests in the same PR or raises the budget with a
stated net test delta and reason. Tests are production code with a run-time
cost on every merge; more of them is not free.

A pull request whose diff clears the docs filter uses the stdlib-only `route` job.
It reuses the established affected-test planner with more conservative omission admission:
only proven unchanged collection inputs may use a fresh scoped subset.
All test, conftest, dependency/config, CI and unknown changes widen to full collection and
execution. Merge-group always runs all five unit and six integration groups, followed by
fresh lane collection for totals and budgets. Push to main skips backend groups and preflight
after the merge queue has validated the tree. Docs-only skips remain explicit. The required `check`
verifies exact event/mode pairings from prerequisite verdicts only, with no checkout, installation or report work.

Test support modules and package markers with unproved import ownership escalate to every
configured pytest root, including both `tests/` and `roster/`; adjacent test files cannot prove
that a helper has no consumers elsewhere. CI validates the selected scope independently of the
changed-file list. Empty, unsupported, outside-root or root-wide selections, and any selected
ancestor or descendant of `tests/e2e/`, fall back to the complete matrix. Known small API scopes,
direct test edits and pytest-governed nested conftest scopes remain eligible.

The conditional affected job accepts canonical selected Python files only, excluding E2E.
It freshly collects the actual default-selected item identities, runs the unchanged affected
pytest flags and independently recollects after execution. Its source/run/attempt/config/nonce
bound receipt must prove exact item multiplicity, logical starts and all actual setup/call/teardown
phases, including named skips. Count-only JUnit cannot authorize it. The job uploads minimized
reference, execution and independently verified proof carriers; required check also demands
its explicit successful verifier output. Each subprocess belongs to the same finite affected
job envelope and cleans up only its own process group. No cached inventory or full-lane evidence
is borrowed to certify a scoped execution.
Affected-receipt completion/exit/count/start fields require their exact JSON boolean or integer
representations; finite nonnegative integer or floating-point phase timers remain valid durations.
Backend lane-group records require integer group identifiers and nonnegative integer counts;
booleans and floating-point counts are rejected. Malformed evidence fails without coercion.

The backend lanes run as pytest-split groups: five unit and six integration children, each
selecting its slice of the lane's fresh marker selection with `duration_based_chunks` over the
committed `.test_durations`. Durations only balance the groups; a test without one gets the
average and still runs. Each child records how many tests it collected, and check-preflight sums
each lane's groups and compares the sum with one fresh `--collect-only` count of the same
selection. Missing, extra or duplicate group records and lane-total mismatches fail the run.
These totals are count evidence, not per-item execution receipts. The fresh lane counts feed
both lane budgets. Lane children, affected, Vitest and the browser job (which builds its own
bundle) start after route; every final required verdict still requires successful guards. The
nightly covered groups measure per-test durations, and its `durations-refresh` job opens a pull
request with the merged file instead of pushing to main (bu-29wkwj.2).

The scheduled weight observer is retired. The computed partition, inventory collector and
shard observer remain only for the manual `ci-shard-watchdog-diagnostic.yml` workflow, whose
retirement is tracked in bu-29wkwj.8. Required and nightly lanes use `scripts/ci_lanes.py` and
do not consume that diagnostic's identity or phase receipts. Diagnostic UNKNOWN results remain
explicit and cannot supply missing required-CI evidence or change lane membership.

Preflight runs the dedicated smoke command
`uv run pytest tests/ --ignore=tests/e2e -m smoke -q --tb=short` in both full and scoped CI modes,
and in nightly coverage preflight. Its release evidence records the actual command, checkout
SHA, exit code, status, elapsed time and skipped classes. Smoke evidence is not derived from
backend group identities.
Coverage runs nightly, not per pull request (owner decision bu-w72da2 = A). The scheduled or
manually dispatched nightly workflow runs all eleven covered groups, checks their counts and
budgets against fresh lane collections, and validates every actual CoverageData database before
combination. `ci-coverage.v2` metadata binds each input to the checkout head, workflow run, attempt,
event, lane, group, lane configuration, source population and data digest. Validation also checks
compatible tracing and executed source lines, rejecting missing, extra, unreadable, stale or
incompatible inputs. The combined report is uploaded as an artifact. Its always-running
preflight/report gates refuse failed or skipped prerequisites; only successful scheduled/manual
main runs may update the badge. A coverage-pipeline change is validated by dispatching the nightly
workflow on its branch. Reporting integrity remains separate from required check/guards/frontend
verdicts. Protected merge-group and ordinary PR groups use `CI_COVERAGE=0` with the same lane
selection, workers, markers, timeouts and collected-count publication. Nightly groups use
`CI_COVERAGE=1`; ordinary standalone calls retain covered defaults.

Frontend lint, copy/coercion gates, knip, the one build and existing bounded Node/owner-time/CSS
survivors run in guards. Two locked Vitest children independently prove complete disjoint
actual test membership. Required frontend fails if guards or either child fails. The
Playwright browser job is required through `check`: every merge group and main push runs it,
as does every pull request the route classifies as frontend (a change under `frontend/`,
`.github/workflows/`, `scripts/ci_` or `scripts/check_ci_`), and its failure fails `check`. Browser
builds its own checked-out frontend after route, independently of guards, and retains the locked
browser version, mandatory OS dependencies on cache hits/misses (two bounded attempts with privileged
group cleanup), then three bounded browser attempts within the 720-second installer budget.
Dependency retries require confirmed termination and reaping of the owned process group.
No source control or inferred setup saving establishes current timing or hosted upload proof.

The frontend verifier compares configuration booleans, module error counters and reconstructed
count/multiplicity/outcome summaries with exact JSON types; equal integers, floats and booleans
cannot substitute for one another in these declarations or counters.
The optional PATH Node/CPU diagnostic may be UNKNOWN without blocking those mandatory installed
collector/configuration and population proofs; it never substitutes for their admission.

All workflow jobs have finite positive watchdogs. Pytest defaults to 300 seconds per item;
existing finite overrides and frontend test defaults remain. An item timer does not bound
collection, worker startup or session finalization. The owning job watchdog bounds those phases,
and missing terminal evidence stays UNKNOWN. `scripts/ci-job-timeouts.json` records the named
before samples, full retry/setup reserves and explicitly provisional values. Ten current clean
merge-group samples and the complete two-week long-tail audit remain required calibration;
initial caps and synthetic controls cannot substitute for those observations.

For an uncalibrated lane, a named healthy whole-job observation supplies a provisional
compatibility floor, not p95. The affected lane's run 37597511584, job 112713967258 completed
successfully in 2,124 seconds, including 2,077 seconds of tests. Its provisional 82-minute cap
uses twice that complete envelope plus the full 640-second UV recovery reserve; observed setup
and evidence time are retained. This finite bound does not promise zero jobs over 60 minutes or
replace the required current ten-run calibration and complete two-week audit.

**Measured planner precision.** The scoped lane shipped after the planner escalated to `mode=full`
for all 7 of 7 PRs with a real shard failure in a 50-PR sample; the merge queue still runs the full
matrix on every landing tree, so the lane can only speed PRs up, never let a failure through.

Use `make test-qg-serial` when debugging order-dependent failures.

Both targets run pytest through `scripts/pytest_gate.py` and end on a `PASS` / `FAILED` / `UNKNOWN`
verdict line. That line is the evidence: quote it. `UNKNOWN` means the run rendered no verdict at all
(killed, truncated, nothing collected), and an absent failure line is not a pass.

## Five-Minute Routine-Lane Target

The staged performance target is five minutes for the routine unit lane and
five minutes for the integration lane, measured independently on the reference
CI runner. Completion requires p95 at or below five minutes across ten clean
samples for each lane. This is a target, not a claim that current CI has met it.

Improve targetability first, then condense duplicated behavior coverage, reduce
repeated work in the slowest domains, and introduce duration enforcement only
after stable measurements. Each reduction needs scoped before/after timing and
an account of the public behavior or contract that still protects each removed
case. Preserve architecture, wire, privacy, authorization, migration, retry,
and idempotency coverage. Test counts, mocks, or similar-looking cases alone
are not grounds for deletion. A missed target keeps the phase open with its
limiting domains recorded in the owning Beads task.

The graduated commands and planner behavior live in
[CI and test scope policy](../../docs/testing/ci.md) and the
[testing spec](../../openspec/specs/testing/spec.md). The performance target
does not weaken their escalation rules or the merge queue's full-tree gate.

## Removing or Weakening Tests

Test deletion is governed by review, not by a mechanical gate. There is no
protected-test inventory, survivor ledger or deletion guard script; a reviewer
enforces this rule on every pull request.

- **Every test-changing PR states its delta.** The PR body carries
  `Tests: +a ~m -d` (added, modified, deleted), measured from the PR's own
  collection rather than estimated.
- **Every removed check names its survivor or its reason.** Deleting, moving or
  renaming a test, or weakening one in place (dropping an assertion, a
  parameter case, a fixture or a marker), names the executing test that still
  enforces that behavior at the PR head (`Survivor: <old> -> <new>`), or states
  that the behavior itself is intentionally removed or changed by the same PR.
- **Protected behavior always keeps a survivor.** Architecture, wire, privacy,
  authorization, no-send, retry, idempotency and migration-outcome checks are
  removed only with a named survivor, unless the governing requirement changes
  in the same PR.
- **Reviewers refuse** a missing `Tests:` line, a survivor that does not exist
  or does not exercise the removed behavior, and a protected-check loss with
  neither a survivor nor a requirement change.

Matching names, smaller counts and deleted production paths are not survivors
on their own; the reviewer checks what the survivor executes. A mock-call
assertion (`assert_not_called`, `call_count`) often encodes a real contract such
as idempotency, delivery cadence or a no-send boundary, so treat it as protected
until shown otherwise. This rule does not change the per-lane test budget.

## Evidence Expectations

When reporting completion, include the checks that actually ran. For example:

- targeted pytest file or test node
- Ruff check and format verification
- `make test-qg` as local broad regression evidence, plus the CI-shaped lanes when the claim requires them
- manual verification steps for docs or operator workflows

If something could not be verified, state that plainly.

## Repo-Specific Risk Areas

Read [`docs/testing/test-traps.md`](../../docs/testing/test-traps.md) and `AGENTS.md` before broad verification in these areas:

- DB-backed tests using `testcontainers`
- asyncio loop-scope and xdist interactions
- migration coverage and chain naming/path rules
- SQL safety: static gates such as `check-for-update-joins` exist because
  PostgreSQL rejects `FOR UPDATE` on the nullable side of an outer join only at
  runtime, and mock-based tests never reach that failure
- known FastMCP introspection drift in tests

Do not mislabel a known baseline flake as a product regression without checking
the repo notes first.

## CI fixed-overhead observations

Required and nightly backend lanes use pytest-split with measured `.test_durations` values
for balancing. Environment caches remain advisory: setup always runs frozen editable repair
and verifies the current checkout's real venv/import. The computed-runner identity, phase and
worker/tracer observations described in
[CI shard overhead](../../docs/operations/ci-shard-overhead.md) are historical or belong to the
retained manual watchdog diagnostic; they are not receipts from the current lane groups.
Unavailable diagnostic observations remain UNKNOWN. Neither the balancing data nor those
diagnostics establish the staged five-minute goal or replace the terminal hosted gate,
original hard targets or ten-run observations.

The [2026-10-15 ten-run historical cost baseline](../../docs/testing/ci-cost-baseline-2026-10-15.md)
preserves exact main/run/attempt identities, job and step decompositions,
selected populations and sanitized per-file costs. It separates source variants
and missing observations explicitly: covered pre-rollout costs are not current
untraced latency, and two current-file samples do not establish a current p95.
Neither the staged five-minute lanes nor the seven-minute required-context
natural target is established by that historical cohort.
