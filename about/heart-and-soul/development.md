# Development Principles

Four workflow constraints that are doctrine. Everything operational (test
ladder, quality gates, worktree and merge route, environment setup) lives in
[craft-and-care](../craft-and-care/README.md), the root `CLAUDE.md`, and
`AGENTS.md`; this file does not repeat it.

## Test First

Write the failing test, then the code that makes it pass. A failing test is a
precise specification of "done"; tests written afterwards test what the code
does, not what it should do. New features carry tests before merge; bug fixes
carry a reproducing test before the fix.

## Spec Before Code, RFC Before Exception

Significant work starts from an OpenSpec change (proposal, design, delta
specs): new modules, changes
to core infrastructure (state store, scheduler, spawner, session log), new
roster entries, cross-butler protocol changes, and schema changes that cross
butlers. Bug fixes, behavior-preserving refactors, documentation, tests, and
single-butler tool additions inside a module boundary do not need one. An
implementation that changes observable behavior amends its governing spec in
the same change.

The architectural constraints in [vision.md](vision.md) are non-negotiable by
default. A proposed exception (for example cross-schema access that bypasses
MCP-only inter-butler communication) needs a dedicated RFC with explicit
guardrails, reuse criteria, and cost justification, stating when the pattern
MAY be reused and when it MUST NOT (RFC 0010 is the template). An exception is
never precedent for unconstrained reuse.

## Manifesto-Driven Design

Every butler's `MANIFESTO.md` is a binding contract, not marketing copy. Check
it before adding a tool; amend it before expanding scope, and let the code
follow the manifesto rather than the reverse. When two butlers could own a
capability, the manifestos decide. A feature that contradicts its manifesto
does not ship.

## Work Exists Only in Beads

All work tracking is `bd` (beads): dependency-aware, Dolt-backed, agent
readable. Not markdown TODOs, inline TODOs, or external trackers. Work that is
not in beads does not exist for planning. An epic closes only after its shipped
OpenSpec change is archived (`/opsx:archive` runs before `bd close`) and the
affected specs are synced, in the same delivery.

## Anti-Patterns

- Code before its test.
- Core-infrastructure changes without a spec; doctrine exceptions without an RFC.
- Tools added to a butler without checking its manifesto.
- Work tracked anywhere but beads.
- Treating a granted exception as precedent.
