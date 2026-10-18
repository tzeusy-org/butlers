## MODIFIED Requirements

### Requirement: Schedule Creation via Core Scheduler

The card reconciler (see "Review card schedule reconciliation"), not the response-recording path, SHALL create the one-shot review schedule for a review card via `schedule_create()`, so that a scheduler failure can never undo recorded learning evidence. The cron expression encodes the exact target review datetime of the card's `scheduled_for` (minute and hour resolution). The schedule's `until_at` is set to `scheduled_for + 24 hours` so that if the review window is missed, the schedule auto-disables without firing.

ID: REQ-module-education-spaced-repetition-005
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: One-shot cron computed from next review datetime

- **WHEN** the reconciler materializes a card whose `scheduled_for` is `2026-03-05 14:30 UTC`
- **THEN** `schedule_create()` is called with `cron="30 14 5 3 *"`
- **AND** `dispatch_mode="prompt"`

#### Scenario: until_at set to next_review_at plus 24 hours

- **WHEN** the reconciler materializes a card whose `scheduled_for` is `2026-03-05T14:30:00Z`
- **THEN** `schedule_create()` is called with `until_at=2026-03-06T14:30:00Z` (scheduled_for + 24 hours)
- **AND** if the scheduled task fires after `until_at`, the scheduler auto-disables it without dispatching

#### Scenario: Prompt includes enough context for the ephemeral session

- **WHEN** `schedule_create()` is called for a review schedule
- **THEN** the `prompt` argument includes the `review_card_id`, `node_id`, `mind_map_id`, `node.label`, the upcoming repetition number, and the current `ease_factor`
- **AND** the prompt instructs the session to conduct a spaced repetition review quiz for the named concept

#### Scenario: Missed review window auto-disables

- **WHEN** the review schedule fires at a time after `until_at`
- **THEN** the core scheduler sets the task to `enabled=false` and `next_run_at=NULL` without dispatching
- **AND** the node's `next_review_at` in `mind_map_nodes` remains set until the next manual or rescheduled review

### Requirement: Schedule Naming Convention

Each review schedule SHALL be named `review-card-{card_id}`, where `card_id` is the UUID of the review card it serves. The name is stable and derived from the card alone, so that a schedule is unique per card, replaying reconciliation can detect an existing schedule by name, and a stale schedule is identifiable by its card.

ID: REQ-module-education-spaced-repetition-006
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Schedule name encodes node and repetition

- **WHEN** the reconciler materializes the card with id `abc123` (node repetition `3`)
- **THEN** `schedule_create()` is called with `name="review-card-abc123"`

#### Scenario: Prior-cycle schedule is superseded on new record

- **WHEN** a node's open card is graded and a successor card is created
- **THEN** the successor's schedule is named `review-card-{successor_id}`
- **AND** the graded card's schedule is deleted (or was already auto-disabled by its `until_at`)

#### Scenario: Failed recall resets repetition counter in schedule name

- **WHEN** a failed owner-evidenced recall (`quality=1`) resets a node that had `repetitions=5`
- **THEN** the successor card records `repetition = 0`
- **AND** its schedule is named `review-card-{successor_id}`, not by repetition count

#### Scenario: Duplicate schedule name rejected by scheduler

- **WHEN** `schedule_create()` is called with a name that already exists in `scheduled_tasks`
- **THEN** the core scheduler raises a `ValueError`
- **AND** the reconciler treats the existing schedule as the card's schedule and creates no second one

### Requirement: Batch Review Cap — Maximum 20 Pending Schedules Per Mind Map

To prevent schedule proliferation, no mind map SHALL have more than 20 pending review schedules active simultaneously. If the cap would be exceeded, the card reconciler checks the current count before calling `schedule_create()`. When pending reviews exceed 20, all overdue cards are batched into a single "review session" schedule rather than creating individual schedules. A card covered by a batch session remains a durable card and remains pending until it is answered or expires; batching changes how it is scheduled, never whether it exists.

ID: REQ-module-education-spaced-repetition-007
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Schedule created when under cap

- **WHEN** the reconciler runs for a mind map with 15 pending review schedules and one unscheduled card
- **THEN** an individual review schedule for the card is created normally
- **AND** the pending schedule count for the mind map reaches 16

#### Scenario: Individual schedule blocked when cap is reached

- **WHEN** the reconciler runs for a mind map that already has 20 pending review schedules and an unscheduled card
- **THEN** no individual schedule is created for the new card
- **AND** instead, a single batch "review session" schedule is created (or the existing batch schedule is updated) covering all overdue cards

#### Scenario: Batch schedule prompt covers all overdue nodes

