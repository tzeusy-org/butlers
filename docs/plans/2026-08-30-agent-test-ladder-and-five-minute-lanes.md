# Agent Test Ladder and Five-Minute Routine Lanes

**Date:** 2026-08-30
**Status:** Owner-approved implementation plan
**Tracking:** `bu-hciey`

## Decision

Butlers will improve developer feedback before removing broad coverage. The
near-term contract is a fail-closed, plan-only test selector plus an explicit
agent test ladder. The longer-term program targets **five minutes per routine
CI lane** (unit and integration independently), while retaining architectural,
wire, privacy, authorization, migration, retry, and idempotency coverage.

This is not an authorization to deselect half the suite or to treat a targeted
PASS as merge readiness. Current main measures 16,092 selected non-integration
cases and 4,150 integration cases; a recent successful CI run took 20m51s and
16m18s in those stages. Reaching an 8,000-case global target immediately would
remove more than half the selected suite without evidence that the removed
coverage is redundant.

## Immediate implementation

1. Harden the existing `butlers.testing` selector for agent worktrees.
   It must consider branch, staged, unstaged, untracked, deleted, and renamed
   paths; include both configured pytest roots (`tests/`, `roster/`); and never
   present an unrecognised or topology-changing diff as "no tests."
2. Make the initial command plan-only (`make test-plan`): print inputs,
   suggested paths, and explicit `ESCALATE` reasons. It does not execute pytest
   and cannot be cited as a passing test result.
3. Add selector contract tests for mapping, dirty worktree detection, deleted
   test handling, root/test configuration, migrations, shared core, CI/Make
   changes, and unknown files. A selected path must exist or be replaced by its
   existing parent scope for collection.
4. Add a compact ladder to `AGENTS.md`, with a link to the detailed testing
   [strategy](../testing/testing-strategy.md). The ladder starts at a node or file, widens to the owning scope,
   requires collection after topology changes, and names the CI-shaped unit and
   integration lanes for final verification.

## Safety contract

- `make test-qg` is useful local evidence, but it does not cover `roster/`,
  root DB/migration suites, or the exact CI selection. It must never be called
  universal coverage.
- `-m unit` is not the routine fast lane: it currently selects 13,735 cases.
- Root `conftest.py`, test tooling, `pyproject.toml`, CI/Make changes,
  migrations, shared core, registry/discovery code, and unknown source paths
  return `ESCALATE`; the agent states and runs the affected suites rather than
  trusting inference.
- A targeted run proves only its named scope. Hosted terminal CI remains broad
  evidence; it is a discipline gate rather than a technically protected branch
  requirement on this repository.
- Broad-gate ownership is singular per exact SHA from a clean worktree: the
  receipt records the SHA, and any dirty edit or rebase invalidates reuse.
  Reviewers then run only focused reproduction tests for their findings. Local
  CI-lane targets are parity/debugging tools, not a routine pre-review ritual.

## Staged five-minute program

| Phase | Outcome | Budget / gate |
| --- | --- | --- |
| 0 — targetability | Tested planner and documented ladder | No CI selection changes; measure baseline on every PR that changes test policy |
| 1 — contract-preserving condensation | Per-domain deletion/rewrite slices, starting with proven tautologies and duplicated public behavior | Re-measure static, collected, duration, mutation/replacement evidence for the exact named lane; no aggregate-count target |
| 2 — lane-specific reduction | Remove duplicate work from the slowest unit and integration domains, retaining public behavior and Tier 1/2 contracts | Each candidate lane has a before/after timing report and a 5-minute budget trajectory |
| 3 — enforce and reconcile | Add a non-flaky duration budget only after several stable CI samples; reconcile every retained/replaced contract | Unit <=5m and integration <=5m on the reference CI runner at p95 across ten clean samples; a miss keeps the phase open and records the limiting domains and next approved slice |

No phase may delete a test merely because it uses a mock, is parametrized, or
looks similar. Each deletion follows the condensation classification rule and
is independently verified through a public behavior, invariant, or wire
contract. The program is tracked separately from the immediate targetability
work so a timing target cannot quietly weaken behavior coverage.

## Verification

- Focused selector contract tests plus an explicit `--collect-only` check.
- Ruff check and format check on all changed Python and documentation paths.
- Representative `make test-plan` output from a dirty worktree.
- For every condensation slice: scoped suite, collection, Tier 1 contracts,
  and CI-shaped hosted or one-owned-local remeasurement before a new target is
  claimed.
