# RFC0031: Public Entity Graph Projection

**Status:** Decision record; implementation partial. Source audit: 2026-10-05.

The complete behavior contract is [entity-graph](../../../openspec/specs/entity-graph/spec.md), requirements `REQ-entity-graph-001` through `012`. This record retains the projection decision and its tradeoffs. Source transfer does not complete the graph feature or adopt an unresolved grant posture.

## Problem

Entity anchors already exist in the memory catalog, but similarity and full-text search do not answer graph questions. Relationship's canonical triples are private to its schema, and memory assertions and commitments live elsewhere. Reconstructing relationships through a per-question LLM session in every butler makes a deterministic read expensive and unreliable.

## Decision

Use a source-owned, write-behind public projection for eligible entity-to-entity assertions. Each owning source transaction maintains its projection; recursive public graph reads provide zero-LLM traversal and eventual dossier accounting without granting readers access to canonical private schemas.

The stable source natural key preserves provenance and supports repeatable historical projection. Count-only sensitivity stubs preserve honest coverage while structurally withholding predicate/object payload. Canonical requirements 001-004 and 012 carry the table, atomicity, privacy, lifecycle, recovery and substrate obligations; requirements005-011 carry traversal, registration, catalog coverage and the original dossier API/dashboard outcomes.

## Tradeoffs and Alternatives

- **Projection versus private-schema view.** A live view over Relationship, Memory and commitment sources would depend on cross-schema grants or a security-definer exception. Source-owned projection avoids that read-authority expansion, at the cost of mandatory atomic writer effects and historical recovery. A silently divergent projection cannot be trusted without re-deriving from source.
- **Recursive SQL versus Switchboard fan-out.** Per-step MCP/LLM fan-out reintroduces session cost proportional to traversal depth. The public recursive read follows the deterministic-access principle of RFC0010/RFC0030 without adding their cross-schema read exception.
- **Withheld stubs versus silent omission.** Dropping sensitive assertions makes coverage look complete when it is not. Stubs permit known/withheld accounting without traversable hidden content; they do not authorize disclosure of the underlying assertion.
- **Cooperative graph DML versus centralized catalog GC.** Graph writers need DELETE for their own retraction effects. Existing shared runtime grants are an application cooperation model, not row ownership isolation or protection against a shared-login compromise. The separate catalog/central-GC grant contract remains in database-security; broad bootstrap DELETE is still an unresolved S7 rider, not an exception adopted here.

No belief revision, contradiction docket, ontology unification or duplicate-predicate resolution is added. Readers do not write. Entity identity/channel resolution and existing memory-catalog anchor columns remain governed by their current contracts. The distinct entity_neighbors Google-account exclusion is not a new public graph traversal filter.

## Observed Implementation and Remaining Obligations

This is a source audit, not an all-clause behavioral PASS:

| Original slice | Current source | Mandatory remaining outcome or proof |
| --- | --- | --- |
| S1: substrate | Core215 table/keys/checks/indexes/grants; core222 adds Concierge | Dedicated migrated FK/cascade/role/bootstrap/core-only/shared replay proof remains012 debt; fixtures without entity foreign keys traversal stand-ins cannot certify it. |
| S2: writers and historical backfill | Several Memory/Relationship writers, commitment creation hook, three bounded helpers | Complete eligible writer/lifecycle inventory and historical invocation/recovery parity remain requirements 002/004 debt. Unary rules cannot receive invented anchors. Resolved commitments retain historical edges while current backfill selects open/aging only; that gap is not authority to retract history. |
| S3: walk/path tools | Public recursive helpers, wrappers and graph-group registration | Existing cited tests cover selected SQL/control-flow seams; hard-limit and broader runtime/authority clauses require their own meaningful evidence. |
| S4: catalog coverage | Incident known/withheld count helper and catalog response mapping | Stand-in SQL and mocked API mapping do not certify migrated handler/source-count equivalence. |
| S5: original dossier API | No `/api/entities/{id}/dossier` handler/model found | Requirement010 remains mandatory and uncited. |
| S6: EntityDetailPage dossier panel | Existing neighbor/activity panels, no graph dossier consumer | Requirement011 remains mandatory and uncited. |
| S7: debt riders | Activity fixed degradation envelope exists in router/model/UI | Exact backend discriminator controls remain unverified. Catalog bootstrap/default grants still conflict with central-GC no-runtime-DELETE intent; no narrowing posture is adopted. |

The existing feature terminal is `bu-8cdl1.8`, still open under its existing coordinator. The [source-transfer design](../../../openspec/changes/archive/2026-10-05-document-rfc0031-entity-graph/design.md) retains the clause/test limits and bounded residual foundation, dossier-flow and debt-rider allocations. These are followup proposals, not new feature terminals or ownership transfers.

## Governing Neighbors

- [Identity model](../../../docs/concepts/identity-model.md), RFC0004 and [entity-identity](../../../openspec/specs/entity-identity/spec.md) retain shared entity identity, channel resolution and the owner carve-out.
- RFC0002/[core-daemon](../../../openspec/specs/core-daemon/spec.md) and the existing runtime tool-discovery source retain graph-group registration authority.
- RFC0006/[database-security](../../../openspec/specs/database-security/spec.md) retain schema/role and catalog write authority. The active public-write audit is proposal-only; no per-table narrowing is inferred.
- [Memory catalog schema](../../../openspec/specs/memory-catalog-schema/spec.md) and its read-authority contracts retain entity anchors and canonical-content custody.
- [Dashboard Relationship](../../../openspec/specs/dashboard-relationship/spec.md) owns the existing activity degradation envelope. The active Chronicles/commitment-door proposal excludes graph consumption and is not the dossier outcome.
- Relationship assertion lifecycle/effective-time source remains unchanged; graph work neither adopts a temporal cutover nor narrows active assertions to effective-now.
