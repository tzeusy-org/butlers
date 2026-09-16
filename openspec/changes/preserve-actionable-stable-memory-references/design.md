## Context

`memory_context` deterministically renders local facts and rules under one
server-held sensitivity ceiling, but its text lines omit their stable IDs.
The existing `memory_confirm`, `memory_mark_helpful`, and
`memory_mark_harmful` actions therefore cannot reliably target a memory used
from injected context.

## Goals / Non-Goals

**Goals:**

- Preserve one canonical bounded reference containing only memory type and UUID.
- Resolve references through the existing feedback actions.
- Enforce the same server-held privacy ceiling and live-memory rules in the
  atomic mutation.
- Keep all reference text inside existing section and total token budgets.

**Non-Goals:**

- No new memory source, entity/graph behavior, cross-butler mutation, caller
  authority input, raw identity field, or privacy-ceiling relaxation.
- No actionable reference for episodes or Fleet Knowledge catalog summaries.

## Decisions

### Use a canonical typed UUID reference

Local context renders `fact:<uuid>` and `rule:<uuid>`. The type prevents a
rule-only action from being redirected to a fact while the UUID remains the
stable opaque identifier already owned by the memory row. The reference adds
no subject, entity, tenant, sensitivity, or source identity.

### Resolve authority at the module boundary

Public MCP closures load the existing runtime-config-held read policy; callers
cannot supply or raise it. Feedback mutations apply the allowed-sensitivity
predicate and live-row predicate in the same SQL statement that changes the
memory. A denied UUID therefore has neither an observable distinction from an
absent one nor a mutation side effect.

### Keep the current inputs as a compatibility surface

The existing feedback tools retain their current type/ID or rule-ID inputs and
add a mutually exclusive `memory_ref` input. This is a public MCP tool surface
used by ephemeral sessions, so abruptly deleting the current shape would turn
the actionability repair into an unrelated breaking migration.

### Render references only for locally owned actionable rows

Profile Facts, Task-Relevant Facts, and Active Rules receive references.
Episodes have no matching feedback contract, and Fleet Knowledge rows point at
another butler's canonical store, so neither section receives an action handle.

## Risks / Trade-offs

- [Reference text reduces room for content] -> It participates in the existing
  section allocation by construction; the renderer omits a whole line that no
  longer fits rather than overflowing.
- [A stale or above-ceiling UUID looks absent] -> This deliberate
  non-disclosure prevents the feedback surface from becoming a privacy oracle.
- [Two target input shapes remain temporarily] -> The existing MCP contract is
  preserved, but both shapes resolve through one parser and one authorized
  storage path rather than parallel implementations.

## Migration Plan

No schema or data migration is required. Deploy the renderer, resolver, and
atomic authorization predicates together; existing UUID callers remain valid.
