## MODIFIED Requirements

### Requirement: Entity activity aggregator (cross-butler read surface)

The dashboard API SHALL expose `GET /api/relationship/entities/{id}/activity` as a relationship-owned aggregator over three existing sources: narrative relationship memory, identity and relational triples, and Chronicler episodes. For omitted `source` or `source=all`, it MUST preserve the records already returned by the endpoint while making records written through the existing relationship activity actions visible with their stored text. The optional source selector and its validation/execution/failure matrix are defined by `Entity Shared Chronicles is a bounded view of unified activity`. The following inclusion, fetch, merge, and failure obligations apply only to selected contributions; an unselected contribution MUST NOT execute or affect degradation. Projection, privacy, identity, and sorting rules apply in every mode.
- **Historical source clause, superseded by the effective contract below:** 1. Relationship-domain rows from `relationship.entity_facts` (notes, interactions, life events, gifts, loans, dunbar_tier_override) — tagged `src: 'relationship'`. 2. Chronicler-domain rows tagged with `src: 'chronicler'` (kind: `episode`). This exact prior authority is retained for review and archive safety; it is not an operative instruction to move or dual-write narrative records into the identity store.
- **Narrative Relationship rows:** When Relationship is selected, the response MUST include active rows from the relationship schema's memory-module `facts` store where `entity_id` is the requested entity, `scope = 'relationship'`, and the predicate is `contact_note`, `life_event`, `gift`, `loan`, `dunbar_tier_override`, or matches `interaction_%`. Each row MUST carry `src: 'relationship'`, `store: 'narrative'`, its source UUID as `id`, its exact stored `content` as `summary`, and `ts = COALESCE(valid_at, created_at)`. The `dunbar_tier_override` row is read from this store because the existing tier-override writers persist it there; this amendment does not change that writer or move existing rows.
- **Identity Relationship rows:** When Relationship is selected, the response MUST retain every active row it already exposes from `relationship.entity_facts` where the requested entity is the subject or is the entity-typed object. Each row MUST carry `src: 'relationship'`, `store: 'identity'`, its source UUID as `id`, its exact stored `object` as `summary`, and `ts = COALESCE(observed_at, last_seen, created_at)`. Adding narrative rows MUST NOT drop an existing identity or relational row.
- **Separate local reads:** When Relationship is selected, the two Relationship stores MUST be queried independently and merged after the reads. They MUST NOT be joined or cross-joined to each other. Rows MUST NOT be copied, rewritten, restored, or heuristically deduplicated across stores. The source UUID remains the `id`; consumers MUST treat `(src, store, id)` as the stable source-qualified row identity and MUST NOT assume that `id` alone is globally unique.
- The chronicler rows MUST be fetched **via chronicler's MCP tools** — `chronicler_list_episodes` per RFC 0014:255-258 (the brief named `chronicler_list_events` but RFC 0014 lists only `list_episodes` / `get_episode` / `submit_correction`; Phase 2 chooses to use only currently-listed MCP tools rather than propose a new tool).
- **Chronicler row mapping:** When Chronicler is selected, the MCP call MUST use the requested entity as the participant filter. Each episode MUST carry `src: 'chronicler'`, `store: null`, its episode UUID as both `id` and `episode_id`, `kind: 'episode'`, `summary = COALESCE(canonical_title, title)`, and `ts = COALESCE(canonical_start_at, start_at)`. The Relationship butler MUST NOT substitute a catalog read for the Chronicler MCP call.
- **Hard invariant (mirror `rfcs/0014:178` "Tests MUST exercise the no-LLM invariant for every adapter"):** the relationship butler MUST NOT issue direct SQL into `chronicler.*` schemas. A guardrail test in `roster/relationship/tests/test_chronicler_boundary.py` MUST assert that the `activity` aggregator implementation does not import any `chronicler.*` ORM model and does not contain the substring `FROM chronicler.` or `JOIN chronicler.` in any SQL string.
- **Success projection and authority:** Every returned row MUST carry `id`, `ts`, `kind`, `src`, `store`, `predicate`, `episode_id`, and `summary`, using explicit nulls for fields that do not apply. `summary` is the only new display projection: the response MUST NOT include raw memory metadata, assertion evidence notes, stored sensitivity labels, upstream payloads, or failure text. Existing owner-only authorization for this PII-bearing read MUST remain in force. This read MUST NOT add a database grant, a caller-selected sensitivity ceiling, or wider memory-catalog authority.
- **Merge and pagination:** The merged candidate set MUST include only selected active Relationship rows plus selected successfully read Chronicler episodes. Malformed selected Chronicler rows follow the exclusion and partial-degradation matrix in `Entity Shared Chronicles is a bounded view of unified activity`. It MUST sort by `ts` descending, place null timestamps after timestamped rows, and use `(src, COALESCE(store, ''), id)` ascending as the deterministic tie-break. Offset and limit MUST be applied only after that merge and sort. `total` MUST equal the number of entries in the candidate set before offset and limit; it MUST NOT claim entries an upstream bounded read did not return.
- **Daily bins:** When `bins=daily` is requested with a valid existing `window=<N>d`, the response MUST derive exactly `N` ascending UTC date bins from the same selected merged candidate set before stream pagination. Each entry with a timestamp inside the inclusive UTC window contributes exactly once to its date; out-of-window and null-timestamp entries contribute zero. Quiet dates MUST remain present with `count=0`. With `bins_only=true`, the response MUST omit stream items and totals but MUST retain the same bins and degradation fields. Binning MUST NOT perform another source read or use a different source set from the stream.
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

