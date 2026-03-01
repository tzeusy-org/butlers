---
name: reconcile-spec-to-project
description: >-
  Deep-dive reconciliation between OpenSpec specifications and the actual codebase
  implementation. Identifies feature gaps in both directions: (1) implemented but
  not documented in specs — creates new spec documents, (2) specified but not
  implemented — creates beads issues via /beads-writer. Use when auditing project
  completeness, after a milestone, before a release, or when the user asks to
  reconcile, audit, or compare specs vs. implementation.
---

# Reconcile Spec to Project

Systematically compare every spec in `openspec/specs/` against the codebase to
find mismatches, then remediate: create missing specs for undocumented features,
and create beads for unimplemented requirements.

## Workflow

### Phase 1: Inventory Both Sides

Build two parallel inventories using subagents:

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

Coverage ratings:
- **Full** — all spec requirements have corresponding implementation
- **Partial** — some requirements implemented, gaps remain
- **None** — spec exists but no implementation found
- **Undocumented** — implementation exists but no spec covers it

### Phase 3: Gap Analysis

Produce two gap lists:

**A. Spec-exists, not-implemented** (coverage = None or Partial):
- List each unimplemented requirement with its spec file path and scenario text
- Group by priority: core infrastructure gaps > module gaps > dashboard gaps > butler-specific gaps

**B. Implemented, no-spec** (coverage = Undocumented):
- List each implementation with file paths and a summary of what it does
- Group by category

### Phase 4: Remediation

**For gap list A (unimplemented specs):**
1. Create a parent epic bead if more than 3 gaps exist:
   ```
   bd create --title="Implement spec gaps from reconciliation audit" \
     --type=epic --priority=2
   ```
2. For each gap, invoke `/beads-writer` to create a well-structured bead:
   - Reference the spec file path and specific unmet requirements in the description
   - Set `--parent` to the reconciliation epic
   - Use appropriate type (`task` for straightforward work, `feature` for new capability)
   - Priority: P1 for core/infrastructure, P2 for modules, P3 for dashboard/cosmetic
3. Wire cross-dependencies with `bd dep add` where beads have ordering constraints
4. Create child beads sequentially (avoid parallel `bd create` — ID collision risk)

**For gap list B (undocumented implementations):**
1. For each undocumented feature, create a new spec document:
   - Path: `openspec/specs/{spec-name}/spec.md`
   - Follow the existing spec format (see references/spec-format.md)
   - Extract requirements from the actual code behavior
   - Use Gherkin-style ADDED scenarios matching existing spec conventions
2. If the undocumented feature fits within an existing spec's scope, extend that spec
   instead of creating a new one

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
Specs describe **what** the system should do and **why**, not **how** it's built.
When comparing specs to code, focus on whether the *intent* and *user-facing behavior*
described in the spec is fulfilled — not whether the code structure matches the spec's
wording. Only flag a gap when the functional capability is missing or the documented
behavior diverges from reality. Technical implementation choices (data structures,
internal APIs, module boundaries) are NOT spec concerns unless they are critical to
the feature's correctness or user experience.

When writing new specs for undocumented features, describe the feature's purpose and
observable behavior. Avoid prescribing internal architecture, class hierarchies, or
database schemas unless they are load-bearing contracts (e.g., shared schema tables
that other butlers depend on).

### Use Subagents Aggressively for Investigation
This is a large, multi-directory repository. Always dispatch subagents (Agent tool
with `subagent_type=Explore` or `subagent_type=general-purpose`) for investigation
work rather than trying to read everything in the main thread. Typical patterns:
- One subagent per spec category to read and summarize requirements
- One subagent per roster butler to inventory tools, skills, API routes, and modules
- Dedicated subagents for cross-cutting concerns (shared schema, connectors, dashboard)

The main thread should orchestrate, merge results, and make decisions — not do the
heavy file-reading itself.

## Key Conventions

### Spec File Format
Each spec lives at `openspec/specs/{name}/spec.md` and follows this structure:

```markdown
# {Component Name}

## Purpose
One-line mission statement.

## ADDED Requirements

### Scenario: {Scenario Name}
WHEN {condition}
THEN {expected behavior}
AND {additional assertions}
```

### Spec Naming
- Butler specs: `butler-{name}` (e.g., `butler-finance`)
- Core specs: `core-{component}` (e.g., `core-daemon`)
- Module specs: `module-{name}` (e.g., `module-calendar`)
- Connector specs: `connector-{name}` (e.g., `connector-telegram-bot`)
- Dashboard specs: `dashboard-{area}` (e.g., `dashboard-api`)

### Active Changes Override Main Specs
Specs in `openspec/changes/{change-name}/specs/` may override or extend main specs.
Always check active (non-archived) changes before flagging a gap — the delta spec
may already account for it.

### Beads Creation Safety
- Create child beads sequentially (`&&`-chained), never in parallel
- Run `bd sync` between unrelated batches
- Use `bd dep add` after creation, not `--deps` flag
- See CLAUDE.md "Beads CLI Gotchas" for full list

## Parallelization Strategy

Use subagents for the read-heavy inventory phases:
- One agent per spec category (butler, core, connector, module, dashboard)
- One agent per roster butler for implementation inventory
- Cross-reference and gap analysis in the main thread (needs both inventories)
- Remediation: specs can be written in parallel, but beads must be sequential
