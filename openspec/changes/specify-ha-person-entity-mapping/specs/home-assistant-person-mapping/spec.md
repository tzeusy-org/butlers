## ADDED Requirements

### Requirement: Owner-authenticated dashboard-only mapping submission

The system SHALL expose exactly one Home Assistant person-mapping mutation at
`POST /api/home/person-mappings`. It SHALL accept no path or query parameter and
SHALL have no mapping list, detail, update, delete, rollback, or remap endpoint.
The route SHALL not be registered as an MCP/runtime tool or callable from an
LLM session, CLI, connector, scheduled job, background retry, or direct-SQL
operator workflow.

Before any component reads or buffers the body, generates a receipt, acquires a
database pool, or observes protected state, the route SHALL pass the existing
fail-closed `require_dashboard_owner_control` contract or a separately
owner-approved successor with equivalent guarantees. Absent owner-control
configuration SHALL return `503`; a missing or mismatched owner credential
SHALL return `401`. The optional fail-open `ApiKeyMiddleware` is insufficient
for this route.

After authentication, the audit actor SHALL be derived server-side through
`authenticated_principal()`. The request SHALL expose no caller-asserted actor.
`authenticated_principal()` is attribution only and SHALL NOT be treated as an
authentication check.

The `bu-pb6oy` browser-session policy is closed and adopted: configured-key
browser authentication uses an expiring/revocable server-managed
HttpOnly/Secure/SameSite=Strict session over HTTPS with CSRF protection,
preserves `X-API-Key` for non-browser callers, and does not treat same-origin as
authentication. Its conforming configured-key session/CSRF implementation
remains unimplemented and SHALL remain a prerequisite for this mapping UI.

Default keyless Compose/browser usability SHALL remain separately blocked on
`bu-azqfpk`: the owner has not selected E1 host-authority transport or E2 HTTPS
entry, nor adopted and implemented the resulting exact enrollment contract.
This mapping artifact SHALL select neither E1 mechanism nor E2 mechanism and
SHALL NOT imply that default keyless Compose is enrolled or browser-usable.

#### Scenario: Unconfigured owner control fails before body access

- **WHEN** `DASHBOARD_API_KEY` is unavailable under the current owner-control
  contract and a caller submits the mapping endpoint
- **THEN** the API SHALL return `503` with a fixed content-blind error
- **AND** no layer SHALL read or buffer the request body, create a receipt,
  acquire a database pool, read mapping/entity state, or write audit/mapping
  state

#### Scenario: Wrong credential is denied before body access

- **WHEN** owner control is configured but the credential is missing or wrong
- **THEN** the API SHALL return `401` after constant-time comparison
- **AND** no layer SHALL read or buffer the body or perform a database,
  provider, mapping, receipt, or audit operation

#### Scenario: Authenticated actor is server-derived

- **WHEN** a request has passed the owner-control boundary
- **THEN** the mutation's actor SHALL come from `authenticated_principal()`
- **AND** no request field, header, query parameter, or path value SHALL assert
  or override the actor
- **AND** the actor label alone SHALL never make an unauthenticated request
  eligible

#### Scenario: Browser workflow waits for its authentication prerequisite

- **WHEN** the adopted `bu-pb6oy` configured-key session/CSRF contract has not
  been implemented and proven at the owner-control boundary
- **THEN** the mapping UI SHALL NOT ship or be described as usable
- **AND** no alternate credential transport or same-origin bypass SHALL be
  introduced by this capability

#### Scenario: Default keyless workflow waits for host enrollment choices

- **WHEN** `bu-azqfpk` E1 host-authority transport and E2 HTTPS entry remain
  unselected, unadopted, or unimplemented
- **THEN** the mapping workflow SHALL NOT ship or be described as usable in
  default keyless Compose
- **AND** this capability SHALL NOT select a host code, browser challenge,
  Tailscale Serve, loopback TLS, or any other enrollment mechanism

### Requirement: Exact bounded mapping request

An authenticated request SHALL carry a JSON object with the sole field
`mappings`, containing one through 50 objects. The raw encoded HTTP request body
SHALL be no more than 32 KiB (exactly 32,768 octets, excluding transport framing).
After owner authentication and before UTF-8 or JSON decoding, a bounded request
reader SHALL count the actual streamed body octets and stop once it observes octet
32,769, retaining no more than those first 32,769 octets and reading no later
chunks. It SHALL NOT use `Content-Length` as acceptance or rejection authority:
an absent, understated, overstated, or otherwise misleading header and chunked
delivery SHALL all be decided from the bytes actually read. Each decoded object
SHALL contain exactly `ha_person_id` and `entity_id`.

