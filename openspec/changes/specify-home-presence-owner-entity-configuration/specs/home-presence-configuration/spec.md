## ADDED Requirements

### Requirement: Owner-authenticated dashboard-only presence entity settings surface

The system SHALL expose exactly two mutations of `home:presence:owner_entities`
under this capability: `GET /api/home/settings/presence/owner-entities` and
`PUT /api/home/settings/presence/owner-entities`. Neither route SHALL accept a
path or query parameter. Neither route SHALL be registered as an MCP/runtime
tool or be callable from an LLM session, CLI, connector, scheduled job,
background retry, or direct-SQL operator workflow.

Before any component reads or buffers the request body, acquires a database
pool, or observes protected state, both routes SHALL pass the existing
fail-closed `require_dashboard_owner_control` contract or a separately
owner-approved successor with equivalent guarantees. Absent owner-control
configuration SHALL return `503`; a missing or mismatched owner credential
SHALL return `401`. The optional fail-open `ApiKeyMiddleware` is insufficient
for either route.

After authentication, the audit actor SHALL be derived server-side through
`authenticated_principal()`. Neither request SHALL expose a caller-asserted
actor field. `authenticated_principal()` is attribution only and SHALL NOT be
treated as an authentication check.

The dashboard UI SHALL remain blocked until `bu-pb6oy` supplies a separately
approved and implemented browser credential-transport contract. This
requirement SHALL NOT choose or imply a cookie, JavaScript-held credential,
build-time credential, same-origin bypass, or any other new browser
authentication mechanism.

#### Scenario: Unconfigured owner control fails before body or pool access

- **WHEN** `DASHBOARD_API_KEY` is unavailable and a caller invokes either
  route
- **THEN** the API SHALL return `503` with a fixed content-blind error
- **AND** no layer SHALL read or buffer the request body, acquire a database
  pool, read `home:presence:owner_entities` or `ha_entity_snapshot`, or write
  audit state

#### Scenario: Wrong credential is denied before body or pool access

- **WHEN** owner control is configured but the credential is missing or wrong
- **THEN** the API SHALL return `401` after constant-time comparison
- **AND** no layer SHALL read or buffer the body or perform a database
  operation

#### Scenario: Authenticated actor is server-derived

- **WHEN** a request has passed the owner-control boundary
- **THEN** the audit actor SHALL come from `authenticated_principal()`
- **AND** no request field, header, query parameter, or path value SHALL
  assert or override the actor

#### Scenario: Browser workflow waits for its authentication prerequisite

- **WHEN** `bu-pb6oy` has not produced an approved and implemented
  browser-auth contract that can satisfy the owner-control boundary
- **THEN** the settings UI SHALL NOT ship or be described as usable
- **AND** no alternate credential transport or same-origin bypass SHALL be
  introduced by this capability

### Requirement: Authenticated GET reflects the exact stored configuration

An authenticated `GET` SHALL return `ApiResponse` data of
`{configured: bool, owner_entities: string[], version: int}` reflecting the
literal `home:presence:owner_entities` row, with `Cache-Control: no-store`.
When no row exists, the response SHALL be
`{configured: false, owner_entities: [], version: 0}`. When a row exists
holding an empty list, the response SHALL be
`{configured: false, owner_entities: [], version: <the row's real version>}`
-- version SHALL NOT be reported as `0` merely because the list is empty.
When a row holds a non-empty list, `configured` SHALL be `true` and
`owner_entities` SHALL be the exact stored, ASCII-sorted list.

#### Scenario: No row on file

- **WHEN** `home:presence:owner_entities` has never been written
- **THEN** GET SHALL return `configured=false`, `owner_entities=[]`,
  `version=0`, with `Cache-Control: no-store`

#### Scenario: Row exists but was explicitly cleared

- **WHEN** the stored value is `[]` at row version `N` (`N` >= 1)
- **THEN** GET SHALL return `configured=false`, `owner_entities=[]`,
  `version=N`

#### Scenario: Row exists with configured entities

- **WHEN** the stored value is a non-empty list at row version `N`
- **THEN** GET SHALL return `configured=true`, the exact ASCII-sorted stored
  list, and `version=N`

### Requirement: Bounded, exact, locally-observed PUT payload

An authenticated `PUT` body SHALL contain exactly `owner_entities` (an array
of zero to 50 distinct strings) and `expected_version` (a non-negative
integer), with no other field accepted (`extra="forbid"`). The decoded body
SHALL be at most 32 KiB.

