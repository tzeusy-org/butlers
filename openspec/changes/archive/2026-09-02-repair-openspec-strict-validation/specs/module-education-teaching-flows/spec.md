## MODIFIED Requirements

### Requirement: Teaching Phase — Explain, Question, Evaluate

The implementation SHALL provide the behavior described by this requirement.
While in `teaching` status, the session moves through three sub-phases for the current node: `explaining` → `questioning` → `evaluating`. After a successful evaluation, mastery is updated and the flow advances to `quizzing`.

#### Scenario: Session delivers explanation for current node

- **WHEN** `current_phase = 'explaining'`
- **THEN** the session sends a focused explanation of the concept named by `current_node_id` via `notify(channel="telegram", intent="send", message=...)`
- **AND** the explanation covers only the target concept, not the full curriculum
- **AND** the session updates `current_phase` to `questioning` in the KV store

#### Scenario: Session asks comprehension question after explanation

- **WHEN** `current_phase = 'questioning'`
- **THEN** the session sends one comprehension question via `notify(channel="telegram", intent="send", message=...)`
- **AND** the question is directly about the concept just explained
- **AND** the session updates `current_phase` to `evaluating` in the KV store

#### Scenario: Session evaluates user answer and gives feedback

- **WHEN** `current_phase = 'evaluating'` and the user's answer arrives
- **THEN** the session evaluates the answer and sends feedback via `notify(channel="telegram", intent="reply", message=..., request_context=...)`
- **AND** if the answer is correct, the session sends a positive acknowledgment via `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
- **AND** a `quiz_responses` row is inserted with `response_type = 'teach'` and the appropriate `quality` score (0–5)

#### Scenario: Mastery updated after teaching evaluation

- **WHEN** the evaluation is complete
- **THEN** `mind_map_node_update()` sets `mastery_score` and `mastery_status` based on the quality score
- **AND** if `quality >= 3`, `mastery_status` advances toward `reviewing`; if `quality < 3`, `mastery_status` remains `learning`

#### Scenario: Flow advances to QUIZZING after teaching evaluation

- **WHEN** the evaluation step is complete
- **THEN** `teaching_flow_advance()` transitions the flow to `quizzing`
- **AND** `current_phase` is set to `null`

### Requirement: Quizzing Phase — Comprehension Testing

The implementation SHALL provide the behavior described by this requirement.
In `quizzing` status the session asks 1–3 additional quiz questions that vary in format (free-form, multiple-choice) to solidify comprehension. After the final question is evaluated, the session calls `teaching_flow_advance()` to branch toward the next frontier node or toward `completed`.

#### Scenario: Session asks at least one quiz question in quizzing phase

- **WHEN** the flow is in `quizzing` status
- **THEN** the session asks at least one quiz question via `notify(channel="telegram", intent="send", message=...)`
- **AND** the question tests recall or application of the concept in `current_node_id`

#### Scenario: Each quiz answer is recorded

- **WHEN** the user responds to a quiz question
- **THEN** a `quiz_responses` row is inserted with `response_type = 'teach'`, the full question text, the user's answer text, and the evaluated `quality` score

#### Scenario: SM-2 schedule created after successful quiz

- **WHEN** all quiz questions for the current node are answered and the average quality is >= 3
- **THEN** `sm2_update()` computes the next review interval
- **AND** `schedule_create()` creates a one-shot review schedule named `"review-{node_id}-rep{repetitions}"` with `dispatch_mode = 'prompt'` and `until_at = next_review + 24 hours`

#### Scenario: Node reset to learning on failed quiz

- **WHEN** the user's average quality score across quiz questions is < 3
- **THEN** `mind_map_node_update()` sets `repetitions = 0` and `mastery_status = 'learning'`
- **AND** `teaching_flow_advance()` returns the flow to `teaching` for the same node in the next session

### Requirement: Reviewing Phase — Spaced Repetition Sessions

The implementation SHALL provide the behavior described by this requirement.
A scheduled trigger fires when a review is due. The spawned session reads all nodes with `next_review_at <= now()` for the mind map, asks 1–3 recall questions per batch, records responses, updates SM-2 parameters, and schedules the next review. After processing, the session calls `teaching_flow_advance()`.

#### Scenario: Scheduled trigger fires for due review

- **WHEN** a one-shot review scheduled task fires (created by the SM-2 scheduler)
- **THEN** the spawner launches an ephemeral session with the trigger source `"schedule:review-{node_id}-rep{n}"`
- **AND** the session prompt includes the full flow state, frontier, and recent quiz responses for the relevant mind map

#### Scenario: Review session asks recall questions

- **WHEN** the session is in `reviewing` status and has 1–3 nodes due for review
- **THEN** the session asks one recall question per due node via `notify(channel="telegram", intent="send", message=...)`
- **AND** each question targets the specific concept label of the due node

#### Scenario: Batch review when more than 20 nodes are due

- **WHEN** more than 20 nodes have `next_review_at <= now()` for a single mind map
- **THEN** the session batches the overdue nodes into a single review session, prioritizing nodes with the lowest `ease_factor` first
- **AND** a single "review session" scheduled prompt is used rather than 20 individual schedules

#### Scenario: SM-2 parameters updated after recall

- **WHEN** the user answers a review question with `quality = 4`
- **THEN** `sm2_update()` computes new `ease_factor`, `repetitions`, and `interval_days`
- **AND** `mind_map_node_update()` persists the updated SM-2 parameters and `next_review_at`
- **AND** `schedule_create()` registers the next one-shot review cron

#### Scenario: Failed recall resets SM-2 and schedules short interval

- **WHEN** the user answers a review question with `quality < 3`
- **THEN** `sm2_update()` resets `repetitions = 0` and sets `interval = 1.0` day
- **AND** `mind_map_node_update()` persists `mastery_status = 'reviewing'` (not regressed to `learning`)
- **AND** the next review is scheduled for the following day

#### Scenario: Review response recorded with correct response_type

- **WHEN** a review question is answered
- **THEN** a `quiz_responses` row is inserted with `response_type = 'review'`
- **AND** only `response_type = 'review'` rows are used for retention rate analytics calculations

#### Scenario: Review advances flow to teaching when frontier remains

- **WHEN** the review session completes and the frontier has at least one unmastered node
- **THEN** `teaching_flow_advance()` transitions the flow to `teaching`
- **AND** `current_node_id` is set to the highest-priority frontier node

#### Scenario: Review advances flow to completed when all nodes mastered

- **WHEN** the review session completes and all nodes have `mastery_status = 'mastered'`
- **THEN** `teaching_flow_advance()` transitions the flow to `completed`
- **AND** the mind map row is updated to `status = 'completed'`

### Requirement: Mid-Flow User Questions — Contextual Help

The implementation SHALL provide the behavior described by this requirement.
When a user sends a freeform question (e.g., "I don't understand recursion") during an active teaching flow, the Switchboard routes it to the education butler. The session identifies the relevant node in the current mind map, provides a targeted explanation, asks a follow-up comprehension question, and records the response — without disrupting the main flow sequence.

#### Scenario: Mid-flow question matched to current node

- **WHEN** the user sends "I don't understand recursion" and the current node is "Recursion"
- **THEN** the session provides a targeted clarification of the current node via `notify(channel="telegram", intent="send", message=...)`
- **AND** the session asks one follow-up question to verify the clarification landed

#### Scenario: Mid-flow question matched to non-current node

- **WHEN** the user sends a question about a concept that is a different node in the current mind map
- **THEN** the session identifies the relevant node by label match or semantic proximity
- **AND** provides a targeted explanation of that node without permanently changing `current_node_id`
- **AND** the flow state `current_node_id` remains pointing to the original teaching node

#### Scenario: Mid-flow response recorded as teach type

- **WHEN** the user answers the follow-up question prompted by a mid-flow help request
- **THEN** a `quiz_responses` row is inserted with `response_type = 'teach'`

#### Scenario: Mid-flow question outside current mind map scope

- **WHEN** the user asks a question about a concept not found in any node of the active mind map
- **THEN** the session acknowledges the question is out of current scope
- **AND** suggests starting a new teaching flow or offers a brief off-curriculum answer without creating a new node

### Requirement: Staleness Detection and Auto-Abandonment

The implementation SHALL provide the behavior described by this requirement.
A weekly scheduled task (`stale-flow-check`) checks all active flows. Any flow with `last_session_at` more than 30 days before the check time is automatically abandoned. All pending review schedules for the abandoned mind map are deleted.

#### Scenario: Stale flow detected and abandoned

- **WHEN** the `stale-flow-check` scheduled task fires
- **AND** a flow has `last_session_at` that is more than 30 days in the past
- **THEN** `teaching_flow_abandon()` is called for that flow
- **AND** the flow state transitions to `abandoned`
- **AND** all review scheduled tasks with names matching `"review-{mind_map_node_id}-*"` for nodes in the mind map are deleted

#### Scenario: Recently active flow is not abandoned

- **WHEN** the `stale-flow-check` scheduled task fires
- **AND** a flow has `last_session_at` within the past 30 days
- **THEN** the flow status is unchanged

#### Scenario: Multiple stale flows processed in a single check

- **WHEN** the `stale-flow-check` fires and 3 flows are stale
- **THEN** all 3 flows are abandoned in the same check invocation
- **AND** each mind map's review schedules are cleaned up independently

#### Scenario: Completed and already-abandoned flows are skipped

- **WHEN** the `stale-flow-check` fires
- **THEN** flows with `status IN ('completed', 'abandoned')` are not evaluated for staleness
