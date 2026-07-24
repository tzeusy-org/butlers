## ADDED Requirements

### Requirement: Versioned Precommit Cancellation Request
The system SHALL expose `wake_recovery.cancel_admit.v1` only as an
authenticated Switchboard-to-Messenger MCP operation for a complete,
already-prepared wake-recovery cohort. The request SHALL contain immutable
`run_id`, `fence`, `participant_digest`, `cohort_digest`, and
`release_action_key`; a stable `cancellation_action_key` and
`cancellation_request_id`; a versioned request fingerprint; an opaque
authorized decision source/reference and reason; and canonical inactive-DND
snapshot evidence consisting of `expected_dnd_generation`, `dnd_observed_at`,
and `dnd_revalidate_at`.

The coordinator SHALL bind every field to its persisted current-fence run
before forwarding it. It SHALL reject a request whose run/fence/digests/action
do not exactly match the frozen cohort and SHALL NOT create a new cancellation
action key on retry. Health and origin Schedulers SHALL provide only their own
local decision evidence to Switchboard; they SHALL NOT call Messenger directly.

At prepare time, Switchboard SHALL persist an immutable ordered manifest of
`(origin_butler, origin_frozen_subset_digest,
origin_frozen_subset_count)` entries. The
`participant_digest` commits to the ordered participant identities and the
`cohort_digest` commits to the exact canonical manifest. This gives every
origin its own locally provable frozen-subset commitment while preserving one
global all-cohort commitment without peer-schema access.

#### Scenario: Complete current-fence request reaches Messenger
- **WHEN** an authorized local decision matches the coordinator's persisted
  prepared run, participant set, cohort digest, and release action
- **THEN** Switchboard invokes Messenger with one matching
  `wake_recovery.cancel_admit.v1` packet
- **AND** the packet contains no notification content, raw DND payload, or
  peer-schema record

#### Scenario: Partial or changed request is rejected before admission
- **WHEN** a request omits a frozen participant or changes its run, fence,
  participant/cohort digest, release action, or cancellation action key
- **THEN** the coordinator rejects it without invoking a changed cancellation
  admission
- **AND** no participant row becomes scheduler-visible

### Requirement: Durable Messenger Cancellation Decision
Messenger SHALL persist one private cancellation-admission record keyed by
`cancellation_action_key` before it returns any result. The record SHALL retain
the schema version, semantic request fingerprint, immutable run/fence/digests/
release-action binding, opaque correlation and decision evidence, captured and
observed DND generations, decision state/code, release-gate state, and database
decision timestamp. It SHALL NOT retain notification content, raw DND
value/metadata, canonical DND mutation fingerprints, or copied provider payload.

An exact replay SHALL return the original durable receipt without changing the
record or release gate. Reusing an action key or request ID with a different
semantic request SHALL fail closed as `idempotency_conflict`. A distinct
cancellation action for the same `(run_id, fence, release_action_key)` SHALL
fail closed as `conflicting_cancellation_action`.

#### Scenario: Exact cancellation replay returns the original receipt
- **WHEN** Messenger receives a retry with the same cancellation action key
  and identical request fingerprint after a terminal decision
- **THEN** it returns the original receipt and decision state
- **AND** it does not create a second cancellation record, egress intent, or
  provider attempt

#### Scenario: Conflicting replay changes no durable state
- **WHEN** a caller reuses a cancellation action key or request ID with a
  changed fence, digest, release action, decision reference, reason, or DND
  evidence
- **THEN** Messenger returns `idempotency_conflict`
- **AND** it leaves the existing cancellation record and release gate unchanged

### Requirement: Guarded No-Egress Precommit Admission
Messenger SHALL decide a new cancellation inside one local transaction. It
SHALL authenticate the Switchboard caller, lock the matching private
prepared-release gate with `FOR UPDATE`, and use the canonical RFC 0009 DND
admission helper on the same connection. The helper SHALL lock the public guard
with `FOR SHARE`, require the captured generation to match, and require a
database-time inactive DND state while Messenger writes its local decision.

Messenger SHALL accept cancellation only when the immutable prepared binding is
valid and it proves that no wake-recovery egress intent, `send_started_at`
marker, provider receipt, provider-attempt ambiguity, or incompatible terminal
release state exists. An accepted decision SHALL durably prevent every later
same-fence commit/release from creating an egress intent. The operation SHALL
not hold either lock across MCP, origin-finalization, provider I/O, or retry
delay.

#### Scenario: Cancellation wins the prepared-release gate
- **WHEN** a matching cancellation acquires the private prepared-release gate
  before a same-fence commit/release operation and DND evidence is current and
  inactive
