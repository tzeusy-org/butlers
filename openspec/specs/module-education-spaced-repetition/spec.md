# Education Butler: Spaced Repetition Engine

## Purpose
Defines the SM-2-inspired spaced repetition engine for the education butler, covering per-node ease factor computation, interval scheduling, one-shot schedule creation via the core scheduler, review delivery via `notify()`, and operational cost controls (pending review cap, batch overflow, cleanup on map completion/abandonment).

## ADDED Requirements

### Requirement: SM-2 Interval Calculation — Successful Recall

When a user successfully recalls a node (quality >= 3), the next review interval is determined by the node's current repetition count following a stepped ramp: repetitions == 0 reviews again after 6 hours (`0.25` days); repetitions == 1 after 12 hours (`0.5` days); repetitions == 2 after 1 day (`1.0`); repetitions == 3 after 6 days (`6.0`). For repetitions >= 4 the interval is `last_interval * ease_factor`, producing exponential spacing thereafter.

#### Scenario: First successful recall (repetitions == 0)

- **WHEN** `spaced_repetition_record_response()` is called with `quality=3` and the node has `repetitions=0`
- **THEN** `interval_days` returned is `0.25` (6 hours)
- **AND** `repetitions` in the returned dict is `1`

#### Scenario: Second successful recall (repetitions == 1)

- **WHEN** `spaced_repetition_record_response()` is called with `quality=4` and the node has `repetitions=1`
- **THEN** `interval_days` returned is `0.5` (12 hours)
- **AND** `repetitions` in the returned dict is `2`

#### Scenario: Third successful recall (repetitions == 2)

- **WHEN** `spaced_repetition_record_response()` is called with `quality=5` and the node has `repetitions=2`
- **THEN** `interval_days` returned is `1.0` (fixed 1-day step at repetitions == 2)
- **AND** `repetitions` in the returned dict is `3`

#### Scenario: Fourth successful recall (repetitions == 3)

- **WHEN** `spaced_repetition_record_response()` is called with `quality=4` and the node has `repetitions=3`
- **THEN** `interval_days` returned is `6.0` (fixed 6-day step at repetitions == 3)
- **AND** `repetitions` in the returned dict is `4`

#### Scenario: Fifth and subsequent successful recall (repetitions >= 4)

- **WHEN** `spaced_repetition_record_response()` is called with `quality=5` and the node has `repetitions=4`, `ease_factor=2.5`, and `last_interval=6.0`
- **THEN** `interval_days` returned is `15.0` (6.0 * 2.5)
- **AND** `repetitions` in the returned dict is `5`

#### Scenario: Interval grows with ease factor over multiple repetitions

- **WHEN** a node undergoes successive successful reviews with constant `ease_factor=2.5` starting from `repetitions=4`, `last_interval=6.0`
- **THEN** for each repetition >= 4 the next `interval_days` is the previous interval multiplied by 2.5
- **AND** intervals form a monotonically increasing sequence: 15, 37.5, 93.75, ...

#### Scenario: Perfect recall (quality == 5)

- **WHEN** `spaced_repetition_record_response()` is called with `quality=5` on a node with `repetitions=0`
- **THEN** `interval_days` is `0.25` (6 hours; the repetitions == 0 step applies regardless of quality when in the 0-state)
- **AND** `repetitions` in the returned dict is `1`

---

### Requirement: Ease Factor Adjustment

After every review, the ease factor is updated using the SM-2 formula:

```
new_ef = max(1.3, old_ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
```

The minimum ease factor is 1.3. Quality values 0-5 are all valid inputs.

#### Scenario: Perfect recall increases ease factor

- **WHEN** `spaced_repetition_record_response()` is called with `quality=5` on a node with `ease_factor=2.5`
- **THEN** the returned `ease_factor` is `2.6` (2.5 + 0.1)

#### Scenario: Good recall (quality == 4) slightly increases ease factor

- **WHEN** `spaced_repetition_record_response()` is called with `quality=4` on a node with `ease_factor=2.5`
- **THEN** the returned `ease_factor` is `2.5` (net delta ≈ 0.0: 0.1 - 1*(0.08 + 1*0.02) = 0.0)

#### Scenario: Marginal pass (quality == 3) decreases ease factor