An authenticated body that exceeds 32,768 octets SHALL return HTTP `413` in the
standard error envelope with exactly the fixed code `REQUEST_BODY_TOO_LARGE` and
fixed message `Request body exceeds 32 KiB.` It SHALL not decode JSON, generate a
receipt, derive an actor, acquire or inspect a pool, read protected mapping/entity
state, or emit generic or explicit audit evidence. The result SHALL contain no
counts, details, submitted value, body fragment, measured size, header value, or
other request-derived data.

`ha_person_id` SHALL be at most 255 UTF-8 bytes and match
`\Aperson\.[a-z0-9_]+\Z` byte-for-byte. It SHALL be an exact owner-supplied,
already-observed Home Assistant identifier; the system SHALL not trim,
case-fold, normalize, autocomplete, fetch, or infer it from a canonical name,
alias, display label, state snapshot, provider payload, or Home Assistant API.
`entity_id` SHALL be a lowercase hyphenated RFC 4122 UUID string naming an existing live
`public.entities` row with `entity_type = 'person'`. The route SHALL create,
merge, promote, rename, or otherwise modify no entity.

The request SHALL carry a required `Idempotency-Key` header generated by the
dashboard from 32 random bytes and encoded as exactly 43 unpadded base64url
characters. The owner SHALL not enter it. It SHALL be independent of both
identifiers, hashed with SHA-256 before durable lookup, and never stored or
emitted raw. Canonical request identity SHALL be the SHA-256 digest of canonical
JSON whose pairs are sorted by the byte-for-byte Home Assistant ID and then the
canonical UUID, making batch order immaterial. Neither digest SHALL leave the
server-side receipt record.

Each Home Assistant ID and entity UUID SHALL appear at most once in a batch. A
duplicate on either side, including an identical duplicate pair, SHALL return a
fixed content-blind `422` and perform zero mapping writes.

#### Scenario: Exact bounded request is accepted for evaluation

- **WHEN** the authenticated owner submits between one and 50 distinct pairs,
  the raw encoded body is at most 32,768 octets, every Home Assistant ID has the
  exact accepted syntax, every entity ID is a lowercase hyphenated RFC 4122 UUID,
  and the idempotency header has the required opaque shape
- **THEN** the server SHALL evaluate exactly those pairs without normalization,
  provider lookup, name/alias resolution, or entity creation

#### Scenario: Oversized encoded body is rejected before decoding

- **WHEN** an authenticated request's streamed raw body exceeds 32,768 octets,
  including excess made only from JSON whitespace or escaped spellings
- **THEN** the route SHALL stop at the bounded read seam and return the fixed
  `413 REQUEST_BODY_TOO_LARGE` result without JSON decoding
- **AND** absent, false, or conflicting `Content-Length` and chunked delivery
  SHALL not change the result
- **AND** it SHALL create no receipt, acquire no pool, observe no protected
  state, and emit no generic or explicit audit evidence

#### Scenario: Duplicate batch member is rejected as a whole

- **WHEN** a batch repeats a Home Assistant ID or an entity UUID, whether the
  repeated pair is identical or conflicting
- **THEN** the API SHALL return `422` with no item index or submitted value
- **AND** it SHALL perform zero mapping writes

#### Scenario: Invalid reference is one content-blind category

- **WHEN** one or more submitted entity UUIDs are missing, tombstoned, merged,
  or do not have `entity_type = 'person'`
- **THEN** the API SHALL return `422 INVALID_REFERENCE` and expose only the
  aggregate `invalid_reference_count`
- **AND** it SHALL not distinguish which condition or which member failed
- **AND** it SHALL perform zero mapping writes and no entity mutation

### Requirement: Atomic idempotent no-remap batch

Every authenticated, structurally valid batch SHALL use one connection and one
transaction. Before idempotency lookup, entity-reference validation, mapping
reads, or mapping writes, it SHALL take the transaction-scoped PostgreSQL
advisory lock derived from the fixed namespace
`butlers:dashboard:ha-person-mapping:v1`. All batches in this workflow SHALL use
that same mapping-specific lock.

After a new idempotency decision and before reading mappings, the transaction
SHALL select every submitted UUID from `public.entities` in ascending UUID
order with `FOR UPDATE`, without filtering invalid rows out of the lock query.
It SHALL retain every acquired entity-row lock through mapping, receipt, and
audit commit. It SHALL then require every submitted UUID to have one locked row
whose `entity_type = 'person'`, `metadata->>'merged_into' IS NULL`, and
`metadata->>'deleted_at' IS NULL`. Missing rows and rows failing any of those
actual live predicates SHALL enter the one content-blind `INVALID_REFERENCE`
category.

