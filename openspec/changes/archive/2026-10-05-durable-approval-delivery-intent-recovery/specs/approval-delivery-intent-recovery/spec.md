## ADDED Requirements

### Requirement: Atomic pending-action delivery admission
When durable admission is enabled under the additive rollout contract, the system SHALL commit every new pending approval action and exactly one schema-local durable delivery-intent root in the same PostgreSQL transaction, with a non-null unique foreign-keyed `action_id`, validated `origin_butler`, and immutable unique `action_key = approval:<owning-schema>:<action_id>`. The admission SHALL create an initial direct-action presentation for `single`; an initial durable cohort, fourth-member membership, and cohort-owned digest presentation for `cohort_anchor`; or a terminal non-sendable collapsed action presentation plus cohort membership for `collapsed`; a failed admission SHALL commit neither record. Each direct presentation SHALL have a monotonic bounded generation and deterministic `<action_key>:p:<generation>` key; a digest SHALL derive its presentation key from its independent cohort key. Admission SHALL snapshot its initial `not_before` and `next_attempt_at` from database time and the applicable admission decision.

ID: REQ-approval-delivery-intent-recovery-001
Source: RFC 0023 §Decision (atomic schema-local intent and fenced presentations)
Scope: v1-mandatory

#### Scenario: Transaction failure rolls back action and delivery state
- **WHEN** an insertion or validation failure occurs after a pending action is staged but before its delivery intent commits
- **THEN** the owning schema contains neither the new pending action nor a root/presentation/cohort-membership/attempt row for its action key
- **AND** a successful duplicate semantic-key admission returns the pre-existing action/intent pair without a second key

#### Scenario: Runtime availability does not remove delivery state
- **WHEN** a valid producer parks an action with durable admission enabled while its notification worker or Switchboard client is unavailable
- **THEN** the action and its required direct presentation, cohort digest, or collapsed membership still commit atomically in a sendable or policy-terminal state
- **AND** no caller may omit that delivery state by passing a null live runtime

#### Scenario: Invalid origin cannot leave an orphan action
- **WHEN** enabled admission receives a missing or invalid origin or cannot create its unique action-intent relationship
- **THEN** it rejects the admission before committing the action
- **AND** concurrent producer deduplication returns the existing action/intent pair and never a second intent or notification key

### Requirement: Closed presentation state, reason, and ownership boundary
The system SHALL keep a stable action-intent root classified as `single`, `cohort_anchor`, or `collapsed`, and persist each direct-action or cohort-owned delivery presentation only as `ready`, `claimed`, `handoff_started`, `retry_wait`, `delivered`, `collapsed`, `cancelled`, `superseded`, or `ambiguous`. Persisted reasons SHALL use the versioned closed vocabulary `quiet_hours`, `owner_recipient_unavailable`, `callback_secret_unavailable`, `transport_unavailable`, `provider_preflight_failed`, `provider_outcome_unknown`, `action_approved`, `action_rejected`, `action_expired`, `action_abandoned`, `defer_rescheduled`, and `cohort_empty`; new codes require an explicit vocabulary revision and unknown codes SHALL be rejected. Durable recovery state, recovery API projections, audit errors, and metric labels SHALL retain only bounded safe metadata, without recipient, callback token/secret, raw provider payload, raw exception, rendered message, or action arguments. `stuck` SHALL be derived rather than a writable terminal state. The worker SHALL be deterministic notification infrastructure with no model invocation or domain-action mutation authority; `pending_actions.status` SHALL remain the sole decision/execution gate.

ID: REQ-approval-delivery-intent-recovery-002
Source: RFC 0023 §Decision (notification-only authority and safe evidence)
Scope: v1-mandatory

#### Scenario: Unknown state or reason is rejected
- **WHEN** a writer attempts to create or transition a presentation with a state or reason outside the closed vocabulary
- **THEN** schema and repository validation reject the write
- **AND** API, metric, and log projections expose only the safe normalized value

