## ADDED Requirements

### Requirement: Atomic pending-action delivery admission
The system SHALL commit every new pending approval action and exactly one schema-local durable delivery-intent root in the same PostgreSQL transaction, with a unique foreign-keyed `action_id` and immutable logical action key. The admission SHALL create an initial direct-action presentation for `single`; an initial durable cohort, fourth-member membership, and cohort-owned digest presentation for `cohort_anchor`; or a terminal non-sendable collapsed action presentation plus cohort membership for `collapsed`; a failed admission SHALL commit neither record.

ID: REQ-approval-delivery-intent-recovery-001
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Transaction failure rolls back action and delivery state
- **WHEN** an insertion or validation failure occurs after a pending action is staged but before its delivery intent commits
- **THEN** the owning schema contains neither the new pending action nor a root/presentation/cohort-membership/attempt row for its action key
- **AND** a successful duplicate semantic-key admission returns the pre-existing action/intent pair without a second key

#### Scenario: Runtime availability does not remove delivery state
- **WHEN** a valid producer parks an action while its notification worker or Switchboard client is unavailable
- **THEN** the action and its required direct presentation, cohort digest, or collapsed membership still commit atomically in a sendable or policy-terminal state
- **AND** no caller may omit that delivery state by passing a null live runtime

### Requirement: Closed presentation state, reason, and ownership boundary
The system SHALL keep a stable action-intent root classified as `single`, `cohort_anchor`, or `collapsed`, and persist each direct-action or cohort-owned delivery presentation only as `ready`, `claimed`, `handoff_started`, `retry_wait`, `delivered`, `collapsed`, `cancelled`, `superseded`, or `ambiguous`; it SHALL persist only an allowlisted safe reason code without recipient, callback material, raw provider payload, raw exception, rendered message, or domain-action mutation authority.

ID: REQ-approval-delivery-intent-recovery-002
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Unknown state or reason is rejected
- **WHEN** a writer attempts to create or transition a presentation with a state or reason outside the closed vocabulary
- **THEN** schema and repository validation reject the write
- **AND** API, metric, and log projections expose only the safe normalized value

#### Scenario: Worker has notification-only authority
- **WHEN** the approval-delivery worker processes an associated pending action
- **THEN** it may read that action/cohort and write only its local presentation, attempt, and safe audit-observability records
- **AND** it has no path to approve, reject, expire, defer, execute, edit, or otherwise mutate `pending_actions`

### Requirement: Fenced claim and at-least-once recovery
The system SHALL claim due sendable presentations with a monotonic fence, opaque claim token, finite lease, and compare-and-set writes, and SHALL retry only safely unstarted or provider-idempotent work with bounded backoff until the presentation is superseded/cancelled, the action becomes terminal, or the outcome is ambiguous.

ID: REQ-approval-delivery-intent-recovery-003
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: A stale worker cannot write after lease succession
- **WHEN** one worker's lease expires and a second worker obtains a higher fence for the same presentation generation
- **THEN** every renewal or transition from the stale token/fence is rejected
- **AND** only the successor may make a new pre-handoff transition

#### Scenario: Restart recovers an unstarted claim
- **WHEN** a process crashes after claiming a presentation but before recording provider handoff start
- **THEN** a later worker reclaims the expired claim and retries the same immutable presentation key after its scheduled backoff
- **AND** no duplicate action, root, or presentation is created

### Requirement: Idempotent, authenticated, and ambiguous provider handoff
The system SHALL persist a fenced pre-provider handoff marker and require the actual Messenger boundary to classify an immutable presentation key as `confirmed`, `safe_retry`, or `ambiguous`; it SHALL bind that key to a transport-authenticated issuer, owning schema, and approved presentation mode before ledger/provider work, and SHALL never blindly resend an uncertain post-start handoff.

ID: REQ-approval-delivery-intent-recovery-004
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Confirmed same-presentation replay is idempotent
- **WHEN** the source worker repeats a delivery request whose Messenger handoff ledger has a confirmed receipt for the same presentation key
- **THEN** Messenger returns a confirmed duplicate-safe result and the presentation is or remains `delivered`
- **AND** the provider adapter is not invoked a second time

#### Scenario: Post-start timeout is quarantined without proof
- **WHEN** a provider call may have started but the source or Messenger loses the outcome and the adapter cannot reconcile the presentation key
- **THEN** the presentation becomes `ambiguous` with a safe reason code
- **AND** restart, lease recovery, and retry scans do not issue another provider send for that presentation key

#### Scenario: Spoofed recovery binding is rejected before handoff state
- **WHEN** a request presents an action/cohort recovery subject whose claimed schema, issuer, or mode differs from the authenticated daemon principal and registered owning schema
- **THEN** Switchboard/Messenger reject it before creating a generic notification row, recovery ledger row, or provider attempt
- **AND** a generic caller cannot promote an ordinary `notify.v1` request into recovery mode by adding recovery-shaped fields

### Requirement: Decision, expiry, and defer presentation fencing
The system SHALL couple every transition out of `pending` to cancellation of that action's nonterminal presentations and an atomic update that marks its cohort membership ineligible in the same local transaction, using a fixed action-then-intent-presentation lock order and treating the committed handoff-start marker as the final cancellation-safe boundary. It SHALL not cancel a shared cohort digest while another member remains eligible; it may cancel an empty cohort's unsent digest with the closed `cohort_empty` reason. Each authenticated dashboard defer operation SHALL use that same lock order to append exactly one successor presentation generation without creating another action or logical action key.