If a concurrent merge, metadata tombstone/delete, physical delete, or
`entity_type` change owns a referenced row first, mapping validation SHALL wait
and then evaluate its committed state; an invalid result SHALL return
`INVALID_REFERENCE`. If mapping validation owns the row lock first, that entity
mutation SHALL wait until the mapping, receipt, and audit decision commits. A
success receipt proves completeness at this serialization point and SHALL NOT
claim to prevent a separately authorized later lifecycle mutation. A deadlock
or aborted transaction SHALL commit no partial mapping or success receipt.

Under the lock, the server SHALL classify an existing exact pair as unchanged.
A Home Assistant ID mapped to a different or null entity, or an entity UUID
mapped to a different Home Assistant ID, SHALL be a conflict. If any conflict
exists, the complete batch SHALL return `409 MAPPING_CONFLICT` and commit zero
mapping inserts, updates, or deletes. Legacy rows MAY be read for conflict
detection but SHALL never be repaired, filled, or remapped by this endpoint.

When no conflict exists, every new pair SHALL be inserted as an entity-only row
and every exact pair SHALL remain untouched in the same transaction. The
mapping writes, success receipt/idempotency record, and success audit SHALL
commit atomically. A transaction failure SHALL leave no partial mapping and no
success receipt.

The durable idempotency record SHALL contain only a digest of the raw key, a
canonical request digest, an opaque server-generated receipt, aggregate counts,
completeness, outcome, a fixed failure category, and timestamps. It SHALL store
no raw key, body, or identifier copy and SHALL not expire in v1. The same key
and canonical request SHALL return the stored terminal receipt without reading
or writing mappings. The same key with a different request SHALL return
`409 IDEMPOTENCY_CONFLICT` with zero mapping writes.

Every authenticated, structurally valid request SHALL durably record its
terminal aggregate receipt for success, invalid reference, or mapping conflict
so an exact replay cannot change outcome after later database state changes.
Structural validation failures SHALL not create durable idempotency state
because no canonical mapping set exists.

#### Scenario: New and identical pairs commit as one complete batch

- **WHEN** a valid batch contains new pairs and exact existing pairs with no
  conflict
- **THEN** every new pair SHALL be inserted and every exact pair SHALL remain
  unchanged in one transaction
- **AND** the response SHALL report the committed created and unchanged counts
  with `complete = true`

#### Scenario: Fresh-key identical submission is a no-op

- **WHEN** every submitted pair already exists exactly and the opaque key is new
- **THEN** the API SHALL return success with `created_count = 0`,
  `unchanged_count = received_count`, and `complete = true`
- **AND** no mapping row SHALL be updated

#### Scenario: Exact idempotent replay returns the stored receipt

- **WHEN** the same opaque key and exact canonical request are submitted again
- **THEN** the API SHALL return the original terminal receipt and aggregate
  counts byte-for-byte
- **AND** it SHALL not read or write mapping rows

#### Scenario: Reusing a key for another request is a conflict

- **WHEN** an opaque key already belongs to a different canonical request
- **THEN** the API SHALL return `409 IDEMPOTENCY_CONFLICT`
- **AND** it SHALL commit zero mapping writes and reveal no request difference

#### Scenario: Either-side mapping conflict writes nothing

- **WHEN** any submitted Home Assistant ID already targets another or null
  entity, or any submitted entity UUID is already targeted by another Home
  Assistant ID
- **THEN** the API SHALL return `409 MAPPING_CONFLICT`
- **AND** it SHALL commit zero mapping inserts, updates, or deletes for the
  complete batch
- **AND** it SHALL not identify the conflicting member or existing value

#### Scenario: Concurrent competing batches cannot cross-map

- **WHEN** concurrent batches compete for either side of a mapping
- **THEN** the fixed transaction lock SHALL serialize their idempotency,
  reference, conflict, and insert decisions
- **AND** at most one complete non-conflicting mapping set SHALL commit
- **AND** the other request SHALL return a content-blind `409` with zero partial
  mapping writes

#### Scenario: Entity mutation commits before mapping validation

- **WHEN** a merge, `metadata.deleted_at` tombstone, physical delete, or
  `entity_type` change holds a submitted entity row and commits before mapping
  validation can lock it
- **THEN** mapping validation SHALL wait, observe the committed missing or
  non-live/non-person state through the actual metadata predicates, and return
  `INVALID_REFERENCE`
