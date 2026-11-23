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
the exact tree about to land. Every pull request and every push to `main`
runs the same complete pipeline; nothing is skipped by path, so the one
required check, `alls-green`, means the same thing on every event. The
suite has no hard test-count cap: the owner decided on 2026-11-03 that test
growth is governed by CI duration targets (see the Five-Minute Routine-Lane
Target below), not by a collected-test budget. Tests are production code with a
run-time cost on every merge; more of them is not free.

Every event runs all five unit and six integration groups beside fresh lane collection for
totals and budgets. There is no change classifier, scoped subset or affected-test lane in CI:
a scoped local run (`make test-plan`) remains an author's tool, never merge evidence. The
required `alls-green` reads prerequisite verdicts only, with no checkout, installation or
report work, and passes only when every needed job succeeded.

Backend lane-group records require integer group identifiers and nonnegative integer counts;
booleans and floating-point counts are rejected. Malformed evidence fails without coercion.

The backend lanes run as pytest-split groups: five unit and six integration children, each
selecting its slice of the lane's fresh marker selection with `duration_based_chunks` over the
committed `.test_durations`. Durations only balance the groups; a test without one gets the
average and still runs. Each child records how many tests it collected; smoke takes one
fresh `--collect-only` count of the same selection beside the groups, and check-totals sums each
lane's groups and compares the sum with it. Missing, extra or duplicate group records and lane-total mismatches fail the run.
These totals are count evidence, not per-item execution receipts. The fresh lane counts feed
both lane budgets. Lane children, Vitest and the browser job (which builds its own
bundle) start immediately beside guards; `alls-green` requires every one of them to succeed. The
required lane groups publish per-test durations, and the nightly `durations-refresh` job merges
those of the latest successful merge group and opens a pull request with the merged file instead
of pushing to main (bu-29wkwj.2, bu-q1hzqn.13).

The scheduled weight observer, the computed partition and inventory collector, and the manual
shard-watchdog diagnostic are retired (owner decision on bu-29wkwj.8, 2026-11-02). Required and
nightly lanes use `scripts/ci_lanes.py`; every job keeps its finite `timeout-minutes` watchdog.

The `smoke` job runs the dedicated smoke command
`uv run pytest tests/ --ignore=tests/e2e -m smoke -q --tb=short` (through `scripts/ci_smoke.py`)
on every event, and nightly coverage preflight runs it too. Its release evidence records the actual command, checkout
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
workflow on its branch. Reporting integrity remains separate from the required `alls-green`
verdict. Protected merge-group and ordinary PR groups use `CI_COVERAGE=0` with the same lane
selection, workers, markers, timeouts and collected-count publication. Nightly groups use
`CI_COVERAGE=1`; ordinary standalone calls retain covered defaults.

Frontend lint, copy/coercion gates, knip, the one build and existing bounded Node/owner-time/CSS
survivors run in guards. Two locked Vitest children independently prove complete disjoint
actual test membership, reconciled by `frontend-assurance`. The Playwright browser job runs on
every event and is required through `alls-green` (owner decision bu-w72da2 = A). Browser
builds its own checked-out frontend, independently of guards, and retains the locked
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
compatibility floor, not p95. Such a floor does not replace the required current ten-run
calibration and complete two-week audit.

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
are not grounds for deletion. As of 2026-11-03 there is no test-count cap, so
lane duration is the only growth pressure; it is a target today, with no
enforced duration gate beyond job `timeout-minutes`. A missed target keeps the phase open with its
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
until shown otherwise. Deletion stays a review decision, not a count target.

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
