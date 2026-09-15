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
exercises the fallback path, not the new lane.

**Post-ship `check-affected` census (fixed cutoff 2026-09-10T23:15:38Z).** The collection
interval starts immediately after PR #4001 merged at 2026-09-05T01:12:21Z, so the inclusive query
start was 2026-09-05T01:12:22Z. Full pagination of the workflow-run API was restricted to
`event=pull_request` and returned 275 workflow-run records: 186 successful, 53 failed, 35
cancelled, and 1 still in progress at the cutoff. Seven records represented a latest attempt
number greater than 1. This run-history population is context for success-only survivorship bias,
not additional samples; push and `merge_group` events were excluded by the event filter.

The run census is reproducible with the following content-blind metadata query:

```bash
gh api --paginate \
  'repos/tzeusy-org/butlers/actions/workflows/ci.yml/runs?event=pull_request&created=2026-09-05T01:12:22Z..2026-09-10T23:15:38Z&per_page=100'
```

There were 111 distinct merged PRs after PR #4001 through the cutoff. Matching each PR's final
`headRefOid` to the workflow `head_sha` produced 110 final-head runs in the interval. PR #4005's
final-head run ([33934848649](https://github.com/tzeusy-org/butlers/actions/runs/33934848649)) was
created at 2026-09-05T01:01:09Z, before both the interval and the shipped lane, so it was excluded.
Of the 110 in-interval final-head runs, 22 were docs/spec-only
path-filtered runs with the backend jobs skipped, 72 were full-matrix/fallback runs with
`check-affected` skipped, and 16 were successful scoped runs whose `Affected tests (scoped plan)`
step completed successfully. Two final-head PRs had a prior failed attempt; their terminal
successful attempt was retained and the earlier attempts were not counted as samples (PR #4022 run
33963157532 and PR #4119 run 34358913108). No final-head terminal attempt was failed, cancelled,
or in progress.

The fixed cohort is the first five distinct merged PRs with a terminal successful scoped run,
ordered by `mergedAt` then PR number. The runner label was `ubuntu-latest` (runner group
`GitHub Actions`). Selected-test count was not exposed by job metadata and was intentionally not
recovered from logs or JUnit artifacts.

| PR / workflow run | Merged (UTC) | Final head / attempt | Plan / check-affected | `check-affected` job (started -> completed) | Job duration | Workflow (first job start -> last job completion) | Workflow duration / queue delay |
| --- | --- | --- | --- | --- | ---: | --- | ---: |
| [#4036](https://github.com/tzeusy-org/butlers/pull/4036) / [34004507085](https://github.com/tzeusy-org/butlers/actions/runs/34004507085) | 2026-09-06T02:05:04Z | `fd45e3c0b371c646fb1f3f3bfae2e3a39111146b` / 1 | success / success | 2026-09-06T01:41:20Z -> 2026-09-06T01:43:57Z | 157s (2m37s) | 2026-09-06T01:40:36Z -> 2026-09-06T01:48:42Z | 486s (8m06s) / 2s |
| [#4055](https://github.com/tzeusy-org/butlers/pull/4055) / [34053416716](https://github.com/tzeusy-org/butlers/actions/runs/34053416716) | 2026-09-06T19:16:43Z | `fead88d364a993ac5663b0a0aa5c1cf6c2e26132` / 1 | success / success | 2026-09-06T18:59:57Z -> 2026-09-06T19:01:49Z | 112s (1m52s) | 2026-09-06T18:59:04Z -> 2026-09-06T19:06:25Z | 441s (7m21s) / 3s |
| [#4060](https://github.com/tzeusy-org/butlers/pull/4060) / [34065205777](https://github.com/tzeusy-org/butlers/actions/runs/34065205777) | 2026-09-06T23:24:04Z | `99899551850bfedc84c4c9d5c2c37575f6d97ae3` / 1 | success / success | 2026-09-06T22:57:30Z -> 2026-09-06T23:06:03Z | 513s (8m33s) | 2026-09-06T22:56:37Z -> 2026-09-06T23:06:10Z | 573s (9m33s) / 331s (5m31s) |
| [#4063](https://github.com/tzeusy-org/butlers/pull/4063) / [34067651306](https://github.com/tzeusy-org/butlers/actions/runs/34067651306) | 2026-09-07T00:15:31Z | `b202836a5888e111d2ca3b6345dfcf6338a7437f` / 1 | success / success | 2026-09-06T23:43:42Z -> 2026-09-06T23:46:12Z | 150s (2m30s) | 2026-09-06T23:42:48Z -> 2026-09-06T23:50:49Z | 481s (8m01s) / 2s |
| [#4068](https://github.com/tzeusy-org/butlers/pull/4068) / [34177956052](https://github.com/tzeusy-org/butlers/actions/runs/34177956052) | 2026-09-08T02:15:04Z | `aebe48fac5690d0edf1aaa75c82c27cbd2fad0dd` / 1 | success / success | 2026-09-08T01:50:37Z -> 2026-09-08T01:54:45Z | 248s (4m08s) | 2026-09-08T01:49:52Z -> 2026-09-08T01:57:42Z | 470s (7m50s) / 2s |

Durations use only named timestamp fields from the per-attempt jobs endpoint
(`/actions/runs/{run_id}/attempts/{attempt}/jobs`):

```python
from datetime import datetime

def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def seconds(start, end):
    return int((parse(end) - parse(start)).total_seconds())

job_seconds = seconds(started_at, completed_at)
workflow_seconds = seconds(first_started_job, latest_completed_job)
queue_seconds = seconds(run_created_at, first_started_job)
```

For the five rows, the `check-affected` job-duration median is 157s (2m37s), with a range of
112-513s (1m52s-8m33s). Workflow execution has a median of 481s (8m01s), with a range of
441-573s (7m21s-9m33s). Queue delay has a median of 2s and a range of 2-331s. These are
descriptive n=5 results, not a percentile or statistical claim, and the sample does not establish
causal speedup: no matched pre-ship scoped-eligible cohort was recovered with the same timestamps
and method. The merge queue remains the terminal broad gate and always runs the full matrix.

An independent re-fetch of the per-attempt job metadata for #4036 and #4055 matched their recorded
timestamps, plan/check-affected conclusions, test-step conclusions, and recomputed durations.

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
- known FastMCP introspection drift in tests

Do not mislabel a known baseline flake as a product regression without checking
the repo notes first.