Each member of `owner_entities` SHALL be 1-255 UTF-8 bytes matching
`\A[a-z0-9_]+\.[a-z0-9_]+\Z` byte-for-byte. The system SHALL NOT trim,
case-fold, normalize, autocomplete, or resolve a member from a friendly name,
alias, or Home Assistant API/provider call. The accepted shape SHALL NOT be
restricted to a `person`/`device_tracker` domain allowlist: a non-person
entity id (e.g. a room-scoped sensor already consumed by the `in_space`
producer contract) SHALL be accepted on the same terms as a `person.*` or
`device_tracker.*` id.

A duplicate member (identical or divergent submissions of the same id) SHALL
return one fixed content-blind `422 INVALID_REQUEST` category with no member
index or value, and SHALL perform zero state writes. The same category SHALL
apply to an unknown field, a non-array `owner_entities`, a non-integer or
negative `expected_version`, an out-of-bounds count, or a member failing the
length/character constraints above.

Each non-empty submitted id SHALL additionally name a row already present in
the same butler's `ha_entity_snapshot` table at PUT time; the system SHALL
NOT query Home Assistant directly to validate a reference. One or more
missing references SHALL return a single aggregate content-blind
`422 INVALID_REFERENCE` response exposing only `invalid_reference_count`, and
SHALL perform zero state writes. A locally observed row that is stale (an old
`last_updated`) SHALL remain a valid reference; freshness governs signal
derivation in the context producer, not storage eligibility here.

An empty `owner_entities` array SHALL be accepted as the explicit, reversible
way to restore `configured=false`; it SHALL store an empty JSON array and
SHALL NOT delete the underlying row.

#### Scenario: Valid bounded submission is evaluated without normalization

- **WHEN** an authenticated PUT submits zero to 50 distinct ids, each
  matching the exact accepted syntax and observed in `ha_entity_snapshot`,
  within the 32 KiB body bound
- **THEN** the server SHALL evaluate exactly those ids without trimming,
  case-folding, autocomplete, or name/alias resolution

#### Scenario: Duplicate member is rejected as a whole

- **WHEN** `owner_entities` repeats an id, identical or not
- **THEN** the API SHALL return `422 INVALID_REQUEST` with no member index or
  value
- **AND** it SHALL perform zero state writes

#### Scenario: Unobserved reference is one content-blind category

- **WHEN** one or more submitted ids have no matching row in
  `ha_entity_snapshot`
- **THEN** the API SHALL return `422 INVALID_REFERENCE` exposing only
  `invalid_reference_count`
- **AND** it SHALL NOT distinguish which member failed
- **AND** it SHALL perform zero state writes

#### Scenario: Non-person room sensor is a valid reference

- **WHEN** a submitted id is a non-`person`/non-`device_tracker` domain
  entity already observed in `ha_entity_snapshot` (e.g. a room-area sensor)
- **THEN** the API SHALL accept it on the same terms as a `person.*` or
  `device_tracker.*` id
- **AND** no domain allowlist SHALL reject it

#### Scenario: Empty submission restores unconfigured state

- **WHEN** an authenticated PUT submits `owner_entities: []` with the correct
  `expected_version`
- **THEN** the stored value SHALL become `[]` at an incremented version
- **AND** the row SHALL NOT be deleted
- **AND** a subsequent GET SHALL report `configured=false` with that real
  version

### Requirement: Version-CAS write semantics with atomic whole-row visibility

An authenticated, structurally and referentially valid PUT SHALL execute
under one Home database transaction holding the fixed advisory lock derived
from `butlers:home:presence-owner-entities:v1`. Under that lock the server
SHALL compare the validated, sorted submitted list to the current stored
value:

- If the submitted list equals the current value (including both being
  absent-or-empty), the PUT SHALL return `200` with the current version and
  SHALL perform zero writes, regardless of whether the caller's
  `expected_version` matches.
- If the submitted list differs and `expected_version` equals the current
  row's version (or the row is absent and `expected_version == 0`), the PUT
  SHALL commit a single-row upsert -- incrementing the version for an
  existing row or inserting at version `1` when none exists -- inside the
  same transaction as its audit evidence.
- If the submitted list differs and `expected_version` does not match, the
  PUT SHALL return `409 VERSION_CONFLICT` with no current identifiers and
  SHALL perform zero writes.

A concurrent GET, and the scheduled context-producer's read of the same key,
SHALL each observe either the complete prior value or the complete new value
-- never a partial value -- because the JSONB column is replaced in one
statement. The advisory lock SHALL serialize only this route's writes; it
SHALL NOT be taken by, and SHALL NOT add latency or contention to, the
producer's or GET's plain read of the row.

