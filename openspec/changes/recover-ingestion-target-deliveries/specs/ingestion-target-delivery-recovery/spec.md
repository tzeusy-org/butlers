## ADDED Requirements

### Requirement: One deduplicated goal admission per reasoning envelope

In ISOLATED_CANDIDATE and SWITCHED_RELEASE, each accepted envelope submitted to admission SHALL reach at most one terminal admission decision, keyed by its durable source key: `admitted`, `joined`, or `recorded_without_goal`. Each attempt's decision and its admission receipt SHALL commit in the same transaction as any goal or goal-inbox row. A duplicate ingress SHALL return the committed terminal receipt rather than admit again. A retryable refusal SHALL be an immutable decision for its attempt only and SHALL NOT end admission for the source key. No per-target classifier, decomposition plan, fan-out or delivery intent SHALL precede or replace admission.

ID: REQ-ingestion-target-delivery-recovery-001
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0038 D1 and D5; generalist-lead-focused-delegation REQ-goal-orchestration-005; [Observed] roster/switchboard/migrations/047_goal_ledger.py, 051_record_only_admission_reason.py; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Decision and receipt commit together

- **WHEN** admission admits, joins, records without a goal, or refuses an envelope
- **THEN** the decision, its receipt and any goal or goal-inbox row SHALL commit atomically
- **AND** a failure before that commit SHALL leave no decision, receipt or goal

#### Scenario: A batch envelope is one admission

- **WHEN** one conversation-history batch carries signals for several domains
- **THEN** admission SHALL reach one terminal decision and at most one goal for the batch
- **AND** no per-domain segment identity or delivery intent SHALL be created

#### Scenario: Duplicate ingress keeps its terminal decision

- **WHEN** a duplicate envelope arrives after its source key's terminal decision committed
- **THEN** Switchboard SHALL return the original receipt and any goal identity
- **AND** it SHALL NOT admit a second goal or change the original outcome

#### Scenario: Deterministic ingestion needs no admission

- **WHEN** an envelope is withheld by a deterministic ingestion rule (skip, metadata-only, bulk backfill or sync) or bound to a domain's deterministic channel ingest on the service plane
- **THEN** it SHALL NOT be submitted to admission and no admission decision or receipt SHALL be created
- **AND** that outcome SHALL remain distinguishable from a failed admission

#### Scenario: An unsubscribed feed is recorded without a goal

- **WHEN** a feed envelope submitted to admission matches no declared subscription
- **THEN** admission SHALL commit one terminal `recorded_without_goal` decision, stored as result `refused` with reason `recorded_without_goal`
- **AND** no goal, goal-inbox row or wake SHALL be created, and a duplicate or replay SHALL return that decision rather than mint a goal

### Requirement: Stable admission receipt identity

Each admission receipt SHALL bind the durable source key, the submitting attempt identity, the decision identity and its outcome through the goal ledger's immutable attempt-to-decision binding. A repeated attempt SHALL receive its exact bound decision. A new attempt for a source key that has a terminal decision (`admitted`, `joined` or `recorded_without_goal`) SHALL be bound to that decision and receive the same receipt, including under concurrent submissions and after a lost response. Any other refusal SHALL bind only its own attempt, so a later attempt under the same source key may be evaluated again. The source-key, attempt, decision and outcome ledger SHALL be append-only and content-blind in v1: message bodies MAY be pruned, but receipts SHALL NOT be deleted or rewritten, and a duplicate SHALL be resolved against the ledger even after its inbox or buffer row has aged out. A future bounded-retention design requires a separate database-enforced non-readmission fence before any receipt may be pruned; mutable source timestamps, UUID age and tombstone absence are not such proof.

ID: REQ-ingestion-target-delivery-recovery-002
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0038 D5; generalist-lead-focused-delegation REQ-goal-orchestration-005; [Observed] roster/switchboard/migrations/047_goal_ledger.py, 050_admission_attempt_bindings.py; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Concurrent duplicate admission

- **WHEN** two submissions with the same source key reach admission concurrently
- **THEN** both SHALL receive the same terminal receipt, each attempt bound to that one decision
- **AND** at most one goal SHALL be admitted and at most one lead SHALL start

#### Scenario: Receipt survives response loss

- **WHEN** admission commits but the response is lost before the caller records it
- **THEN** a resubmission with the same attempt identity SHALL return that attempt's committed decision, and one with the same source key SHALL return the terminal receipt, without admitting again

#### Scenario: A retryable refusal does not fence the source key

