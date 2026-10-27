## Purpose

This capability specifies the future D19 dashboard goal education owner experience. It preserves the complete source-bound functional contracts below, remains subject to RFC 0038 and owner-controlled M6 integration, and conveys no current runtime or capability authority.

## ADDED Requirements

### Requirement: Region education Curriculum request submission endpoint

The system SHALL expose `POST /api/education/curriculum-requests` accepting a JSON body `{"topic": "<topic>", "goal": "<optional_goal>"}`.
The `topic` field SHALL be required and non-empty (max 200 characters). The `goal` field SHALL be optional (max 500 characters).
Before any detached work begins, the endpoint SHALL insert an immutable receipt row into `education.curriculum_requests` with status `accepted`, the submitted topic and goal, and a generated `id`. That `id` is the request's permanent receipt.
The endpoint SHALL NOT persist a `pending_curriculum_request` KV key. The one-pending-at-a-time guard SHALL be the partial unique index `uq_curriculum_requests_one_open`, which permits at most one receipt in a non-terminal status (`accepted`, `running`). When the insert is refused by that index, the endpoint SHALL return 409 Conflict.
When the receipt store cannot be read or written because the education migration chain is absent, the endpoint SHALL return 503 rather than accepting a request it cannot evidence.
On success, the endpoint SHALL return 202 Accepted with body `{"status": "accepted", "topic": "<topic>", "request_id": "<uuid>"}`. `202` SHALL mean *accepted and recorded* only; it SHALL NOT be treated by any caller as evidence that a curriculum was created or that the owner was contacted.
The existing education request/schema/attention-ledger owner SHALL retain the domain receipt authority and all database constraints. In the future, the request's submitted goal text remains text, distinct from nullable recorded runtime goal_id/invocation_id. Historical session_id SHALL remain historical and may be populated only from an actual historical producer receipt. Future notification lookup SHALL use the actual producer's stored dispatch correlation; it SHALL NOT put an invocation UUID in session_id or guess a delivered notice from goal completion. Adding that correlation and replacing the unit trigger is an exact education/backend amendment held by its current owner before M6; until adopted, the full current producer and receipt contracts remain authoritative. All old request IDs, topic 200/goal 500 limits, one-open index, accepted-before-work, 409/422/503 distinctions, stable trigger_unreachable/session_error/no_curriculum_created/timed_out codes, first-terminal-wins, every-submit/read abandonment sweep, and notice evidence/atomic CHECK rules remain. The Region placement confers no extra schema, notify, retry or teaching authority.
The modern receipt SHALL carry a server-stored binding from immutable request_id to its producing goal_id, fresh eligible invocation_id and the exact calibration notify_dispatch_id. The notify-dispatch identity is the actual durable notification intent/dispatch identity, not a UUID alias for a session, goal or invocation. A matching ledger row SHALL match that binding and source="notify"; unrelated notices from the same goal SHALL NOT qualify the calibration receipt. Neither model text, submitted goal text, channel/thread identity, a map's diagnosing state nor a historical session field may supply this binding.
A readable matched delivered row SHALL prove delivered and its ledger occurred_at even when historical session_id is null. A readable valid modern binding with no row SHALL prove no_record even when historical session_id is null. Missing/unusable modern correlation or an unreadable ledger SHALL be unproven, independently of whether a historical session exists. A first settlement with no prior notice evidence may record that unproven classification with a null acceptance moment; a later/duplicate settlement lacking usable evidence SHALL leave both existing notice columns untouched. The paired write/CHECK, first-terminal-wins and every-submit/read abandonment sweep remain authoritative; no lookup failure or absent historical field may overwrite an existing delivered receipt.
This is the complete proposed modern projection/settlement contract. Adding actual correlation fields/indexes, choosing the exact existing notify intent key and replacing the legacy producer require the current education/schema/notify owner's exact amendment before M6. No backend schema change or public endpoint is implemented or allocated by this design.

ID: REQ-dashboard-goal-education-001
Source: openspec/specs/dashboard-education-api/spec.md; RFC 0038 D11/D19; design.md
Scope: post-v1

#### Scenario: Submit a new curriculum request