- **AND** it SHALL commit zero mapping writes and no false success receipt

#### Scenario: Mapping validation commits before entity mutation

- **WHEN** mapping validation locks a referenced entity row before a concurrent
  merge, metadata tombstone/delete, physical delete, or `entity_type` change
  reaches that row
- **THEN** the entity mutation SHALL wait until the complete
  mapping/receipt/audit decision commits
- **AND** the success receipt SHALL be truthful at that serialization point
- **AND** the later entity mutation SHALL not cause partial mapping writes or
  retroactively rewrite the receipt

#### Scenario: Simultaneous identical idempotency requests share one result

- **WHEN** two real concurrent transactions submit the same opaque key and the
  same canonical request with forced overlap before idempotency lookup/insert
- **THEN** the fixed mapping lock and durable key uniqueness SHALL produce
  exactly one terminal idempotency record
- **AND** both callers SHALL receive the identical receipt and aggregate counts
- **AND** the waiting caller SHALL perform zero mapping writes

#### Scenario: Simultaneous divergent idempotency requests elect one winner

- **WHEN** two real concurrent transactions submit the same opaque key with
  different canonical requests and force overlap before idempotency
  lookup/insert
- **THEN** the first lock holder SHALL create the only terminal idempotency
  record and complete according to its request
- **AND** the loser SHALL return fixed `IDEMPOTENCY_CONFLICT`, create no second
  terminal idempotency record, and perform zero mapping writes

#### Scenario: Failure after an attempted insert rolls back the whole batch

- **WHEN** a database failure occurs after one or more inserts are attempted but
  before commit
- **THEN** every mapping insert in that batch and its success receipt/audit SHALL
  roll back
- **AND** no response SHALL claim a created mapping

### Requirement: Aggregate-only receipt, audit, and observability

A new, mixed, or identical no-op success SHALL return `200` using `ApiResponse`
and expose exactly an opaque UUIDv4 `receipt`, `complete`, `received_count`,
`created_count`, `unchanged_count`, `conflict_count`, and
`invalid_reference_count` in `data`, with empty `meta`.
It SHALL expose no identifier, mapping, name, alias, provider datum, raw or
hashed idempotency material, request digest, row ID, table name, field position,
or submitted value. `complete` SHALL be true only when the whole submitted set
exists after the transaction. A successful response SHALL have
`received_count = created_count + unchanged_count` and zero refusal counts.

A non-2xx response SHALL use RFC 0007's standard error envelope with a fixed
code and message. Except for the pre-decode fixed `413 REQUEST_BODY_TOO_LARGE`
result, every post-authentication terminal response SHALL include a receipt;
its `error.details` SHALL contain only that receipt, completeness flag,
and aggregate count fields. A conflict SHALL
be `409`, invalid structure/duplicates/references SHALL be `422`, and database
unavailability SHALL be `503`. The oversize result SHALL contain no receipt or
details and SHALL touch no pool, protected state, or audit path. Mapping
failures SHALL report `created_count = 0`.

The endpoint SHALL be exempt from generic `DashboardAuditMiddleware` body
reading and path-parameter capture. Post-read redaction is insufficient. Its
explicit `home_assistant_person_mapping_batch` audit allowlist SHALL contain
only server-derived actor, opaque receipt, the five aggregate counts, fixed
outcome, and optional fixed failure category. Audit target, note, request
summary/body, path parameters, free-text error, provider response, raw
idempotency key, request digest, and both identifiers SHALL be absent.
An oversize request SHALL emit no explicit audit event because rejection occurs
before actor derivation, receipt creation, or pool access.

Logs SHALL contain only the fixed route template, fixed outcome/failure
category, and aggregate counts. Metrics and traces SHALL contain only fixed
low-cardinality outcome/failure-category values and numeric aggregate counts.
No request URL as supplied, header, body, receipt, raw or hashed identifier,
idempotency material, request digest, entity name, SQL argument, provider data,
exception rendering, or dynamic string SHALL enter logs, metric names/labels,
span attributes/events, baggage, or resource attributes.

The request and all mapping details SHALL remain absent from prompts, model
input/output, sessions, tool calls, MCP tools/resources, connector events,
notifications, browser storage/query state, and Beads. There SHALL be no mapping
read or verification endpoint; an exact replay returns only the stored aggregate
receipt.

#### Scenario: Success publishes only the aggregate allowlist

- **WHEN** a complete mapping batch succeeds
- **THEN** the response and audit SHALL contain their exact allowlisted receipt,
  actor, count, completeness, and outcome fields