A validation failure, lock failure, database failure, or injected
pre-commit failure SHALL leave the prior value and version completely
unchanged and SHALL NOT return a success response. A successful PUT SHALL be
solely a configuration receipt: it SHALL NOT invoke
`run_home_presence_context_producer`, SHALL NOT write `public.user_context`,
and SHALL NOT synchronously assert, clear, or otherwise change `at_home` or
`in_space`. The next scheduled producer run SHALL apply its existing
healthy/unmeasurable/unconfigured/freshness/TTL handling to whatever it next
reads.

#### Scenario: Exact resubmission is a no-op success

- **WHEN** the submitted, validated, sorted list exactly equals the current
  stored value
- **THEN** the API SHALL return `200` with the unchanged current version
- **AND** it SHALL perform zero writes, even if `expected_version` is stale

#### Scenario: Matching version commits the change

- **WHEN** the submitted list differs from the current value and
  `expected_version` equals the current row's version
- **THEN** the API SHALL commit the new sorted list at an incremented version
- **AND** the response SHALL report the new version

#### Scenario: First-ever configuration uses version zero

- **WHEN** no row exists yet and a PUT submits `expected_version: 0` with a
  non-empty valid list
- **THEN** the API SHALL insert a new row at version `1`
- **AND** the response SHALL report `configured=true` and `version=1`

#### Scenario: Stale version on a divergent submission is a conflict

- **WHEN** the submitted list differs from the current value and
  `expected_version` does not equal the current row's version
- **THEN** the API SHALL return `409 VERSION_CONFLICT` with no current
  identifiers
- **AND** it SHALL perform zero writes

#### Scenario: Concurrent divergent writers serialize to one winner

- **WHEN** two PUTs submitting different valid lists race from the same
  observed version
- **THEN** the fixed advisory lock SHALL serialize their evaluation
- **AND** at most one SHALL commit a changed row
- **AND** the other SHALL observe the new version and return
  `409 VERSION_CONFLICT`

#### Scenario: Concurrent identical writers converge

- **WHEN** two PUTs submit the same valid list concurrently
- **THEN** exactly one SHALL commit the write
- **AND** the other SHALL observe an identical current value and return the
  same `200` no-op success

#### Scenario: Injected pre-commit failure changes nothing

- **WHEN** a database or pre-commit failure occurs after validation but
  before the transaction commits
- **THEN** the prior value and version SHALL remain unchanged
- **AND** no response SHALL claim success

#### Scenario: A committed PUT produces no synchronous context effect

- **WHEN** a PUT commits a new sorted list
- **THEN** the API response SHALL settle before any producer runs
- **AND** the call SHALL NOT invoke the context producer or change
  `public.user_context`
- **AND** the next scheduled producer run SHALL be the only thing that
  reads the new list into `at_home`/`in_space`

### Requirement: Malformed or unavailable state fails closed

When the stored `home:presence:owner_entities` value exists but is not a JSON
array of strings, both GET and PUT SHALL return `503
PRESENCE_CONFIG_UNAVAILABLE` and SHALL disclose no raw value, type, or key
name. Neither route SHALL silently treat that malformed value as
`configured: false` the way the context producer's own fallback does, and a
PUT SHALL NOT overwrite it while it is in that state. Ordinary database or
connection-pool unavailability after authentication SHALL use the same fixed
`503` and SHALL NOT be presented as an empty or unconfigured success.

#### Scenario: Malformed pre-existing value blocks read and write

- **WHEN** the stored value is present but is not a JSON array of strings
- **THEN** GET and PUT SHALL both return `503 PRESENCE_CONFIG_UNAVAILABLE`
- **AND** neither response SHALL include the raw value, its type, or the
  state key name
- **AND** PUT SHALL perform zero writes

#### Scenario: Database unavailability is a fixed failure, not a false empty

- **WHEN** the database or connection pool is unavailable after
  authentication succeeds
- **THEN** the API SHALL return `503` without disclosing driver or
  connection detail
- **AND** it SHALL NOT return `configured: false` as if the key were simply
  unset

### Requirement: Aggregate-only audit, logs, and telemetry; structural middleware exemption

`DashboardAuditMiddleware` SHALL NOT read or buffer the request body for
`PUT /api/home/settings/presence/owner-entities`. This exemption SHALL be
evaluated by exact path and method before the body is read, so it applies
identically to an authenticated call, a `401`/`503` denial, and malformed
JSON. A field-name redaction list alone SHALL NOT satisfy this requirement.