#### Scenario: Worker has notification-only authority
- **WHEN** the approval-delivery worker processes an associated pending action
- **THEN** it may read that action/cohort and write only its local presentation, attempt, and safe audit-observability records
- **AND** it has no path to approve, reject, expire, defer, execute, edit, or otherwise mutate `pending_actions`

#### Scenario: Recovery dependencies preserve schema ownership
- **WHEN** a daemon constructs its recovery worker
- **THEN** the worker receives only its schema-local pool and recovery repository, deterministic renderer, current owner-recipient/callback-secret resolvers, and narrow authenticated notify/reconciliation client
- **AND** it receives no peer pool, DSN, cross-schema grant, approval operations/executor, entity/fact service, generic scheduler writer, or dashboard defer capability

### Requirement: Fenced claim and at-least-once recovery
The system SHALL claim due sendable presentations with `FOR UPDATE SKIP LOCKED`, an incremented monotonic fence, unpredictable claim token, finite lease, and compare-and-set writes matching presentation identity, generation, token, and fence. Its daemon-owned loop SHALL start only for active Approvals schemas with the server-held worker flag enabled and poll idle work every 5 seconds with a 30-second claim lease. Safe retries SHALL use database-time scheduling, start at 15 seconds, double through six bounded exponent steps, add deterministic 0–20% jitter from presentation key and attempt number, and cap at 15 minutes. Recovery SHALL retry only safely unstarted or provider-idempotent work until the presentation is superseded/cancelled, the action becomes terminal/expired, or the outcome is ambiguous. An expired unstarted claim MAY be reclaimed; an expired `handoff_started` claim SHALL be reconciliation-only, never permission for a blind resend.

ID: REQ-approval-delivery-intent-recovery-003
Source: RFC 0023 §Decision (leased recovery and bounded safe retry)
Scope: v1-mandatory

#### Scenario: A stale worker cannot write after lease succession
- **WHEN** one worker's lease expires and a second worker obtains a higher fence for the same presentation generation
- **THEN** every renewal or transition from the stale token/fence is rejected
- **AND** only the successor may make a new pre-handoff transition

#### Scenario: Restart recovers an unstarted claim
- **WHEN** a process crashes after claiming a presentation but before recording provider handoff start
- **THEN** a later worker reclaims the expired claim and retries the same immutable presentation key after its scheduled backoff
- **AND** no duplicate action, root, or presentation is created

#### Scenario: Safe pre-handoff failure keeps the same bounded recovery schedule
- **WHEN** current owner resolution, callback-secret lookup, or transport fails before a provider effect starts
- **THEN** the worker records a closed safe reason and retries the same presentation after its deterministic database-time backoff
- **AND** it never substitutes the generic scheduler cadence, terminalizes the action to clear its queue, or claims exactly-once external delivery

### Requirement: Idempotent, authenticated, and ambiguous provider handoff
The system SHALL persist a fenced pre-provider handoff marker and require the actual Messenger boundary to classify an immutable presentation key as `confirmed`, `safe_retry`, or `ambiguous`; it SHALL bind that key to a transport-authenticated issuer, owning schema, and approved presentation mode before ledger/provider work, and SHALL never blindly resend an uncertain post-start handoff.
An absent or null `recovery` member is ordinary notification traffic. Only a
non-null recovery value enters this authenticated handoff boundary, and a
malformed non-null value is rejected rather than downgraded to ordinary
delivery.

ID: REQ-approval-delivery-intent-recovery-004
Source: RFC 0023 §Decision (trusted Messenger handoff and honest uncertainty)
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

#### Scenario: Source attestation is independent of caller correlation
- **WHEN** a non-null recovery request reaches Switchboard or Messenger
- **THEN** it must be an `approval_request` with a valid action/cohort subject and generation-specific presentation, whose registered owning schema matches its transport-authenticated daemon issuer
- **AND** Switchboard derives trusted issuer/schema context and verifies a non-caller-serializable source-schema subject/presentation attestation before any handoff state or provider call
- **AND** Messenger accepts that context only from authenticated Switchboard and keys its narrowly wired ledger by `(issuer, owning_schema, presentation_key, presentation_mode)` without a peer-schema read grant
- **AND** origin text, key prefixes, recovery mode, and bare correlation keys never supply authority

