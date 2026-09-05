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

The normal local hygiene gate in this repo is:

```bash
uv run ruff check src/ tests/ roster/ conftest.py
uv run ruff format --check src/ tests/ roster/ conftest.py
make test-qg
```

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
the ten-shard matrix: `scripts/ci_test_plan.py` (bu-v28ho, a CI-only wrapper around
`butlers.testing.scoped_runner.plan_scoped_tests`) plans the affected test paths for the diff. When
the plan is a clean, bounded scope (no escalation trigger, no empty plan, no reach into
`tests/e2e/`) the `check-affected` job runs only those test paths and the ten-shard matrix is
skipped; the `check` fan-in enforces that pairing (either the shards ran, or `check-affected` ran
and the shards were skipped -- never both, never neither). Any planner uncertainty reports
`mode=full`, which leaves the shard matrix running exactly as it always has: the lane only ever
narrows away from that default, never replaces or widens it on its own authority. The merge queue's
`merge_group` run is unaffected either way -- it always runs the full matrix, unabridged, against
the tree about to land.

**Measured planner precision (2026-09-05).** Before shipping this lane, the planner's selection was
checked against the last 50 merged PRs (#3948-#3999, spanning 2026-08-30 to 2026-09-04):
for each PR, the changed-file list (`gh pr view --json files`) was fed through the same
plan-and-decide path `ci_test_plan.py` uses, and separately every `check-unit-*`/`check-integration-*`
job across every CI run attempt on that PR's branch was inspected for real pytest failures (parsed
from the uploaded JUnit evidence; a `check-preflight` failure on "Verify CI test shard manifests" is
a static manifest-consistency gate, not a test result, and was excluded). 7 of the 50 PRs had at
least one real shard test failure somewhere in their CI history. For all 7, the planner's decision
was to escalate to `mode=full` (each PR's diff touched a shared-infrastructure path such as
`.github/ci-test-shards/*.txt` or another cross-cutting file), so the full matrix -- and therefore
every one of those failures -- would have run regardless. **Measured precision: 7/7 = 100%.** Of the
same 50 PRs, 6 would have taken the new scoped lane and 44 would have escalated to `mode=full`; none
of those 6 scoped-mode PRs had a real shard failure in the sample, so the sample contains no direct
test of scoped-mode precision, only of the escalation triggers' precision, which is the far more
common branch of the decision (44/50) and the one this measurement bars shipping without. This is a
small sample (n=7 for the failure-containment check) from one week of this repo's own PR traffic;
treat 100% as "no counter-evidence found," not as a statistical guarantee, and revisit the measurement
if the escalation triggers or the source-to-test map change materially.

**Wall-time effect.** Baseline (measured): the median wall time of the last 50 merged PRs' final
green `pull_request` CI run was ~10 minutes (`gh run list --json startedAt,updatedAt`). The 6 PRs
identified above as scoped-mode candidates were not materially faster under the *old* all-or-nothing
gate (their median was ~12 minutes, since the ten-shard matrix's wall time is bounded by its slowest
parallel shard regardless of how little a given PR touches) -- that flat cost is exactly what this
lane removes for that subset. This PR's own diff cannot supply an "after" data point: it touches
`.github/` and `src/butlers/testing/`, both full-suite escalation triggers, so its own CI run
exercises the fallback path, not the new lane. Real post-ship timing for `check-affected` should be
captured once a handful of non-infra PRs land after this merges (tracked as a fast-follow so the
number here does not go stale).

Use `make test-qg-serial` when debugging order-dependent failures.

Both targets run pytest through `scripts/pytest_gate.py` and end on a `PASS` / `FAILED` / `UNKNOWN`
verdict line. That line is the evidence: quote it. `UNKNOWN` means the run rendered no verdict at all
(killed, truncated, nothing collected), and an absent failure line is not a pass.

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
- known FastMCP introspection drift in tests

Do not mislabel a known baseline flake as a product regression without checking
the repo notes first.
