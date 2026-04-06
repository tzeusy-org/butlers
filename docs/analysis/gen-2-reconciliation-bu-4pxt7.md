# Gen-2 Reconciliation Analysis: bu-4pxt7

**Date:** 2026-04-06
**Branch:** agent/bu-4pxt7
**Analyst:** beads worker (claude-sonnet-4-6)

---

## Executive Summary

The test suite currently stands at **2,941 collected tests** (out of 2,975 total, with 34 deselected by default `nightly`/`bench` markers). The target range is 1,500–2,200. **The suite is 741 tests over the upper bound** and approximately 1,441 tests over the lower bound.

All three gap beads from gen-1 reconciliation are closed:
- **bu-tp7lh** (core+daemon condensation): CLOSED — 395 tests ✓ meets ≤400 AC
- **bu-pjqcb** (config/ condensation): CLOSED — 51 tests ✓ meets ≤60 AC
- **bu-l5076** (unassigned dirs sweep): CLOSED — partially met ACs (see below)

Despite all gap beads being closed, the total test count has not reached the target range. This analysis explains why and identifies what remains.

---

## Current Test Count by Directory

| Directory | Current | Target | Delta | Status |
|-----------|--------:|-------:|------:|--------|
| `tests/modules/` (non-memory) | 757 | ~400 | +357 | OVER |
| `tests/api/` | 341 | ~200 | +141 | OVER |
| `tests/contracts/` | 311 | ~200 | +111 | OVER |
| `tests/core/` | 315 | ~300 | +15 | MARGINAL |
| `tests/adapters/` | 182 | ~60 | +122 | OVER (never condensed) |
| `tests/tools/` | 172 | ~40 | +132 | OVER (never condensed) |
| `tests/integration/` | 186 | ~150 | +36 | OVER |
| `tests/connectors/` | 171 | ~150 | +21 | MARGINAL |
| `tests/e2e/` | 129 | ~93 | +36 | OVER |
| `tests/jobs/` | 118 | ~50 | +68 | OVER |
| `tests/daemon/` | 80 | ~80 | 0 | AT TARGET |
| `tests/modules/memory/` | 115 | ~100 | +15 | MARGINAL |
| `tests/scripts/` | 45 | ~20 | +25 | OVER |
| `tests/cli/` | 31 | ~20 | +11 | MARGINAL |
| `tests/features/` | 29 | ~30 | -1 | AT TARGET |
| `tests/telemetry/` | 20 | ~15 | +5 | MARGINAL |
| `tests/config/` | 51 | ~50 | +1 | AT TARGET |
| `tests/migrations/` | 3 | ~3 | 0 | AT TARGET |
| **TOTAL** | **2,941** | **~1,961** | **+980** | **OVER** |

---

## Root Cause Analysis

### Issue 1: bu-l5076 Closed With Partial ACs (Most Significant)

The bu-l5076 close reason says: "scripts/71→27, cli/64→30, features/51→29, telemetry/38→19, integration/267→180, jobs/270→50, e2e/93→93."

**Tests/adapters/ and tests/tools/ were never included in PR #999.** The bu-l5076
issue description listed adapters (406 → ~60) and tools (346 → ~40) as in scope,
but the implementation commit and PR only touched: scripts, cli, features, telemetry,
integration, jobs, e2e.

Current state of these two domains:
- `tests/adapters/`: 182 tests — **no condensation PR found in git history**
- `tests/tools/`: 172 tests — **no condensation PR found in git history**

These represent 354 uncondensed tests against a combined target of ~100 (254 excess).

### Issue 2: Post-Condensation Feature Additions (Legitimate Growth)

After domain condensation PRs merged, significant new feature work added tests:

| New Feature | Tests Added | PR |
|-------------|------------:|-----|
| QA module implementation (bu-cg2y3) | ~119 | #986 |
| QA pipeline integration tests (bu-c8gfj.3) | ~119 | #991 |
| QA dashboard API endpoints (bu-l0red) | ~60 | #988 |
| Additional QA API/metrics (bu-azzsk, bu-18phl) | ~48 | #996, #997 |
| **QA feature total** | **~346** | — |

