# Beads Epics: Test Suite Condensation

> **DO NOT file Phase 3 beads casually.** Creating READY beads in `~/gt/butlers`
> auto-triggers the autonomous fleet (opens + merges PRs within minutes). Keep
> any proposed beads BLOCKED until a coordinated cycle is intentionally started.

## Phase 3 (proposed, 2026-06-21) — NOT YET FILED

Baseline **7,494 def-test / 8,107 collected, 657 files**. A 22-auditor pass found
only **~1,050–1,250 SAFELY removable** — most growth is real coverage. Target a
moderate, disciplined trim, NOT a fixed number. Proposed structure (text only):

```
bu-PHASE3 (P1) Phase 3: Test suite maintenance (7,494 → ~6,300; moderate)
  ├── (P2) Migrations boilerplate: delete/parametrize tautological metadata
  │        tests (48/52 files) into tests/migrations/conftest.py     ~769 → ~450
  ├── (P2) tests/api/ regrowth (largest domain, 2264) — schema-validate,
  │        parametrize, drop field-by-field asserts                  ~2264 → ~1900
  ├── (P3) modules non-memory loose (706) — consolidate per-module    review
  ├── (P3) core (1079) — duplicate-pair merges, helper-test pruning    review
  └── (P2) Reconciliation gen-3: re-measure, confirm CI green, update skill counts
```

Sequencing: migrations boilerplate is the safest, highest-confidence lever — do
it first. api is the biggest absolute lever but needs care (schema-validate, not
delete). Each child MUST apply classification.md §1 (mock-wiring plumbing vs
contract) and the shared-helper guard before any DELETE_FILE.

## Phase 2 (CLOSED 2026-05-05, archive)

**Epic**: `bu-hg8rl` — Test suite maintenance, 3,704 → ~2,500 tests. **CLOSED
2026-05-05 ("all steps complete").** All children done.

```
bu-hg8rl (P1) Phase 2: Test suite maintenance               [CLOSED]
  ├── bu-9riic (P1) Backfill Tier 1 architectural-invariant tests (137 → ~200) ✓
  ├── bu-gg4y1 (P2) Condense tests/api/ (492 → ~160)                           ✓
  └── bu-m564i (P2) Condense tests/chronicler/ + promote 13 to contracts/      ✓
                    (519 → ~250 in chronicler + 13 promoted)
```

| Bead | Domain | Current | Target | Closed |
|---|---|--:|--:|:-:|
| bu-9riic | Tier 1 contracts | 137 | ~200 | ✓ |
| bu-gg4y1 | API tests | 492 | ~160 | ✓ |
| bu-m564i | Chronicler | 519 | ~250 + 13 promoted | ✓ |

## Phase 1 (closed, archive)

**Epic**: `bu-rhztl` — Condense test suite from ~13,675 to ~2,000
contract-driven tests. **CLOSED 2026-04-06** at 2,196 tests across 10 PRs.

```
bu-zkrix (P1) Extract architectural contracts [PHASE 1 — MUST COMPLETE FIRST]
  │
  ├── bu-v7dn3 (P2) Memory module: 1,544 → ~100
  ├── bu-35fm7 (P2) Connectors: 2,284 → ~150
  ├── bu-eu6jh (P2) Migrations: 890 → ~50
  ├── bu-egmz6 (P2) API tests: 1,779 → ~200
  ├── bu-7sd7a (P2) Modules non-memory: 2,170 → ~400
  └── bu-l1obx (P2) Core tests: 1,453 → ~300
        │
        └── bu-s8hn8 (P3) Root cleanup & final sweep [depends on ALL above]
              │
              └── bu-ud62t (P1) Reconciliation gen-1 [depends on ALL above]
```

| Bead | Domain | Baseline | Target | Closed |
|------|--------|--------:|-------:|---|
| bu-zkrix | Contract extraction | 0 | ~200 | ✓ |
| bu-v7dn3 | Memory module | 1,544 | ~100 | ✓ |
| bu-35fm7 | Connectors + root conn files | 2,615 | ~150 | ✓ |
| bu-eu6jh | Migrations | 749 | ~50 | ✓ |
| bu-egmz6 | API tests | 1,779 | ~200 | ✓ (drifted; see Phase 2) |
| bu-7sd7a | Modules (non-mem) | 2,170 | ~400 | ✓ |
| bu-l1obx | Core + Daemon | 1,869 | ~400 | ✓ |
| bu-s8hn8 | Unassigned + root + sweep | 3,149 | ~600 | ✓ |
| bu-ud62t | Reconciliation gen-1 | — | — | ✓ |

## Commands

```bash
bd show bu-rhztl          # Epic details
bd list --parent bu-rhztl # All children with status
bd ready                  # What's unblocked now
bd dep tree bu-rhztl      # Full dependency tree
```

## Bead Lifecycle

### Claiming and starting work

```bash
bd update <bead-id> --claim    # Claim the bead
# Run scoped discovery on your domain first
# Then follow the workflow in SKILL.md
```

### Completing a bead

1. Pass all quality gates (SKILL.md)
2. Commit with delta: `"Condense X: N → M tests (rationale)"`
3. Close: `bd close <bead-id> --reason "Reduced from N to M tests. Details..."`

### If you need to pause mid-work

Do NOT close the bead. Commit your work-in-progress and add a comment:
```bash
bd update <bead-id> --comment "Pausing at N tests (target M). Next agent: resume from commit abc123."
```

### Creating new beads (for new modules or round 2)

If you discover a new test domain not covered by existing beads:

```bash
# Assess the new domain
NEW_DIR=tests/modules/NEW_MODULE
find "$NEW_DIR" -name '*.py' -exec grep -c 'def test_' {} + 2>/dev/null | awk -F: '{sum+=$2} END {print sum}'

# If >50 tests, create a new child bead
bd create \
  --title "Condense NEW_MODULE tests: XXX → ~YY" \
  --type task --priority 2 --parent bu-rhztl \
  --description "Apply three-tier architecture to new module. See butler-test-condensation skill."

# Wire dependency on Phase 1
bd dep add <new-bead-id> bu-zkrix
```

### Skill maintenance after completing a bead

Update this file's Quick Reference table with the actual final count and mark
the bead as done. This keeps the skill accurate for subsequent agents.
