## MODIFIED Requirements

### Requirement: Entity activity aggregator (cross-butler read surface)

The dashboard API SHALL expose `GET /api/relationship/entities/{id}/activity` as a relationship-owned aggregator over three existing sources: narrative relationship memory, identity and relational triples, and Chronicler episodes. It MUST preserve the records already returned by the endpoint while making records written through the existing relationship activity actions visible with their stored text.
- **Historical source clause, superseded by the effective contract below:** 1. Relationship-domain rows from `relationship.entity_facts` (notes, interactions, life events, gifts, loans, dunbar_tier_override) — tagged `src: 'relationship'`. 2. Chronicler-domain rows tagged with `src: 'chronicler'` (kind: `episode`). This exact prior authority is retained for review and archive safety; it is not an operative instruction to move or dual-write narrative records into the identity store.
- **Narrative Relationship rows:** The response MUST include active rows from the relationship schema's memory-module `facts` store where `entity_id` is the requested entity, `scope = 'relationship'`, and the predicate is `contact_note`, `life_event`, `gift`, `loan`, `dunbar_tier_override`, or matches `interaction_%`. Each row MUST carry `src: 'relationship'`, `store: 'narrative'`, its source UUID as `id`, its exact stored `content` as `summary`, and `ts = COALESCE(valid_at, created_at)`. The `dunbar_tier_override` row is read from this store because the existing tier-override writers persist it there; this amendment does not change that writer or move existing rows.
- **Identity Relationship rows:** The response MUST retain every active row it already exposes from `relationship.entity_facts` where the requested entity is the subject or is the entity-typed object. Each row MUST carry `src: 'relationship'`, `store: 'identity'`, its source UUID as `id`, its exact stored `object` as `summary`, and `ts = COALESCE(observed_at, last_seen, created_at)`. Adding narrative rows MUST NOT drop an existing identity or relational row.
- **Separate local reads:** The two Relationship stores MUST be queried independently and merged after the reads. They MUST NOT be joined or cross-joined to each other. Rows MUST NOT be copied, rewritten, restored, or heuristically deduplicated across stores. The source UUID remains the `id`; consumers MUST treat `(src, store, id)` as the stable source-qualified row identity and MUST NOT assume that `id` alone is globally unique.
- The chronicler rows MUST be fetched **via chronicler's MCP tools** — `chronicler_list_episodes` per RFC 0014:255-258 (the brief named `chronicler_list_events` but RFC 0014 lists only `list_episodes` / `get_episode` / `submit_correction`; Phase 2 chooses to use only currently-listed MCP tools rather than propose a new tool).
- **Chronicler row mapping:** The MCP call MUST use the requested entity as the participant filter. Each episode MUST carry `src: 'chronicler'`, `store: null`, its episode UUID as both `id` and `episode_id`, `kind: 'episode'`, `summary = COALESCE(canonical_title, title)`, and `ts = COALESCE(canonical_start_at, start_at)`. The Relationship butler MUST NOT substitute a catalog read for the Chronicler MCP call.
- **Hard invariant (mirror `rfcs/0014:178` "Tests MUST exercise the no-LLM invariant for every adapter"):** the relationship butler MUST NOT issue direct SQL into `chronicler.*` schemas. A guardrail test in `roster/relationship/tests/test_chronicler_boundary.py` MUST assert that the `activity` aggregator implementation does not import any `chronicler.*` ORM model and does not contain the substring `FROM chronicler.` or `JOIN chronicler.` in any SQL string.
- **Success projection and authority:** Every returned row MUST carry `id`, `ts`, `kind`, `src`, `store`, `predicate`, `episode_id`, and `summary`, using explicit nulls for fields that do not apply. `summary` is the only new display projection: the response MUST NOT include raw memory metadata, assertion evidence notes, stored sensitivity labels, upstream payloads, or failure text. Existing owner-only authorization for this PII-bearing read MUST remain in force. This read MUST NOT add a database grant, a caller-selected sensitivity ceiling, or wider memory-catalog authority.
- **Merge and pagination:** The merged candidate set MUST include only active Relationship rows plus successfully read Chronicler episodes. It MUST sort by `ts` descending, place null timestamps after timestamped rows, and use `(src, COALESCE(store, ''), id)` ascending as the deterministic tie-break. Offset and limit MUST be applied only after that merge and sort. `total` MUST equal the number of entries in the candidate set before offset and limit; it MUST NOT claim entries an upstream bounded read did not return.
- **Daily bins:** When `bins=daily` is requested with a valid existing `window=<N>d`, the response MUST derive exactly `N` ascending UTC date bins from the same merged candidate set before stream pagination. Each entry with a timestamp inside the inclusive UTC window contributes exactly once to its date; out-of-window and null-timestamp entries contribute zero. Quiet dates MUST remain present with `count=0`. With `bins_only=true`, the response MUST omit stream items and totals but MUST retain the same bins and degradation fields. Binning MUST NOT perform another source read or use a different source set from the stream.
- The Timeline tab (defined above) and the activity aggregator coexist; the Timeline tab renders the aggregator output as the merged stream.