#### Scenario: Ordinary and malformed recovery values remain distinct
- **WHEN** an ordinary caller omits `recovery` or provides a null value
- **THEN** ordinary notification semantics apply and new serializers omit the unset member
- **AND** any malformed non-null recovery value is instead rejected before generic logging, recovery persistence, or provider egress

#### Scenario: Current adapters cannot prove a post-start resend safe
- **WHEN** Telegram, email, or WhatsApp loses its provider result after the provider-start marker
- **THEN** the handoff remains `ambiguous` because their current Messenger adapters accept no presentation idempotency key and expose no `reconcile_approval_delivery` lookup
- **AND** an exception, explicit failure, malformed acceptance, timeout, or lost response after start never becomes a safe retry without proof
- **AND** future adapter capability changes require an updated capability inventory and behavior evidence before relaxing this classification

#### Scenario: Confirmed delivery reports acceptance rather than owner attention
- **WHEN** Messenger durably records provider acceptance or a duplicate-safe receipt for the trusted tuple
- **THEN** it returns `confirmed` with only a closed safe code and optional bounded opaque non-secret receipt reference, and the source presentation becomes `delivered`
- **AND** neither result claims that the owner read or acted on the notification

### Requirement: Decision, expiry, and defer presentation fencing
The system SHALL couple every transition out of `pending` to cancellation of that action's nonterminal presentations and an atomic update that marks its cohort membership ineligible in the same local transaction, using a fixed action-then-intent-presentation lock order and treating the committed handoff-start marker as the final cancellation-safe boundary. It SHALL not cancel a shared cohort digest while another member remains eligible; it may cancel an empty cohort's unsent digest with the closed `cohort_empty` reason. Each authenticated dashboard defer operation SHALL use that same lock order to append exactly one successor presentation generation without creating another action or logical action key.

ID: REQ-approval-delivery-intent-recovery-005
Source: RFC 0023 §Decision (terminal cancellation and dashboard-only defer)
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

#### Scenario: Every terminal path shares cancellation authority
- **WHEN** approve, reject, explicit expiry, stale-expiry sweep, or a future path transitions an action out of `pending`
- **THEN** it uses the shared action-then-intent-then-current-presentation/cohort-membership transaction to cancel that action's nonterminal presentations and mark only its membership ineligible
- **AND** a direct SQL pending-to-terminal writer outside that shared helper is prohibited and must be caught by the transition contract gate
- **AND** an expired action merely observed by the worker cancels only its presentation; the canonical expiry path alone changes the domain status

### Requirement: RFC 0021 policy preservation
The system SHALL preserve RFC 0021 one-logical-action-key, exact global Owner Attention Policy quiet-hours admission, no re-gate behavior, control-plane insight-budget exemption, authenticated-defer re-presentation, and per-schema ten-minute first-three/single-cohort-digest/later-collapse burst semantics. Edits, restarts, ordinary retries, and duplicate producer calls SHALL NOT append a new presentation; only authenticated dashboard defer appends an action successor. A cohort SHALL own its digest independently of the fourth action, maintain eligible membership after member decisions/expiry, and permit an empty pre-handoff cohort's replacement only when no unsent generation exists and no generation has reached handoff start or delivery. The digest SHALL link to the dashboard without weakening action expiry/decision semantics. The generic deferred-notification scheduler, quiet-hours wake recovery, budgets, claims, cancellation, manual retry/escalation/acknowledgement, and history SHALL NOT back these presentations.

