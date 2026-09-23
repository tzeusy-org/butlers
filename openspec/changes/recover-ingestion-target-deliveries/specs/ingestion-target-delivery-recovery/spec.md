## ADDED Requirements

### Requirement: Durable classified target plan before delivery

For non-dashboard ingestion-to-domain `route.execute`, Switchboard SHALL durably commit the completed classification or decomposition plan and one immutable intent for each target segment before calling any target. A duplicate ingress SHALL reuse the committed plan and its intent identities rather than classify or decompose again.

ID: REQ-ingestion-target-delivery-recovery-001
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0003 §Pre-Classification Triage Pipeline; design.md Decision 1
Scope: v1-mandatory

#### Scenario: Classification and intents commit before dispatch

- **WHEN** classification selects one or more ordinary domain `route.execute` targets
- **THEN** the already accepted source event's completed classification result, immutable target plan, and every per-target delivery intent SHALL commit atomically before the first target call
- **AND** a failure before that commit SHALL make no target call and SHALL leave no partial plan or intent

#### Scenario: Decomposition preserves distinct segments

- **WHEN** one conversation produces two concepts for the same target butler
- **THEN** the committed plan SHALL give the concepts distinct stable segment identities and intents
- **AND** retrying either intent SHALL preserve its original conceptual payload and segment identity

#### Scenario: Duplicate ingress keeps its first decision

- **WHEN** a duplicate event arrives after its target plan committed
- **THEN** Switchboard SHALL return the original ingestion identity and resume the existing intents
- **AND** it SHALL NOT run a new classifier or silently change the target set

#### Scenario: No target means no delivery intent

- **WHEN** the governed triage outcome is skip, metadata-only, empty decomposition, or another decision with no ordinary domain `route.execute` target
- **THEN** no target-delivery intent SHALL be created
- **AND** that outcome SHALL remain distinguishable from a failed attempt to deliver a selected target

### Requirement: Stable atomic target acceptance

Each intent SHALL carry a stable `(ingestion_event_id, target_butler, segment_id)` delivery identity and canonical immutable payload digest. The target SHALL atomically associate that identity, target binding, and digest with one accepted route-inbox row and return the same acceptance receipt on matching duplicate calls, including concurrent calls and recovery after a lost response.

ID: REQ-ingestion-target-delivery-recovery-002
Source: heart-and-soul/vision.md Rules 3 and 4; RFC 0003 §route.execute Envelope; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Concurrent duplicate acceptance

- **WHEN** two calls for the same delivery identity reach the target concurrently
- **THEN** both SHALL receive the same accepted receipt
- **AND** the target SHALL create one route-inbox row and at most one downstream session for that identity

#### Scenario: Distinct concepts remain distinct work

- **WHEN** the same event routes two segments to one target
- **THEN** each segment SHALL have its own acceptance receipt and route-inbox row
- **AND** acceptance of one segment SHALL NOT deduplicate the other

#### Scenario: Receipt survives response loss

- **WHEN** the target commits acceptance but the response is lost before Switchboard records it
- **THEN** a lookup carrying the same delivery identity, receiving target, and canonical immutable payload digest SHALL return the committed receipt without creating another row or session

#### Scenario: Receipt lookup rejects changed work

- **WHEN** a receipt lookup reuses an accepted identity with a different receiving target or canonical immutable payload digest
- **THEN** the target SHALL return a conflict and SHALL NOT return the old receipt as proof that the changed work was accepted
- **AND** the original accepted row, digest, and receipt SHALL remain unchanged

#### Scenario: Same key with different payload is refused

- **WHEN** a second call reuses an accepted delivery identity with a different canonical payload or target binding
- **THEN** the target SHALL return a conflict without creating another inbox row or returning the earlier receipt as acceptance of the changed work
- **AND** the original receipt and payload SHALL remain unchanged

### Requirement: Closed delivery state and ownership

Switchboard SHALL represent each intent as `pending`, `attempting`, `accepted`, `retry_wait`, `ambiguous`, or `terminal_failed`, with fenced attempts and content-blind reason codes. `accepted` SHALL mean target acceptance only; subsequent target processing and crash recovery SHALL remain owned by that target route inbox.

ID: REQ-ingestion-target-delivery-recovery-003
Source: RFC 0003 §route.execute Envelope; design.md Decision 3
Scope: v1-mandatory

#### Scenario: Worker succession is fenced

- **WHEN** a stale worker writes after a successor has claimed an intent
- **THEN** its transition SHALL be rejected without changing the successor's state or receipt

#### Scenario: Accepted is terminal for Switchboard

- **WHEN** a target returns a valid stable acceptance receipt
- **THEN** Switchboard SHALL mark that intent `accepted` and SHALL NOT resend it because a later target session is slow or fails
- **AND** the owner-visible projection SHALL distinguish target acceptance from session completion

#### Scenario: Crash during an attempt preserves uncertainty

