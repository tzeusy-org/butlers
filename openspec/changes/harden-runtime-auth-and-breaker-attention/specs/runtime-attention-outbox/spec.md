## ADDED Requirements

### Requirement: Durable Runtime Attention Episodes

The system SHALL represent model-breaker and fleet-halt owner pages as durable,
public runtime-attention episodes. A producer SHALL append an immutable,
safe-payload episode in the same database transaction that establishes the
corresponding operational state edge. Each episode SHALL have a stable ID, a
unique immutable triggering-attempt/edge key, source, lifecycle state,
timestamps, optional terminal delivery evidence, and optional lineage to an
explicitly reissued episode. Terminal evidence SHALL consist only of a paired,
finite sanitized error class/detail vocabulary and an optional scalar
notification reference; it SHALL store neither credential values nor raw
provider error payloads, and it SHALL NOT impose a cross-schema notification
foreign key. The schema SHALL enforce a partial unique
`model_breaker` triggering-dispatch-attempt key and a partial unique
`fleet_halt` source/month breach key. It SHALL retain the safe source snapshot
when a catalog entry or dispatch-attempt record is later deleted under its own
retention policy.

ID: REQ-runtime-attention-outbox-001
Source: heart-and-soul/vision.md Rule 3 and Rule 4; RFC 0001; RFC 0011 Amendment 1; design.md Decisions 3-5
Scope: v1-mandatory

#### Scenario: Core-only staging retains only bounded dormant delivery evidence

- **WHEN** the core migration is applied before a Switchboard delivery worker
  or notification schema is active
- **THEN** a terminal episode can retain only an allowlisted error class/detail
  pair and an optional scalar notification reference
- **AND** producers leave those fields `NULL`, no runtime role receives an
  evidence-update grant, and no worker or transport is activated by the
  migration

#### Scenario: A breaker opening atomically creates one episode

- **WHEN** recording a qualifying `runtime_failure` changes one catalog
  entry's derived breaker from closed to open
- **THEN** that transaction appends exactly one `model_breaker` attention
  episode keyed to the immutable triggering dispatch-attempt ID
- **AND** a transaction rollback persists neither the qualifying outcome nor
  its episode

#### Scenario: Concurrent breaker failures cannot create duplicate episodes

- **WHEN** concurrent qualifying failures target the same catalog entry
- **AND** one transaction has already established the closed-to-open breaker
  edge
- **THEN** every other transaction observes the resulting breaker state before
  it can append an episode
- **AND** only the edge-triggering dispatch attempt has an episode

#### Scenario: A failed half-open probe is a new episode

- **WHEN** a cooldown-expired breaker entry is selected as a half-open probe
- **AND** that routed probe records a qualifying failure that reopens the
  breaker
- **THEN** the new closed-to-open edge creates one new episode
- **AND** its deduplication key is distinct from the earlier opening episode

#### Scenario: Concurrent failed half-open probes create one new episode

- **WHEN** a breaker is already open, its cooldown is expired, and two or more
  routed probes with distinct attempt IDs fail concurrently
- **THEN** exactly one new reopening episode is appended for that cooldown
  episode
- **AND** every other failed probe remains dispatch evidence without appending
  a duplicate episode

#### Scenario: Concurrent ceiling denials create one fleet-halt episode

- **WHEN** monthly-ceiling denials call the fleet-halt producer concurrently for
  the same calendar month
- **THEN** exactly one `fleet_halt` episode exists for that month
- **AND** every denial that did not create it is handed that same episode rather
  than an absent one
- **AND** the producer alone establishes that, so a denial it resolves without
  appending an episode commits without waiting on the month's serialization

#### Scenario: Existing incidents are not re-paged during migration

- **WHEN** the outbox migration is deployed while a breaker is already open or
  a calendar-month fleet halt is already active
- **THEN** it creates no retrospective episode or external page
- **AND** a fleet halt with current-month evidence older than the producer
  activation boundary remains unpaged for the rest of that month
- **AND** historical dispatch, ledger, notification, and audit evidence
  remains readable and unchanged

#### Scenario: Producer rollback retains the legacy debounce-marker planter

- **WHEN** the privileged core_199 rollback disables runtime-attention producers
- **THEN** qualifying dispatch attempts remain recordable but append no new episodes
- **AND** the outbox, existing evidence, v2 producer functions, and the
  old-binary debounce-marker planter remain installed
- **AND** the planter blocks no insert; it records the marker rows a legacy
  direct-delivery helper debounces itself on, so suppression holds only while
  such a helper honours its own debounce
- **AND** rollback does not restore either legacy direct-delivery helper or
  permit a further core_198 teardown beneath the retained planter

### Requirement: Switchboard-Owned At-Most-Once Attention Delivery

