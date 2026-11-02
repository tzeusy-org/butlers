## ADDED Requirements

### Requirement: Owner knowledge gaps

A memory-enabled butler SHALL record an unanswered owner question as a typed knowledge gap in its own
memory schema: `entity_id`, `predicate`, `question_summary`, `asked_at`, `expires_at` (default
ninety days after `asked_at`), one origin row per asking thread (`origin_conversation_id`,
`origin_request_id`, `origin_channel`), and a `status`
of `open`, `answerable`, `delivered`, `dismissed` or `expired`. At most one `open` gap SHALL exist
per `(entity_id, predicate)`. Gap capture SHALL validate that the entity exists and is not merged
away and that the predicate is registered in the recording schema, SHALL admit only an origin that
REQ-module-memory-019 admits, and SHALL NOT read or write any other butler's schema. A gap SHALL
carry no content authority and SHALL never be owner-authority or steering memory.

A write of an active fact for the same `(entity_id, predicate)` SHALL move the open gap to
`answerable` in the same transaction as the fact write, recording the fact reference, a bounded
value excerpt and the fact's server-stamped authority. A gap whose matching fact already exists
when it is captured SHALL be born `answerable`. Capture and closure SHALL serialize so that a
fact write racing a gap insert cannot leave the gap `open`. A closure failure SHALL roll back only
the closure: the fact write SHALL still commit and the gap SHALL stay `open`.

A deterministic, model-free module-default job (`memory_knowledge_gap_delivery`) SHALL post exactly
one notice per origin thread of each `answerable` gap, then mark the gap `delivered`. It admits no
goal, spends no goal budget and does not reopen the goal that recorded the gap. A dashboard origin
SHALL receive its notice in its own thread through the same persistence dashboard replies use. Any
other admitted origin channel SHALL receive its notice only through the canonical owner-delivery
subject for that thread (REQ-owner-delivery-001), with a retry key of subject, surface and
generation, and its origin SHALL count as delivered only on a delivered receipt; an ambiguous
provider send SHALL stay `unknown` and be reconciled, never resent as new. A notice SHALL never be
redirected to a recipient other than its origin thread. A fact whose authority is owner-class or
`system` SHALL read "now known"; a fact of any other authority SHALL read "reported by" the sender
and SHALL NOT be presented as established. A failed delivery SHALL keep the gap `answerable`, count
the attempt and retry with bounded backoff; once attempts are exhausted the gap SHALL stay visible in
the open-gap list with its last error, flagged `delivery_failed` in the listing, and SHALL record
one attention event (outcome `failed`, intent `knowledge_gap_answer`). The
same job SHALL mark `open` gaps past `expires_at` as `expired` without notice. `dismissed` and
`expired` are terminal.

The owner SHALL be able to list gaps through the `memory_open_gaps` tool and `GET /api/memory/gaps`
(cursor pagination, degraded envelope), each with a coverage state composed from the existing
`present`, `absent_proven`, `unknown` or `unavailable` vocabulary; missing coverage SHALL read
`unknown`.