- **WHEN** `GET /api/relationship/entities/<id>/activity` selects Chronicler (including omitted `source`) and chronicler episodes mention the entity
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

- **WHEN** the selected Chronicler MCP activity contribution is unavailable, times out, errors, or returns an unreadable response envelope
- **THEN** every response shape selecting that Chronicler contribution MUST set `degraded=true` with the fixed content-blind reason `chronicler_activity_unavailable`
- **AND** the response MAY retain available Relationship activity but clients MUST NOT render its zero counts as a complete inactivity claim
- **AND** a successful selected Chronicler read with zero episodes MUST set `degraded=false` and `degraded_reason=null`
- **AND** every available selected Relationship row MUST remain in the response
- **AND** no upstream exception, response payload, failure tail, or source record content beyond the normal success projection MUST appear in the failure fields

#### Scenario: Successful empty sources remain healthy

- **WHEN** all selected source reads succeed and contain zero readable matching rows
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

## ADDED Requirements

### Requirement: Meeting Prep attendee door preserves Calendar query context

Meeting Prep SHALL render an attendee with a nonempty `entity_id` as a link to
the canonical `/entities/{entity_id}` route. The entity ID MUST be URI encoded,
and the link MUST append the complete current Calendar `location.search`
unchanged, including repeated and currently unknown query pairs. An empty query
MUST produce no trailing `?`.

If retained or test-supplied data has a missing or blank `entity_id`, Meeting
Prep MUST render the attendee name as plain text and MUST NOT create a broken
entity link. Activating the link MUST perform no fetch, mutation, LLM call, or
other effect beyond ordinary client navigation.

#### Scenario: Resolved attendee opens the canonical entity with query preserved

- **WHEN** Meeting Prep renders entity `65e9b763-5e57-4e48-90a8-1e49f451789b` from `/calendar?view=user&range=week&anchor=2026-09-06&overlays=1`
- **THEN** the attendee name MUST link to `/entities/65e9b763-5e57-4e48-90a8-1e49f451789b?view=user&range=week&anchor=2026-09-06&overlays=1`
- **AND** no new internal link MUST target the retired `/butlers/relationship/entities/...` route

#### Scenario: Repeated and unknown Calendar query pairs survive the door

- **WHEN** the current Calendar query contains repeated or currently unknown pairs
- **THEN** the attendee link MUST preserve all pairs in their current order
- **AND** the link MUST NOT reduce the query to a hardcoded allowlist

#### Scenario: Unresolved attendee remains noninteractive

- **WHEN** legacy or test-supplied Meeting Prep data has no nonblank `entity_id`
- **THEN** the attendee name MUST remain plain text
- **AND** activation MUST NOT navigate to an empty or fabricated entity route