ID: REQ-approval-delivery-intent-recovery-006
Source: RFC 0023 §Decision (preserved RFC 0021 policy); RFC 0021
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
The system SHALL retain nonterminal and ambiguous presentations while their action or still-open cohort remains pending, append safe attempt and immutable terminal-summary evidence, and expose derived stuck/ambiguous backlog truth without deleting, replaying, or mutating historical parked actions. Attempts SHALL remain append-only for the presentation lifetime and contain only generation, attempt number, fence, started/completed times, normalized outcome/reason, and bounded opaque provider reference. Terminal action/cohort summaries SHALL outlive mutable recovery rows under the established approval-event retention/provenance policy; foreign-key-aware cleanup SHALL follow action/window retention order and never silently terminalize unresolved work. The approvals API/dashboard SHALL expose only safe delivery state, mode/generation, reason, attempt count, next eligible time, and explicit stuck/ambiguous indicators, labeling legacy evidence without inferring “never notified” from a null/failed legacy push. Metrics and structured logs SHALL report per-schema safe state/reason counts, oldest-due age, lease-expiry/ambiguous counts, and scan/claim/handoff outcomes without sensitive or unbounded values.

ID: REQ-approval-delivery-intent-recovery-007
Source: RFC 0023 §Decision (safe projections and retained recovery evidence)
Scope: v1-mandatory

#### Scenario: Retention does not erase unresolved delivery work
- **WHEN** routine approval retention encounters a pending action or open cohort with a retrying, leased, handoff-started, or ambiguous presentation
- **THEN** it retains the required root, presentation/cohort, and attempts rather than deleting or terminalizing them
- **AND** terminal cleanup follows the action/cohort retention order and preserves the safe immutable approval-event summary

#### Scenario: Operators can see a stuck recovery path safely
- **WHEN** a due retry exceeds the recovery SLO, a lease is expired, or a presentation is ambiguous
- **THEN** metrics and the approval read model report state/count/oldest-age and safe reason information
- **AND** they omit action arguments, recipient, callback material, message body, raw provider response, and raw exception text

#### Scenario: Stuck status reflects due age and uncertainty
- **WHEN** `ready`/`retry_wait` work is more than 15 minutes overdue, a `claimed`/`handoff_started` lease expires, or a presentation is ambiguous
- **THEN** the service derives stuck status without adding a writable `stuck` state
- **AND** overdue age starts at the due time rather than presentation creation and no observability control approves, rejects, expires, executes, replays, or edits the action

### Requirement: Complete pending-action producer coverage
The system SHALL route every production `pending_actions(status='pending')` admission through the shared atomic helper and SHALL mechanically reject any new direct pending insert outside that helper while allowing deliberately auto-approved inserts.

ID: REQ-approval-delivery-intent-recovery-008
Source: RFC 0023 §Decision (one shared admission boundary)
Scope: v1-mandatory

#### Scenario: Existing producers share the one admission path
- **WHEN** the gate, recipient guards, core notify guard, calendar overlap, connector disconnect, relationship assertion, and relationship curation producers park an action
- **THEN** each call uses the same atomic pending-action-plus-intent admission path
- **AND** no producer creates a pending action whose action key lacks an intent

#### Scenario: A future direct pending insert is caught
- **WHEN** source code introduces a direct `INSERT` of a `pending_actions` row with status `pending` outside the approved helper
- **THEN** the producer-coverage contract test fails
- **AND** direct inserts whose status is auto-approved remain explicitly excluded from the notification requirement

### Requirement: Recovery isolation from generic controls and history
The recovery-only `notify.v1` branch SHALL bypass generic `log_notification()` persistence and MUST NOT call `_write_outbound_message_inbox()` and MUST NOT insert an outbound `switchboard.message_inbox` row, including a redacted substitute. All generic conversation/LLM-history readers SHALL be unable to retrieve recovery presentation data, including current or successor pipeline readers. Generic notification list/history/detail/read models, aggregate counts, acknowledgement, retry, escalation, and stored-envelope reconstruction SHALL exclude recovery-keyed records entirely. The narrow Messenger handoff ledger and safe approvals projection SHALL be the only permitted recovery records; rendered message, recipient-derived thread identity, callback material, and full envelopes SHALL remain egress-local. A future redacted non-history record requires a separately reviewed contract.

ID: REQ-approval-delivery-intent-recovery-009
Source: RFC 0023 §Decision (separate protected recovery records)
Scope: v1-mandatory

