## Context

See `proposal.md` for motivation. The current entity activity handler reads only `relationship.entity_facts`, although the accepted Relationship role contract and the existing note, interaction, gift, loan, life-event, and tier-override writers use the relationship schema's memory-module `facts` table. The current DTO has no relationship content field. Existing mocked tests encode the incorrect single-store query and never exercise POST-to-read persistence.

The endpoint also has established behavior that this amendment must retain: all active subject/object identity rows are returned, Chronicler is called through MCP, source failure is content-blind and distinguishable from healthy empty, the endpoint is owner-only, and stream and daily-bin responses share one aggregator.

## Goals / Non-Goals

**Goals:**

- Read each existing Relationship store according to its current ownership and lifecycle contract, then merge the results with Chronicler episodes.
- Preserve current identity rows and make existing direct narrative writes visible with meaningful text.
- Give every row a stable source-qualified identity without manufacturing a new persistence identifier.
- Keep pagination, bins, lifecycle filtering, authorization, and failure semantics exact and testable.

**Non-Goals:**

- Changing any writer, predicate registry, schema, migration, backfill, grant, catalog policy, sensitivity ceiling, entity graph, or frontend.
- Copying, repairing, deleting, restoring, or deduplicating persisted rows.
- Exposing raw metadata, assertion evidence, sensitivity labels, upstream payloads, or failure tails.
- Adopting, syncing, archiving, implementing, marking ready, or merging this owner-decision draft.

## Decisions

### Read the two local stores separately and union normalized rows

The backend will issue one bounded read against local memory `facts` and one against `relationship.entity_facts`, normalize each result to `ActivityEntry`, and merge them in application memory with the existing Chronicler result. It will not join the local stores. This preserves the Relationship Facts no-cross-join boundary and lets each query use its own lifecycle, entity anchor, and timestamp columns.

The narrative query uses the same exact predicate family as the existing entity Timeline, with `dunbar_tier_override` explicitly included because both linked-contact and contactless tier writers currently persist it in memory `facts`. This statement corrects the activity source prose only; no writer or stored row moves.

Alternative: replace the identity source with narrative facts. Rejected because it silently removes rows the current activity endpoint returns. Alternative: move or dual-write narratives into `entity_facts`. Rejected because it changes data ownership and lifecycle, needs migration/reconciliation, and creates partial-write states.

### Add one normalized display field and preserve source fields

`summary` becomes the normalized display value for all rows: narrative `content`, identity `object`, or the corrected Chronicler title. This is additive to the current payload. `store` distinguishes the two local stores while `src` continues to identify the owning domain (`relationship` or `chronicler`). Fields that do not apply remain explicit nulls. No raw metadata is added.

For identity rows, returning the exact stored object avoids inferred prose and extra identity lookups. The owner-only gate already protects raw identity objects. Any future friendly-name projection for entity-valued objects is separate product behavior.

Alternative: overload `predicate` as display text. Rejected because that is the current information loss. Alternative: add source-specific `content` and `value` fields. Rejected because consumers would need source branching and could still omit one; the normalized display contract is smaller.

### Define row identity as the source tuple

The source UUID remains `id`; `(src, store, id)` is the stable row identity. This avoids a migration and prevents same-valued UUIDs from different stores from colliding in clients. No text/time heuristic may collapse cross-store rows because no approved equivalence key exists.

### Normalize time before one merge, page, and bin pass

Narrative time is `COALESCE(valid_at, created_at)`. Identity time is `COALESCE(observed_at, last_seen, created_at)`. Chronicler time remains `COALESCE(canonical_start_at, start_at)`. The merged list sorts descending, nulls last, with the source tuple as a deterministic tie-break. Offset/limit and daily bins are derived only after this normalization.

`total` describes the materialized candidate set for the request before pagination. It does not pretend that a bounded upstream result contains unseen history. Daily bins count that same pre-pagination set, so changing the page cannot change the sparkline.

### Preserve the established trust boundary

The handler keeps its owner-role gate. Both local reads use the existing relationship pool and need no new grant or catalog path. The relationship memory catalog ceiling remains unrelated and unchanged. Chronicler remains an MCP call with the participant entity filter; the fixed `chronicler_activity_unavailable` discriminator is the only failure detail returned.

## Risks / Trade-offs

- [Adding the narrative source can increase response work] → Keep the existing bounded route parameters, use indexed active/entity/predicate filters, and measure the targeted API test before considering optimization.
- [The current identity stream includes state-like triples as well as events] → Preserve them because removal is a separate owner-visible contract change; mark them `store: 'identity'` so clients can distinguish them.
- [Legacy rows can be semantically duplicated across stores] → Return both with source-qualified identities; do not guess equivalence or rewrite history.
- [Identity objects can contain PII] → Preserve the existing owner-only gate and emit no metadata or evidence fields.
- [One source can fail while others succeed] → Preserve the existing explicit Chronicler degradation and never convert it to a healthy empty response.
- [Future loan work overlaps lifecycle assertions] → Do not edit loan writers here; refresh and serialize the implementation tests with `bu-2jtfw.12` before backend work begins.

## Migration Plan

1. Obtain exact owner adoption of this draft before implementation.
2. Add the real-Postgres reproducer and focused contract tests, then implement only the DTO and aggregator read changes.
3. Deploy through the normal merge queue after exact-head review. No data migration or backfill runs.
4. Rollback is a code revert of the additive read/DTO change; both stores and all rows remain untouched.

## Open Questions

None. Changing which identity rows are shown, adding friendly-name resolution, or re-homing tier overrides would alter the approved scope and requires a separate proposal.