### Requirement: Entity Shared Chronicles is a bounded view of unified activity

`GET /api/relationship/entities/{id}/activity` SHALL accept an optional
`source` query parameter with values `all`, `relationship`, or `chronicler` and
default `all`. Query validation MUST reject any other value with HTTP 422
before either source fetch begins. After the existing owner and entity gates,
the selected source MUST control execution as follows. This selector extends
`Entity activity aggregator (cross-butler read surface)` as rebuilt in this
same artifact from the separately proposed source-union contract. Relationship
means both active local stores (narrative and identity), read independently;
it never means only the identity store. That source-union contract at PR #4058
`652257065e7fda464979f6ce2bd76075052e4d78` must be adopted first and integrated before this derivative is integrated.
Its row projection, source-qualified identity, ordering, and default `all`
behavior remain binding; this requirement adds no second activity authority:

| `source` | Source work | Successful response | Selected-source failure |
|---|---|---|---|
| omitted or `all` | Fetch Relationship facts and Chronicler episodes. | Merge both selected contributions. | A Relationship fetch failure retains the existing non-2xx request failure. A total or partial Chronicler failure returns the readable selected rows with `degraded=true` and `degraded_reason="chronicler_activity_unavailable"`. |
| `relationship` | Fetch Relationship facts only. The endpoint MUST NOT acquire a Chronicler client or call a Chronicler tool. | Return only Relationship rows with `degraded=false` and `degraded_reason=null`, regardless of Chronicler availability. | A Relationship fetch failure returns a non-2xx request failure and MUST NOT return an empty success. |
| `chronicler` | Fetch Chronicler episodes only. The endpoint MUST NOT query Relationship activity facts. | Return only readable Chronicler rows. | A total or partial Chronicler failure returns any readable Chronicler rows with `degraded=true` and `degraded_reason="chronicler_activity_unavailable"`; no readable rows returns the same degraded response with `items=[]`. |

For a selected Chronicler contribution, a non-object row, a missing or invalid
episode UUID, or a missing or unparseable selected timestamp is a malformed
row. The selected timestamp is `canonical_start_at` when that field is
nonempty, otherwise `start_at`. One or more malformed rows MUST make the
contribution partially failed: the endpoint MUST exclude those rows, retain
every readable row, and set the fixed Chronicler degraded state. A null title
is valid and uses the documented display fallback; it is not malformed. A
successful selected source with zero rows is the only source-empty result.

Source filtering MUST occur before total calculation, daily binning, ordering,
and the existing `limit`/`offset` slice. `total` MUST count the full selected
readable candidate set actually returned by the selected bounded source reads,
not unseen upstream history, before pagination. With `bins=daily`, bins MUST be computed from
that same full selected readable set before pagination; `limit` and `offset`
govern only returned stream items and MUST NOT truncate bin input. With
`bins_only=true`, the selected-source execution and degraded matrix still
applies, while stream items and totals are omitted as required by the aggregator. Omitting `source`, or
passing `source=all`, MUST otherwise preserve the existing unified response and
its Relationship plus Chronicler contributions. For every source mode,
selected readable entries MUST be ordered by
`ts DESC NULLS LAST, (src, COALESCE(store, ''), id) ASC` before pagination.

For `source=chronicler`, the endpoint MUST return the existing unwrapped
`ActivityResponse` shape and only entries with `src="chronicler"`,
`kind="episode"`, `store=null`, nullable `summary`, and the episode timestamp in `ts`.
`episode_id` MUST equal the `id` field on that same activity row, which is the
Chronicler episode UUID; it MUST NOT equal the route's entity UUID merely
because that entity selected the episode. Existing validation remains: `limit`
defaults to 50 and is constrained to `1..200`; `offset` defaults to 0 and MUST
be nonnegative. Every mode MUST retain the aggregator's full row projection
and use `(src, store, id)` as stable row identity; UUID alone is not globally
unique across the selected stores. Chronicler episode navigation still uses
`episode_id`, which equals that Chronicler row's `id`; adding `store` does not
change the episode door. Equal timestamps MUST use the same ascending source
tuple in mixed-source pages and the Chronicler-only preview, never `id DESC`.
A stable candidate set produces stable offset pages; concurrent source writes
or a changed upstream bounded set do not imply a snapshot across requests.