- **WHEN** `spaced_repetition_record_response()` is called with `quality=3` on a node with `ease_factor=2.5`
- **THEN** the returned `ease_factor` is less than `2.5`
- **AND** the returned `ease_factor` is greater than `1.3`

#### Scenario: Ease factor never drops below 1.3

- **WHEN** `spaced_repetition_record_response()` is called repeatedly with `quality=0` on a node whose ease factor has already reached `1.3`
- **THEN** each call returns `ease_factor=1.3` (the floor is enforced by `max(1.3, ...)`)

#### Scenario: Ease factor starts at default 2.5 for a new node

- **WHEN** a new `mind_map_nodes` row is inserted
- **THEN** `ease_factor` is `2.5` and `repetitions` is `0`

#### Scenario: Quality 0 (complete blackout) applies maximum ease factor penalty

- **WHEN** `spaced_repetition_record_response()` is called with `quality=0` on a node with `ease_factor=2.5`
- **THEN** the formula produces `2.5 + (0.1 - 5*(0.08 + 5*0.02)) = 2.5 - 0.8 = 1.7`
- **AND** returned `ease_factor` is `1.7` (above floor; floor is only clamped when result < 1.3)

---

### Requirement: Failed Recall Reset

A quality score below 3 (0, 1, or 2) constitutes a failed recall. On failure, repetitions are reset to 0 and the interval resets to the repetitions == 0 step of 6 hours (`0.25` days). The ease factor is still adjusted (penalized) per the standard formula, it is not reset.

#### Scenario: Quality 2 triggers reset

- **WHEN** `spaced_repetition_record_response()` is called with `quality=2` on a node with `repetitions=5`, `ease_factor=2.8`, `last_interval=93.75`
- **THEN** the returned `repetitions` is `0`
- **AND** the returned `interval_days` is `0.25` (6 hours, reset to the repetitions == 0 step)
- **AND** the returned `ease_factor` is less than `2.8` (penalized but not reset to default)

#### Scenario: Quality 1 triggers reset

- **WHEN** `spaced_repetition_record_response()` is called with `quality=1` on a node with `repetitions=3`
- **THEN** the returned `repetitions` is `0`
- **AND** the returned `interval_days` is `0.25` (6 hours, reset to the repetitions == 0 step)

#### Scenario: Quality 0 triggers reset with maximum ease factor penalty

- **WHEN** `spaced_repetition_record_response()` is called with `quality=0` on a node with `repetitions=4`, `ease_factor=2.5`
- **THEN** the returned `repetitions` is `0`
- **AND** the returned `interval_days` is `0.25` (6 hours, reset to the repetitions == 0 step)
- **AND** the returned `ease_factor` is `1.7`

#### Scenario: Quality == 3 is not a failure

- **WHEN** `spaced_repetition_record_response()` is called with `quality=3` on a node with `repetitions=2`
- **THEN** the returned `repetitions` is `3` (incremented, not reset)
- **AND** the returned `interval_days` is NOT `1.0` (interval progression continues)

---

### Requirement: Schedule Creation via Core Scheduler

After computing the new SM-2 state, `spaced_repetition_record_response()` creates a one-shot review schedule via `schedule_create()`. The cron expression encodes the exact target review datetime (minute and hour resolution). The schedule's `until_at` is set to `next_review_at + 24 hours` so that if the review window is missed, the schedule auto-disables without firing.

#### Scenario: One-shot cron computed from next review datetime

- **WHEN** `spaced_repetition_record_response()` is called and the next review datetime is computed as `2026-03-05 14:30 UTC`
- **THEN** `schedule_create()` is called with `cron="30 14 5 3 *"`
- **AND** `dispatch_mode="prompt"`

#### Scenario: until_at set to next_review_at plus 24 hours

- **WHEN** `spaced_repetition_record_response()` computes `next_review_at = 2026-03-05T14:30:00Z`
- **THEN** `schedule_create()` is called with `until_at=2026-03-06T14:30:00Z` (next_review_at + 24 hours)
- **AND** if the scheduled task fires after `until_at`, the scheduler auto-disables it without dispatching

#### Scenario: Prompt includes enough context for the ephemeral session

- **WHEN** `schedule_create()` is called for a review schedule
- **THEN** the `prompt` argument includes the `node_id`, `mind_map_id`, `node.label`, the upcoming repetition number, and the current `ease_factor`
- **AND** the prompt instructs the session to conduct a spaced repetition review quiz for the named concept

