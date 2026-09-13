## Context

`roster/relationship/modules/tools.py` currently assigns eight tools to the
Relationship module's `entity` group:

| Tool | Effect | Selected state |
|---|---|---|
| `entity_resolve` | read | include in `entity` |
| `entity_get` | read | include in `entity` |
| `entity_neighbors` | read | include in `entity` |
| `relationship_fact_evidence` | read | include in `entity` |
| `relationship_predicate_coverage` | read | include in `entity` |
| `relationship_lookup` | read | include in `entity` |
| `entity_update` | write | keep pruned |
| `relationship_record_coverage` | write | keep pruned |

`relationship_assert_fact` is deliberately ungrouped because the daemon must
resolve approved fact writes against that registered handler even when it is
not selected as part of a module group.

The Relationship roster separately enables the memory module's `entity` group.
That module uses prefixed MCP names, including `memory_entity_create`. The
module-scoped group name does not make a memory tool part of the Relationship
module's group, and calling the Relationship group read-only does not describe
the entire combined daemon surface.

## Goals / Non-Goals

**Goals:**

- Give Relationship sessions the six owner-selected reads as one exact group.
- Keep both Relationship grouped writes absent from the activated roster
  surface.
- Preserve approved replay through `relationship_assert_fact`.
- Make canonical registration names and module ownership unambiguous.
- Keep misses, unknown coverage, unavailable targets, and backend failures
  distinguishable.

**Non-Goals:**

- Activating any group or editing roster/runtime configuration in this change.
- Changing the signatures or result schemas of the six reads.
- Renaming memory-module tools or adding a bare `entity_create` alias.
- Removing the two dormant write handlers from the Relationship module.
- Reading entity, relationship, provider, or production data.
- Restarting, deploying, merging, or queueing an implementation.

## Decisions

### The Relationship `entity` group is read-only by membership

The `entity` group SHALL contain exactly the six selected reads. The two writes
remain registered in source but SHALL use a separate, inactive
`entity_write` group so that selecting `groups = ["entity"]` cannot make them
callable. This makes the property mechanically testable at FastMCP registration
time.

The separate write group is not activated by this specification. Any future
proposal to expose it is a new tool-surface decision.

### The unconditional fact writer remains outside both groups

`relationship_assert_fact` SHALL remain registered independently of
`entity` and `entity_write`. Approved actions persist that exact tool name and
the daemon dispatches an approval by resolving it in the Relationship FastMCP
registry. Grouping it would break approved replay when the group is pruned.

This preserves existing behavior; it does not expand write authority or change
the writer's approval rules.

### Entity creation keeps one canonical MCP owner and name

The Relationship module SHALL NOT add `entity_create`. The memory module owns
entity creation and registers the canonical MCP name
`memory_entity_create`. Relationship's entity-resolution flow may call that
separately configured tool when resolution proves that creation is needed.

The bare read names remain Relationship-module tools because they provide the
Relationship-specific entity-first surface selected by the owner. Their names
do not create a second entity-creation path.

### Domain misses remain values; infrastructure failures remain failures

The new group does not change the read contracts:

- An unknown `fact_id` from `relationship_fact_evidence` returns
  `{fact: null, provenance: null, evidence: []}`. This differs from a known fact
  whose evidence list happens to be empty.
- `relationship_predicate_coverage` returns `unknown` when nobody has recorded
  coverage and `unavailable` for a missing or merged-away entity. Neither state
  is proof of absence.
- Resolution misses and ambiguity retain their existing structured
  `relationship_lookup`/entity-resolution results.

A database, registration, or handler failure is not any of those domain
states. The six tools SHALL let such failures surface through the normal MCP
error path and SHALL NOT fabricate `null`, an empty collection, `unknown`, or
`unavailable` as a successful result.

## Activation Boundary

After separate owner approval and implementation, the Relationship roster may
add `entity` to `[modules.relationship].groups` while leaving `entity_write`
absent. The relevant combined registration inventory is then:

- present from the Relationship read group: the exact six selected reads;
- absent from the Relationship write group: `entity_update` and
  `relationship_record_coverage`;
- present independently: `relationship_assert_fact`;
- present from the separately configured memory module:
  `memory_entity_create` under its canonical prefixed name.

No bare `entity_create` handler or alias may appear.

## Verification

The implementation must use a real FastMCP instance and inspect
`await mcp.list_tools()` after Relationship module registration. One focused
test selects only `entity` and proves the six reads are present, the two writes
are absent, and `relationship_assert_fact` remains present. A second focused
test omits both `entity` and `entity_write` and proves all eight grouped tools
are absent while the unconditional writer remains present.

Focused behavior tests must also pin the structured unknown-fact result,
uncovered-predicate `unknown`, missing-target `unavailable`, and propagation of
a representative backing-store failure. These tests verify the contract at the
handler boundary without reading live owner or provider data.

## Approval Sequence

1. Draft and independently review the exact OpenSpec artifact.
2. Obtain owner approval for the exact reviewed artifact.
3. Only then create and review a separate implementation change.
4. Keep restart, deployment, merge, and runtime verification separately
   authorized.