Entity detail SHALL request `source=chronicler&limit=5&offset=0` and render at
most five rows in a section named `Shared Chronicles`. This section is a
preview of Chronicler entries that remain part of the unified activity
contract. It MUST NOT replace, filter, or remove the existing unified
ActivityTimeline requirement, and it MUST NOT be credited as satisfying any
pre-existing gap between that requirement and the current entity-page wiring.
Duplicate presentation of a previewed episode in the required unified history
is intentional.

Relationship MUST obtain Chronicler activity only by calling
`chronicler_list_episodes(participant_entity_id=<entity-id>)` through MCP. The
entity page MUST NOT call the full `/api/chronicler/episodes` response for this
summary, and Relationship MUST NOT read `chronicler.*` through SQL or another
direct schema path. The summary MUST render only corrected title, date, and the
episode door; it MUST NOT render episode payload, source reference,
participant IDs, correction notes, privacy metadata, or another unlisted
field. The new source filter MUST narrow the returned contribution and MUST
NOT replace `participant_entity_id` with the legacy owner-only `entity_id`
filter; organizer and participant links remain eligible.

#### Scenario: Five most recent shared episodes render as a preview

- **WHEN** a known entity has more than five readable Chronicler episodes and the entity detail page loads
- **THEN** the page MUST request `source=chronicler&limit=5&offset=0`
- **AND** exactly the first five rows in `ts DESC NULLS LAST, (src, COALESCE(store, ''), id) ASC` order MUST render with date, corrected title, and episode door
- **AND** a null title MUST render as `Untitled episode`

#### Scenario: Successful zero is the only Shared Chronicles empty state

- **WHEN** the filtered activity response is valid, has `degraded=false`, and has `items=[]`
- **THEN** Shared Chronicles MUST render `No shared episodes yet.`
- **AND** it MUST NOT render a degraded note

#### Scenario: Unavailable or incomplete Chronicler evidence is not empty history

- **WHEN** the filtered activity request fails, times out, is unreadable, or returns `degraded=true`
- **THEN** Shared Chronicles MUST render a `SourceDegradedNote` naming `Shared Chronicles`
- **AND** it MUST suppress `No shared episodes yet.`
- **AND** any valid returned rows MAY remain visible but MUST NOT be presented as complete history
- **AND** the ActivityTimeline and all other entity sections MUST remain usable

#### Scenario: Mixed readable and malformed Chronicler rows degrade honestly

- **WHEN** a selected Chronicler result contains at least one readable episode and at least one malformed row
- **THEN** the readable episode MUST remain in the response and the malformed row MUST be excluded
- **AND** the response MUST set `degraded=true` with `degraded_reason="chronicler_activity_unavailable"`
- **AND** neither the item list, total, nor bins MUST be presented as complete evidence

#### Scenario: Relationship-only selection does not execute Chronicler

- **WHEN** `source=relationship` is requested while Chronicler is unavailable
- **THEN** no Chronicler client acquisition or tool call MUST occur
- **AND** a successful Relationship read MUST return only Relationship rows with `degraded=false`
- **AND** daily bins, total, ordering, and pagination MUST use only the Relationship rows

#### Scenario: Chronicler-only selection does not execute Relationship activity

- **WHEN** `source=chronicler` is requested
- **THEN** no Relationship activity-fact query MUST occur after the owner and entity gates
- **AND** daily bins, total, ordering, and pagination MUST use only readable Chronicler rows
- **AND** a Relationship activity-source failure MUST NOT affect this response

#### Scenario: All-source selection retains source-specific failure behavior

- **WHEN** `source=all` or no `source` is requested
- **THEN** both Relationship and Chronicler contributions MUST be selected
- **AND** a Relationship fetch failure MUST retain the existing non-2xx request failure
- **AND** a total or mixed-row Chronicler failure MUST retain readable selected rows in the fixed Chronicler degraded response

#### Scenario: Invalid source is rejected before execution

