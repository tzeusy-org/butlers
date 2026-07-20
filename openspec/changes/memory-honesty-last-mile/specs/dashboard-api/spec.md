## ADDED Requirements

### Requirement: Truthful Memory Fan-Out Resolution and Pagination

The memory dashboard API SHALL distinguish a resolved record, a clean
cross-pool absence, and an unresolved named source failure. It SHALL apply this
contract to episode, fact, and rule details and SHALL make list/inspect
degradation and paging counts truthful without treating a known absent memory
schema as a failed source.

#### Scenario: Detail record wins over an unrelated failed source

- **WHEN** `GET /api/memory/episodes/{id}`, `/facts/{id}`, or `/rules/{id}`
  resolves the requested row in a reachable memory pool while another pool
  genuinely fails
- **THEN** the endpoint MUST return 200 with the resolved row
- **AND** it MUST NOT downgrade the resolved result to a 404 or 503 because of
  the unrelated failed pool

#### Scenario: Clean detail absence remains a 404

- **WHEN** a memory detail lookup finds no requested row and all queried pools
  either answered successfully or are known to lack the memory schema
- **THEN** it MUST return 404
- **AND** it MUST NOT name known absent memory schemas as degraded sources

#### Scenario: Unresolved detail miss names failed pools

- **WHEN** a memory detail lookup finds no requested row and one or more named
  memory pools genuinely fail or become unavailable during the lookup
- **THEN** it MUST return 503 rather than 404
- **AND** its safe error detail MUST name the unavailable pool or pools and
  explain that the requested record may live in an unqueried source
- **AND** it MUST NOT expose raw database errors, connection strings, worker
  leases, prompts, or runtime output

#### Scenario: Lists and inspect retain partial results honestly

- **WHEN** `GET /api/memory/episodes`, `/facts`, `/rules`, or `/inspect`
  receives rows from one or more reachable pools while a selected pool
  genuinely fails
- **THEN** the endpoint MUST return its successful response envelope with the
  reachable rows
- **AND** `meta.pools_failed` MUST name every genuinely failed selected pool
- **AND** known absent memory schemas MUST be silently skipped rather than
  included in `meta.pools_failed`

#### Scenario: Exact total is independent of bounded page collection

- **WHEN** a caller requests any memory episode, fact, rule, or inspect page
  with filters, `offset`, and `limit`
- **THEN** `meta.total` MUST equal the exact filtered count across all
  successfully queried selected memory pools, independent of the number of
  rows fetched to assemble the requested page
- **AND** `meta.has_more` MUST be derived from that exact total, offset, and
  limit
- **AND** the globally ordered page MUST contain only the requested offset
  range, not one independently paginated range per source

#### Scenario: Paginated memory metadata is typed for register and inspect clients

- **WHEN** a memory episode, fact, rule, or inspect response crosses the
  frontend client boundary
- **THEN** the client contract MUST expose `MemoryPaginatedResponse<T>` with
  `meta: MemoryPaginationMeta`, where `MemoryPaginationMeta` extends
  `PaginationMeta` with typed `pools_failed?: string[]`
- **AND** an absent `pools_failed` MUST be treated as an empty list while a
  non-empty list names genuinely failed selected pools
- **AND** register and inspect consumers MUST be able to render the typed value
  without an unsafe cast or untyped metadata access

#### Scenario: Degraded total is explicitly scoped to reachable sources

- **WHEN** a paginated memory response includes non-empty `meta.pools_failed`
- **THEN** its `meta.total` MUST remain exact for the responding selected pools
- **AND** the response MUST preserve `meta.pools_failed` so consumers can
  identify that the total is not a complete all-memory total
- **AND** the endpoint MUST NOT substitute a bounded slice count, zero, or an
  all-clear value for the omitted sources

### Requirement: Owner-Scoped Dead-Letter Episode Requeue API

