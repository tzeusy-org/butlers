## MODIFIED Requirements

### Requirement: Session Context Assembly

The education domain SHALL assemble a structured flow context block and return it through an education read capability that a goal invocation calls within its grant; no spawner assembles or injects it. The context block MUST include four components in order: (1) current flow state from the KV store, (2) current mind map frontier from a DB query, (3) recent quiz responses from a DB query, (4) memory context from the memory module. Components drawn from stores that `record` capabilities write SHALL carry their stamps. The invocation MUST NOT rely on prior session transcripts for continuity. Throughout this capability, an "ephemeral session" or "session" that advances a flow is a goal invocation: an owner goal admitted from the owner's message, or an education obligation goal, never a session the Spawner starts; `session_count` and `last_session_at` keep their names and count those invocations' flow advances.

Source: RFC 0038 D2, D6, D15
Scope: v1-mandatory

#### Scenario: Flow state included in session prompt

- **WHEN** a goal invocation retrieves the context block for a flow in `teaching` status
- **THEN** the block contains the full flow state JSON, including `status`, `current_node_id`, `current_phase`, `session_count`, and `last_session_at`

#### Scenario: Frontier nodes included in session prompt

- **WHEN** the education domain assembles the context block
- **THEN** the block includes the result of the frontier query: all nodes with `mastery_status IN ('unseen', 'diagnosed', 'learning')` whose prerequisite nodes are all mastered, ordered by `depth ASC, effort_minutes ASC NULLS LAST`

#### Scenario: Recent quiz responses included in session prompt

- **WHEN** the education domain assembles the context block
- **THEN** the block includes the most recent 10 quiz responses for the current node, including `question_text`, `user_answer`, `quality`, `response_type`, and `responded_at`

#### Scenario: Memory context included in session prompt

- **WHEN** the memory module is enabled and context is available
- **THEN** the memory context is included in the block, providing relevant user preferences, prior learning history, and entity associations
- **AND** each memory item carries its stamp, so tainted or non-owner items expose the reading invocation

#### Scenario: Memory context failure is fail-open

- **WHEN** `fetch_memory_context()` raises an exception during context assembly
- **THEN** the failure is logged and the block is returned with the remaining three context components

#### Scenario: No prior session transcript is required

- **WHEN** a user resumes a flow after 3 days of inactivity
- **THEN** the education domain assembles context purely from the KV store, DB queries, and memory module
- **AND** the goal invocation can continue the flow without access to previous session logs

### Requirement: Reviewing Phase — Spaced Repetition Sessions

A due review schedule SHALL submit an obligation admission under the git-declared education review obligation instead of spawning a session. The review goal reads all nodes with `next_review_at <= now()` for the mind map and asks 1–3 recall questions per batch; recording responses, updating SM-2 parameters, scheduling the next review, and calling `teaching_flow_advance()` happen in the goal that processes the owner's answers.

Source: RFC 0038 D2, D5
Scope: v1-mandatory

#### Scenario: Scheduled trigger fires for due review

- **WHEN** a one-shot review scheduled task fires (created by the SM-2 scheduler)
- **THEN** the education daemon submits an obligation admission for the occurrence `review-{node_id}-rep{n}` and launches no session
- **AND** the review goal's lead retrieves the full flow state, frontier, and recent quiz responses for the relevant mind map through education read capabilities

#### Scenario: Review session asks recall questions

- **WHEN** the session is in `reviewing` status and has 1–3 nodes due for review
- **THEN** the session asks one recall question per due node via `notify(channel="telegram", intent="send", message=...)`
- **AND** each question targets the specific concept label of the due node

#### Scenario: Batch review when more than 20 nodes are due

- **WHEN** more than 20 nodes have `next_review_at <= now()` for a single mind map
- **THEN** the session batches the overdue nodes into a single review session, prioritizing nodes with the lowest `ease_factor` first
- **AND** a single review obligation occurrence is used rather than 20 individual schedules

#### Scenario: SM-2 parameters updated after recall

- **WHEN** the user answers a review question with `quality = 4`
- **THEN** `sm2_update()` computes new `ease_factor`, `repetitions`, and `interval_days`
- **AND** `mind_map_node_update()` persists the updated SM-2 parameters and `next_review_at`
- **AND** the next one-shot review is registered through the core scheduler's internal API as an occurrence of the declared review obligation, not through `schedule_create()`

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