- **THEN** Messenger persists `accepted_precommit` before returning its receipt
- **AND** the later commit/release rejects before creating an egress intent or
  send-start marker

#### Scenario: Commit wins the prepared-release gate
- **WHEN** a same-fence commit operation has already persisted an egress intent
  before cancellation locks the gate
- **THEN** Messenger persists `rejected_egress_present`
- **AND** cancellation does not remove the intent, publish scheduler work, or
  invoke a provider action

### Requirement: DND Mismatch and Uncertainty Fail Closed
Messenger SHALL record `rejected_blocked_dnd` when the expected DND generation
has changed, current DND is active, or the required guard/database-time/snapshot
evidence is stale, missing, or unprovable. That result SHALL cause Switchboard
to durably replay the parent `abort.v1(reason=blocked_dnd)` operation to every
current-fence participant until each has its parent receipt and every origin
participant has recorded the parent-defined `aborted_dnd` /
`release_retained_dnd` result for its own frozen subset. No member may become
`pending`, receive `cancel_publish.v1`, or enter an egress path from that
cancellation request.

Messenger SHALL record `ambiguous` when it cannot prove a coherent local
release/attempt state or recover a prior decision after timeout or restart. A
timeout without a durable receipt SHALL never be treated as accepted. A
send-start marker, provider receipt, or ambiguous provider attempt SHALL never
be rewritten into cancellation success and SHALL prohibit automatic resend.

#### Scenario: DND generation changes before effective admission
- **WHEN** a local decision carries inactive generation `N` and a canonical
  DND writer commits generation `N + 1` before Messenger obtains the guard
- **THEN** Messenger records `rejected_blocked_dnd` before any egress intent
- **AND** Switchboard replays the same-fence parent `abort.v1(reason=blocked_dnd)`
  packet until every participant has its receipt and every origin has its
  durable `release_retained_dnd` result
- **AND** the complete cohort remains retained and scheduler-ineligible

#### Scenario: Lost response is recovered by exact replay
- **WHEN** Messenger commits a cancellation decision but its MCP response is
  lost or the process restarts
- **THEN** Switchboard retries the same action key and receives the durable
  receipt
- **AND** neither side infers acceptance from the timeout or creates a new
  cancellation action

### Requirement: All-Cohort Scheduler Return Requires Complete Finalization
An `accepted_precommit` Messenger receipt SHALL begin, not complete, a
same-fence all-cohort cancellation finalization. Switchboard SHALL first send
each participant a versioned `wake_recovery.cancel_finalize.v1` request that
contains the accepted Messenger evidence and that recipient's immutable
frozen-subset manifest entry. Each origin SHALL verify the complete matching
local prepared subset and move it atomically to a scheduler-ineligible
cancellation-ready state while persisting its durable finalization receipt.
Switchboard SHALL collect a compatible finalization receipt from every
snapshotted participant and derive a durable finalization digest before it sends
replay-safe `wake_recovery.cancel_publish.v1` packets.

Only a publish packet carrying the accepted decision and complete finalization
digest may move an origin's matching cancellation-ready rows to the
prerequisite-defined scheduler return state. A partial, stale, different-fence,
or changed-digest packet SHALL be rejected. An origin Scheduler and final
Messenger egress admission SHALL preserve the published run/fence/DND evidence;
the parent wake release and legacy unguarded flush SHALL NOT consume it.

#### Scenario: Missing participant prevents scheduler publication
- **WHEN** Messenger has accepted cancellation but one current-fence participant
  has not produced a compatible `cancel_finalize.v1` receipt
- **THEN** every unfinalized participant remains prepared or cancellation-ready
  and scheduler-ineligible
- **AND** replay retries only the same cancellation action and participant set

#### Scenario: Complete finalization publishes only the matching cohort
- **WHEN** Switchboard has persisted compatible finalization receipts for every
  snapshotted participant under one run, fence, action, and cohort digest
- **THEN** each matching origin accepts the same finalization digest and moves
  all of its cancellation-ready rows through the defined scheduler-return path
- **AND** no individual row, late row, or subset can be published independently

### Requirement: Versioned Same-Fence Origin Finalization
`wake_recovery.cancel_finalize.v1` SHALL be an authenticated
Switchboard-to-origin MCP operation that precedes every `cancel_publish.v1`
authorization. Its semantic request SHALL include exact `run_id`, `fence`,
`origin_butler`, `participant_digest`, `cohort_digest`,
`origin_frozen_subset_digest`, `origin_frozen_subset_count`,
`release_action_key`, `cancellation_action_key`, opaque accepted Messenger
`accepted_admission_receipt` and its `admission_receipt_digest`, a stable
`cancel_finalize_action_key` and `cancel_finalize_request_id`, and a versioned
request fingerprint. The evidence SHALL identify the immutable
`accepted_precommit` decision without carrying notification, DND, or provider
payload. The action and request identities SHALL be reused for every retry of
that origin's finalization.

