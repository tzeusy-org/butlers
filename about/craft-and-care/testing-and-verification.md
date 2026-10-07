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
`AGENTS.md` only when local reproduction is needed; otherwise push the exact
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

A pull request whose diff clears the docs filter gets one more layer of narrowing before it reaches
the ten-shard matrix: `scripts/ci_test_plan.py` (a CI-only wrapper around
`butlers.testing.scoped_runner.plan_scoped_tests`) plans the affected test paths for the diff. When
the plan is a clean, bounded scope (no escalation trigger, no empty plan, no reach into
`tests/e2e/`) the `check-affected` job runs only those test paths and the ten-shard matrix is
skipped; the `check` fan-in enforces that pairing (either the shards ran, or `check-affected` ran
and the shards were skipped -- never both, never neither). Any planner uncertainty reports
`mode=full`, which leaves the shard matrix running exactly as it always has: the lane only ever
narrows away from that default, never replaces or widens it on its own authority. The merge queue's
`merge_group` run is unaffected either way -- it always runs the full matrix, unabridged, against
the tree about to land.

The required `check` evaluates needed-job verdicts without checkout or dependency installation.
The visible, non-required `coverage` job runs only after all ten merge-group shards succeed.
It validates each database and its same-checkout/run/attempt shard metadata before combining
and publishing the existing report/badge. PRs run the same selected corpus without coverage;
merge-group shards and direct standalone shard calls retain coverage. A reporting failure is
visible independently of required test verdicts. Source/fixture controls do not establish hosted
upload success or timing improvements; those need their named actual merge-group observations.

All workflow jobs have finite positive watchdogs. Pytest defaults to 300 seconds per item;
existing finite overrides and frontend test defaults remain. An item timer does not bound
collection, worker startup or session finalization. The owning job watchdog bounds those phases,
and missing terminal evidence stays UNKNOWN. `scripts/ci-job-timeouts.json` records the named
before samples, full retry/setup reserves and explicitly provisional values. Ten current clean
merge-group samples and the complete two-week long-tail audit remain required calibration;
initial caps and synthetic controls cannot substitute for those observations.

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
[AGENTS.md](../../AGENTS.md#test-scope-policy) and the
[testing spec](../../openspec/specs/testing/spec.md). The performance target
does not weaken their escalation rules or the merge queue's full-tree gate.

## Evidence Expectations

When reporting completion, include the checks that actually ran. For example:

- targeted pytest file or test node
- Ruff check and format verification
- `make test-qg` as local broad regression evidence, plus the CI-shaped lanes when the claim requires them
- manual verification steps for docs or operator workflows

If something could not be verified, state that plainly.

## Repo-Specific Risk Areas

Read `AGENTS.md` before broad verification in these areas:

- DB-backed tests using `testcontainers`
- asyncio loop-scope and xdist interactions
- migration coverage and chain naming/path rules
- SQL safety: static gates such as `check-for-update-joins` exist because
  PostgreSQL rejects `FOR UPDATE` on the nullable side of an outer join only at
  runtime, and mock-based tests never reach that failure
- known FastMCP introspection drift in tests

Do not mislabel a known baseline flake as a product regression without checking
the repo notes first.