- **WHEN** a POST request is made to `/api/education/curriculum-requests` with body `{"topic": "Python", "goal": "Learn web development with Flask"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the response body SHALL contain `{"status": "accepted", "topic": "Python"}` and a `request_id`
- **AND** a receipt row SHALL exist in `education.curriculum_requests` with status `accepted`, the topic and goal, and no outcome evidence

#### Scenario: Receipt precedes detached work

- **WHEN** a curriculum request is accepted
- **THEN** the receipt row SHALL be persisted before the detached curriculum task is created

#### Scenario: Submit request without goal

- **WHEN** a POST request is made with body `{"topic": "Linear Algebra"}`
- **AND** no non-terminal receipt exists
- **THEN** the response status SHALL be 202
- **AND** the receipt row SHALL have `goal` set to null

#### Scenario: Duplicate request while one is in flight

- **WHEN** a POST request is made to `/api/education/curriculum-requests`
- **AND** a receipt already exists in status `accepted` or `running`
- **THEN** the response status SHALL be 409
- **AND** the response body SHALL indicate a curriculum request is already pending

#### Scenario: Receipt store unavailable

- **WHEN** a POST request is made and `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 503

#### Scenario: Empty topic

- **WHEN** a POST request is made with body `{"topic": ""}`
- **THEN** the response status SHALL be 422

#### Scenario: Topic exceeds length limit

- **WHEN** a POST request is made with a `topic` longer than 200 characters
- **THEN** the response status SHALL be 422

---



### Requirement: Region education Curriculum request receipt lifecycle

