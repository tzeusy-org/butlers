## Why

At baseline `63de6168fc852fcbf4e8fc24717585f64df0e2a5`, Butlers has two incompatible
system-prompt authority stories. Vision Rule 5 and the roster contract make git-tracked roster
content authoritative for butler identity, while the implemented dashboard editor makes the newest
`public.system_prompt_history` row replace that identity wholesale. The owner approved the bounded
direction "Roster identity plus owner operations overlay" in `bu-f5146g`; this change drafts the
separately reviewable contract required before that direction can be adopted or implemented.

The evidence map is `docs/plans/system-prompt-authority-decision.md`, merged in PR #4152 and last
changed by `d44446ea0ca6d35eee5098c7d9e4b57de50a5dd3`. Its Travel observation is stale at this baseline:
PR #4154 (`ebdfd64ea74787a89de3f723d2258304464c840c`) already restored Travel's first-line shared
include. This draft preserves that repair and preserves QA's separately approved staffer exception;
it changes no roster or prompt bytes.

## What Changes

- **BREAKING**: Replace full database prompt substitution with a closed, ordered composition:
  resolved roster identity, shared prompt snippets, an optional delimited owner-operations overlay,
  general timezone/locale/date/time/week/currency/measurement-system settings, situational context,
  blind-spot disclosure, Switchboard routing instructions,
  and memory context. An unnamed source cannot enter the system prompt.
- Keep the resolved roster chain present for every admitted roster agent in the target
  `roster_overlay` mode. Existing agents may remain in a one-way migration-seeded
  `precutover_legacy_hold` until individually reviewed; unknown agents and invalid roster roots fail
  before a target-mode runtime starts rather than allowing mutable database text or a generic
  fallback to stand in for identity.
- Specify recursive bare `@file.md` expansion in the shared core-skills seam, separately from the
  existing non-recursive HTML include contract. Domain butlers retain the first-line
  `@../shared/AGENTS.md` rule. Staffers use an explicit infrastructure-contract opt-in/opt-out; QA's
  approved opt-out remains in force and its prompt bytes remain unchanged.
- Reframe the existing prompt GET/PUT/history surface as owner-overlay management. Writes require
  fail-closed owner control before body or pool access, server-derived attribution, bounded input,
  optimistic concurrency, atomic audit, and content-blind non-owner evidence. The dashboard must
  present roster identity and mutable overlay as distinct sources and disclose that delimiters do
  not semantically constrain natural-language instructions.
- Add an independently enforceable database boundary: a direct-login prompt writer using its own
  pool, a non-login table owner, RLS-scoped runtime reads, and removal of historic runtime,
  connector, generic API, sequence, and public DML authority. No design depends on API `SET ROLE`.
- Preserve all historical rows with explicit legacy provenance. Stage schema and credentials before
  cutover, require owner-reviewed overlay activation, retain a reversible legacy-selection switch
  during the rollback window, and never silently reinterpret a legacy full replacement as an
  operations overlay.
- Specify that rollback switch as a separate owner-gated, append-only, compare-and-swap mode-history
  control using the same dedicated pool, ownership, RLS, audit, and privacy boundaries as overlays.
  Database privilege cutover must pass before either mode can make an overlay load-bearing.
- Make the rollback window fail closed from deployment-owned server configuration. No request,
  database row, MCP tool, runtime session, or generic API can open or extend it.
- Inventory every current source and writer seam in `design.md`. The active
  `k3s-deployment-helm-chart` and `specify-improvement-proposal-spine` changes remain the only
  whole-requirement owners of `core-skills / AGENTS.md Read/Write Access`; this change neither
  modifies that requirement nor adopts either candidate target state.

This is a draft contract only. It does not authorize implementation, schema or grant changes,
credential provisioning, prompt or roster edits, runtime/database access, deployment, archive,
merge, or release. Independent semantic/security review must pass on an exact commit, after which
the owner must separately adopt that exact reviewed artifact before implementation may be planned.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `butler-base-spec`: Preserve roster-rooted identity, domain shared-instruction composition, and
  the closed-source boundary for every spawned runtime.
- `core-skills`: Assign recursive, roster-confined bare-reference resolution to shared core without
  touching the actively contested AGENTS read/write requirement.
- `core-spawner`: Define the complete allowed prompt-layer order, source ownership, failure posture,
  and adapter-invariant composition.
- `dashboard-butler-management`: Replace full-prompt editing semantics with owner-gated,
  provenance-visible, concurrency-safe overlay management and history.
- `database-security`: Constrain prompt-history ownership, direct-login writes, RLS reads, historic
  grants, and bootstrap reconciliation.
- `staffer-archetype`: Make shared-instruction participation an explicit infrastructure-contract
  choice for every staffer.
- `staffer-qa`: Canonicalize QA's approved shared-instruction opt-out without changing QA prompt
  bytes.

## Impact

- Future implementation seams: `src/butlers/core/skills.py`,
  `src/butlers/core/spawner.py`, `src/butlers/core/spawner_context.py`,
  `src/butlers/api/routers/butler_management.py`, `src/butlers/api/db.py`,
  `src/butlers/api/owner_control.py`, a new forward core migration, and bootstrap/finalizer grants.
- Future verification seams: `tests/features/test_skills.py`,
  `tests/core/test_core_spawner.py`, `tests/core/test_core_spawner_context.py`,
  `tests/api/test_butler_management.py`, the existing roster include test, and a real-PostgreSQL
  migration/role suite using the actual direct-login and runtime-role contexts.
- Future governance-only QA seam: `roster/qa/MANIFESTO.md` plus `staffer-qa`; QA's `CLAUDE.md` and
  `AGENTS.md` remain byte-identical. Travel needs no further prompt edit from this change.
- Tests for this spec-only draft: `Tests: +0 ~0 -0`.