- **WHEN** a client requests `source=unknown`
- **THEN** the API MUST return HTTP 422
- **AND** neither the Relationship activity fetch nor Chronicler client/tool path MUST execute

#### Scenario: Source selection precedes bins and pagination

- **WHEN** `source=chronicler&bins=daily&limit=2&offset=1` is requested against more than three readable Chronicler rows plus Relationship rows
- **THEN** `total` MUST count every readable Chronicler row and exclude every Relationship row
- **AND** daily bins MUST be computed from every readable Chronicler row before the page slice
- **AND** returned stream items MUST be the second and third rows after `ts DESC NULLS LAST, (src, COALESCE(store, ''), id) ASC` ordering
- **AND** no Relationship row MUST contribute to items, total, or bins

#### Scenario: Equal timestamps and cross-store UUID collisions remain deterministic

- **WHEN** the selected candidate set contains equal timestamps and the same UUID in narrative, identity, and Chronicler stores
- **THEN** every selected row MUST survive with its `(src, store, id)` identity
- **AND** stream pages MUST sort by `ts DESC NULLS LAST` followed by `(src, COALESCE(store, ''), id) ASC`, including when the set is narrowed to one source
- **AND** a stable candidate set MUST produce the same pages without dropping or merging a colliding row
- **AND** the first-five preview MUST use that same ordering while its door still targets the Chronicler episode UUID

#### Scenario: Unified activity defaults remain compatible

- **WHEN** an existing client calls `/api/relationship/entities/{id}/activity` without `source`
- **THEN** the endpoint MUST retain the existing response fields, default pagination, Relationship rows, Chronicler rows, and degraded semantics
- **AND** the Shared Chronicles feature MUST NOT create a second activity authority

#### Scenario: Chronicler boundary remains MCP only

- **WHEN** source-filtered or unfiltered activity is read
- **THEN** every Chronicler row MUST still arrive through `chronicler_list_episodes` over MCP
- **AND** the existing no-direct-`chronicler.*` SQL guard MUST continue to pass
- **AND** no request-time LLM call or write MUST occur

#### Scenario: Organizer and participant filter coverage executes through the aggregator

- **WHEN** one Chronicler episode links the requested entity through `episode_entities` as `organizer` and another links it as `participant`, with neither link using the owner role
- **THEN** `chronicler_list_episodes` MUST receive that entity as `participant_entity_id`
- **AND** both episodes MUST appear in the `source=chronicler` Relationship response and Shared Chronicles preview
- **AND** implementation verification MUST exercise both roles through the real participant-filtered Chronicler tool plus the Relationship route, rather than only inject a pre-shaped Relationship mock result

### Requirement: Entity commitments read is minimal, paginated, and honest

The Relationship API SHALL expose
`GET /api/relationship/entities/{id}/commitments` as a read-only entity view of
`public.owner_conditions`. The endpoint MUST select only rows with commitment
class, matching `counterparty_entity_id`, active state (`open` or `aging`), and
direction `owner_to_other` or `other_to_owner`. It MUST span every originating
source and MUST exclude resolved, self-directed, wrong-entity, and
non-commitment conditions.

The endpoint SHALL return this unwrapped response:

```text
{
  items: [{ kind, direction, summary, deadline, escalation_level, fingerprint }],
  total: integer,
  limit: integer,
  offset: integer,
  degraded: boolean,
  degraded_reason: "commitment_source_unavailable" | null
}
```

`kind` MUST be one of `promise`, `waiting_for`, `follow_up`, `obligation`, or
`decision`; `direction` MUST be `owner_to_other` or `other_to_owner`;
`deadline` MUST be the stored nullable ISO-8601 value; and
`escalation_level` MUST be serialized as `L0`, `L1`, `L2`, or `L3`. The
response MUST NOT expose source, condition ID, episode number, state,
confidence, timestamps, opening or closing evidence, resolution reason,
identity payload, or arbitrary metadata.

`limit` MUST default to 50 and be constrained to `1..200`; `offset` MUST
default to 0 and be nonnegative. Matching rows MUST be ordered by
`first_detected_at DESC, fingerprint ASC` before pagination. A stable data set
MUST therefore return the same page for repeated identical reads. The endpoint
does not promise a snapshot across offset pages while concurrent writes occur.