#### Scenario: Missed review window auto-disables

- **WHEN** the review schedule fires at a time after `until_at`
- **THEN** the core scheduler sets the task to `enabled=false` and `next_run_at=NULL` without dispatching
- **AND** the node's `next_review_at` in `mind_map_nodes` remains set until the next manual or rescheduled review

---

### Requirement: Schedule Naming Convention

Each review schedule is named following the pattern `review-{node_id}-rep{N}`, where `node_id` is the node's UUID and `N` is the new repetition count after the update. This ensures schedule names are unique per node per repetition cycle, and that stale schedules from prior repetitions are identifiable by name.

#### Scenario: Schedule name encodes node and repetition

- **WHEN** `spaced_repetition_record_response()` is called for node `abc123` and the new repetition count is `3`
- **THEN** `schedule_create()` is called with `name="review-abc123-rep3"`

#### Scenario: Prior-cycle schedule is superseded on new record

- **WHEN** a node advances from `repetitions=2` to `repetitions=3` after a successful review
- **THEN** the new schedule is named `review-{node_id}-rep3`
- **AND** the prior schedule named `review-{node_id}-rep2` is deleted (or was already auto-disabled by its `until_at`) before the new one is created

#### Scenario: Failed recall resets repetition counter in schedule name

- **WHEN** `spaced_repetition_record_response()` is called with `quality=1` on a node that had `repetitions=5`
- **THEN** the new schedule is named `review-{node_id}-rep0`

#### Scenario: Duplicate schedule name rejected by scheduler

- **WHEN** `schedule_create()` is called with a name that already exists in `scheduled_tasks`
- **THEN** the core scheduler raises a `ValueError`
- **AND** `spaced_repetition_record_response()` deletes the prior schedule with the same name before creating the new one

---

### Requirement: Batch Review Cap — Maximum 20 Pending Schedules Per Mind Map

To prevent schedule proliferation, no mind map may have more than 20 pending review schedules active simultaneously. If the cap would be exceeded, `spaced_repetition_record_response()` checks the current count before calling `schedule_create()`. When pending reviews exceed 20, all overdue nodes are batched into a single "review session" schedule rather than creating individual schedules.

#### Scenario: Schedule created when under cap

- **WHEN** `spaced_repetition_record_response()` is called for a mind map with 15 pending review schedules
- **THEN** an individual review schedule for the node is created normally
- **AND** the pending schedule count for the mind map reaches 16

#### Scenario: Individual schedule blocked when cap is reached

- **WHEN** `spaced_repetition_record_response()` is called for a mind map that already has 20 pending review schedules
- **THEN** no individual schedule is created for the new node
- **AND** instead, a single batch "review session" schedule is created (or the existing batch schedule is updated) covering all overdue nodes

#### Scenario: Batch schedule prompt covers all overdue nodes

- **WHEN** a batch review session schedule is created due to cap overflow
- **THEN** the `prompt` includes the `mind_map_id` and a directive to conduct a review session for all pending nodes
- **AND** the schedule name is `review-{mind_map_id}-batch`

#### Scenario: Batch schedule has 24-hour until_at window

- **WHEN** a batch review session schedule is created
- **THEN** `until_at` is set to the batch `next_review_at + 24 hours`

#### Scenario: `spaced_repetition_pending_reviews` returns only overdue nodes

- **WHEN** `spaced_repetition_pending_reviews(pool, mind_map_id)` is called
- **THEN** it returns a list of dicts for all nodes in that mind map where `next_review_at <= now()`
- **AND** nodes where `next_review_at IS NULL` or `next_review_at > now()` are excluded
- **AND** each dict includes `node_id`, `label`, `ease_factor`, `repetitions`, `next_review_at`, and `mastery_status`

#### Scenario: Pending count is computed from scheduler, not from node table alone

- **WHEN** counting pending review schedules for the cap check
- **THEN** the count is the number of active (enabled=true) rows in `scheduled_tasks` whose names match the pattern `review-{node_id}-*` for nodes belonging to `mind_map_id`
- **AND** the batch schedule `review-{mind_map_id}-batch` counts as one schedule regardless of how many nodes it covers

---

### Requirement: Schedule Cleanup on Mind Map Completion or Abandonment