- **WHEN** a worker stops after target transport may have begun and before it records a confirmed receipt
- **THEN** recovery SHALL query the same acceptance identity with the original target and canonical immutable payload digest and SHALL keep the intent `ambiguous` unless a matching receipt is found
- **AND** it SHALL NOT infer `not_attempted` from the worker lease expiring

### Requirement: Bounded safe retry and ambiguity resolution

Switchboard SHALL use the existing `confirmed | rejected | uncertain | not_attempted` route transport taxonomy. It SHALL retry only transient outcomes proven `not_attempted` before target acceptance, with a bounded attempt and age budget plus backoff; policy refusal, rejection, uncertainty, and exhausted budgets SHALL not trigger automatic resend.

ID: REQ-ingestion-target-delivery-recovery-004
Source: design.md Decision 3; [Observed] roster/switchboard/tools/routing/transport.py
Scope: v1-mandatory

#### Scenario: Target temporarily unavailable before any call

- **WHEN** a target is unreachable with canonical `not_attempted` evidence and the retry budget remains
- **THEN** the same intent SHALL enter `retry_wait` and retry after bounded backoff with its original identity and payload
- **AND** renewed target availability SHALL resume delivery without reclassification or source replay

#### Scenario: Policy denial and rejection stop retries

- **WHEN** route policy refuses the target or a target explicitly rejects the call
- **THEN** the intent SHALL enter `terminal_failed` with a safe reason
- **AND** the system SHALL NOT treat the transport envelope's generic retryable field as authority to override policy or rejection

#### Scenario: Unknown handoff is not an automatic retry

- **WHEN** transport is `uncertain` or an attempt loses its worker after transport may have begun
- **THEN** the intent SHALL remain `ambiguous` and SHALL NOT be sent again automatically
- **AND** only an affirmative same-identity, same-target, same-payload-digest receipt SHALL promote it to `accepted`; a missing or conflicting receipt SHALL NOT prove non-acceptance

#### Scenario: Retry budget expires

- **WHEN** the configured attempt or age budget expires without acceptance
- **THEN** the intent SHALL enter `terminal_failed` with a bounded reason
- **AND** it SHALL remain visible for owner review rather than disappear from the event ledger

### Requirement: Partial fan-out and truthful event projection

Switchboard SHALL recover each target intent independently and expose per-target state and a derived event-level delivery summary. A successful target SHALL never be re-sent because another target failed; an event SHALL not be shown as fully delivered while any selected target remains waiting, ambiguous, or terminally failed. The coarse `public.ingestion_events.status = 'ingested'` records source acceptance or processing and SHALL NOT by itself assert universal target acceptance.

ID: REQ-ingestion-target-delivery-recovery-005
Source: RFC 0003 §Conversation-History Decomposition and Fan-Out; design.md Decision 4
Scope: v1-mandatory

#### Scenario: One fan-out target fails

- **WHEN** target A has accepted its segment and target B has a proven pre-accept transient failure
- **THEN** only B's intent SHALL retry
- **AND** event and target projections SHALL retain A's accepted receipt and show B as waiting

#### Scenario: Summary follows intent state

- **WHEN** every selected target intent has an accepted receipt
- **THEN** the event MAY report delivery accepted, while still showing downstream sessions separately
- **AND** an event with any `ambiguous` or `terminal_failed` intent SHALL NOT report complete delivery

#### Scenario: Projection source unavailable

- **WHEN** the intent or receipt store cannot be read
- **THEN** the ingestion API SHALL report delivery availability as unavailable
- **AND** it SHALL NOT synthesize an empty or all-accepted target set

#### Scenario: Source accepted while target delivery is unresolved

- **WHEN** an event has `public.ingestion_events.status = 'ingested'` and one selected target has no acceptance receipt
- **THEN** its source status SHALL remain distinguishable from the separate non-complete target-delivery summary
- **AND** no API or UI SHALL infer full target acceptance from the coarse source status alone

### Requirement: Failed-event recovery is executable and honest

An owner recovery request for a non-dashboard ingestion event SHALL use its immutable classified plan and existing unaccepted target intents only when policy and evidence permit, or return a specific non-success result. The `failed`, `ingested`, `replay_failed`, and `replay_pending` source-status branches SHALL never reset `message_inbox` for reclassification or resend an accepted target. A recovery transition SHALL never report pending or clear failure solely by changing an ingestion status column.

ID: REQ-ingestion-target-delivery-recovery-006
Source: [Observed] src/butlers/core/ingestion_events.py:1107; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Eligible failed intent is queued

- **WHEN** the owner requests recovery for an event with a durable eligible `not_attempted` target intent
- **THEN** the request SHALL durably queue that same intent and return its target identity and waiting state
- **AND** accepted targets SHALL remain accepted and SHALL not be replayed

#### Scenario: Ingested or replay-failed row has eligible outstanding work