#### Scenario: Active commitments in both directions span domains

- **WHEN** a known entity has active commitment-class rows from multiple sources in both `owner_to_other` and `other_to_owner` directions
- **THEN** the endpoint MUST return every matching row in the exact safe projection
- **AND** entity detail MUST render both direction labels distinctly outside any calendar event
- **AND** deadline and escalation MUST render when present without adding a mutation affordance

#### Scenario: Nonmatching commitment rows stay private

- **WHEN** the ledger also contains resolved rows, self-directed rows, rows for another entity, or non-commitment conditions
- **THEN** none of those rows MUST appear in the response
- **AND** no omitted metadata or evidence field MUST appear in serialized items

#### Scenario: Pagination and ordering are deterministic for stable data

- **WHEN** a client requests `?limit=2&offset=2` against an unchanged matching set
- **THEN** the endpoint MUST apply the slice after `first_detected_at DESC, fingerprint ASC` ordering
- **AND** it MUST echo `limit=2` and `offset=2` with `total` equal to the full matching count
- **AND** invalid limits, negative offsets, or invalid entity UUIDs MUST return HTTP 422 before a source read

#### Scenario: Known entity with no commitments is honestly empty

- **WHEN** the owner gate and entity existence check succeed and the commitment query returns zero matching rows
- **THEN** the endpoint MUST return HTTP 200 with `items=[]`, `total=0`, `degraded=false`, and `degraded_reason=null`
- **AND** entity detail MUST render `No open commitments.`

#### Scenario: Commitment source failure cannot impersonate zero

- **WHEN** the commitment table is missing or its read fails through permission, timeout, connection, or another source error
- **THEN** the endpoint MUST return HTTP 200 with `items=[]`, `total=0`, `degraded=true`, and `degraded_reason="commitment_source_unavailable"`
- **AND** no upstream exception text or row content MUST enter the response
- **AND** entity detail MUST render a `SourceDegradedNote` naming `Commitments` and suppress `No open commitments.`

#### Scenario: Entity commitment reads have no effects

- **WHEN** the endpoint or its frontend retry is invoked repeatedly or concurrently
- **THEN** no commitment or entity row MUST be created, resolved, updated, or deleted
- **AND** no LLM, MCP tool, provider, notification, connector, or outbound queue MUST be invoked

### Requirement: Entity life-graph reads retain existing owner authorization

The Shared Chronicles activity request and entity commitments endpoint MUST
remain behind the dashboard API-key middleware and MUST run the existing
Relationship owner-role assertion before returning entity or source data.
Failure to confirm an owner MUST return HTTP 403 with
`{"code":"owner_required","message":"Owner entity not found"}`. After that
gate, a confirmed unknown entity MUST return HTTP 404 with
`{"detail":"Entity not found"}` through the existing Relationship
entity-existence behavior.

This additive read authority MUST NOT grant Relationship access to any private
Chronicler schema. The commitments read is limited to the existing shared
`public.owner_conditions` surface.

#### Scenario: Missing owner is refused before entity data

- **WHEN** no owner-role entity can be confirmed and either new entity read is requested
- **THEN** the response MUST be HTTP 403 with code `owner_required`
- **AND** no entity, episode, or commitment item MUST be returned

#### Scenario: Unknown entity is distinct from an empty known entity

- **WHEN** owner status is confirmed but the path UUID has no row in `public.entities`
- **THEN** the response MUST be HTTP 404 with detail `Entity not found`
- **AND** it MUST NOT return an empty or degraded HTTP 200 response

#### Scenario: Rollback preserves existing data and surfaces

- **WHEN** the attendee link treatment, activity filter, Shared Chronicles section, commitments endpoint and section, and episode query door are rolled back
- **THEN** no migration, backfill, stored episode, entity fact, commitment, or Meeting Prep envelope MUST require reversal
- **AND** the default activity response, unified ActivityTimeline, Meeting Prep commitment rows, and existing Chronicles date navigation MUST remain available