When the owner sends a freeform question (e.g., "I don't understand recursion") during an active teaching flow, the message SHALL be admitted as an owner goal with no classification or route-dispatch step. The goal's lead, calling education capabilities directly or through a depth-1 child, identifies the relevant node in the current mind map, provides a targeted explanation, asks a follow-up comprehension question, and records the response — without disrupting the main flow sequence.

Source: RFC 0038 D1, D5
Scope: v1-mandatory

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

### Requirement: Flow Initialization — PENDING and DIAGNOSING

`teaching_flow_start(pool, topic, goal?)` creates a new mind map record and writes initial flow state at `pending` **as one atomic operation**, then immediately transitions to `diagnosing`, and returns the flow state dict. The goal invocation that advances a flow in `diagnosing` phase MUST generate 3–7 adaptive probe questions using a binary-search strategy targeting the topic's concept inventory. Each probe is sent by one goal, and the owner's answer is admitted as an owner goal that evaluates it and sends the next probe; no Spawner session is started.

The mind map row insert and the `flow:{mind_map_id}` state write MUST occur in
a single database transaction. Both `education.mind_maps` and the butler
`state` table live in the same PostgreSQL pool, so this is one commit, not a
compensating cleanup path. If either write fails, neither MUST be visible: no
mind map row and no flow state. A mind map with no flow state MUST NOT be a
reachable outcome of `teaching_flow_start()`.

The mind map is created with `status = 'draft'`. It MUST NOT be created
`active`: at creation it has zero nodes, and an `active` zero-node map is
forbidden by the mind map content invariant. The map becomes `active` only
when `curriculum_generate()` activates it at the end of the `planning` phase.

Source: RFC 0038 D2 and D5
Scope: v1-mandatory

#### Scenario: Flow created at PENDING then immediately advances to DIAGNOSING

- **WHEN** `teaching_flow_start(pool, topic="Python", goal=None)` is called
- **THEN** a new mind map row is inserted with `title = "Python"` and `status = 'draft'`
- **AND** the KV store entry `flow:{mind_map_id}` is written with `status = 'pending'`
- **AND** the mind map row and the flow state entry are committed in the same transaction
- **AND** `teaching_flow_advance()` is called immediately, transitioning the state to `diagnosing`
- **AND** the returned dict reflects `status = 'diagnosing'`

#### Scenario: Failure to write flow state leaves no orphan mind map

- **WHEN** `teaching_flow_start()` inserts the mind map row and the `flow:{mind_map_id}` state write then fails
- **THEN** the transaction MUST roll back
- **AND** no row MUST exist in `education.mind_maps` for that topic
- **AND** the caller MUST receive an error rather than a mind map id

#### Scenario: Failure to insert the mind map leaves no orphan flow state

- **WHEN** the mind map insert in `teaching_flow_start()` fails
- **THEN** no `flow:{mind_map_id}` entry MUST exist in the KV store
- **AND** the caller MUST receive an error

#### Scenario: A started flow never yields an active zero-node map

- **WHEN** `teaching_flow_start()` completes successfully and the diagnostic phase has not yet created any nodes
- **THEN** the mind map's `status` MUST be `'draft'`
- **AND** no mind map MUST exist with `status = 'active'` and zero nodes

#### Scenario: User receives first diagnostic probe question

- **WHEN** a goal invocation advances a flow in `diagnosing` status
- **THEN** it sends one probe question via `notify(channel="telegram", intent="send", message=...)` targeting a median-difficulty concept for the topic
- **AND** it does not send multiple questions at once

#### Scenario: Adaptive probing narrows difficulty

- **WHEN** the user answers a diagnostic probe correctly
- **THEN** the next probe targets a harder concept
- **WHEN** the user answers a diagnostic probe incorrectly
- **THEN** the next probe targets an easier concept

#### Scenario: Diagnostic phase ends after convergence

- **WHEN** the adaptive probe sequence has asked between 3 and 7 questions and has converged
- **THEN** the session calls `teaching_flow_advance()` to transition to `planning`
- **AND** the `diagnostic_results` in the flow state contains a quality score and inferred mastery for each probed concept node

#### Scenario: Diagnostic seeds mastery conservatively

- **WHEN** a probe question is answered correctly with high confidence
- **THEN** `mind_map_node_update()` sets `mastery_score` to a value between 0.3 and 0.7 (never 1.0)
- **AND** `mastery_status` is set to `'diagnosed'`
