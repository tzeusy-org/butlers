# Landed B5/B6 Transfer Record

## Status and scope

This is an [Observed] governance transfer for PR #3728 only. It moves the
already-landed consolidation producer/storage fence out of this unfinished
carrier and into the canonical `module-memory` requirement
`Consolidation narrative edges use an exact local allowlist`.

It is not acceptance of B1-B4 or Tracks C-E. Their unchanged active
requirements and 17 unchecked tasks remain the authority for the carrier's
registry, generic-writer, skill, backfill, vCard, migration, live-verification,
and archive work.

## Canonical observed authority

| Landed slice | Canonical destination | Observed source and focused evidence |
|---|---|---|
| B5 — retain a valid `object_entity_id`, reject malformed targets, and forward a well-formed new edge to the storage boundary | `openspec/specs/module-memory/spec.md` — `REQ-module-memory-012`, approved-edge scenario | PR #3728; `tests/modules/memory/test_consolidation_executor.py::test_execute_consolidation_forwards_new_narrative_edge_target`; `tests/modules/memory/test_consolidation_executor.py::test_execute_consolidation_defers_unapproved_edge_to_storage_boundary` |
| B6 — exact local three-predicate admission at the storage boundary, fail closed on unavailable classification, and preserve generic/relationship isolation | `openspec/specs/module-memory/spec.md` — `REQ-module-memory-012`, rejected-edge scenario | PR #3728; `tests/modules/memory/test_storage_provenance.py::TestConsolidationNarrativeEdgeBoundary::test_direct_storage_fails_closed_for_unavailable_edge_classification`; `tests/modules/memory/test_consolidation.py::test_consolidation_skill_names_the_exact_v1_narrative_edge_allowlist` |

The canonical observed requirement permits only `planned_dinner_with`,
`wake_coordination`, and `social_exchange_with` for newly consolidated edges.
It is a storage-boundary rule; it neither reads nor writes
`relationship.entity_predicate_registry` or `relationship.entity_facts`, and
it leaves generic `memory_store_fact()` admission unchanged.

## Remaining carrier authority

The active carrier deliberately retains only its unfinished scopes. It is not
archived and does not gain completion or acceptance from this transfer:

- Track A — registry seed, aliases, and tests remain unfinished.
- Track B1-B4 — relationship-skill routing, generic writer enforcement, and
  their contract/regression tests remain unfinished.
- Track C — backfill, dry-run/apply evidence, and live-dev action remain
  unfinished and unauthorized here.
- Track D — `quick_facts` deprecation and migration remain unfinished.
- Track E — focused verification, manual concentration verification, and
  carrier archive remain unfinished.

No source/API/UI/runtime/DB/migration/provider behavior is changed by this
record. It is a canonical-authority transfer for an already-merged bounded
slice, not a general graph-policy decision or a relationship-write grant.