ID: REQ-module-memory-018
Source: bu-q7vx1q.9 (S1-S3, merged PR #4354); bu-q7vx1q.74, bu-q7vx1q.76; re-scope bu-q7vx1q.93.2 per generalist-lead-focused-delegation collision-dispositions.md; RFC 0038 D2, D4, D6, D12, D15, D19, S6; REQ-dashboard-goal-continuity-008; REQ-owner-delivery-001; REQ-relationship-facts-008
Scope: v1-mandatory

#### Scenario: A decline with a gap records exactly one open row

- **WHEN** an admitted owner decline records a gap naming a live entity and a registered predicate
- **THEN** exactly one `open` gap SHALL exist for that pair with the origin thread, request id and channel taken from server-side provenance
- **AND** an invalid entity or predicate SHALL fail the gap write without blocking the decline reply

#### Scenario: A duplicate decline merges

- **WHEN** a second decline names the same `(entity_id, predicate)` from another conversation
- **THEN** the single open gap SHALL list both origins and SHALL receive one notice per origin

#### Scenario: A fact write answers the gap in its own transaction

- **WHEN** `store_fact` stores an active fact for an open gap's `(entity_id, predicate)`
- **THEN** the gap SHALL become `answerable` in the same transaction with the fact's `memory_ref`
- **AND** a replay of the same fact write SHALL NOT notify again

#### Scenario: A fact that predates the gap yields an answerable gap

- **WHEN** a gap is captured for a pair that already has an active fact
- **THEN** the gap SHALL be born `answerable`

#### Scenario: A third-party fact is reported, not established

- **WHEN** the closing fact carries `third_party` authority
- **THEN** the notice SHALL read "reported by" the sender entity and SHALL NOT use "now known"

#### Scenario: Expiry and dismissal are terminal

- **WHEN** an `open` gap passes `expires_at` or the owner dismisses it
- **THEN** it SHALL become `expired` or `dismissed`, SHALL send no notice, and SHALL NOT reopen on a later fact write

#### Scenario: Delivery failure never drops a gap silently

- **WHEN** posting the notice fails
- **THEN** the gap SHALL stay `answerable` with its attempt count and last error and SHALL be retried until attempts are exhausted
- **AND** exhaustion SHALL record one attention event with outcome `failed` and intent `knowledge_gap_answer`, and keep the gap listed with `delivery_failed` set

#### Scenario: A closure failure never loses the fact

- **WHEN** closing a matching gap fails inside a fact write
- **THEN** the fact SHALL commit, the gap SHALL stay `open`, and the failure SHALL be counted

#### Scenario: An ambiguous send is reconciled, not repeated

- **WHEN** an owner-delivery send for a non-dashboard origin returns no provider outcome
- **THEN** that origin SHALL stay undelivered with an `unknown` receipt and SHALL NOT be posted again under a new retry key

### Requirement: Knowledge-gap capture admits only a verified owner origin

Gap capture SHALL accept a gap only from a server-derived owner origin and only through one of two
seams. Before the generalist switch, the frozen dashboard answer lane MAY record a gap carried by a
sourceless `conversation_reply` decline in a dashboard-routed session; that seam is fixes-only and
SHALL be removed with the answer lane at the switch. After the switch, the only seam SHALL be the
broker-granted, non-steering Memory gap `record` capability that REQ-dashboard-goal-continuity-004
admits for an owner-authenticated answer goal. That capability SHALL be declared with effect class
`record`, `owner_authority` false and `idempotency` `key_required`; its arguments SHALL be the
entity, the predicate and the question summary only. Each memory-enabled domain SHALL declare its
own gap `record` capability with itself as owner domain, and that capability SHALL record a gap only
in its own schema, behind a valid broker assertion (REQ-capability-authorization-007), and only for
a predicate registered in that schema; any other predicate SHALL be refused with a typed
`predicate_unregistered` result and record nothing. A predicate registered in more than one schema
SHALL be recorded only in the schema of the domain whose capability was called, where a matching
fact write can close it; there SHALL be no cross-schema capture, closure or fan-out. Origin thread,
request and channel SHALL come from the broker assertion's goal provenance, never from arguments;
the origin goal identity SHALL be stored on the origin row once `bu-q7vx1q.70` adds that column
(task 3.2). This requirement is v1-mandatory for the store's admission rule; its post-switch seam
is exercised only through the post-v1 REQ-dashboard-goal-continuity-008 answer goal. A repeated call with the
same effect identity SHALL return the original result, and a repeated origin thread for the same
open gap SHALL merge without a second origin row.

The admitted origin channels SHALL be a closed set. `dashboard` is admitted. `telegram` SHALL be
admitted only after owner-channel authorization (REQ-capability-authorization-003) verifies both
channel and sender for the goal and the canonical owner-delivery path can deliver a receipted
notice into that thread (bu-q7vx1q.70). Every other channel SHALL be refused with a typed
`channel_unsupported` result and SHALL record nothing. A goal whose origin is not owner, including a
third-party or obligation goal, SHALL hold no gap `record` grant. An exposed owner goal MAY record a
gap; the broker taints that effect as for any `record` call (REQ-capability-authorization-006), and
taint never changes how closure attributes the answer. A gap failure SHALL be reported in the tool
or goal result and SHALL NOT prevent the decline from reaching the owner. Disabling the capability
SHALL stop new capture and SHALL preserve existing gaps, origins and delivery receipts.

ID: REQ-module-memory-019
Source: bu-q7vx1q.9 answer-lane capture (merged PR #4354); re-scope bu-q7vx1q.93.2 per generalist-lead-focused-delegation collision-dispositions.md; RFC 0038 D2, D4, D5, D6, D7, D10, D12, D19 and Rollback; REQ-dashboard-goal-continuity-004, REQ-dashboard-goal-continuity-008; REQ-capability-authorization-002, -003, -006, -007; bu-q7vx1q.70
Scope: v1-mandatory

#### Scenario: A gap on a sourced reply is refused

- **WHEN** a decline carries sources, or a gap `record` call names a pair a read in the same goal already returned
- **THEN** the reply or answer SHALL still reach the owner, no gap SHALL be recorded, and the result SHALL report the gap as refused

#### Scenario: A decline without a memory module still replies

- **WHEN** the recording domain has no memory module or the gap write fails
- **THEN** the decline SHALL still reach the owner and the result SHALL report the gap as unavailable or failed

#### Scenario: Model-supplied origin fields are ignored

- **WHEN** a capture request carries a conversation, request, goal or channel in its arguments
- **THEN** the recorded origin SHALL be the server-side provenance and the argument values SHALL be ignored

#### Scenario: An unverified sender or unsupported channel records nothing

- **WHEN** the origin is not owner-verified, or its channel is outside the admitted set
- **THEN** no gap or origin row SHALL be written and the result SHALL name the refusal

#### Scenario: A gap is recorded only where it can close

- **WHEN** a domain's gap `record` capability is called for a predicate its schema does not register, or for a predicate also registered in another domain's schema
- **THEN** an unregistered predicate SHALL be refused `predicate_unregistered` with nothing written, and a registered one SHALL be recorded only in the called domain's schema, never in another

#### Scenario: A replayed record call does not duplicate

- **WHEN** a gap `record` call is repeated with the same effect identity, or the same thread asks again for an open pair
- **THEN** the gap SHALL keep one origin row for that thread and the call SHALL return the original result