These are legitimate tests for a new feature (QA module), not bloat. However,
they were added to already-condensed directories, pushing those directories back
over target:
- `tests/modules/` gained ~139 QA tests post-condensation (PR #989 condensed to 450, but QA added ~139 back = ~590 non-memory module tests)
- `tests/api/` gained ~60 QA API tests post-condensation (PR #987 condensed to 250, but QA added ~60 back = ~310)
- `tests/integration/` gained ~18 QA integration tests post-condensation

### Issue 3: Contracts Count Exceeds ~200 Target

`tests/contracts/` has 311 tests collected vs. target of ~200. The bu-zkrix PR (#975)
created a comprehensive contract suite. Examination shows the 19 contract test files
average ~16 tests each; the largest files are:
- `test_context_bus.py`: 29 tests
- `test_insight_delivery.py`: 26 tests
- `test_identity_resolution.py`: 23 tests
- `test_module_boundaries.py`: 20 tests

This overshoot is modest (111 tests) and the contracts test quality is high — all
15 architectural invariants are covered. Aggressive pruning could cause coverage loss.

---

## Acceptance Criteria Status

| AC | Requirement | Status |
|----|-------------|--------|
| AC1 | Total test count 1,500–2,200 | ❌ FAIL (2,941 collected) |
| AC2a | Tier 1 contracts ~200 | ⚠️ PARTIAL (311, target ~200) |
| AC2b | Tier 2 wire contracts ~500–800 | ℹ️ NOT DIRECTLY MEASURED |
| AC2c | Tier 3 capability behavior ~800–1,200 | ℹ️ NOT DIRECTLY MEASURED |
| AC3 | All gap beads closed | ✅ PASS (bu-tp7lh, bu-pjqcb, bu-l5076 all closed) |
| AC4 | Full test suite passes | ✅ PASS (suite is green based on recent CI) |
| AC5 | Final count documented | ❌ NOT YET (pending this report) |

---

## Gaps That Need New Beads

The following work was identified as out-of-scope for this analysis task.
These gaps should be tracked as new beads (listed for coordinator to create):

### Gap 1: Condense tests/adapters/ from 182 to ~60 [NEVER CONDENSED]

**Suggested:** type=task, priority=1, parent=bu-rhztl  
**Rationale:** tests/adapters/ was listed in bu-l5076 scope (406 → ~60) but was
never touched by PR #999. Current 182 tests, target ~60. The adapter contract tests
(`test_adapter_contract.py`: 35 tests) are valuable behavioral tests. Integration
and implementation-detail tests should be pruned. Net reduction needed: ~122 tests.

### Gap 2: Condense tests/tools/ from 172 to ~40 [NEVER CONDENSED]

**Suggested:** type=task, priority=1, parent=bu-rhztl  
**Rationale:** tests/tools/ was listed in bu-l5076 scope (346 → ~40) but was
never touched by PR #999. Current 172 tests, target ~40. Top files:
`test_extraction.py` (38 tests), `test_tools_extraction_queue.py` (30 tests),
`test_relationship_types.py` (28 tests). Net reduction needed: ~132 tests.

### Gap 3: Condense tests/jobs/ from 118 to ~50 [PARTIAL — bu-l5076 AC unmet]

**Suggested:** type=task, priority=2, parent=bu-rhztl  
**Rationale:** bu-l5076 PR #999 close note says jobs/270→50, but current count is 118.
This suggests tests were re-added or the reported baseline was wrong. The PR touched
jobs/ but current count (118) exceeds the ≤70 AC. Net reduction needed: ~68 tests.

### Gap 4: Condense tests/e2e/ from 129 to ~93 [PARTIAL — bu-l5076 AC unmet]

**Suggested:** type=task, priority=2, parent=bu-rhztl  
**Rationale:** bu-l5076 PR #999 close note says e2e/93→93 (no changes made),
but current count is 129. This represents a 36-test overshoot. New e2e tests may
have been added by feature work after PR #999. Net reduction needed: ~36 tests.

### Gap 5: Condense tests/modules/ (non-memory) from 757 to ~400 [POST-CONDENSATION GROWTH]

**Suggested:** type=task, priority=1, parent=bu-rhztl  
**Rationale:** bu-7sd7a condensed modules to ~450 (PR #989), but QA feature PRs
(#986, #991) added ~139 new QA module tests. However, 757 is still 357 over the
~400 target. The QA module tests are legitimate; the excess is a combination of
QA growth plus the original condensation landing at 450 (not 400). A focused
review of `test_insight_engine.py` (79 tests), `test_module_qa.py` (75 tests),
`test_module_self_healing_relay.py` (20 tests) could find pruning opportunities.
Net reduction needed: ~357 tests (but ~179 of those are legitimate QA feature tests).

### Gap 6: Gen-3 Reconciliation [after all above are resolved]

**Suggested:** type=task, priority=1  
**Rationale:** Once gaps 1–5 are addressed, a gen-3 reconciliation bead should
verify total count, run the full suite, and close the epic if target is met.
Estimated reduction from gaps 1–5: ~715 tests (would bring suite to ~2,226 — just
above upper bound). Additional minor cleanup in scripts (+25) and cli (+11) would
close the gap.

---

## Path to Target

To reach 2,200 (upper bound), need to reduce by **741 tests**. Prioritized work:

| Priority | Domain | Current | Target | Reduction |
|----------|--------|--------:|-------:|----------:|
| P1 | adapters/ | 182 | 60 | 122 |
| P1 | tools/ | 172 | 40 | 132 |
| P1 | modules/ (non-mem) | 757 | 500* | 257 |
| P2 | jobs/ | 118 | 50 | 68 |
| P2 | api/ | 341 | 275* | 66 |
| P3 | e2e/ | 129 | 93 | 36 |
| P3 | scripts/ | 45 | 20 | 25 |
| P3 | contracts/ | 311 | 275* | 36 |
| — | **Total** | — | — | **742** |

\* Adjusted targets account for legitimate post-condensation feature additions (QA module):
- modules/: raised from ~400 to ~500 (allows ~179 QA tests)
- api/: raised from ~200 to ~275 (allows ~60 QA API tests)
- contracts/: raised from ~200 to ~275 (accounts for 19-file coverage at current depth)

With these adjusted targets, reaching ~2,200 is achievable.

---

## Test Suite Health

- **Suite is GREEN** based on recent CI passing in PRs #998, #999
- **No duplicate file pairs** in core/ (test_seasonal_unit.py resolved)
- **All 15 architectural invariants** are covered by tests/contracts/
- **Three-tier architecture** is structurally present but counts don't match targets
  due to uncondensed adapters/tools and post-condensation QA growth

---

## Final Test Count (as of this analysis)

- **Total tests (collected by default):** 2,941
- **Total tests (including nightly/bench):** 2,975
- **Distance from target:** 741 over upper bound (2,200)
- **Primary remaining work:** adapters/ (182), tools/ (172), modules/ (357 excess), jobs/ (68 excess)