#### Scenario: Recovery creates no generic conversation record
- **WHEN** a trusted recovery presentation reaches confirmed, safe-retry, or ambiguous handoff
- **THEN** no generic notification or outbound conversation row is persisted for it
- **AND** generic conversation-history and LLM-history readers cannot retrieve its rendered message, recipient-derived thread identity, or callback material

#### Scenario: Planted recovery evidence cannot become a generic replay control
- **WHEN** a generic list, read, count, acknowledge, retry, escalation, or envelope-reconstruction path encounters a recovery-keyed record
- **THEN** it excludes the record or returns an uninformative no-control result before envelope reconstruction or delivery
- **AND** only the fenced authenticated recovery path may reconcile the presentation

### Requirement: Prepared pending actions remain durable and silent
Prepared relationship reach-out and travel connection-risk doors SHALL use the shared pending-action admission boundary. When durable admission is enabled, `origin='prepared'` SHALL create one intent classified `collapsed` and one standalone terminal non-sendable collapsed action presentation, with no ordinary burst membership/count effect, due work, provider work, or dashboard-defer activation. Default-off admission SHALL preserve the established prepared pending row only.

ID: REQ-approval-delivery-intent-recovery-010
Source: RFC 0023 §Decision (prepared-action representation)
Scope: v1-mandatory

#### Scenario: Enabled prepared admission does not become a notification
- **WHEN** either prepared producer parks a digest-only action with durable admission enabled
- **THEN** its action, intent, and standalone collapsed presentation commit atomically
- **AND** it cannot join/influence an ordinary burst cohort, become due, invoke a provider, create a legacy emission, or gain a defer successor

#### Scenario: Default-off prepared admission preserves its existing silent row
- **WHEN** durable admission is disabled or unavailable
- **THEN** the shared helper creates only the prepared pending action
- **AND** deduplication still returns the existing active action without activating notification work

### Requirement: Additive rollout and non-destructive rollback
Durable admission and worker startup SHALL remain server-held, per-schema, default-off controls; absent/invalid configuration SHALL fail closed. Compatible additive schemas, read paths, migration-shape and role-isolation verification, safe metrics, real-PostgreSQL fault/concurrency evidence, an owner-authorized synthetic staging/canary drill, and dashboard truth review SHALL precede gradual schema-by-schema activation. Cutover SHALL apply only to newly parked actions and SHALL NOT backfill or replay historical actions, legacy push emissions, generic deferred rows, or provider sends, dual-send legacy and durable delivery, or synthesize new legacy evidence. Binary rollback SHALL disable new admission first and retain all durable recovery data; active, handoff-started, or ambiguous work SHALL stay visible and resume only on a compatible recovery binary. Schema downgrade SHALL refuse while any root, presentation, cohort, attempt, handoff, or audit evidence exists; rollback SHALL NOT delete, replay, approve, or execute historical actions.

ID: REQ-approval-delivery-intent-recovery-011
Source: RFC 0023 §Decision (separate operational activation and additive rollback)
Scope: v1-mandatory

#### Scenario: Default-off admission cannot be enabled by producer input
- **WHEN** a producer parks an action with disabled, absent, or rejected-invalid server rollout configuration
- **THEN** it follows established pending-action admission without durable recovery rows, legacy dual writes, or provider calls
- **AND** caller tool arguments cannot enable admission or worker startup

#### Scenario: Source delivery does not authorize activation
- **WHEN** a compatible source release or specification archive lands before the required canary/authority evidence
- **THEN** new admission and worker controls remain disabled
- **AND** the unchecked operational drill and activation tasks remain separate from source completion

#### Scenario: Rollback retains unresolved recovery evidence
- **WHEN** an older binary is selected or schema downgrade is attempted while recovery or safe audit evidence remains
- **THEN** new admission is disabled before binary rollback, durable tables remain, and destructive schema downgrade is refused
- **AND** an older binary is not represented as a recovery substitute or permission for historical replay