- **WHEN** a source key's only decisions are refusals other than `recorded_without_goal`, such as `quota_exhausted` or `limits_unavailable`
- **THEN** a later attempt under the same source key SHALL be evaluated again and MAY be admitted
- **AND** each earlier refusal SHALL remain immutable and SHALL still be returned to its own attempt identity

#### Scenario: An attempt identity cannot be rebound

- **WHEN** a submission reuses an attempt identity already bound to a different source key
- **THEN** admission SHALL fail as unavailable and SHALL NOT return that attempt's decision as a receipt for the new source
- **AND** the existing binding, decision and receipt SHALL remain unchanged

#### Scenario: Retention cannot reopen admission

- **WHEN** an inbox or buffer retention job runs, a migration is replayed, or the service rolls back after admitting a source key
- **THEN** the key, attempt bindings, decision and receipt SHALL remain in the ledger and cannot be deleted or rewritten by that operation
- **AND** a same-key duplicate SHALL find the retained receipt, and no missing source row, changed source timestamp or owner historical approval SHALL authorize a second admission for that key

### Requirement: Closed admission state and ownership

Switchboard SHALL represent each in-scope envelope's admission as `buffered`, `admitted`, `joined`, `recorded_without_goal`, `refused`, or `admission_failed`, with content-blind reason codes. `admitted`, `joined` and `recorded_without_goal` SHALL be terminal for the source key; `refused` SHALL denote the latest refusal other than `recorded_without_goal` and SHALL NOT be terminal for the source key. These states SHALL map onto the goal ledger without new storage: `admitted` and `joined` to the matching decision result, `recorded_without_goal` and `refused` to result `refused` distinguished by reason. `admitted` and `joined` SHALL mean admission only; goal execution, effects and their receipts SHALL remain owned by the goal ledger.

ID: REQ-ingestion-target-delivery-recovery-003
Source: RFC 0038 D5, D11 and D12; generalist-lead-focused-delegation REQ-goal-orchestration-005; [Observed] roster/switchboard/migrations/047_goal_ledger.py, 051_record_only_admission_reason.py; design.md Decision 3
Scope: v1-mandatory

#### Scenario: Admitted is terminal for ingress

- **WHEN** admission returns a receipt with an admitted or joined goal
- **THEN** ingress SHALL NOT resubmit the envelope because the goal later waits, fails or is stopped
- **AND** the owner-visible projection SHALL distinguish admission from goal completion

#### Scenario: A join attaches to the existing goal

- **WHEN** admission attaches a verified owner reply or input to an existing goal
- **THEN** the envelope's state SHALL be `joined` with that goal's identity and its goal-inbox entry
- **AND** no new goal SHALL be admitted and no new-goal quota slot SHALL be consumed

#### Scenario: Effects are not replayed by ingress

- **WHEN** an admitted goal has committed or unknown effects
- **THEN** no ingress retry, recovery request or historical admission SHALL re-dispatch those effects
- **AND** their settlement SHALL follow the goal ledger's effect rules

### Requirement: Bounded safe admission retry and legacy ambiguity

Buffer-to-admission retry SHALL be bounded by attempt and age budgets with jittered backoff, and SHALL resolve an interrupted attempt by reading the decision bound to its attempt identity, then any terminal decision for its source key, before retrying. A retryable refusal MAY be retried under the same source key with a new attempt identity, no earlier than its recorded `retry_after` when present. Exhausted budgets SHALL leave the envelope visible as `admission_failed`. Legacy route work draining before the switch SHALL keep the `confirmed | rejected | uncertain | not_attempted` transport taxonomy: rejection, uncertainty and policy refusal SHALL NOT trigger automatic resend.

ID: REQ-ingestion-target-delivery-recovery-004
Source: RFC 0038 D5 and Hard cutover; generalist-lead-focused-delegation REQ-goal-orchestration-005; design.md Decisions 3 and 4; [Observed] roster/switchboard/tools/routing/transport.py
Scope: v1-mandatory

#### Scenario: Interrupted admission resolves by source key

- **WHEN** an admission attempt is interrupted and the buffer retries it
- **THEN** a decision committed for that attempt, or a terminal decision for its source key, SHALL be returned unchanged
- **AND** only the absence of both SHALL permit another attempt with the original envelope

#### Scenario: Transient quota refusal is retried

- **WHEN** admission refuses a buffered envelope with `quota_exhausted` and a `retry_after`
- **THEN** the buffer MAY submit a new attempt under the same source key after `retry_after` and within its budgets
- **AND** the earlier refusal SHALL remain immutable, and a later admission SHALL become the source key's terminal decision

