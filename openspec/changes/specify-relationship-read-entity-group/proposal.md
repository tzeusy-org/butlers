## Why

The Relationship module's `entity` group currently mixes six reads with three
writes. The roster therefore keeps the whole group pruned, which also hides the
evidence and coverage reads that let a Relationship session distinguish known
facts, proven absence, and unchecked data.

The Relationship role spec also names a bare `entity_create` tool that the
Relationship module does not register. Entity creation belongs to the separately
configured memory module and its canonical MCP name is `memory_entity_create`.
Keeping both names in the contract makes registration inventories ambiguous.

Owner decision `bu-uedyz` selected a read-only Relationship group containing
exactly six named reads. That initial decision authorized the specification only. Closed owner decision
`bu-t89d9m`, option A (2026-10-20 14:36 UTC), subsequently authorized the
implementation, six-read roster selection, posture/write-group amendments and
the Relationship AGENTS Your Tools update through normal PR review.

## What Changes

- Define the Relationship module's `entity` group as exactly
  `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`, and
  `relationship_lookup`.
- Keep the grouped writes `entity_update`,
  `relationship_record_coverage`, and `entity_set_posture` outside that read group and outside the
  Relationship roster's activated groups.
- Preserve the ungrouped `relationship_assert_fact` registration required by
  approved fact-write replay.
- Reconcile the Relationship role contract to name the memory module's entity
  creation tool only as `memory_entity_create`; no bare `entity_create` MCP
  alias is introduced.
- Require exact FastMCP registration and absence tests, explicit miss/unknown
  results, and failures that remain failures instead of becoming empty data.

## Capabilities

### Modified Capabilities

- `butler-relationship`: correct the tool inventory and define the read-only
  Relationship entity group and its activation boundary.

## Impact

Under that closed implementation authority, the implementation changes:

- `roster/relationship/modules/tools.py` to separate the three grouped writes
  from the six reads.
- `roster/relationship/butler.toml` to activate only the Relationship `entity`
  read group.
- Focused Relationship module tests using a real FastMCP registry.
- The maintained tool-group inventory after registration behavior changes.

The source configuration selects only the six-read group. It does not perform
entity or coverage mutation, provider/data reads, daemon restart, deployment,
production activation, or merge.
