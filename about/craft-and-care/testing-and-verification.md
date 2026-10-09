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

A pull request whose diff clears the docs filter uses the stdlib-only `route` job.
It reuses the established affected-test planner with more conservative omission admission:
only proven unchanged collection inputs may use a fresh scoped subset without full inventory.
All test, conftest, dependency/config, CI and unknown changes widen to full collection and
execution. Merge-group always uses fresh complete inventory and all eleven5+6 children.
Push to main collects/budget-checks fresh inventory but skips heavy execution after protected
validation. Docs-only skips remain explicit. The required `check` verifies exact event/mode
pairings from prerequisite verdicts only, with no checkout, installation or report work.

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
Completion/exit/count/start fields require their exact JSON boolean or integer representations.
A boolean is never an integer count/exit, and an equal floating-point number is never a count.
Both scoped and full-matrix consumers reject malformed representations without coercion;
finite nonnegative integer or floating-point phase timers remain valid numeric durations.

Guards performs one actual installed `pytest tests/ roster/ --collect-only -q -n0 -m ""`
collection. Exact inherited markers establish both populations; advisory weights never do.
Missing, malformed, nonobject or duplicate-key advisory weight files fall back to the same
finite deterministic costs and set assignment degradation; inventory, assignment and execution
evidence still use strict JSON admission in every entrypoint. Both unchanged lane budgets consume this inventory. Deterministic LPT assigns whole files to
five unit and six integration children. Each child independently recollects its assignment;
preflight reconciles complete nonce-hashed identity multiplicity, logical starts and phases
across all eleven children. Missing/extra/stale receipts cannot hide behind a matrix aggregate.
Raw parameter identities stay in RAM; public nonces limit linkability, not guessing or trust.

Full-mode smoke may use exact successful child identities only. An uncovered item runs the
original dedicated command. Derived release evidence names actual child commands, selector
provenance and maximum actual child elapsed time; it never invents a dedicated smoke timer.
The non-required `coverage` job verifies all eleven actual CoverageData databases, source
population, tracing, digest and same-checkout/run/attempt/computed assignment before combine.
PRs still run without coverage; merge-group and ordinary standalone shard calls retain it.
Reporting integrity remains separate from required check/guards/frontend verdicts.

Frontend lint, copy/coercion gates, knip, the one build and existing bounded Node/owner-time/CSS
survivors run in guards. Two locked Vitest children independently prove complete disjoint
actual test membership. Required frontend fails if guards or either child fails. Browser
reuses only the source/attempt/content-bound build and retains locked browser version,
mandatory OS dependencies on cache hits/misses, three bounded attempts and ordinary cleanup.
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

## CI fixed-overhead observations

The preserved runner within the computed eleven-child topology records private hashed selected identities and
real phase/worker/tracer observations. Advisory duration ordering never changes
membership; unavailable or incompatible timings fall back lexically with
UNKNOWN. Environment caches always run frozen editable repair and verify the
current checkout's real venv/import. Experimental unit workers and coverage
cores preserve ordinary defaults until complete comparable evidence selects a
change. See [CI shard overhead](../../docs/operations/ci-shard-overhead.md) for
exact receipt meanings, twelve-job service retirement, CPU dependencies and
rollback. These mechanisms do not establish the staged five-minute goal or
replace the terminal hosted gate, original hard targets or ten-run observations.