ID: REQ-dashboard-relationship-001
Source: Relationship Butler Role "CRUD-to-SPO migration"; Relationship Facts "Relationship entity facts triple store"; RFC 0006 schema isolation
Scope: v1-mandatory

#### Scenario: Direct activity writes become visible with their stored text

- **WHEN** an owner creates a note, interaction, or gift through the existing entity endpoint and then reads that entity's activity
- **THEN** the activity response MUST contain the persisted row with the same source UUID, the correct predicate family and timestamp, `store: 'narrative'`, and the exact stored content in `summary`
- **AND** the row MUST appear without a dual write or a copied identity-store row

#### Scenario: Narrative update lifecycle returns only the active successor

- **WHEN** an existing gift or loan row is superseded by its current update operation
- **THEN** activity MUST contain the active successor exactly once with its current stored content
- **AND** the superseded row MUST NOT be returned or restored

#### Scenario: Existing identity rows remain visible with their values

- **WHEN** an active `relationship.entity_facts` row names the requested entity as subject or entity-typed object
- **THEN** activity MUST retain that row with `store: 'identity'` and the exact stored object in `summary`
- **AND** adding narrative rows MUST NOT remove, rewrite, or heuristically merge the identity row

#### Scenario: Retracted and superseded rows stay absent

- **WHEN** a relationship row in either local store has validity `retracted` or `superseded`
- **THEN** activity and daily bins MUST exclude that row
- **AND** entity merge and forget operations MUST retain their existing repoint and retraction behavior

#### Scenario: Source-qualified identities prevent cross-store collisions

- **WHEN** two returned rows from different stores have the same UUID value
- **THEN** both rows MUST remain in the response
- **AND** consumers MUST distinguish them by `(src, store, id)` rather than dropping either row

#### Scenario: Activity aggregator merges via MCP only

- **WHEN** `GET /api/relationship/entities/<id>/activity` is called and chronicler episodes mention the entity
- **THEN** the aggregator MUST call `chronicler_list_episodes` via MCP with an entity filter
- **AND** chronicler rows MUST appear in the response with `src: 'chronicler'`
- **AND** the response MUST NOT include any row sourced via direct SQL from `chronicler.*`
- **AND** the entity filter MUST be the participant entity filter and each Chronicler row MUST carry `store: null` and its canonical title in `summary`

#### Scenario: Boundary guardrail test passes

- **WHEN** the test suite runs `tests/test_chronicler_boundary.py::test_no_direct_chronicler_sql`
- **THEN** the test MUST scan the relationship router for `FROM chronicler.` / `JOIN chronicler.`
- **AND** the test MUST fail if any such string is found
- **AND** the effective repository path MUST be `roster/relationship/tests/test_chronicler_boundary.py`; the historical path above is retained only as prior-contract provenance
- **AND** it MUST also fail if the two Relationship stores are joined or cross-joined instead of read independently

#### Scenario: Chronicler activity failure is not rendered as inactivity

- **WHEN** the Chronicler MCP activity contribution is unavailable, times out, errors, or returns an unreadable response envelope
- **THEN** every entity-activity response shape MUST set `degraded=true` with the fixed content-blind reason `chronicler_activity_unavailable`
- **AND** the response MAY retain available Relationship activity but clients MUST NOT render its zero counts as a complete inactivity claim
- **AND** a successful Chronicler read with zero episodes MUST set `degraded=false` and `degraded_reason=null`
- **AND** every available Relationship row MUST remain in the response
- **AND** no upstream exception, response payload, failure tail, or source record content beyond the normal success projection MUST appear in the failure fields

#### Scenario: Successful empty sources remain healthy

- **WHEN** both Relationship stores contain no matching active rows and a successful Chronicler read returns zero episodes
- **THEN** activity MUST return a successful empty candidate set with `degraded=false` and `degraded_reason=null`
- **AND** daily-bin responses MUST contain the requested quiet bins rather than report source failure

#### Scenario: Pagination and daily bins use one merged set

- **WHEN** a merged candidate set contains rows from all three sources and the request applies offset, limit, and daily bins
- **THEN** `total` MUST equal the candidate-set size before stream pagination and the page MUST follow the required deterministic order
- **AND** each in-window candidate row MUST contribute exactly once to the bins regardless of whether it appears on the requested stream page
- **AND** `bins_only=true` MUST return those same bins and degradation fields without stream items or totals

#### Scenario: Non-owner activity read returns no content

- **WHEN** a caller who does not resolve to an owner-role entity requests activity
- **THEN** the response MUST be 403 with `owner_required`
- **AND** it MUST return no narrative summary, identity object, Chronicler title, metadata, or failure detail