- **WHEN** a batch review session schedule is created due to cap overflow
- **THEN** the `prompt` includes the `mind_map_id` and a directive to conduct a review session for all pending nodes
- **AND** the schedule name is `review-{mind_map_id}-batch`

#### Scenario: Batch schedule has 24-hour until_at window

- **WHEN** a batch review session schedule is created
- **THEN** `until_at` is set to the batch `next_review_at + 24 hours`

#### Scenario: `spaced_repetition_pending_reviews` returns only overdue nodes

- **WHEN** `spaced_repetition_pending_reviews(pool, mind_map_id)` is called
- **THEN** it returns a list of dicts for all open review cards of that mind map where `scheduled_for <= now()`
- **AND** nodes without an open card, and cards with `scheduled_for > now()`, are excluded
- **AND** each dict includes `review_card_id`, `node_id`, `label`, `state`, `scheduled_for`, `ease_factor`, `repetitions`, `next_review_at`, and `mastery_status`

#### Scenario: Pending count is computed from scheduler, not from node table alone

- **WHEN** counting pending review schedules for the cap check
- **THEN** the count is the number of active (enabled=true) rows in `scheduled_tasks` whose names are `review-card-{card_id}` for cards belonging to `mind_map_id`
- **AND** the batch schedule `review-{mind_map_id}-batch` counts as one schedule regardless of how many nodes it covers

### Requirement: Schedule Cleanup on Mind Map Completion or Abandonment

When a mind map transitions to `status='completed'` or `status='abandoned'`, all pending review schedules for its nodes SHALL be removed. This prevents stale review prompts from firing after the user has finished or given up on a topic. Each open review card of the map SHALL be marked `expired` in the same step, without altering any answered or graded card, response or ledger entry.

ID: REQ-module-education-spaced-repetition-008
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Cleanup removes all node review schedules on completion

- **WHEN** `spaced_repetition_schedule_cleanup(pool, mind_map_id)` is called and the mind map status is `completed`
- **THEN** all `scheduled_tasks` rows named `review-card-{card_id}` for cards of that mind map are deleted
- **AND** each open card of the mind map becomes `expired`
- **AND** the batch schedule `review-{mind_map_id}-batch` (if present) is also deleted
- **AND** the function returns the count of deleted schedules

#### Scenario: Cleanup removes all node review schedules on abandonment

- **WHEN** `spaced_repetition_schedule_cleanup(pool, mind_map_id)` is called and the mind map status is `abandoned`
- **THEN** all pending review schedules for that mind map's nodes are deleted (same behavior as completion)

#### Scenario: Cleanup is idempotent

- **WHEN** `spaced_repetition_schedule_cleanup(pool, mind_map_id)` is called twice for the same mind map
- **THEN** the second call returns `0` (no schedules remain to delete) without raising an error

#### Scenario: Cleanup does not affect other mind maps

- **WHEN** `spaced_repetition_schedule_cleanup(pool, mind_map_id=A)` is called
- **THEN** review schedules for nodes belonging to mind map B are unaffected

#### Scenario: Cleanup called on active mind map is a no-op

- **WHEN** `spaced_repetition_schedule_cleanup(pool, mind_map_id)` is called and the mind map status is `active`
- **THEN** the function returns `0` and no schedules are deleted
- **AND** a warning is logged indicating the mind map is still active


### Requirement: Node State Updates and Mastery Status Transitions

The SM-2 state of a node in `mind_map_nodes` (`ease_factor`, `repetitions`, `next_review_at`, `last_reviewed_at`) and its `mastery_status` SHALL be updated only inside the shared evidence transaction that `mastery_record_response()` runs for an owner-evidenced response (module-education-mastery "One public evidence writer"). The SM-2 calculation itself MAY remain a pure internal function, but `spaced_repetition_record_response()` SHALL NOT be a second public writer of node state, and a response that fails the owner-evidence gate SHALL NOT advance any SM-2 field. Within that transaction a failed recall demotes the node's `mastery_status` as the mastery state machine specifies (`reviewing` to `learning`, `mastered` to `reviewing`) and appends the corresponding ledger entry; forward promotions are specified in `module-education-mastery`, not here. A successful recall advances scheduling state (`repetitions`, `interval`, `next_review_at`) but leaves `mastery_status` unchanged unless the mastery module's thresholds promote it.

ID: REQ-module-education-spaced-repetition-001
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Successful recall leaves mastery_status unchanged

- **WHEN** an owner-evidenced response with `quality >= 3` is applied to a node with `mastery_status='reviewing'` below the mastery threshold
- **THEN** `mastery_status` remains `'reviewing'`
- **AND** `repetitions` is incremented by 1
- **AND** `next_review_at` and `last_reviewed_at` are updated for the next interval

