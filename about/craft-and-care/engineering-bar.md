# Engineering Bar

This file defines what "done" means for non-trivial work in Butlers.

## Default Bar (Adopted by Reference)

The canonical default quality bar -- the nine engineering biases, the
definition of done, and the change-level subskills that operationalize them
(code-readability, test-rigor, dependency-hygiene, cruft-cleanup) -- lives in
`/th-engineering` (engineering-bar subskill, part of the maintainer's global
skill catalog). Butlers adopts that bar by reference; it is deliberately not
restated here, because a copied bar drifts while a referenced bar stays
current.

Reviewers enforce the merged result: the default biases plus the
Butlers-specific standards below. When this file conflicts with the default
bar, this file wins. If the skill is unavailable in your environment, the
narrower standards docs in this directory still bind on their own.

## Butlers-Specific Definition of Done

In addition to the default definition of done, a non-trivial change here is
complete only when:

1. The change aligns with the owning butler's manifesto and with
   `about/heart-and-soul/`.
2. The relevant design contract (`about/legends-and-lore/`) and capability
   spec (`openspec/`) still hold, or they were updated in the same change.
3. The work can be pushed and handed off without hidden local state.

## Bias Overrides and Additions

No default bias is overridden. One Butlers-specific addition:

- **Prefer repository conventions over private style.** Use `uv`, Ruff,
  pytest, beads, OpenSpec, and the established async/testcontainer patterns
  already used here. Do not invent new process layers when the repo already
  has a standard one.

## Unacceptable Change Shapes

Beyond default-bar violations, these are Butlers-specific grounds for blocking
a change:

- Code that contradicts a butler `MANIFESTO.md`
- Cross-schema or cross-butler shortcuts that bypass established contracts
- Spec-required work implemented without checking or updating the spec

## Change Hygiene

- Keep edits scoped to the actual problem; avoid broad rewrites unless the
  work is explicitly a refactor.
- Preserve user-owned changes and unrelated local work.

## Cross-Pillar Discipline

Before landing a change, ask four questions:

1. **Doctrine**: Should this exist at all?
2. **Design contracts**: Does it honor the RFC-level rules?
3. **Specs**: Does the behavior match what the system promises?
4. **Topology**: Does it live in the right place with the right boundaries?

If the answer to any of those changes, the docs must change too.
