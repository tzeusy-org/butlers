# Engineering Bar

This file defines what "done" means for non-trivial work in Butlers.

## Definition of Done

A non-trivial change is complete only when all of the following are true:

1. The change aligns with the owning butler's manifesto and with
   `about/heart-and-soul/`.
2. The relevant design contract and capability spec still hold, or they were
   updated in the same change.
3. The implementation is readable, explicit, and narrow in scope.
4. Regression protection exists at the right level.
5. The change was verified with risk-scaled checks, not just by inspection.
6. The affected docs, contracts, or operator guidance were updated in the same
   change.
7. The work can be pushed and handed off without hidden local state.

## Default Engineering Biases

These are the review defaults in this repository:

- **Prefer cleanup over same-repo compatibility cruft.** If the old path has no
  real external consumer, delete wrappers, aliases, dead flags, and fallback
  branches instead of preserving them indefinitely.
- **Prefer simple, explicit code over cleverness.** The daemon, modules, tools,
  and migrations should be easy to inspect and reason about.
- **Prefer durable fixes over expedient patches.** Do not stop at clearing the
  symptom when the real failure mode is tractable to fix.
- **Prefer fail-fast over silent fallback.** Incorrect assumptions should be
  surfaced clearly unless doctrine, a spec, or an RFC explicitly requires
  graceful degradation.
- **Prefer same-change doc updates.** Behavior, workflow, and contract changes
  must update the corresponding docs at the same time.
- **Prefer repository conventions over private style.** Use `uv`, Ruff, pytest,
  beads, OpenSpec, and the established async/testcontainer patterns already used
  here.

## Unacceptable Change Shapes

These are grounds for blocking a change:

- Code that contradicts a butler `MANIFESTO.md`
- Cross-schema or cross-butler shortcuts that bypass established contracts
- New behavior without tests when tests are practical
- Spec-required work implemented without checking or updating the spec
- Compatibility layers preserved only because deleting them felt risky
- Silent fallback branches that hide invalid state
- Partial fixes that leave the real invariant unclear
- New docs drift introduced by the change itself

## Change Hygiene

Good change hygiene in Butlers means:

- Keep edits scoped to the actual problem.
- Match local naming, structure, and file organization.
- Avoid broad rewrites unless the work is explicitly a refactor.
- Preserve user-owned changes and unrelated local work.
- Keep comments rare and intent-focused.
- Do not invent new process layers when the repo already has a standard one.

## Cross-Pillar Discipline

Before landing a change, ask four questions:

1. **Doctrine**: Should this exist at all?
2. **Design contracts**: Does it honor the RFC-level rules?
3. **Specs**: Does the behavior match what the system promises?
4. **Topology**: Does it live in the right place with the right boundaries?

If the answer to any of those changes, the docs must change too.