Each accepted curriculum request SHALL settle to a terminal outcome on its receipt row, so that a failure of the detached work is visible to the owner rather than only to the log.
The receipt SHALL carry, in addition to `id`, `topic` and `goal`: `status` (one of `accepted`, `running`, `completed`, `failed`), `goal_id`, `invocation_id`, `notify_dispatch_id`, nullable historical `session_id`, `mind_map_id`, `calibration_ready_at`, `calibration_notice_outcome`, `calibration_notice_accepted_at`, `failure_reason`, `requested_at`, `triggered_at`, `settled_at`, and `updated_at`.
The detached task SHALL stamp `status = running` and `triggered_at` before handing the immutable request to its admitted education obligation goal, and SHALL await the recorded producer outcome.
The task SHALL settle `status = completed` only when the correlated producer invocation reported success **and** a mind map created at or after `triggered_at` is found. The actual producer receipt SHALL record its `goal_id` and fresh `invocation_id`; `mind_map_id` SHALL come from the recorded request/producer correlation. Historical `session_id` SHALL remain null unless an actual historical producer separately supplies it, and `calibration_ready_at` SHALL be set only when the correlated mind map's teaching flow has reached `diagnosing` or a later state.
`calibration_ready_at` attests that calibration **began**. It SHALL NOT be read, rendered, or documented as evidence that the owner was contacted.
Whether the owner was contacted SHALL be recorded separately, from the notification path and never from teaching-flow state. On the completed path the task SHALL consult `public.attention_ledger` for the `source="notify"` row selected by the server-stored immutable request/goal/invocation/notify-dispatch binding, and SHALL settle `calibration_notice_outcome` to one of:
- the ledger's own word for the dispatch (`delivered`, `coalesced`, `deferred`, `suppressed`, `failed`);
- `no_record` when the actual modern notify-dispatch binding exists, the ledger was readable, and no matching notify row was recorded;
- `unproven` when the ledger could not be read or the actual modern notify-dispatch binding was missing or unusable; absence of historical session_id SHALL NOT establish unproven.
`calibration_notice_accepted_at` SHALL be set from the ledger row's own `occurred_at`, and SHALL be set for `outcome = delivered` alone. It means a delivery channel accepted the message; it does not mean the owner read it, and no surface SHALL describe it as though it did.
An absent ledger row SHALL NOT be settled as a failed notice. Ledger recording is best-effort and never raises, so `no_record` records that the evidence is missing, which is a weaker claim than the notice having failed and SHALL stay distinguishable from it.
A settle that has no notice evidence to record SHALL leave both columns as it found them, so a later or duplicate settle cannot blank evidence an earlier one recorded. The two columns SHALL be written as a single unit, because they are decided from one piece of evidence and the database binds them.
The database SHALL enforce that `calibration_notice_outcome` is one of the values above, and that `calibration_notice_accepted_at` is present exactly when the outcome is `delivered` — absent for every other outcome, and absent when the outcome is null.
The task SHALL settle `status = failed` with a stable `failure_reason` on every other exit path: `trigger_unreachable` when the admitted producer could not be reached, `session_error` when the producer invocation reported its own failure, and `no_curriculum_created` when a producer invocation exited cleanly without producing a curriculum. A clean invocation exit SHALL NOT by itself settle `completed`.
Settlement SHALL be idempotent: the first terminal write SHALL win, and a later or duplicate settle SHALL be a no-op rather than a contradicting outcome.
A receipt that remains non-terminal for longer than the abandonment timeout SHALL be settled to `failed` with `failure_reason = "timed_out"`, releasing the pending guard. This sweep SHALL run on every submit and every status read, so that an API restart that kills an in-flight task cannot strand the guard.
The database SHALL enforce that a terminal status carries `settled_at`, that a non-terminal status does not, and that `failed` carries a `failure_reason`.
The existing education request/schema/attention-ledger owner SHALL retain the domain receipt authority and all database constraints. In the future, the request's submitted goal text remains text, distinct from nullable recorded runtime goal_id/invocation_id. Historical session_id SHALL remain historical and may be populated only from an actual historical producer receipt. Future notification lookup SHALL use the actual producer's stored dispatch correlation; it SHALL NOT put an invocation UUID in session_id or guess a delivered notice from goal completion. Adding that correlation and replacing the unit trigger is an exact education/backend amendment held by its current owner before M6; until adopted, the full current producer and receipt contracts remain authoritative. All old request IDs, topic 200/goal 500 limits, one-open index, accepted-before-work, 409/422/503 distinctions, stable trigger_unreachable/session_error/no_curriculum_created/timed_out codes, first-terminal-wins, every-submit/read abandonment sweep, and notice evidence/atomic CHECK rules remain. The Region placement confers no extra schema, notify, retry or teaching authority.
The modern receipt SHALL carry a server-stored binding from immutable request_id to its producing goal_id, fresh eligible invocation_id and the exact calibration notify_dispatch_id. The notify-dispatch identity is the actual durable notification intent/dispatch identity, not a UUID alias for a session, goal or invocation. A matching ledger row SHALL match that binding and source="notify"; unrelated notices from the same goal SHALL NOT qualify the calibration receipt. Neither model text, submitted goal text, channel/thread identity, a map's diagnosing state nor a historical session field may supply this binding.
A readable matched delivered row SHALL prove delivered and its ledger occurred_at even when historical session_id is null. A readable valid modern binding with no row SHALL prove no_record even when historical session_id is null. Missing/unusable modern correlation or an unreadable ledger SHALL be unproven, independently of whether a historical session exists. A first settlement with no prior notice evidence may record that unproven classification with a null acceptance moment; a later/duplicate settlement lacking usable evidence SHALL leave both existing notice columns untouched. The paired write/CHECK, first-terminal-wins and every-submit/read abandonment sweep remain authoritative; no lookup failure or absent historical field may overwrite an existing delivered receipt.
This is the complete proposed modern projection/settlement contract. Adding actual correlation fields/indexes, choosing the exact existing notify intent key and replacing the legacy producer require the current education/schema/notify owner's exact amendment before M6. No backend schema change or public endpoint is implemented or allocated by this design.

ID: REQ-dashboard-goal-education-002
Source: openspec/specs/dashboard-education-api/spec.md; RFC 0038 D11/D19; design.md
Scope: post-v1

#### Scenario: Successful curriculum settles with evidence

- **WHEN** the correlated producer invocation reports success and a mind map was created after `triggered_at`
- **THEN** the receipt SHALL settle `status = completed` with `goal_id`, `invocation_id`, `notify_dispatch_id`, nullable historical `session_id`, `mind_map_id`, and `settled_at` recorded

#### Scenario: Trigger cannot reach the butler

- **WHEN** producer admission or handoff raises
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "trigger_unreachable"`

#### Scenario: Session reports its own failure

- **WHEN** the correlated producer result reports `success: false`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "session_error"`
- **AND** the receipt SHALL retain the `session_id` of a historical failed session only when actually supplied

