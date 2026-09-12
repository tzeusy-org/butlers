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
the selected source MUST control execution as follows:

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
readable set before pagination. With `bins=daily`, bins MUST be computed from
that same full selected readable set before pagination; `limit` and `offset`
govern only returned stream items and MUST NOT truncate bin input. With
`bins_only=true`, the selected-source execution and degraded matrix still
applies, while no paginated item slice is returned. Omitting `source`, or
passing `source=all`, MUST otherwise preserve the existing unified response and
its Relationship plus Chronicler contributions. For every source mode,
selected readable entries MUST be ordered by
`ts DESC NULLS LAST, id DESC` before pagination.

For `source=chronicler`, the endpoint MUST return the existing unwrapped
`ActivityResponse` shape and only entries with `src="chronicler"`,
`kind="episode"`, nullable `summary`, and the episode timestamp in `ts`.
`episode_id` MUST equal the `id` field on that same activity row, which is the
Chronicler episode UUID; it MUST NOT equal the route's entity UUID merely
because that entity selected the episode. Existing validation remains: `limit`
defaults to 50 and is constrained to `1..200`; `offset` defaults to 0 and MUST
be nonnegative.

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
- **AND** exactly the first five rows in `ts DESC NULLS LAST, id DESC` order MUST render with date, corrected title, and episode door
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
- **AND** returned stream items MUST be the second and third rows after `ts DESC NULLS LAST, id DESC` ordering
- **AND** no Relationship row MUST contribute to items, total, or bins

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