ID: REQ-approval-delivery-intent-recovery-005
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Decision wins before send start
- **WHEN** an authenticated approval/rejection or canonical expiry transaction commits before a worker's handoff-start transaction
- **THEN** the terminal action transition and presentation cancellation commit together
- **AND** the worker cannot start a provider call or revive the cancelled presentation

#### Scenario: Send start wins the unavoidable race
- **WHEN** a worker commits its fenced handoff-start marker before a later decision or expiry transaction
- **THEN** the later domain transition cancels future recovery without changing the already-started action attempt
- **AND** a late provider result is append-only evidence and never changes the action or makes the presentation sendable again

#### Scenario: Defer replaces the current presentation generation atomically
- **WHEN** an authenticated dashboard actor defers a still-pending action for a valid bounded hour value
- **THEN** the transaction extends the action expiry, supersedes any pre-handoff current generation, and creates generation `g + 1` with `not_before = now + hours`
- **AND** it retains the same logical action key, does not use `deferred_notifications`, and emits no provider call before the new generation is due

#### Scenario: Defer races a handoff start
- **WHEN** a worker and authenticated defer contend for a current presentation
- **THEN** defer-first prevents that generation's provider call, while handoff-start-first leaves its result append-only historical evidence and still schedules that successful defer's generation `g + 1`
- **AND** the worker cannot create, re-run, or advance a generation itself

#### Scenario: Defer marks a cohort member ineligible without cancelling its cohort
- **WHEN** an authenticated dashboard actor defers a still-pending fourth `cohort_anchor` or later `collapsed` cohort member before the cohort digest begins handoff
- **THEN** the same transaction marks that membership ineligible for the unstarted digest and appends its direct action successor for `now + hours`
- **AND** it leaves the shared digest sendable for other eligible members and never routes the deferred successor through generic notification controls

### Requirement: RFC 0021 policy preservation
The system SHALL preserve RFC 0021 one-logical-action-key, exact quiet-hours admission, no re-gate behavior, control-plane budget exemption, authenticated-defer re-presentation, and first-three/single-cohort-digest/later-collapse burst semantics without using the generic deferred-notification queue.

ID: REQ-approval-delivery-intent-recovery-006
Source: RFC-0021
Scope: v1-mandatory

#### Scenario: Quiet-hours presentation releases at its admitted time
- **WHEN** an action parks within an end-exclusive RFC 0021 quiet-hours interval
- **THEN** its sendable presentation stores the exact configured interval end as `not_before`
- **AND** a later policy change does not re-gate or recalculate that already-admitted presentation

#### Scenario: Burst policy creates durable collapsed evidence
- **WHEN** more than three actions park concurrently within one schema's ten-minute burst window
- **THEN** the first three have `single` action presentations, one durable cohort owns the digest presentation, and remaining actions are terminal `collapsed` members of that cohort
- **AND** terminalizing the fourth action before send marks only that membership ineligible and cannot strand a still-pending fifth-or-later member: the cohort retains its current digest or, after an empty pre-handoff cohort receives a later member, creates a successor only when no unsent generation remains
- **AND** all pending actions retain one unique action key and none are written to `deferred_notifications`

#### Scenario: Fourth representative becomes terminal before a later member parks
- **WHEN** the fourth action's cohort has no handoff-started/delivered digest, that fourth action becomes cancelled or expired, and a fifth action parks in the same burst window
- **THEN** the fifth action joins the durable cohort rather than depending on the terminal fourth action's root
- **AND** the admission transaction creates at most one current unsent cohort digest successor and never a second provider handoff for an already handoff-started/delivered cohort generation

### Requirement: Retention and stuck-presentation observability
The system SHALL retain nonterminal and ambiguous presentations while their action or still-open cohort remains pending, append safe attempt and terminal-summary evidence, and expose derived stuck/ambiguous backlog truth without deleting, replaying, or mutating historical parked actions.

ID: REQ-approval-delivery-intent-recovery-007
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Retention does not erase unresolved delivery work
- **WHEN** routine approval retention encounters a pending action or open cohort with a retrying, leased, handoff-started, or ambiguous presentation
- **THEN** it retains the required root, presentation/cohort, and attempts rather than deleting or terminalizing them
- **AND** terminal cleanup follows the action/cohort retention order and preserves the safe immutable approval-event summary

#### Scenario: Operators can see a stuck recovery path safely
- **WHEN** a due retry exceeds the recovery SLO, a lease is expired, or a presentation is ambiguous
- **THEN** metrics and the approval read model report state/count/oldest-age and safe reason information
- **AND** they omit action arguments, recipient, callback material, message body, raw provider response, and raw exception text

### Requirement: Complete pending-action producer coverage
The system SHALL route every production `pending_actions(status='pending')` admission through the shared atomic helper and SHALL mechanically reject any new direct pending insert outside that helper while allowing deliberately auto-approved inserts.

ID: REQ-approval-delivery-intent-recovery-008
Source: RFC-0023
Scope: v1-mandatory

#### Scenario: Existing producers share the one admission path
- **WHEN** the gate, recipient guards, core notify guard, calendar overlap, connector disconnect, relationship assertion, and relationship curation producers park an action
- **THEN** each call uses the same atomic pending-action-plus-intent admission path
- **AND** no producer creates a pending action whose action key lacks an intent

#### Scenario: A future direct pending insert is caught
- **WHEN** source code introduces a direct `INSERT` of a `pending_actions` row with status `pending` outside the approved helper
- **THEN** the producer-coverage contract test fails
- **AND** direct inserts whose status is auto-approved remain explicitly excluded from the notification requirement