The receiver SHALL authenticate the current-fence Switchboard coordinator before
reading or changing local cancellation state. Messenger, Health, a Scheduler, a
provider, and unauthenticated callers SHALL be rejected. The origin SHALL
verify the version, target origin, run/fence, global commitments, release and
cancellation actions, accepted evidence, and its own immutable frozen-subset
digest/count from local durable state without reading a peer schema. In the same
local transaction, it SHALL move all and only that frozen subset to
`cancellation_ready` and persist one versioned durable finalization receipt
keyed by `cancel_finalize_action_key`. The receipt SHALL contain the request
fingerprint, accepted-admission digest, run/fence/global commitments, local
manifest digest/count, and resulting local state before the receiver responds.

An exact replay with the same action, request ID, and fingerprint SHALL return
the original durable receipt after a Switchboard or origin restart. Reusing
either identity with a changed semantic field SHALL return
`idempotency_conflict` without changing local state. A wrong target, different
or stale fence, missing/non-accepted evidence, or local-manifest mismatch SHALL
fail closed before the transition. A timeout without a durable receipt is not
finalization: Switchboard SHALL retry the same finalization action/request and
SHALL NOT derive a finalization digest or send `cancel_publish.v1`; a committed
receipt with a lost response is recovered only by that exact replay.

#### Scenario: Only Switchboard can request origin finalization
- **WHEN** Messenger, Health, a Scheduler, a provider, or an unauthenticated
  caller invokes `cancel_finalize.v1` directly
- **THEN** the origin rejects the request before reading or changing its local
  cancellation state
- **AND** it creates neither a cancellation-ready transition nor a finalization
  receipt

#### Scenario: Finalization replay survives conflict and restart
- **WHEN** an origin commits its finalization receipt and either side restarts
  before the response is observed
- **THEN** the same finalization action/request/fingerprint returns that receipt
- **AND** reusing either identity with changed receipt evidence, fence, manifest,
  or action returns `idempotency_conflict` without another local transition

#### Scenario: Missing finalization receipt blocks publication
- **WHEN** a finalization response times out before its receipt is durable
- **THEN** Switchboard retains and retries the same finalization action/request
- **AND** it does not derive an all-cohort finalization digest or issue
  `cancel_publish.v1`

### Requirement: Versioned Same-Fence Origin Publication
`wake_recovery.cancel_publish.v1` SHALL be an authenticated
Switchboard-to-origin MCP operation. Its semantic request SHALL include exact
`run_id`, `fence`, `origin_butler`, `participant_digest`, `cohort_digest`,
`origin_frozen_subset_digest`, `origin_frozen_subset_count`, `release_action_key`,
`cancellation_action_key`, accepted Messenger `admission_receipt_digest`,
that origin's `origin_finalization_receipt_digest`,
`cancellation_finalization_digest`, a stable `cancel_publish_action_key` and
`cancel_publish_request_id`, and a versioned request fingerprint. The action and
request identities SHALL be reused for every retry of that origin's publish.

The receiving origin SHALL authenticate Switchboard, verify the version and all
global commitments, and prove its own immutable frozen subset from its local
prepared/cancellation-ready state and durable finalization receipt without
reading any peer schema. It SHALL persist one local publish receipt keyed by
`cancel_publish_action_key` before changing scheduler eligibility. An exact
replay after a Switchboard or origin restart SHALL return that receipt. A changed
semantic field, wrong target origin, different fence, missing or incompatible
origin finalization receipt/finalization evidence, or local subset mismatch SHALL
fail closed without changing local eligibility. A timeout without a durable
receipt SHALL not be treated as publication.

#### Scenario: Origin proves only its own frozen subset
- **WHEN** an origin receives a current-fence `cancel_publish.v1` packet with a
  matching global cohort digest but a changed local subset digest or count
- **THEN** the origin rejects the packet using only its own durable state
- **AND** no row becomes scheduler-visible and no peer-schema read occurs

#### Scenario: Origin replay survives restart
- **WHEN** an origin commits its publish receipt and either it or Switchboard
  restarts before the response is observed
- **THEN** replaying the same publish action/request/fingerprint returns the
  original durable receipt
- **AND** a changed publish action/request/fingerprint cannot create another
  eligibility transition