#### Scenario: Session exits without creating a curriculum

- **WHEN** the correlated producer invocation reports success but no mind map was created at or after `triggered_at`
- **THEN** the receipt SHALL settle `status = failed` with `failure_reason = "no_curriculum_created"`

#### Scenario: Settlement is idempotent

- **WHEN** a receipt already holds a terminal status
- **AND** a second settle is attempted with a different status
- **THEN** the receipt SHALL retain its original terminal status and evidence

#### Scenario: A failed notice never becomes a delivery claim

- **WHEN** the teaching flow has reached `diagnosing`, so `calibration_ready_at` is set
- **AND** the ledger recorded `outcome = "failed"` for the exact calibration notify dispatch bound to the request and its actual goal/invocation
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "failed"` with `calibration_notice_accepted_at` unset
- **AND** `calibration_ready_at` SHALL remain set, because both statements are true and neither implies the other

#### Scenario: A missing ledger row is absence of evidence, not evidence of failure

- **WHEN** the actual request/goal/invocation/notify-dispatch binding exists and a readable ledger holds no matching notify row
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "no_record"` with `calibration_notice_accepted_at` unset

#### Scenario: An unreadable ledger is reported as unproven

- **WHEN** the ledger read raises, or the actual request/goal/invocation/notify-dispatch correlation is missing or unusable
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "unproven"` with `calibration_notice_accepted_at` unset
- **AND** the request SHALL still settle its own status normally

#### Scenario: Channel acceptance is recorded with the ledger's own moment

- **WHEN** the ledger recorded `outcome = "delivered"` for the exact calibration notify dispatch bound to the request and its actual goal/invocation
- **THEN** the receipt SHALL settle `calibration_notice_outcome = "delivered"` and `calibration_notice_accepted_at` to that ledger row's `occurred_at`

#### Scenario: The database refuses an unbacked acceptance moment

- **WHEN** a receipt row is written with `calibration_notice_accepted_at` set and `calibration_notice_outcome` anything other than `delivered`, null included
- **THEN** the write SHALL be rejected by a CHECK constraint

#### Scenario: Abandoned receipt is swept

- **WHEN** a receipt has been non-terminal for longer than the abandonment timeout
- **AND** a curriculum request is submitted or a receipt status is read
- **THEN** the abandoned receipt SHALL settle `status = failed` with `failure_reason = "timed_out"`
- **AND** a new curriculum request SHALL be accepted




#### Scenario: Modern delivered notice needs no historical session

- **WHEN** the receipt's actual request/goal/invocation/notify-dispatch binding resolves a readable delivered notify ledger row and historical session_id is null
- **THEN** calibration_notice_outcome is delivered and calibration_notice_accepted_at is that exact row's occurred_at, with no fabricated session identity or unproven classification.

#### Scenario: Modern absent notice needs no historical session

- **WHEN** the actual modern notify-dispatch binding is usable, the ledger is readable, no matching row exists and historical session_id is null
- **THEN** calibration_notice_outcome is no_record and its acceptance timestamp is null, without treating the missing historical session as a failed or unproven notice.

#### Scenario: A historical session does not repair missing modern correlation

- **WHEN** a historical session_id exists but the modern request/goal/invocation/notify-dispatch binding is missing or unusable
- **THEN** notice evidence remains unproven and no historical-session lookup, invented alias or teaching-flow state produces a delivery claim.

#### Scenario: An unreadable duplicate settle preserves prior delivered evidence

- **WHEN** a receipt already records delivered with its actual ledger moment and a later or duplicate settlement cannot read usable notice evidence
- **THEN** the original terminal outcome and both notice columns remain unchanged as one paired unit.
### Requirement: Region education Curriculum request status read

The system SHALL expose `GET /api/education/curriculum-requests/{request_id}` and `GET /api/education/curriculum-requests/latest`, both returning `{"receipts_available": <bool>, "receipt": <receipt|null>}`.
`receipts_available: false` SHALL mean the receipt store could not be read (for example, the education migration chain is absent). Callers SHALL render that as status unavailable, never as "no request in flight".
`receipts_available: true` with `receipt: null` SHALL mean the store is readable and no matching request exists.
`GET /curriculum-requests/{request_id}` SHALL return 404 for an unknown request ID and 422 for a malformed one. Both reads SHALL be read-only with respect to request outcomes; the only write they perform is the abandonment sweep.
The existing education request/schema/attention-ledger owner SHALL retain the domain receipt authority and all database constraints. In the future, the request's submitted goal text remains text, distinct from nullable recorded runtime goal_id/invocation_id. Historical session_id SHALL remain historical and may be populated only from an actual historical producer receipt. Future notification lookup SHALL use the actual producer's stored dispatch correlation; it SHALL NOT put an invocation UUID in session_id or guess a delivered notice from goal completion. Adding that correlation and replacing the unit trigger is an exact education/backend amendment held by its current owner before M6; until adopted, the full current producer and receipt contracts remain authoritative. All old request IDs, topic 200/goal 500 limits, one-open index, accepted-before-work, 409/422/503 distinctions, stable trigger_unreachable/session_error/no_curriculum_created/timed_out codes, first-terminal-wins, every-submit/read abandonment sweep, and notice evidence/atomic CHECK rules remain. The Region placement confers no extra schema, notify, retry or teaching authority.
The modern receipt SHALL carry a server-stored binding from immutable request_id to its producing goal_id, fresh eligible invocation_id and the exact calibration notify_dispatch_id. The notify-dispatch identity is the actual durable notification intent/dispatch identity, not a UUID alias for a session, goal or invocation. A matching ledger row SHALL match that binding and source="notify"; unrelated notices from the same goal SHALL NOT qualify the calibration receipt. Neither model text, submitted goal text, channel/thread identity, a map's diagnosing state nor a historical session field may supply this binding.
A readable matched delivered row SHALL prove delivered and its ledger occurred_at even when historical session_id is null. A readable valid modern binding with no row SHALL prove no_record even when historical session_id is null. Missing/unusable modern correlation or an unreadable ledger SHALL be unproven, independently of whether a historical session exists. A first settlement with no prior notice evidence may record that unproven classification with a null acceptance moment; a later/duplicate settlement lacking usable evidence SHALL leave both existing notice columns untouched. The paired write/CHECK, first-terminal-wins and every-submit/read abandonment sweep remain authoritative; no lookup failure or absent historical field may overwrite an existing delivered receipt.
This is the complete proposed modern projection/settlement contract. Adding actual correlation fields/indexes, choosing the exact existing notify intent key and replacing the legacy producer require the current education/schema/notify owner's exact amendment before M6. No backend schema change or public endpoint is implemented or allocated by this design.

ID: REQ-dashboard-goal-education-003
Source: openspec/specs/dashboard-education-api/spec.md; RFC 0038 D11/D19; design.md
Scope: post-v1

#### Scenario: Read a terminal receipt

- **WHEN** a GET request is made to `/api/education/curriculum-requests/{request_id}` for a completed request
- **THEN** the response status SHALL be 200
- **AND** the body SHALL contain `receipts_available: true` and a receipt with `status: "completed"`, `goal_id`, `invocation_id`, `notify_dispatch_id`, nullable historical `session_id`, `mind_map_id`, and `settled_at`

#### Scenario: Read a failed receipt

- **WHEN** a GET request is made for a failed request
- **THEN** the receipt SHALL carry the terminal `failure_reason`

#### Scenario: Unknown request ID

- **WHEN** a GET request is made for a request ID that does not exist
- **THEN** the response status SHALL be 404

#### Scenario: Malformed request ID

- **WHEN** a GET request is made with a path segment that is not a UUID
- **THEN** the response status SHALL be 422

#### Scenario: Receipt store unavailable

- **WHEN** `education.curriculum_requests` does not exist
- **THEN** the response status SHALL be 200
- **AND** the body SHALL contain `receipts_available: false` and `receipt: null`

#### Scenario: No request has ever been made

- **WHEN** a GET request is made to `/api/education/curriculum-requests/latest`
- **AND** the store is readable and empty
- **THEN** the body SHALL contain `receipts_available: true` and `receipt: null`




#### Scenario: Read a modern completed receipt without a historical session

- **WHEN** a completed receipt has stored goal/invocation/notify-dispatch evidence and historical session_id is null
- **THEN** the read returns the actual modern evidence and independently nullable historical field without rejecting or relabeling a delivered or no_record outcome.
### Requirement: Region education Curriculum request outcome receipt

After a curriculum request is accepted, the Education page SHALL render a receipt panel for the tracked request, fed by `GET /curriculum-requests/{request_id}` (falling back to `GET /curriculum-requests/latest` when no request has been submitted in this session).
The panel SHALL render four distinct states and SHALL NOT collapse any of them into another:
- **accepted / running** — the request was accepted and work is in flight. The panel SHALL say so and SHALL NOT claim the curriculum exists or that the owner has been contacted.
- **completed** — the receipt carries terminal evidence. The panel SHALL name the curriculum topic and SHALL distinguish `calibration_ready_at` being set (the teaching flow has started calibrating) from it being unset, rather than implying calibration in both cases. The panel SHALL state what became of the calibration notice as a line separate from calibration, so that a reader cannot take calibration having started as evidence of having been messaged.
- **failed** — the panel SHALL render the terminal `failure_reason` in owner-readable language and SHALL offer a retry.
- **unavailable** — when `receipts_available` is `false`, the panel SHALL say the status could not be read, and SHALL NOT render an all-clear or an empty "no request" state.
The panel SHALL claim that a message went out only from `calibration_notice_outcome`, and only for the value `delivered`. That claim SHALL be worded as the delivery channel having accepted the message, because that is the strongest thing the notification path attests; the panel SHALL NOT state or imply that the owner received or read anything.
For every other outcome — a recorded `failed`, `suppressed`, `deferred` or `coalesced` dispatch, a `no_record` absence, an `unproven` read, a null outcome, or an outcome the frontend does not recognise — the panel SHALL say that contact is not confirmed, and SHALL direct the owner to this panel rather than to their messages. An unrecognised outcome SHALL degrade to "not confirmed" and never to an implied yes, so a new backend outcome cannot silently become a delivery claim in the UI.
The panel SHALL provide doors to the evidence it names: a goal/invocation door only for an actual stored modern binding, a separately labeled historical session link (`/sessions/{session_id}`) only when a real historical producer supplied session_id (including on a failure path), and a control that opens the correlated curriculum whenever mind_map_id is present.
While the receipt is non-terminal the query SHALL poll; once terminal it SHALL stop polling.
In fallback mode (no request submitted in this session, so the panel reads the latest request), a receipt that settled longer than the recency window ago SHALL NOT be rendered — otherwise a curriculum created weeks ago would keep a permanent card on the page. A request tracked by `request_id` from this session's 202 SHALL always be rendered, however long ago it settled.
The panel SHALL be announced to assistive technology as a live status region, and every door SHALL be a keyboard-reachable control with an accessible name.
The existing education request/schema/attention-ledger owner SHALL retain the domain receipt authority and all database constraints. In the future, the request's submitted goal text remains text, distinct from nullable recorded runtime goal_id/invocation_id. Historical session_id SHALL remain historical and may be populated only from an actual historical producer receipt. Future notification lookup SHALL use the actual producer's stored dispatch correlation; it SHALL NOT put an invocation UUID in session_id or guess a delivered notice from goal completion. Adding that correlation and replacing the unit trigger is an exact education/backend amendment held by its current owner before M6; until adopted, the full current producer and receipt contracts remain authoritative. All old request IDs, topic 200/goal 500 limits, one-open index, accepted-before-work, 409/422/503 distinctions, stable trigger_unreachable/session_error/no_curriculum_created/timed_out codes, first-terminal-wins, every-submit/read abandonment sweep, and notice evidence/atomic CHECK rules remain. The Region placement confers no extra schema, notify, retry or teaching authority.
The modern receipt SHALL carry a server-stored binding from immutable request_id to its producing goal_id, fresh eligible invocation_id and the exact calibration notify_dispatch_id. The notify-dispatch identity is the actual durable notification intent/dispatch identity, not a UUID alias for a session, goal or invocation. A matching ledger row SHALL match that binding and source="notify"; unrelated notices from the same goal SHALL NOT qualify the calibration receipt. Neither model text, submitted goal text, channel/thread identity, a map's diagnosing state nor a historical session field may supply this binding.
A readable matched delivered row SHALL prove delivered and its ledger occurred_at even when historical session_id is null. A readable valid modern binding with no row SHALL prove no_record even when historical session_id is null. Missing/unusable modern correlation or an unreadable ledger SHALL be unproven, independently of whether a historical session exists. A first settlement with no prior notice evidence may record that unproven classification with a null acceptance moment; a later/duplicate settlement lacking usable evidence SHALL leave both existing notice columns untouched. The paired write/CHECK, first-terminal-wins and every-submit/read abandonment sweep remain authoritative; no lookup failure or absent historical field may overwrite an existing delivered receipt.
This is the complete proposed modern projection/settlement contract. Adding actual correlation fields/indexes, choosing the exact existing notify intent key and replacing the legacy producer require the current education/schema/notify owner's exact amendment before M6. No backend schema change or public endpoint is implemented or allocated by this design.

ID: REQ-dashboard-goal-education-004
Source: openspec/specs/dashboard-education-ui/spec.md; RFC 0038 D11/D19; design.md
Scope: post-v1

#### Scenario: Accepted request does not claim completion

- **WHEN** the tracked receipt has status `accepted`
- **THEN** the panel SHALL describe the request as accepted and in progress
- **AND** SHALL NOT state that a curriculum was created or that the assistant has contacted the owner

#### Scenario: Completed request opens its doors

- **WHEN** the tracked receipt has status `completed` with a mind_map_id and an actual goal_id/invocation_id, while historical session_id may be null
- **THEN** the panel SHALL offer a control that selects that curriculum
- **AND** SHALL offer the recorded goal/invocation doors without fabricating a historical session link
- **AND** a separately supplied actual historical session_id retains its historical session door

#### Scenario: Calibration started does not become "the butler messaged you"

- **WHEN** the tracked receipt has status `completed` with `calibration_ready_at` set
- **AND** `calibration_notice_outcome` is `failed`, `suppressed`, `no_record`, `unproven`, null, or a value the frontend does not recognise
- **THEN** the panel SHALL state that contact is not confirmed and point the owner at this panel
- **AND** SHALL NOT state that a message reached the owner
- **AND** SHALL still state that calibration has started

#### Scenario: Channel acceptance is stated as channel acceptance

- **WHEN** the tracked receipt carries `calibration_notice_outcome: "delivered"`
- **THEN** the panel SHALL say the owner's messaging channel accepted the assistant's starting message
- **AND** SHALL NOT describe it as read, seen, or received by the owner

#### Scenario: Failed request is legible and retryable

- **WHEN** the tracked receipt has status `failed` with `failure_reason: "trigger_unreachable"`
- **THEN** the panel SHALL explain that the admitted producer could not be reached
- **AND** SHALL offer a retry control
- **AND** SHALL still link the session when `session_id` is present

#### Scenario: Unavailable status is not an all-clear

- **WHEN** the status read returns `receipts_available: false`
- **THEN** the panel SHALL state that the request status could not be read
- **AND** SHALL NOT render a success, a failure, or an empty state

#### Scenario: Polling stops at a terminal state

- **WHEN** the tracked receipt reaches `completed` or `failed`
- **THEN** the receipt query SHALL stop refetching

#### Scenario: A long-settled fallback receipt is not parked on the page

- **WHEN** no request has been submitted in this session
- **AND** the latest receipt settled longer than the recency window ago
- **THEN** the panel SHALL render nothing

#### Scenario: A request tracked in this session stays visible

- **WHEN** a request submitted in this session settled longer than the recency window ago
- **THEN** the panel SHALL still render its terminal outcome




#### Scenario: Modern receipt navigation never invents a legacy session

- **WHEN** a completed modern curriculum has actual goal/invocation/map bindings and a delivered or no_record notice with historical session_id null
- **THEN** the panel exposes actual goal/invocation/map doors, applies the same delivered-only channel-acceptance rule, and renders no historical session door.
### Requirement: Region education Education Region Reviews workspace

In addition to the standalone `/education` page, the Education Region page SHALL provide a resident Reviews workspace, linked from its learning records, that aggregates learning state across all active mind maps into a single multi-panel dashboard.
The Reviews workspace SHALL display:
1. A KPI summary row: total review cards, mastered count, overdue count, and average mastery score.
2. A mind maps progress panel: per-map mastery percentage with a progress bar and mastered/total node count.
3. A pending reviews timeline grouped by Overdue / Today / This Week / Later, with colored left borders per group.
4. A frontier nodes panel: the next concepts to learn, each with a mastery badge.
5. A 7-day retention trend chart with the latest mastery percentage as a headline value and a hover tooltip.
Polling intervals SHALL scale with the number of active mind maps so that total request volume stays bounded as the map count grows (base interval 15 seconds for reviews, 30 seconds for mastery and frontier data).
The existing education request/schema/attention-ledger owner SHALL retain the domain receipt authority and all database constraints. In the future, the request's submitted goal text remains text, distinct from nullable recorded runtime goal_id/invocation_id. Historical session_id SHALL remain historical and may be populated only from an actual historical producer receipt. Future notification lookup SHALL use the actual producer's stored dispatch correlation; it SHALL NOT put an invocation UUID in session_id or guess a delivered notice from goal completion. Adding that correlation and replacing the unit trigger is an exact education/backend amendment held by its current owner before M6; until adopted, the full current producer and receipt contracts remain authoritative. All old request IDs, topic 200/goal 500 limits, one-open index, accepted-before-work, 409/422/503 distinctions, stable trigger_unreachable/session_error/no_curriculum_created/timed_out codes, first-terminal-wins, every-submit/read abandonment sweep, and notice evidence/atomic CHECK rules remain. The Region placement confers no extra schema, notify, retry or teaching authority.
The modern receipt SHALL carry a server-stored binding from immutable request_id to its producing goal_id, fresh eligible invocation_id and the exact calibration notify_dispatch_id. The notify-dispatch identity is the actual durable notification intent/dispatch identity, not a UUID alias for a session, goal or invocation. A matching ledger row SHALL match that binding and source="notify"; unrelated notices from the same goal SHALL NOT qualify the calibration receipt. Neither model text, submitted goal text, channel/thread identity, a map's diagnosing state nor a historical session field may supply this binding.
A readable matched delivered row SHALL prove delivered and its ledger occurred_at even when historical session_id is null. A readable valid modern binding with no row SHALL prove no_record even when historical session_id is null. Missing/unusable modern correlation or an unreadable ledger SHALL be unproven, independently of whether a historical session exists. A first settlement with no prior notice evidence may record that unproven classification with a null acceptance moment; a later/duplicate settlement lacking usable evidence SHALL leave both existing notice columns untouched. The paired write/CHECK, first-terminal-wins and every-submit/read abandonment sweep remain authoritative; no lookup failure or absent historical field may overwrite an existing delivered receipt.
This is the complete proposed modern projection/settlement contract. Adding actual correlation fields/indexes, choosing the exact existing notify intent key and replacing the legacy producer require the current education/schema/notify owner's exact amendment before M6. No backend schema change or public endpoint is implemented or allocated by this design.

ID: REQ-dashboard-goal-education-005
Source: openspec/specs/dashboard-education-ui/spec.md; RFC 0038 D11/D19; design.md
Scope: post-v1

#### Scenario: Reviews tab aggregates across maps

- **WHEN** the Education Region Reviews workspace loads with multiple active mind maps
- **THEN** the KPI row, mind maps progress panel, pending reviews timeline, frontier panel, and retention trend chart SHALL each render with data aggregated across those maps
- **AND** the pending reviews SHALL be grouped into Overdue, Today, This Week, and Later sections

## Source References

- Non-Negotiable Rules 1, 3, 4 and 6 in about/heart-and-soul/vision.md.
- RFC 0038 D19 and owner decision 11; further exact decisions and current source boundaries remain the per-requirement Source fields.
- Requirement-level source: openspec/specs/dashboard-education-api/spec.md; RFC 0038 D11/D19; design.md
- Requirement-level source: openspec/specs/dashboard-education-ui/spec.md; RFC 0038 D11/D19; design.md