The route SHALL emit exactly one explicit audit event,
`home_presence_owner_entities_put`, allowlisting only: server-derived actor,
fixed operation name, `configured` (bool), `entity_count` (int),
`prior_version`/`new_version` (nullable ints), and a fixed `outcome` value
drawn from `updated`, `unchanged`, `invalid_request`, `invalid_reference`,
`version_conflict`, or `unavailable`. That event, application/access logs,
metrics, and traces SHALL NOT contain any entity id, request body, raw URL,
header, SQL argument, or exception text. No prompt, model session, tool call,
MCP resource/tool, connector event, notification, or Beads record SHALL
receive an entity id from this capability.

#### Scenario: Generic audit middleware never reads the PUT body

- **WHEN** any caller invokes the PUT route, including before authentication
  and with malformed JSON
- **THEN** `DashboardAuditMiddleware` SHALL NOT read, buffer, parse, redact,
  or store the body
- **AND** no generic audit row SHALL contain the request body or an entity id

#### Scenario: Explicit audit publishes only the fixed allowlist

- **WHEN** a PUT reaches any terminal outcome
- **THEN** the explicit audit event SHALL contain only the allowlisted
  fields
- **AND** it SHALL contain no entity id, list, or free-text detail

#### Scenario: No LLM, MCP, or provider surface can observe a value

- **WHEN** a GET or PUT is authenticated, denied, refused, or fails
- **THEN** no prompt, model session, MCP tool/resource, connector event,
  notification, or Beads record SHALL receive an entity id from this
  capability

### Requirement: This contract does not claim to close the pre-existing generic disclosure and write paths

The generic per-butler state surface
(`GET/PUT/DELETE /api/butlers/{name}/state[/{key}]`) and the
`state_get`/`state_set`/`state_list`/`state_delete` MCP tools remain able to
read and unconditionally overwrite `home:presence:owner_entities` without
passing through this capability's authentication, validation, or version-CAS
logic. This capability's write-serialization guarantee SHALL apply only
among callers of its own two routes.

No response body, audit event, log line, or accompanying documentation
produced by this capability SHALL state or imply that these two routes are
the only way `home:presence:owner_entities` can change, or that this
capability's version-CAS guarantee extends to writes made through the
generic state API or the state MCP tools.

#### Scenario: Generic state routes remain unrestricted by this capability

- **WHEN** a caller reads or writes `home:presence:owner_entities` through
  `GET/PUT /api/butlers/home/state/home:presence:owner_entities` or the
  `state_get`/`state_set` MCP tools
- **THEN** this capability's owner-control, validation, and version-CAS
  logic SHALL NOT apply to that call
- **AND** such a write SHALL change the row's version outside this
  capability's advisory lock, independent of any PUT this capability's
  routes have serialized

#### Scenario: No surface overstates this capability's guarantee

- **WHEN** any response, audit row, log line, or spec/doc text describes
  this capability's concurrency or privacy guarantee
- **THEN** it SHALL NOT claim protection against writes made through the
  generic state API or state MCP tools

### Requirement: Implementation and use remain separately gated

This specification is authority to review the contract only. Implementation
SHALL remain blocked until independent privacy/security review passes on the
exact artifact, the owner separately approves that exact reviewed artifact,
and `bu-pb6oy` supplies an approved browser authentication path. Any semantic
edit invalidates prior review and approval.

Implementation SHALL then require real-PostgreSQL tests at the migrated Home
schema, API tests proving denial before body/pool access, advisory-lock
concurrency tests, transaction-rollback tests, and positive-field plus
absence-sentinel privacy tests, plus one end-to-end test proving the stored
canonical list is what `run_home_presence_context_producer` actually
consumes. Merge, queue, deployment/environment availability, real HA
identifier submission, restart, replay, and later natural at_home/in_space
transition verification remain separate acts requiring their own authority.

#### Scenario: Review and owner approval precede implementation

- **WHEN** the draft has not passed independent privacy/security review at
  its exact commit and then received separate owner approval naming that
  artifact
- **THEN** no implementation, deployment, or real-identifier submission
  SHALL occur

#### Scenario: Future verification exercises the real seams

- **WHEN** an approved implementation is proposed
- **THEN** its tests SHALL exercise the actual migrated PostgreSQL schema,
  mounted API/auth/audit middleware, concurrent advisory-lock behavior,
  transactional rollback, and the real context producer
- **AND** mock-only, source-text-only, or empty-audit checks SHALL NOT
  satisfy the required evidence

#### Scenario: Operational effects remain independently authorized

- **WHEN** an implementation later passes review and terminal hosted CI
- **THEN** that result SHALL NOT authorize deployment, real identifier
  submission, restart, replay, or closure of later natural-transition
  evidence
