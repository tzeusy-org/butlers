## MODIFIED Requirements

### Requirement: Pending reviews endpoint

The system SHALL expose `GET /api/education/mind-maps/{mind_map_id}/pending-reviews` returning the open review cards for the mind map that are due for spaced repetition review (where the card's `scheduled_for <= now()`). A review card (module-education-spaced-repetition "Review cards are the pending-review authority") is the only source of a pending review; the endpoint SHALL NOT infer a pending review from a node's `next_review_at`, from `scheduled_tasks` rows, or from any scheduler row count.

The endpoint SHALL accept an optional `horizon_days` query parameter (integer, `>= 0`). When omitted, only overdue cards are returned. When set, upcoming cards due within that many days from now are also included, enabling timeline grouping (Overdue / Today / This Week / Later).

The response SHALL be a JSON array of review-card objects, each containing: `review_card_id`, `node_id`, `label`, `state`, `scheduled_for`, `ease_factor`, `repetitions`, `next_review_at`, `mastery_status`. `next_review_at` is the node's SM-2 scheduling projection, carried for display only.

The endpoint SHALL return 404 if the mind map does not exist. The endpoint SHALL return an empty array if no cards are due, including when the mind map has nodes with a populated `next_review_at` but no review card.

The endpoint SHALL call the existing `spaced_repetition_pending_reviews(pool, mind_map_id, horizon_days=horizon_days)` tool function without duplicating its SQL logic.

ID: REQ-dashboard-education-api-003
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Reviews due for a mind map with scheduled nodes

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/pending-reviews`
- **AND** the mind map exists with 3 open review cards whose `scheduled_for` is in the past
- **THEN** the response status SHALL be 200
- **AND** the response body SHALL contain exactly 3 review-card objects, each with its `review_card_id` and review metadata

#### Scenario: No reviews due

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/pending-reviews`
- **AND** the mind map exists but every open review card has `scheduled_for` in the future, or the map has no open review card
- **THEN** the response status SHALL be 200
- **AND** the response body SHALL be an empty array

#### Scenario: A node with next_review_at but no card yields no pending review

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/pending-reviews`
- **AND** the mind map has a node whose `next_review_at` is in the past and which has no open review card
- **THEN** the response status SHALL be 200
- **AND** the response body SHALL be an empty array

#### Scenario: Mind map not found

- **WHEN** a GET request is made to `/api/education/mind-maps/{nonexistent-id}/pending-reviews`
- **THEN** the response status SHALL be 404

### Requirement: Mastery summary endpoint

The system SHALL expose `GET /api/education/mind-maps/{mind_map_id}/mastery-summary` returning aggregate mastery statistics for a mind map.

The response SHALL be a JSON object containing: `mind_map_id`, `total_nodes`, `mastered_count`, `learning_count`, `reviewing_count`, `unseen_count`, `diagnosed_count`, `avg_mastery_score`, `measured_over`, `struggling_node_ids`.

`avg_mastery_score` SHALL be nullable and `measured_over` SHALL be a non-negative integer: the number of nodes whose score rests on at least one graded, owner-evidenced response. `avg_mastery_score` SHALL be `null` exactly when `measured_over` is `0`; the API SHALL NOT report `0.0` for an average taken over no graded evidence. A genuine `0.0` SHALL be reported only when `measured_over > 0`.

The endpoint SHALL call the existing `mastery_get_map_summary(pool, mind_map_id)` tool function. The endpoint SHALL return 404 if the mind map does not exist.

ID: REQ-dashboard-education-api-004
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Summary for an active mind map

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/mastery-summary`
- **AND** the mind map has 10 nodes with mixed mastery statuses and 4 nodes with graded evidence
- **THEN** the response status SHALL be 200
- **AND** `total_nodes` SHALL equal 10
- **AND** the status counts SHALL sum to `total_nodes`
- **AND** `measured_over` SHALL equal 4
- **AND** `avg_mastery_score` SHALL be between 0.0 and 1.0

#### Scenario: Summary with no graded evidence is null, not zero

- **WHEN** a GET request is made to `/api/education/mind-maps/{id}/mastery-summary`
- **AND** the mind map has nodes but none has graded evidence
- **THEN** `measured_over` SHALL equal 0
- **AND** `avg_mastery_score` SHALL be `null`

#### Scenario: A genuine zero average is still reported

- **WHEN** every graded node has `mastery_score = 0.0` and `measured_over > 0`
- **THEN** `avg_mastery_score` SHALL be `0.0`

#### Scenario: Mind map not found

- **WHEN** a GET request is made to `/api/education/mind-maps/{nonexistent-id}/mastery-summary`
- **THEN** the response status SHALL be 404


### Requirement: Curriculum request receipt lifecycle

Each accepted curriculum request SHALL settle to a terminal outcome on its receipt row, so that a failure of the detached work is visible to the owner rather than only to the log.

The receipt SHALL carry, in addition to `id`, `topic` and `goal`: `status` (one of `accepted`, `running`, `completed`, `failed`), `session_id`, `mind_map_id`, `calibration_ready_at`, `calibration_notice_outcome`, `calibration_notice_accepted_at`, `plan_digest`, `failure_reason`, `requested_at`, `triggered_at`, `settled_at`, and `updated_at`.

The detached task SHALL stamp `status = running` and `triggered_at` before handing the request to the butler's `trigger` MCP tool, and SHALL await that tool to completion.

The task SHALL settle `status = completed` only when the triggered session reported success **and** exactly one mind map satisfies the Ready predicate for this receipt. The Ready predicate (module-education-curriculum "Curriculum plan identity and digest") holds for a mind map that: carries this receipt's id as its `curriculum_request_id`; has a `created_session_id` equal to the session the trigger returned; has `status = 'active'`; contains at least one node; and carries a nonblank `plan_digest`. Creation time, including "the newest map created at or after `triggered_at`", SHALL NOT be used to identify the map: time-window correlation is not identity, and an unrelated map created concurrently SHALL NOT satisfy the predicate. `session_id` SHALL be recorded from the trigger result, `mind_map_id` and `plan_digest` from the map that satisfies the predicate, and `calibration_ready_at` SHALL be set only when that map's teaching flow has reached `diagnosing` or a later state.

A receipt that settled `completed` before this predicate existed, and therefore carries no `plan_digest`, SHALL remain readable with its recorded evidence but SHALL be exposed as legacy and unverified (see "Curriculum request status read"). Settlement logic SHALL NOT retroactively upgrade, rewrite, or re-verify such a receipt, and SHALL NOT fabricate a `plan_digest` for it.

`calibration_ready_at` attests that calibration **began**. It SHALL NOT be read, rendered, or documented as evidence that the owner was contacted.

Whether the owner was contacted SHALL be recorded separately, from the notification path and never from teaching-flow state. On the completed path the task SHALL consult `public.attention_ledger` for the `source="notify"` dispatch recorded against the session it triggered, and SHALL settle `calibration_notice_outcome` to one of:

- the ledger's own word for the dispatch (`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`);
- `no_record` when the ledger was read and held no notify row for that session;
- `unproven` when the ledger could not be read, or there was no session id to read it with.

`calibration_notice_accepted_at` SHALL be set from the ledger row's own `occurred_at`, and SHALL be set for `outcome = delivered` alone. It means a delivery channel accepted the message; it does not mean the owner read it, and no surface SHALL describe it as though it did.

An absent ledger row SHALL NOT be settled as a failed notice. Ledger recording is best-effort and never raises, so `no_record` records that the evidence is missing, which is a weaker claim than the notice having failed and SHALL stay distinguishable from it.

A settle that has no notice evidence to record SHALL leave both columns as it found them, so a later or duplicate settle cannot blank evidence an earlier one recorded. The two columns SHALL be written as a single unit, because they are decided from one piece of evidence and the database binds them.

The database SHALL enforce that `calibration_notice_outcome` is one of the values above, and that `calibration_notice_accepted_at` is present exactly when the outcome is `delivered` — absent for every other outcome, and absent when the outcome is null.

The task SHALL settle `status = failed` with a stable `failure_reason` on every other exit path: `trigger_unreachable` when the butler could not be reached, `session_error` when the session reported its own failure, `no_curriculum_created` when a session exited cleanly and no mind map is linked to this receipt, and `curriculum_unverified` when a session exited cleanly and a linked mind map exists but fails the Ready predicate (it is `draft`, empty, or without a nonblank `plan_digest`). A clean session exit SHALL NOT by itself settle `completed`.

Settlement SHALL be idempotent: the first terminal write SHALL win, and a later or duplicate settle SHALL be a no-op rather than a contradicting outcome.

A receipt that remains non-terminal for longer than the abandonment timeout SHALL be settled to `failed` with `failure_reason = "timed_out"`, releasing the pending guard. This sweep SHALL run on every submit and every status read, so that an API restart that kills an in-flight task cannot strand the guard.

The database SHALL enforce that a terminal status carries `settled_at`, that a non-terminal status does not, and that `failed` carries a `failure_reason`. Once the evidence columns are enforced for new rows, the database SHALL additionally enforce that a receipt which settles `completed` after that point carries a `mind_map_id` and a nonblank `plan_digest`; rows that settled `completed` earlier SHALL be exempt so that they stay readable.

ID: REQ-dashboard-education-api-002
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Successful curriculum settles with evidence

- **WHEN** the triggered session reports success and exactly one mind map is linked to the receipt, was created by that session, is `active`, has at least one node, and carries a nonblank `plan_digest`
- **THEN** the receipt SHALL settle `status = completed` with `session_id`, `mind_map_id`, `plan_digest`, and `settled_at` recorded

#### Scenario: A recent map that is not linked to the request does not settle it

- **WHEN** the triggered session reports success
- **AND** an active, populated, digested mind map was created after `triggered_at` but carries no `curriculum_request_id` equal to this receipt, or a `created_session_id` other than the triggered session
- **THEN** the receipt SHALL NOT settle `completed`
- **AND** the receipt SHALL settle `status = failed` with `failure_reason = "no_curriculum_created"`

#### Scenario: A linked draft map never settles Ready

- **WHEN** the triggered session reports success and the linked mind map is `draft`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "curriculum_unverified"`

#### Scenario: A linked empty map never settles Ready

- **WHEN** the triggered session reports success and the linked mind map has zero nodes
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "curriculum_unverified"`

#### Scenario: A linked map without a plan digest never settles Ready

- **WHEN** the triggered session reports success and the linked, active, populated mind map has a null or blank `plan_digest`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "curriculum_unverified"`

#### Scenario: Legacy completed receipt is not re-verified

- **WHEN** a receipt settled `completed` before the Ready predicate existed and has no `plan_digest`
- **THEN** it SHALL remain readable with its recorded `session_id`, `mind_map_id` and `settled_at`
- **AND** no settlement pass SHALL rewrite its status or populate its `plan_digest`

#### Scenario: Trigger cannot reach the butler

- **WHEN** the MCP client or `trigger` call raises
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "trigger_unreachable"`

#### Scenario: Session reports its own failure

- **WHEN** the `trigger` result reports `success: false`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "session_error"`
- **AND** the receipt SHALL retain the `session_id` of the failed session

#### Scenario: Session exits without creating a curriculum

- **WHEN** the triggered session reports success but no mind map is linked to the receipt
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "no_curriculum_created"`

#### Scenario: Settlement is idempotent

- **WHEN** a receipt already holds a terminal status
- **AND** a second settle is attempted with a different status
- **THEN** the receipt SHALL retain its original terminal status and evidence

#### Scenario: A failed notice never becomes a delivery claim

- **WHEN** the teaching flow has reached `diagnosing`, so `calibration_ready_at` is set
- **AND** the ledger recorded `outcome = "failed"` for the triggered session's notify
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "failed"` with `calibration_notice_accepted_at` unset
- **AND** `calibration_ready_at` SHALL remain set, because both statements are true and neither implies the other

#### Scenario: A missing ledger row is absence of evidence, not evidence of failure

- **WHEN** the ledger holds no notify row for the triggered session
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "no_record"` with `calibration_notice_accepted_at` unset

#### Scenario: An unreadable ledger is reported as unproven

- **WHEN** the ledger read raises, or the trigger result carried no session id to read it with
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "unproven"` with `calibration_notice_accepted_at` unset
- **AND** the request SHALL still settle its own status normally

#### Scenario: Channel acceptance is recorded with the ledger's own moment

- **WHEN** the ledger recorded `outcome = "delivered"` for the triggered session's notify
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "delivered"` and `calibration_notice_accepted_at` to that ledger row's `occurred_at`

#### Scenario: The database refuses an unbacked acceptance moment

- **WHEN** a receipt row is written with `calibration_notice_accepted_at` set and `calibration_notice_outcome` anything other than `delivered`, null included
- **THEN** the write SHALL be rejected by a CHECK constraint

#### Scenario: Abandoned receipt is swept

- **WHEN** a receipt has been non-terminal for longer than the abandonment timeout
- **AND** a curriculum request is submitted or a receipt status is read
- **THEN** the abandoned receipt SHALL settle `status = failed` with `failure_reason = "timed_out"`
- **AND** a new curriculum request SHALL be accepted

## ADDED Requirements

### Requirement: Curriculum receipt verification state

Every receipt returned by `GET /api/education/curriculum-requests/{request_id}` and `GET /api/education/curriculum-requests/latest` SHALL carry a `verification` field computed by the API from stored evidence, never supplied by a caller and never decided by the frontend. `verification` SHALL be `verified` only when the receipt has `status = completed` **and** a nonblank `plan_digest` **and** a `mind_map_id` that still resolves to a mind map carrying this receipt's id as its `curriculum_request_id`. A receipt with `status = completed` that fails any of those conditions SHALL carry `verification = legacy_unverified`. A receipt that is not `completed` SHALL carry `verification = not_applicable`.

A `legacy_unverified` receipt SHALL be returned with every recorded field intact. The API SHALL NOT omit it, rewrite its `status`, or present it as `verified`.

ID: REQ-dashboard-education-api-005
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Digested, linked, completed receipt is verified

- **WHEN** a completed receipt carries a nonblank `plan_digest` and its mind map carries the receipt's id as `curriculum_request_id`
- **THEN** the response SHALL carry `verification: "verified"`

#### Scenario: Pre-existing completed receipt is legacy and unverified

- **WHEN** a receipt with `status: "completed"` has a null `plan_digest`
- **THEN** the response status SHALL be 200
- **AND** the body SHALL carry the receipt with `verification: "legacy_unverified"` and its original `status`, `session_id`, `mind_map_id` and `settled_at`

#### Scenario: Digest present but link broken

- **WHEN** a completed receipt carries a `plan_digest` but its `mind_map_id` no longer resolves to a mind map linked to the receipt
- **THEN** the response SHALL carry `verification: "legacy_unverified"`

#### Scenario: Non-completed receipt has no verification claim

- **WHEN** a receipt has `status: "running"` or `status: "failed"`
- **THEN** the response SHALL carry `verification: "not_applicable"`

### Requirement: Mastery transition history endpoint

The system SHALL expose `GET /api/education/nodes/{node_id}/mastery-transitions` returning the ordered ledger of mastery transitions for the node (module-education-mastery "Mastery transition ledger"), oldest first, each entry containing `transition_id`, `from_status`, `to_status`, `evidence_kind`, `occurred_at`, `quiz_response_id` and `review_card_id` (each nullable), and `origin`, which SHALL be `evidence` for an entry recorded from owner evidence and `legacy_unknown` for a backfilled entry that records only that the node was already in that status.

The endpoint SHALL return 404 if the node does not exist and an empty array if the node has no ledger entries. The endpoint SHALL be read-only.

If the node's current `mastery_status` differs from the `to_status` of its most recent ledger entry, the endpoint SHALL return the entries together with `consistent: false`, and consumers SHALL NOT present the node's current status as a confident mastery claim. Otherwise `consistent` SHALL be `true`.

ID: REQ-dashboard-education-api-006
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Ordered history with evidence doors

- **WHEN** a GET request is made for a node with two recorded transitions
- **THEN** the response status SHALL be 200
- **AND** the entries SHALL be ordered by `occurred_at` ascending
- **AND** each entry SHALL carry its `evidence_kind` and the identifiers of its quiz response or review card where they exist

#### Scenario: Legacy entry is marked as unproved

- **WHEN** a node that was already `mastered` before the ledger existed is read
- **THEN** its entry SHALL carry `origin: "legacy_unknown"` and null `quiz_response_id` and `review_card_id`
- **AND** its `occurred_at` SHALL be treated as the time the ledger row was written and SHALL NOT be presented as the date mastery was reached

#### Scenario: Projection and ledger disagree

- **WHEN** the node's `mastery_status` differs from the most recent entry's `to_status`
- **THEN** the response SHALL carry `consistent: false`

#### Scenario: Unknown node

- **WHEN** a GET request is made for a node that does not exist
- **THEN** the response status SHALL be 404