- **AND** every request value, mapping detail, provider datum, digest, and
  free-text note/error SHALL be absent

#### Scenario: Conflict error is content-blind

- **WHEN** a mapping or idempotency conflict returns `409`
- **THEN** the fixed error SHALL expose at most the opaque receipt,
  completeness, and aggregate counts
- **AND** it SHALL contain no member index, identifier, current mapping,
  request difference, driver text, or provider detail

#### Scenario: Generic audit capture never reads the body

- **WHEN** any caller invokes the mapping endpoint, including before
  authentication and on malformed JSON
- **THEN** generic audit middleware SHALL not read, buffer, parse, redact, or
  store the body
- **AND** no generic audit row SHALL contain the supplied URL, header, body,
  identifier, idempotency material, or exception rendering

#### Scenario: No LLM, provider, or MCP surface can observe a mapping

- **WHEN** a batch is submitted, replayed, refused, or fails
- **THEN** no provider/credential/snapshot read, prompt, model session, MCP tool
  or resource, tool call, connector event, notification, or Beads record SHALL
  receive its private values or mapping details

#### Scenario: Telemetry and logs remain low-cardinality and content-blind

- **WHEN** the operation emits logs, metrics, or traces
- **THEN** only fixed outcome/failure-category values, the fixed route template,
  and numeric aggregate counts MAY be recorded as applicable
- **AND** receipt, URL as supplied, headers, body, identifiers, idempotency
  material, digests, SQL arguments, provider data, and exception text SHALL be
  absent

### Requirement: Implementation and use remain separately gated

This specification is authority to review the contract only. Implementation
SHALL remain blocked until independent privacy/security review passes on the
exact artifact, the owner separately approves that exact reviewed artifact, and
the adopted `bu-pb6oy` configured-key session/CSRF contract is implemented and
proven. Implementation or usability claims for default keyless Compose SHALL
also remain blocked until the owner resolves `bu-azqfpk` E1/E2, the resulting
exact host-authorized enrollment artifact is independently reviewed and
adopted, and that enrollment/HTTPS path is implemented and proven. This mapping
artifact selects neither enrollment mechanism. Any semantic edit invalidates
prior review and approval.

Implementation SHALL then require real-PostgreSQL tests at the migrated schema,
API and browser-client tests, advisory-lock concurrency tests, transaction
rollback tests, and positive-field plus absence-sentinel privacy tests. The
tests SHALL cover exact replay, fresh-key identical no-op, same-key different
request, duplicate IDs on either side, missing/tombstoned/merged/wrong-type
entities, legacy null targets, conflicts in both directions, injected mid-batch
failure, zero partial writes, and absence from every prohibited surface. Real
PostgreSQL concurrency tests SHALL force both orderings against merge,
`metadata.deleted_at` tombstone, physical delete, and `entity_type` change, and
SHALL force simultaneous same-key/same-request and same-key/different-request
overlap before idempotency lookup/insert.

Merge, queue, deployment/environment availability, actual private mapping
submission (`bu-pvapy`), restart, replay, watermark change, synthetic/natural
transition evidence, remap, delete, and rollback remain separate acts requiring
their own authority.

#### Scenario: Review and owner approval precede implementation

- **WHEN** the draft has not passed independent privacy/security review at its
  exact commit and then received separate owner approval naming that artifact
- **THEN** no implementation, deployment, mapping operation, or private-data
  submission SHALL occur

#### Scenario: Both browser authentication prerequisites precede implementation

- **WHEN** the configured-key session/CSRF path remains unimplemented or the
  host-authorized E1/E2 enrollment path remains unresolved, unadopted, or
  unimplemented
- **THEN** the mapping UI SHALL remain blocked in configured-key deployments
  while the session path is missing, and SHALL remain blocked in default
  keyless Compose while the enrollment path is incomplete
- **AND** neither same-origin access nor an arbitrary first visitor may supply
  the missing authority

#### Scenario: Future verification exercises the real seams

- **WHEN** an approved implementation is proposed
- **THEN** its tests SHALL exercise the actual migrated PostgreSQL schema,
  mounted API/auth/audit middleware, browser client, concurrent lock behavior,
  transactional rollback, and content-blind projections
- **AND** mock-only, source-text-only, empty-audit, or transport-only checks
  SHALL not satisfy the required evidence

#### Scenario: Operational effects remain independently authorized

- **WHEN** an implementation later passes review and terminal hosted CI
- **THEN** that result SHALL not authorize deployment, mapping submission,
  remap/delete/rollback, restart, replay, watermark changes, transition
  simulation, or closure of later natural-transition evidence