#### Scenario: Failed recall demotes a reviewing node back to learning

- **WHEN** an owner-evidenced response with `quality < 3` is applied to a node with `mastery_status='reviewing'`
- **THEN** `mastery_status` is updated to `'learning'`
- **AND** `repetitions` is reset to `0`
- **AND** `next_review_at` is set to `now() + 0.25 day` (6 hours)

#### Scenario: Failed recall demotes a mastered node to reviewing

- **WHEN** an owner-evidenced response with `quality < 3` is applied to a node with `mastery_status='mastered'`
- **THEN** `mastery_status` is updated to `'reviewing'`
- **AND** `repetitions` is reset to `0`

#### Scenario: Node state update is atomic

- **WHEN** an owner-evidenced response updates a node
- **THEN** all of `ease_factor`, `repetitions`, `next_review_at`, `last_reviewed_at`, and `mastery_status` are written in a single database transaction together with the `quiz_responses` row, any ledger entry, and the successor review card
- **AND** if any of those writes fails, the transaction is rolled back and the node retains its prior state

#### Scenario: Scheduler failure does not roll back learning evidence

- **WHEN** the transaction has committed and the later materialization of the successor card's one-shot schedule fails
- **THEN** the node state, the `quiz_responses` row and the ledger entry SHALL remain committed
- **AND** the review card SHALL remain unscheduled and retryable

#### Scenario: A response without owner evidence advances nothing

- **WHEN** a response is rejected by the owner-evidence gate
- **THEN** no SM-2 field and no `updated_at` on the node SHALL change

#### Scenario: `updated_at` timestamp is refreshed on every state update

- **WHEN** an owner-evidenced response is applied to a node
- **THEN** the node's `updated_at` column is set to `now()`

### Requirement: Review Delivery via notify()

Review prompts SHALL be delivered to the user via the `notify()` core tool, without naming a transport. The education butler does not hold direct Telegram or email credentials — it routes all outbound messages through the Switchboard to the Messenger butler, which chooses the channel from the owner's posture. No review schedule, review prompt, daily nudge, or review-related skill instruction SHALL hardcode a channel such as Telegram.

Every review prompt SHALL carry the `review_card_id` of the card it asks about. The delivery outcome recorded on the card (`notice_outcome`, `notice_accepted_at`) SHALL be copied from `public.attention_ledger`'s own record of the dispatch. An absent, unreadable, failed, suppressed or deferred ledger record SHALL be preserved as that exact outcome and SHALL NOT be upgraded to "delivered". Channel acceptance proves only that a channel accepted the message; it SHALL NOT be read as the owner having seen, read or answered it.

ID: REQ-module-education-spaced-repetition-004
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Scheduled review session dispatches a notify call

- **WHEN** a review schedule fires and the ephemeral session runs
- **THEN** the session calls `notify(message=<quiz_content>, intent="send")` without a `channel` argument
- **AND** the quiz content includes the concept being reviewed and one or more questions
- **AND** the prompt carries the `review_card_id`

#### Scenario: Delivery channel defaults to the owner contact's preferred channel

- **WHEN** the review prompt is constructed
- **THEN** it SHALL NOT name a channel
- **AND** channel selection SHALL be left to the Messenger butler's posture for the owner

#### Scenario: Delivery outcome mirrors the attention ledger

- **WHEN** the attention ledger records `outcome = "suppressed"` for a review notify
- **THEN** the review card's `notice_outcome` SHALL be `suppressed` and `notice_accepted_at` SHALL be unset

#### Scenario: A missing ledger record is not delivery

- **WHEN** the ledger holds no row for the review notify, or cannot be read
- **THEN** the card SHALL record `no_record` or `unproven` respectively
- **AND** no surface SHALL describe the review as delivered

#### Scenario: Batch review session notify includes count of pending nodes

- **WHEN** a batch review session fires (triggered by the `review-{mind_map_id}-batch` schedule)
- **THEN** the session calls `notify()` with a message that states how many nodes are due for review
- **AND** the session interleaves questions for multiple nodes within the session

#### Scenario: Review response from user is captured as a quiz_responses row

- **WHEN** the user responds to a review quiz question during an ephemeral session
- **THEN** the session calls `mastery_record_response()` with the owner's answer and the evaluated quality score, as the single public evidence writer
- **AND** a new row is inserted into `quiz_responses` with `response_type='review'`, `quality`, `question_text`, `user_answer`, and `responded_at`
- **AND** the session does not also call a spaced-repetition writer for the same answer

#### Scenario: notify() failure does not corrupt node state