#### Scenario: Control-plane restart admits once

- **WHEN** the control plane restarts with accepted envelopes in the durable buffer
- **THEN** each SHALL be admitted exactly once after the restart, and none SHALL be lost or classified

#### Scenario: Retry budget expires

- **WHEN** the configured attempt or age budget expires without a decision
- **THEN** the envelope SHALL be `admission_failed` with a bounded reason
- **AND** it SHALL remain visible for owner review rather than disappear from the event ledger

#### Scenario: Uncertain legacy handoff is not resent

- **WHEN** draining legacy route work returns `uncertain`, `rejected` or a policy refusal
- **THEN** that legacy work SHALL NOT be sent again automatically
- **AND** it SHALL be recorded as unresolved residue unless an affirmative legacy receipt proves acceptance

### Requirement: Truthful admission and legacy projection

Switchboard SHALL expose each event's admission state, or for a pre-switch event its legacy target evidence, and a derived event summary. An event SHALL NOT be shown as complete while its admission is buffered, failed or unavailable, or while any legacy target remains unresolved. The coarse `public.ingestion_events.status = 'ingested'` SHALL NOT by itself assert admission or target acceptance.

ID: REQ-ingestion-target-delivery-recovery-005
Source: RFC 0038 D5 and D17; design.md Decisions 3 and 4
Scope: v1-mandatory

#### Scenario: Summary follows the receipt

- **WHEN** an event has an admission receipt
- **THEN** the event MAY report its admission outcome while showing goal progress separately
- **AND** an event with `buffered` or `admission_failed` state SHALL NOT report completion

#### Scenario: Partial legacy fan-out stays non-complete

- **WHEN** a pre-switch event has one accepted legacy target and another unresolved
- **THEN** its projection SHALL retain the accepted receipt, show the unresolved target, and report a non-complete summary

#### Scenario: Projection source unavailable

- **WHEN** the admission ledger or legacy evidence cannot be read
- **THEN** the ingestion API SHALL report evidence unavailable
- **AND** it SHALL NOT synthesize an empty or successful summary

### Requirement: Failed-event recovery is executable and honest

An owner recovery request for a non-dashboard ingestion event SHALL return the original admission receipt, a single admission when retained evidence proves no decision was made, or a specific non-success result. In KEY_ABSENT_STAGE it SHALL return a specific non-success result. The `failed`, `ingested`, `replay_failed`, and `replay_pending` branches SHALL never reset `message_inbox` for reclassification, re-route a legacy target, or report pending or clear failure solely by changing an ingestion status column.

ID: REQ-ingestion-target-delivery-recovery-006
Source: [Observed] src/butlers/core/ingestion_events.py:1107; RFC 0038 D5 and Hard cutover; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Recovery returns the existing decision

- **WHEN** after the switch the owner requests recovery for an event whose source key has a terminal admission decision
- **THEN** the request SHALL return that receipt and goal identity
- **AND** it SHALL NOT admit a second goal

#### Scenario: Recovery admits once when no decision exists

- **WHEN** after the switch the owner requests recovery for a `failed` or `replay_failed` event whose retained envelope has no terminal admission decision
- **THEN** the request SHALL submit the original envelope identity to admission once and return its receipt
- **AND** origin and exposure SHALL come from stored evidence, never upgrading authorship or reducing exposure

#### Scenario: Frozen lane refuses rather than relabels

- **WHEN** in KEY_ABSENT_STAGE the owner requests recovery of a `failed`, `ingested` or `replay_failed` event
- **THEN** the request SHALL return a specific non-success result with a content-blind reason
- **AND** it SHALL NOT relabel the event `ingested`, reset `message_inbox` to `accepted`, or classify it again

#### Scenario: Legacy failed row has no proven evidence

- **WHEN** a failed pre-switch event lacks a retained envelope or trustworthy evidence
- **THEN** the request SHALL return a non-success explanation and leave the event failed
- **AND** it SHALL NOT invoke connector ingress replay as a substitute

#### Scenario: Already pending request is idempotent

- **WHEN** an event is `replay_pending` and its recovery work is already durably queued
- **THEN** another recovery request SHALL return the existing pending identity without adding work

#### Scenario: Orphan pending marker is not proof of queued work

- **WHEN** a legacy `replay_pending` event has no corresponding durable queued work
- **THEN** its API projection and recovery response SHALL report unavailable or conflicting evidence rather than pending success
- **AND** no admission, route or status-only success transition SHALL result