When a mind map transitions to `status='completed'` or `status='abandoned'`, all pending review schedules for its nodes are removed. This prevents stale review prompts from firing after the user has finished or given up on a topic.

#### Scenario: Cleanup removes all node review schedules on completion

- **WHEN** `spaced_repetition_schedule_cleanup(pool, mind_map_id)` is called and the mind map status is `completed`
- **THEN** all `scheduled_tasks` rows whose names match `review-{node_id}-*` for nodes in that mind map are deleted
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

---

### Requirement: Node State Updates and Mastery Status Transitions

`spaced_repetition_record_response()` updates the node's persistent state in `mind_map_nodes` after every call: `ease_factor`, `repetitions`, `next_review_at`, `last_reviewed_at`, and `mastery_status`. Within the spaced-repetition engine, the `mastery_status` field changes only on regression (a failed recall demotes the node); forward promotions (`learning` to `reviewing`, `reviewing` to `mastered`) are owned by the mastery module's `mastery_record_response()` write path and are specified in `module-education-mastery`, not here. A successful recall in this engine advances scheduling state (`repetitions`, `interval`, `next_review_at`) but leaves `mastery_status` unchanged.

#### Scenario: Successful recall leaves mastery_status unchanged

- **WHEN** `spaced_repetition_record_response()` is called with `quality >= 3` on a node with `mastery_status='reviewing'`
- **THEN** `mastery_status` remains `'reviewing'`
- **AND** `repetitions` is incremented by 1
- **AND** `next_review_at` and `last_reviewed_at` are updated for the next interval

#### Scenario: Failed recall demotes a reviewing node back to learning

- **WHEN** `spaced_repetition_record_response()` is called with `quality < 3` on a node with `mastery_status='reviewing'`
- **THEN** `mastery_status` is updated to `'learning'`
- **AND** `repetitions` is reset to `0`
- **AND** `next_review_at` is set to `now() + 0.25 day` (6 hours)

#### Scenario: Failed recall demotes a mastered node to reviewing

- **WHEN** `spaced_repetition_record_response()` is called with `quality < 3` on a node with `mastery_status='mastered'`
- **THEN** `mastery_status` is updated to `'reviewing'`
- **AND** `repetitions` is reset to `0`

#### Scenario: Node state update is atomic

- **WHEN** `spaced_repetition_record_response()` updates a node
- **THEN** all of `ease_factor`, `repetitions`, `next_review_at`, `last_reviewed_at`, and `mastery_status` are written in a single database transaction
- **AND** if the subsequent `schedule_create()` call fails, the transaction is rolled back and the node retains its prior state

#### Scenario: `updated_at` timestamp is refreshed on every state update

- **WHEN** `spaced_repetition_record_response()` is called for any node
- **THEN** the node's `updated_at` column is set to `now()`

---

### Requirement: Review Delivery via notify()

Review prompts are delivered to the user via the `notify()` core tool, targeting the user's preferred channel. The education butler does not hold direct Telegram or email credentials — it routes all outbound messages through the Switchboard to the Messenger butler.

#### Scenario: Scheduled review session dispatches a notify call

- **WHEN** a review schedule fires and the ephemeral session runs
- **THEN** the session calls `notify(channel=<user_preferred_channel>, message=<quiz_content>, intent="send")`
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

- **WHEN** the user responds to a review quiz question during an ephemeral session
- **THEN** the session calls `spaced_repetition_record_response(pool, node_id, quality)` with the evaluated quality score
- **AND** a new row is inserted into `quiz_responses` with `response_type='review'`, `quality`, `question_text`, `user_answer`, and `responded_at`

#### Scenario: notify() failure does not corrupt node state

- **WHEN** the ephemeral session fails to deliver the review notification (e.g., Messenger butler unreachable)
- **THEN** `spaced_repetition_record_response()` has not yet been called (delivery precedes quality recording)
- **AND** the node's `next_review_at` remains set from the prior schedule
- **AND** the failed session logs the error to the session log

#### Scenario: Review prompt instructs the session to evaluate user answer and call record_response

- **WHEN** the scheduler fires the review schedule and spawns an ephemeral session
- **THEN** the session prompt includes an explicit directive to: (1) ask the quiz question via notify, (2) await user response (via a subsequent trigger), (3) evaluate the answer on a 0-5 scale, and (4) call `spaced_repetition_record_response()` with the quality score