The dashboard SHALL expose `POST /api/memory/episodes/{episode_id}/requeue` as
the only public manual recovery verb for one memory episode. It SHALL be an
owner-authorized dashboard state transition, not an MCP tool, background job,
or direct execution endpoint. Its target lookup and mutation SHALL be limited
to recovery-eligible sources: a pool whose memory relation is in its owning
butler schema, excluding a distinct private override such as Chronicler's
`chronicler_mem`.

#### Scenario: Authorized owner queues a dead-letter episode

- **WHEN** an authorized owner dashboard caller posts an empty request to a
  valid dead-letter episode URL and its owning memory pool is reachable
- **THEN** the endpoint MUST atomically transition that one episode from
  `dead_letter` to `pending` and return 200 as `ApiResponse[Episode]`
- **AND** the returned episode MUST expose the reset public lifecycle fields
  including `consolidation_attempts=0` and `consolidation_status='pending'`
- **AND** response metadata MUST state that the episode is queued for a future
  scheduled write-up and that no consolidation run was started by the request

#### Scenario: Public episode records expose only recovery-safe fields

- **WHEN** an episode is returned by its detail endpoint, register endpoint, or
  embedded inspect result
- **THEN** it MUST include `consolidation_attempts`,
  `last_consolidation_error`, `dead_letter_reason`, and
  `next_consolidation_retry_at` when applicable
- **AND** operational error and reason strings MUST be sanitized summaries
- **AND** it MUST NOT include `leased_by`, `leased_until`, raw runtime output,
  prompt content, or internal claimant identifiers

#### Scenario: Requeue rejects unauthorized callers without target disclosure

- **WHEN** a caller is not authorized as the dashboard owner
- **THEN** the endpoint MUST return 403 with the stable `owner_required` code
- **AND** it MUST NOT disclose whether the requested episode exists, is
  dead-lettered, or belongs to a reachable pool

#### Scenario: Requeue distinguishes invalid, absent, unresolved, and invalid-state targets

- **WHEN** an authorized caller supplies a malformed episode UUID
- **THEN** the endpoint MUST return 400
- **WHEN** the UUID has no row after every resolvable memory source answers and
  known absent or recovery-ineligible private schemas are skipped
- **THEN** the endpoint MUST return 404
- **WHEN** no usable recovery-eligible memory source exists or a named failed
  eligible source leaves the target's ownership unresolved
- **THEN** the endpoint MUST return 503 with safe named-source context
- **WHEN** a reachable source owns the episode but its status is not
  `dead_letter`
- **THEN** the endpoint MUST return 409 without changing lifecycle fields or
  writing a requeue event

#### Scenario: Requeue leaves private Chronicler memory outside this slice

- **WHEN** an authorized caller supplies an episode identifier that exists only
  in Chronicler's private `chronicler_mem` source
- **THEN** the requeue resolver MUST NOT inspect, claim, or mutate that private
  row
- **AND** it MUST treat the resource as absent from the recovery-eligible
  collection and return the existing clean 404 outcome after eligible sources
  resolve cleanly
- **AND** it MUST NOT report the intentional private-source exclusion as a 503
  or `pools_failed`

#### Scenario: Concurrent requeue requests produce one transition

- **WHEN** two authorized requests concurrently requeue the same dead-letter
  episode
- **THEN** the implementation MUST use one schema-qualified conditional update
  within the lifecycle-event transaction rather than a read-then-write decision
- **AND** exactly one request MUST return 200 and write exactly one
  `episode_consolidation_requeued` event
- **AND** the other request MUST return 409 after observing that the known
  episode is no longer `dead_letter`

#### Scenario: Requeue has no execution or bulk surface

- **WHEN** the requeue endpoint completes successfully or unsuccessfully
- **THEN** it MUST NOT invoke a spawner, `run_consolidation`, an MCP tool, or a
  scheduler run-now API
- **AND** the dashboard API MUST NOT expose a collection requeue route, a bulk
  request body, or a filter that requeues more than the path episode