- **WHEN** the ephemeral session fails to deliver the review notification (e.g., Messenger butler unreachable)
- **THEN** no response has been recorded (delivery precedes quality recording)
- **AND** the card's recorded delivery outcome SHALL reflect the failure and SHALL NOT claim delivery
- **AND** the node's `next_review_at` remains set from the prior schedule
- **AND** the failed session logs the error to the session log

#### Scenario: Review prompt instructs the session to evaluate user answer and call record_response

- **WHEN** the scheduler fires the review schedule and spawns an ephemeral session
- **THEN** the session prompt includes an explicit directive to: (1) ask the quiz question via notify, (2) await user response (via a subsequent trigger), (3) evaluate the owner's answer on a 0-5 scale, and (4) call `mastery_record_response()` with the owner's answer and the quality score

## ADDED Requirements

### Requirement: Review cards are the pending-review authority

The system SHALL persist each review as a durable `education.review_cards` row. A card SHALL carry `id`, `node_id`, `mind_map_id`, `scheduled_for`, `repetition`, `state` (`scheduled`, `asked`, `delivered`, `answered`, `graded`, `expired` or `superseded`), `schedule_name`, `question_text`, `asked_session_id`, `notice_outcome`, `notice_accepted_at`, `answer_evidence_kind`, `answer_evidence_ref`, `user_answer`, `quality`, `grader_model`, `graded_at`, `quiz_response_id`, `superseded_by`, and timestamps.

A review card, not a node's `next_review_at`, a `scheduled_tasks` row, or a scheduler row count, SHALL be the source of truth for what is pending. The presence of a schedule SHALL NOT be taken as proof that a question existed, was delivered, was answered or was graded. `spaced_repetition_pending_reviews()` and every dashboard review projection SHALL read cards.

At most one card per node SHALL be open (`scheduled`, `asked`, `delivered` or `answered`), enforced by a partial unique constraint. `answer_evidence_ref` and `quiz_response_id` SHALL each be unique where present. A successful owner-evidenced response SHALL, in the same Education-schema transaction as its learning-state mutation, mark the answered card `graded` and create or supersede exactly one successor card for the node's next review.

A card's `state` SHALL advance only on evidence: `delivered` only from the attention ledger's own record, `answered` only from captured owner evidence, `graded` only with a stored `quality` and `quiz_response_id`. Where evidence is absent the state SHALL stay at the strongest state the evidence supports.

ID: REQ-module-education-spaced-repetition-002
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: A node with next_review_at but no card has no pending review

- **WHEN** a node has `next_review_at` in the past and no review card
- **THEN** `spaced_repetition_pending_reviews()` SHALL NOT return it

#### Scenario: A graded response creates exactly one successor card

- **WHEN** an owner-evidenced response is graded for the open card of a node
- **THEN** that card SHALL become `graded`
- **AND** exactly one successor card SHALL exist for the node, open and scheduled for its next SM-2 date

#### Scenario: At most one open card per node

- **WHEN** a second open card is inserted for a node that already has one
- **THEN** the database SHALL reject the insert

#### Scenario: Answer captured but grader failed

- **WHEN** an owner answer is captured and grading fails
- **THEN** the card SHALL be `answered` with null `quality` and null `quiz_response_id`
- **AND** the node's mastery SHALL be unchanged

#### Scenario: Failure before capture invents nothing

- **WHEN** the session fails before an owner answer is captured
- **THEN** the card SHALL remain `asked` or `delivered` as the evidence supports
- **AND** no `user_answer`, `quality` or transition SHALL be recorded

### Requirement: Review card schedule reconciliation

A deterministic standing order defined in the butler's git-held configuration (`roster/education/butler.toml`) and registered through the Education job map SHALL reconcile unscheduled review cards into one-shot schedules named `review-card-{card_id}`. The reconciler SHALL be a job, not a model session. Reconciliation SHALL be idempotent: repeated or concurrent runs SHALL create at most one schedule per card, using the card's uniqueness plus a fenced claim or row lock.

If schedule creation fails, the card SHALL remain durable and unscheduled and a later run SHALL retry it. A scheduler failure SHALL NOT roll back or alter learning evidence (responses, mastery projection, ledger entries).

ID: REQ-module-education-spaced-repetition-003
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: An unscheduled card gets one schedule

- **WHEN** the reconciler runs and a `scheduled` card has no schedule
- **THEN** exactly one schedule named `review-card-{card_id}` SHALL exist afterwards

#### Scenario: Repeated reconciliation is idempotent

- **WHEN** the reconciler runs twice, or two runs overlap, for the same card
- **THEN** at most one schedule SHALL exist for the card

#### Scenario: Retry after failure

- **WHEN** schedule creation fails once and the reconciler runs again
- **THEN** the second run SHALL create the schedule
- **AND** the card, the quiz response and the mastery projection SHALL be unchanged by the failure