Switchboard SHALL be the sole external delivery claimant for runtime-attention
episodes. Producers may append their authorized rows but SHALL not claim,
mutate, inspect other producers' payloads, or call Messenger directly. Before
external transport may begin, Switchboard SHALL durably claim the episode as
`sending` with a fresh claim token, monotonically increasing claim epoch,
claimer instance, and timestamp. A confirmed send transitions it to `sent`; a
definitive rejection transitions it to `failed`; an ambiguous send outcome
transitions it to `uncertain` and is never retried automatically. Every claim
and terminal transition SHALL be conditional on the current claim token.

ID: REQ-runtime-attention-outbox-002
Source: heart-and-soul/vision.md Rule 3; RFC 0003; RFC 0011 Amendment 1; design.md Decisions 4-5
Scope: v1-mandatory

#### Scenario: Claim precedes external transport

- **WHEN** Switchboard selects a pending episode for delivery
- **THEN** it durably records `sending` before it invokes Messenger or a
  transport adapter
- **AND** it records the fresh fenced claim identity and verifies it immediately
  before transport
- **AND** concurrent workers cannot claim the same episode

#### Scenario: Proven pre-send failures may use bounded backoff

- **WHEN** a claimed episode fails before any external transport can have
  started
- **THEN** Switchboard MAY return it to pending with a bounded next-attempt
  time and safe failure context
- **AND** it SHALL not consume a new deduplication key or create a duplicate
  attention episode

#### Scenario: Ambiguous transport is never replayed automatically

- **WHEN** an episode is `sending` and Switchboard times out, loses a
  connection, or recovers after a process death without proof that transport
  was not attempted
- **THEN** the episode becomes terminal `uncertain`
- **AND** no worker, recovery loop, or dashboard refresh sends it again

#### Scenario: A live claimant times out its own bounded transport attempt

- **WHEN** the current fenced claimant reaches the bounded external transport
  deadline without a confirmed send or proven pre-send failure
- **THEN** that claimant conditionally transitions its own episode to
  `uncertain` and releases the delivery-service lease
- **AND** no other worker treats row age alone as proof that it may replay or
  reissue the episode

#### Scenario: Recovery fences a dead claim but never reclaims it for send

- **WHEN** a persisted `sending` episode's claimant is no longer able to hold
  the Switchboard delivery-service lease
- **THEN** the recovering worker first takes that lease and atomically fences
  the old claim token before it can transition the episode to `uncertain`
- **AND** it does not return that episode to `pending`, invoke transport, or
  expose manual reissue while a live claimant still owns the lease; a stuck
  live claimant instead remains observable as `sending`
- **AND** a stale claimant whose token was fenced cannot invoke transport or
  change the terminal state

#### Scenario: Post-send bookkeeping cannot reverse delivery

- **WHEN** Messenger confirms delivery and a later routing-log, registry,
  notification-log, audit, or attention-ledger write fails
- **THEN** the episode remains `sent`
- **AND** the bookkeeping failure is recorded as safe observability evidence
  without creating another send attempt

### Requirement: Explicit Uncertain-Episode Reissue

The system SHALL permit only the fail-closed authenticated dashboard-owner
control principal to explicitly reissue an `uncertain` runtime-attention
episode after a confirmation-gated action. The server SHALL enforce that
authorization before it reads the protected episode or creates a successor.
The action SHALL create a new pending episode with immutable lineage to the
original; it SHALL never overwrite the original state, reset a breaker, or
cause an automatic replay. A partial unique direct-parent lineage constraint
and atomic state-checked `INSERT ... ON CONFLICT` operation SHALL create or
return at most one direct successor for an original episode.

ID: REQ-runtime-attention-outbox-003
Source: heart-and-soul/vision.md Rule 1 and Rule 4; RFC 0005; design.md Decisions 4 and 6
Scope: v1-mandatory

#### Scenario: Confirmed manual reissue creates a distinct episode

- **WHEN** an operator confirms a reissue for an `uncertain` episode
- **THEN** the system creates one new pending episode with a fresh ID and
  deduplication key and a reference to the original episode
- **AND** the original remains `uncertain` with its original timestamps and
  safe error evidence

#### Scenario: Concurrent manual reissues return one successor

- **WHEN** two confirmed reissue requests race for the same `uncertain`
  original episode
- **THEN** both callers receive the same one successor episode
- **AND** the database retains one direct successor lineage row and schedules
  only that successor for delivery

#### Scenario: Callers without dashboard-owner control cannot inspect or reissue an episode

- **WHEN** owner-control is not configured, or a caller lacks the configured
  dashboard owner credential, and it requests protected attention episode
  detail or a manual reissue
- **THEN** the API returns its appropriate unavailable or unauthorized response
  before exposing episode data
- **AND** it creates no successor and triggers no transport attempt

#### Scenario: Reissue does not alter routing eligibility

- **WHEN** an operator reissues a model-breaker attention episode
- **THEN** the model's breaker state remains derived only from qualifying
  routed dispatch outcomes
- **AND** a runtime probe, reissue, or outbox state transition does not write a
  synthetic routed success or close the breaker
