---
name: reconcile-spec-to-project
description: >-
  Deep-dive reconciliation between OpenSpec specifications and the actual codebase
  implementation. Identifies feature gaps in both directions: (1) implemented but
  not documented in specs — creates new spec documents, (2) specified but not
  implemented — creates beads issues via /beads-writer. Use when auditing project
  completeness, after a milestone, before a release, or when the user asks to
  reconcile, audit, or compare specs vs. implementation.
metadata:
  owner: tze
  authors: [tze, Claude]
  status: active
  last_reviewed: "2026-09-05"
---

# Reconcile Spec to Project

Systematically compare every spec in `openspec/specs/` against the codebase to
find mismatches, then remediate: create missing specs for undocumented features,
and create beads for unimplemented requirements.

## Workflow

### Phase 1: Inventory Both Sides

Build two parallel inventories using subagents (see Parallelization below):

**Spec inventory** — for each `openspec/specs/*/spec.md`:
- Spec name (directory name)
- Category (butler, core, connector, module, dashboard, other)
- Key requirements (extract ADDED scenarios / WHEN-THEN clauses)
- Any delta specs in active `openspec/changes/*/specs/` that override or extend it

**Implementation inventory** — scan the codebase:
- `roster/*/` — butler configs, tools, skills, API routes, modules, migrations
- `src/butlers/modules/` — core modules and their tools
- `src/butlers/core/` — core infrastructure (daemon, scheduler, spawner, sessions, state, skills, telemetry)
- `src/butlers/api/routers/` — standard dashboard API routes
- `src/butlers/connectors/` — connector implementations
- `roster/*/api/` — butler-specific API routes

Capture for each implementation unit: name, what it does (read docstrings/comments), which tools/endpoints it exposes.

### Phase 2: Cross-Reference

Build a mapping table with columns:

| Spec | Category | Implementation Location | Coverage | Notes |
|------|----------|------------------------|----------|-------|

Coverage ratings: **Full** (all requirements implemented) · **Partial** (some
gaps remain) · **None** (spec exists, no implementation found) · **Undocumented**
(implementation exists, no spec covers it).

### Phase 3: Gap Analysis

Produce two gap lists:

**A. Spec-exists, not-implemented** (coverage = None or Partial): list each
unimplemented requirement with its spec file path and scenario text; group by
priority (core infrastructure gaps > module gaps > dashboard gaps > butler-specific gaps).

**B. Implemented, no-spec** (coverage = Undocumented): list each implementation
with file paths and a summary of what it does; group by category.

### Phase 4: Remediation

**For gap list A (unimplemented specs):**
1. Create a parent epic bead if more than 3 gaps exist:
   ```
   bd create --title="Implement spec gaps from reconciliation audit" \
     --type=epic --priority=2
   ```
2. For each gap, invoke `/beads-writer` to create a well-structured bead:
   reference the spec file path and unmet requirements in the description; set
   `--parent` to the reconciliation epic; use `task` for straightforward work
   or `feature` for new capability; priority P1 for core/infrastructure, P2
   for modules, P3 for dashboard/cosmetic.
3. Wire cross-dependencies with `bd dep add` after creation, not the `--deps`
   flag, where beads have ordering constraints.
4. Create child beads sequentially (`&&`-chained) — never in parallel, to
   avoid ID collisions. Mutations auto-commit to the shared Dolt server; there
   is no `bd sync` step. See `AGENTS.md` § Beads Workflow Integration for the
   current backend contract and full gotcha list.

**For gap list B (undocumented implementations):**
1. For each undocumented feature, create a new spec document at
   `openspec/specs/{spec-name}/spec.md` following the format and naming
   conventions in [references/spec-format.md](references/spec-format.md) —
   load it before writing or extending any spec. Extract requirements from
   actual code behavior; use Gherkin-style ADDED scenarios matching existing
   conventions.
2. If the undocumented feature fits an existing spec's scope, extend that
   spec instead of creating a new one.

### Phase 5: Summary Report

Output a concise reconciliation report:

```
## Reconciliation Summary

### Stats
- Specs audited: N
- Full coverage: N
- Partial coverage: N (M requirements gap)
- No implementation: N
- Undocumented implementations: N

### Actions Taken
- Beads created: N (epic: <id>)
- Specs created: N
- Specs extended: N

### Remaining Risks
- [any items that need human judgment]
```

## Critical Principles

### Specs Capture Spirit, Not Implementation Details
Specs describe **what** the system should do and **why**, not **how** it's
built. When comparing specs to code, focus on whether the *intent* and
*user-facing behavior* is fulfilled — not whether code structure matches the
spec's wording. Only flag a gap when the functional capability is missing or
documented behavior diverges from reality. Technical implementation choices
(data structures, internal APIs, module boundaries) are NOT spec concerns
unless load-bearing for correctness or UX.

When writing new specs for undocumented features, describe purpose and
observable behavior. Avoid prescribing internal architecture, class
hierarchies, or database schemas unless they are load-bearing contracts
(e.g., public schema tables other butlers depend on).

### Use Subagents Aggressively, Sequence Remediation Correctly
This is a large, multi-directory repository — always dispatch subagents
(Agent tool, `subagent_type=Explore` or `general-purpose`) for investigation
rather than reading everything in the main thread:
- One subagent per spec category for Phase 1's spec inventory
- One subagent per roster butler for Phase 1's implementation inventory
- Dedicated subagents for cross-cutting concerns (public schema, connectors, dashboard)

The main thread orchestrates, merges results, and does Phase 2/3 (cross-
reference and gap analysis need both inventories together) — not heavy
file-reading. Phase 4 remediation: specs can be written in parallel; beads
must be created sequentially (see Phase 4).

### Active Changes Override Main Specs
Specs in `openspec/changes/{change-name}/specs/` may override or extend main
specs. Always check active (non-archived) changes before flagging a gap — the
delta spec may already account for it.

## Key Conventions

- **Spec format and naming**: see
  [references/spec-format.md](references/spec-format.md) for the full
  structure (Purpose / ADDED Requirements / Scenario blocks), heading
  hierarchy, and naming patterns (`butler-{name}`, `core-{component}`,
  `module-{name}`, `connector-{name}`, `dashboard-{area}`) — load it whenever
  creating or extending a spec document.
- **Beads creation safety**: sequential creation only, `bd dep add` over
  `--deps`, no `bd sync` step (see Phase 4 above and `AGENTS.md` § Beads
  Workflow Integration).