- **WHEN** the owner requests recovery of an `ingested` or `replay_failed` event with an original classified plan and a safely retryable unaccepted target intent
- **THEN** only that original intent SHALL be queued with its unchanged target, segment, payload, and delivery identity
- **AND** the terminal `message_inbox` SHALL not be reset to `accepted`, and the event SHALL not be classified or decomposed again

#### Scenario: Accepted event has no recoverable target

- **WHEN** the owner requests recovery of an `ingested` or `replay_failed` event whose targets are all accepted, terminally refused, ambiguous, or otherwise ineligible
- **THEN** the request SHALL return a specific non-success or conflict result without creating a new intent or sending another target call

#### Scenario: Legacy failed row has no proven intent

- **WHEN** a failed pre-cutover event lacks a retained classified plan, target payload, or trustworthy no-acceptance evidence
- **THEN** the request SHALL return a non-success explanation and leave the event failed
- **AND** it SHALL NOT reset a terminal message inbox or invoke connector ingress replay as a substitute

#### Scenario: Ambiguous or rejected target is refused

- **WHEN** a requested target has `ambiguous`, canonical `rejected`, or policy-denied evidence
- **THEN** the recovery request SHALL preserve that state and return a conflict with a safe reason
- **AND** no new target call SHALL be queued

#### Scenario: Already pending request is idempotent

- **WHEN** an event is `replay_pending` and an eligible original target intent is already durably queued
- **THEN** another recovery request SHALL return the existing queued intent identity without adding work or resetting `message_inbox`

#### Scenario: Orphan pending marker is not proof of queued work

- **WHEN** a legacy `replay_pending` event has no corresponding durable queued intent
- **THEN** its API projection and recovery response SHALL report unavailable or conflicting delivery evidence rather than pending success
- **AND** no target call or status-only success transition SHALL result

#### Scenario: Other source statuses remain non-replayable

- **WHEN** a `public.ingestion_events` row has any status outside the governed recovery branches
- **THEN** the recovery request SHALL return a non-success status conflict without mutating the event, inbox, or target intents

#### Scenario: Connector filtered events retain their replay contract

- **WHEN** the requested ID belongs to `connectors.filtered_events` rather than `public.ingestion_events`
- **THEN** the connector's existing server-derived replay-safe policy and drain transition SHALL govern it independently
- **AND** the target-delivery worker SHALL NOT claim that connector row or rewind its provider cursor

### Requirement: Exact owner-gated historical late delivery

Historical recovery SHALL begin with a content-blind dry run identifying only retained, provably pre-acceptance failed target deliveries. No historical delivery SHALL be queued until the owner approves the exact event-target-segment set and its bounded age and channel policy.

ID: REQ-ingestion-target-delivery-recovery-007
Source: heart-and-soul/vision.md Rule 1; design.md Decision 6
Scope: v1-mandatory

#### Scenario: Dry run inventories recoverable evidence

- **WHEN** an operator runs a historical eligibility preview
- **THEN** it SHALL group counts by time bucket, channel, target, and safe failure class and identify the exact eligible delivery keys for private owner review
- **AND** it SHALL exclude missing payloads, acknowledged targets, pruned rows, canonical `rejected`, `uncertain` attempts, and rows lacking both a receipt and independent proof of pre-accept no-effect; absence of a receipt alone SHALL NOT prove eligibility

#### Scenario: Exact approval admits only selected work

- **WHEN** the owner approves a bounded age and channel policy and an exact set of eligible delivery keys
- **THEN** only that set SHALL be admitted into durable delivery intents after rechecking current evidence and policy
- **AND** changed, missing, or newly ambiguous keys SHALL remain unqueued and be reported as exclusions

#### Scenario: No approval means no historical delivery

- **WHEN** a historical dry run completes without owner approval
- **THEN** no target delivery SHALL be queued
- **AND** no provider cursor, connector replay queue, or source event shall be rewound or reclassified

### Requirement: Content-blind delivery evidence

Delivery intents, attempt summaries, APIs, logs, metrics, and historical previews SHALL expose only fixed state and reason vocabularies plus bounded identifiers needed for correlation. They SHALL NOT publish message content, raw exception text, sender identity, provider payload, or credentials through recovery evidence.

ID: REQ-ingestion-target-delivery-recovery-008
Source: heart-and-soul/vision.md Rule 1; design.md Decision 7
Scope: v1-mandatory

#### Scenario: Failure evidence is projected

- **WHEN** an attempt fails or a dry run excludes a historical target
- **THEN** the owner-facing and telemetry projection SHALL carry only a safe normalized reason and its delivery identity
- **AND** raw transport messages, message payloads, and sender identity SHALL remain absent

#### Scenario: Invalid state or reason is rejected

- **WHEN** a writer supplies a delivery state, transport outcome, or reason outside the closed vocabulary
- **THEN** persistence SHALL reject it without converting it into a success or retryable state
