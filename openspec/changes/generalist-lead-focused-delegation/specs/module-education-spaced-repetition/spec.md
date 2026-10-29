## MODIFIED Requirements

### Requirement: Schedule Creation via Core Scheduler

After computing the new SM-2 state, `spaced_repetition_record_response()` SHALL create a one-shot review schedule through the core scheduler's internal API, not through the model-reachable `schedule_create` tool. The schedule SHALL be an occurrence of the education review obligation declared in git, whose capability set is fixed in that declaration; recording a response SHALL NOT create a new obligation, widen that set, or create a prompt-mode schedule. When due, the schedule SHALL submit an obligation admission to the control plane instead of spawning a session. An occurrence whose schedule was written under an exposed assertion SHALL be admitted exposed. The cron expression encodes the exact target review datetime (minute and hour resolution). The schedule's `until_at` is set to `next_review_at + 24 hours` so that if the review window is missed, the schedule auto-disables without firing.

Source: RFC 0038 D2, D5, D6
Scope: v1-mandatory

#### Scenario: One-shot cron computed from next review datetime

- **WHEN** `spaced_repetition_record_response()` is called and the next review datetime is computed as `2026-03-05 14:30 UTC`
- **THEN** a one-shot review schedule is created with `cron="30 14 5 3 *"` as an occurrence of the git-declared education review obligation
- **AND** it is not a `dispatch_mode="prompt"` schedule and spawns no session when due

#### Scenario: until_at set to next_review_at plus 24 hours

- **WHEN** `spaced_repetition_record_response()` computes `next_review_at = 2026-03-05T14:30:00Z`
- **THEN** the review schedule is created with `until_at=2026-03-06T14:30:00Z` (next_review_at + 24 hours)
- **AND** if the scheduled task fires after `until_at`, the scheduler auto-disables it without submitting an admission

#### Scenario: Prompt includes enough context for the ephemeral session

- **WHEN** a review schedule becomes due
- **THEN** the obligation admission's goal input includes the `node_id`, `mind_map_id`, `node.label`, the upcoming repetition number, and the current `ease_factor`
- **AND** the goal input asks the lead to conduct a spaced repetition review quiz for the named concept
- **AND** `node.label` is carried as stamped data, so a tainted label exposes the goal

#### Scenario: Missed review window auto-disables

- **WHEN** the review schedule fires at a time after `until_at`
- **THEN** the core scheduler sets the task to `enabled=false` and `next_run_at=NULL` without dispatching
- **AND** the node's `next_review_at` in `mind_map_nodes` remains set until the next manual or rescheduled review

### Requirement: Review Delivery via notify()

Review prompts SHALL be delivered to the owner via the `notify()` capability, targeting the owner's preferred channel. The education butler does not hold direct Telegram or email credentials — every outbound message goes through Switchboard `deliver()` to Messenger's deterministic delivery endpoint. A review goal SHALL NOT wait inside a session for the owner's answer: the answer SHALL be admitted as an owner-origin goal that records the review goal as its cause, so the answer is graded against that review (the reply-join mechanism is settled during the dark build, RFC 0038 "Details settled during the dark build"), and failures SHALL be recorded in the goal ledger.

Source: RFC 0038 D2, D5, D7, D17
Scope: v1-mandatory

#### Scenario: Scheduled review session dispatches a notify call

- **WHEN** a review obligation occurrence is admitted and its lead runs
- **THEN** the lead calls `notify(channel=<user_preferred_channel>, message=<quiz_content>, intent="send")`
- **AND** the quiz content includes the concept being reviewed and one or more questions

#### Scenario: Delivery channel defaults to the owner contact's preferred channel

- **WHEN** the review prompt is constructed
- **THEN** the session resolves the user's preferred channel from `public.contact_info` for the owner contact
- **AND** if no explicit preference is set, `channel="telegram"` is used as the default

#### Scenario: Batch review session notify includes count of pending nodes

- **WHEN** a batch review session fires (triggered by the `review-{mind_map_id}-batch` schedule)
- **THEN** the session calls `notify()` with a message that states how many nodes are due for review
- **AND** the session interleaves questions for multiple nodes within the session

#### Scenario: Review response from user is captured as a quiz_responses row

- **WHEN** the owner replies to a review quiz question
- **THEN** the reply is admitted as an owner goal whose lead, or a depth-1 child it briefs, evaluates the answer and calls `spaced_repetition_record_response(pool, node_id, quality)` with the evaluated quality score
- **AND** a new row is inserted into `quiz_responses` with `response_type='review'`, `quality`, `question_text`, `user_answer`, and `responded_at`

#### Scenario: notify() failure does not corrupt node state

- **WHEN** the review goal fails to deliver the review notification (e.g., Messenger delivery unreachable)
- **THEN** `spaced_repetition_record_response()` has not yet been called (delivery precedes quality recording)
- **AND** the node's `next_review_at` remains set from the prior schedule
- **AND** the failure is recorded as the goal's outcome in the goal ledger

#### Scenario: Review prompt instructs the session to evaluate user answer and call record_response

- **WHEN** a review obligation occurrence is admitted
- **THEN** its goal input directs the lead to ask the quiz question via `notify` and complete the goal without waiting for an answer
- **AND** evaluating the owner's later answer on a 0-5 scale and calling `spaced_repetition_record_response()` happen in the owner goal admitted from that answer
