## Why

The canonical entity activity endpoint currently reads identity triples from `relationship.entity_facts`, while notes, interactions, gifts, loans, life events, and tier overrides are written to the relationship schema's memory-module `facts` table. The split makes owner-authored activity disappear and reduces relationship rows to predicate labels even though the stored records carry meaningful text.

This draft proposes an exact owner decision before backend work resumes. It reconciles the dashboard contract with the accepted two-store Relationship contract while preserving the identity rows the endpoint returns today and the existing Chronicler boundary.

## What Changes

- Modify the entity activity contract to merge active rows from three existing sources: relationship-scoped narrative `facts`, currently returned `relationship.entity_facts` rows, and Chronicler episodes fetched through MCP.
- Add a normalized `summary` for meaningful display and a nullable `store` discriminator (`narrative | identity`) for relationship rows. A row's source-qualified identity is `(src, store, id)`; Chronicler rows use `(src, null, id)`.
- Define exact row families, timestamp mapping, active-only lifecycle behavior, merged totals, pagination, daily bins, healthy-empty behavior, and content-blind Chronicler degradation.
- Preserve existing direct-write and update behavior. The activity read neither restores deleted rows nor rewrites or heuristically deduplicates data across stores.
- Correct the activity requirement's tier-override source prose to match the current writer without changing that writer.
- Preserve owner-only authorization, the Chronicler MCP-only/no-SQL invariant, and current source-failure semantics.
- Keep this change unapproved until the owner adopts the exact draft. Backend implementation and frontend PR 4057 remain blocked until then.

This change does not add schema, migrations, backfills, dual writes, grants, catalog reads, sensitivity ceilings, raw metadata projection, graph-catalog behavior, frontend behavior, runtime actions, or provider access.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-relationship`: Amend the existing entity activity aggregator requirement to bind the source-correct three-way union and meaningful row projection.

## Impact

- Proposed API contract: `GET /api/relationship/entities/{id}/activity`, including its paginated, daily-bin, and bins-only forms.
- Future bounded backend implementation: `roster/relationship/api/models.py`, `roster/relationship/api/router.py`, and relationship API/real-Postgres/contract/boundary tests.
- Preserved boundaries: Relationship remains the only SQL reader of its two local stores; Chronicler remains MCP-only; the owner read gate remains unchanged.
- Dependencies: owner adoption of this exact changeset precedes `bu-th3ikr`; `bu-bbwur` / draft PR 4057 remains blocked on the later backend fix.