#### Scenario: Other source statuses remain non-replayable

- **WHEN** a `public.ingestion_events` row has any status outside the governed recovery branches
- **THEN** the recovery request SHALL return a non-success status conflict without mutating the event, inbox or admission ledger

#### Scenario: Connector filtered events retain their replay contract

- **WHEN** the requested ID belongs to `connectors.filtered_events` rather than `public.ingestion_events`
- **THEN** the connector's existing server-derived replay-safe policy and drain transition SHALL govern it independently
- **AND** recovery SHALL NOT claim that connector row or rewind its provider cursor

### Requirement: Exact owner-gated historical late admission

Historical recovery SHALL begin with a content-blind dry run identifying only retained events that have no admission receipt and either no legacy route or independently proven no-effect legacy outcome. Legacy acceptance evidence SHALL come from receipts retained before the switch, not cross-schema SELECT; missing, conflicting or unsupported evidence SHALL exclude the event as unprovable. No historical event SHALL be admitted until the owner approves the exact source-key set and bounded age/channel policy in an immutable server-side owner-authenticated approval record with preview evidence version, canonical set/policy digest, expiry, and revocation. The admission operation SHALL accept only that record's opaque ID, SHALL derive its exact keys and policy from server storage, and SHALL refuse before approval or after expiry or revocation. Consumption SHALL be atomic with one deduplicated admission per eligible key and idempotent on repeats. A Beads decision or owner session alone SHALL NOT authorize an arbitrary set.

ID: REQ-ingestion-target-delivery-recovery-007
Source: heart-and-soul/vision.md Rule 1; RFC 0038 D5 and D6; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Dry run inventories recoverable evidence

- **WHEN** an operator runs a historical eligibility preview
- **THEN** it SHALL group counts by time bucket, channel and safe failure class and identify the exact eligible source keys for private owner review
- **AND** it SHALL exclude missing envelopes, admitted events, legacy-accepted events, pruned rows, canonical `rejected`, `uncertain` legacy attempts, and rows lacking independent proof of no effect; absence of a receipt alone SHALL NOT prove eligibility

#### Scenario: Exact approval admits only selected work

- **WHEN** the owner approves a bounded age and channel policy and an exact set of eligible source keys
- **THEN** the approval record SHALL bind that set, policy, preview version, digest and expiry, and only its opaque ID may be consumed after rechecking current evidence
- **AND** changed, missing or newly admitted keys SHALL remain unadmitted and be reported as exclusions

#### Scenario: Late admission keeps stored origin and exposure

- **WHEN** an approved historical key is admitted
- **THEN** its origin and exposure SHALL come from the stored envelope evidence
- **AND** third-party material SHALL stay third-party and exposed

#### Scenario: Approval cannot be forged or broadened by an operator

- **WHEN** an admission request supplies no approval, a revoked or expired approval, a changed set or policy, or only a Beads signoff
- **THEN** no historical admission SHALL occur and no generic owner-authenticated replay path SHALL be opened

#### Scenario: Approval consumption is idempotent

- **WHEN** an approved set is admitted and the same approval ID is submitted again after a timeout or retry
- **THEN** the server SHALL return the first consumed result without admitting again or widening the set

#### Scenario: No approval means no historical admission

- **WHEN** a historical dry run completes without owner approval
- **THEN** no historical event SHALL be admitted or routed
- **AND** no provider cursor, connector replay queue or source event SHALL be rewound or reclassified

### Requirement: Content-blind recovery evidence

Admission receipts, legacy residue, APIs, logs, metrics and historical previews SHALL expose only fixed state and reason vocabularies plus bounded identifiers needed for correlation. They SHALL NOT publish message content, raw exception text, sender identity, provider payload or credentials through recovery evidence.

ID: REQ-ingestion-target-delivery-recovery-008
Source: heart-and-soul/vision.md Rule 1; RFC 0038 D17; design.md Decision 7
Scope: v1-mandatory

#### Scenario: Failure evidence is projected

- **WHEN** an admission fails, legacy work is left as residue, or a dry run excludes a historical event
- **THEN** the owner-facing and telemetry projection SHALL carry only a safe normalized reason and bounded identifiers
- **AND** raw messages, payloads and sender identity SHALL remain absent

#### Scenario: Invalid state or reason is rejected

- **WHEN** a writer supplies an admission state, transport outcome or reason outside the closed vocabulary
- **THEN** persistence SHALL reject it without converting it into a success or retryable state
