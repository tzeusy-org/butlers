# Education Butler — Teaching Flows

## Purpose
Defines the end-to-end teaching UX flows for the education butler: the flow state machine, session context assembly, the full teaching lifecycle (PENDING through COMPLETED), spaced repetition review sessions, mid-flow contextual help, staleness detection, multi-channel delivery, flow listing and management, and cross-session coherence.

## ADDED Requirements

### Requirement: Flow State Machine — Valid Transitions

The teaching flow is a named state machine persisted under the key `flow:{mind_map_id}` in the butler's core state store. Valid status values are `pending`, `diagnosing`, `planning`, `teaching`, `quizzing`, `reviewing`, `completed`, and `abandoned`. Only the transitions listed below are permitted; any other transition MUST raise a `ValueError` before persisting.

Permitted transitions:
- `pending` → `diagnosing`
- `diagnosing` → `planning`
- `planning` → `teaching`
- `teaching` → `quizzing`
- `quizzing` → `reviewing`
- `quizzing` → `teaching` (when the frontier has more unmastered nodes)
- `reviewing` → `teaching` (when the frontier has more unmastered nodes)
- `reviewing` → `completed` (when all nodes are mastered)
- any non-terminal state → `abandoned`

The terminal states `completed` and `abandoned` SHALL NOT transition to any other state.

#### Scenario: Valid forward transition persists new status

- **WHEN** `teaching_flow_advance()` is called on a flow in `teaching` status
- **THEN** the flow status transitions to `quizzing`
- **AND** the updated state is written to the KV store atomically under `flow:{mind_map_id}`
- **AND** `teaching_flow_get()` returns the new status immediately after

#### Scenario: Invalid transition raises an error

- **WHEN** `teaching_flow_advance()` is called on a flow in `completed` status
- **THEN** a `ValueError` is raised with a message identifying the invalid transition
- **AND** the state store entry is not modified

#### Scenario: Transition from quizzing branches on frontier

- **WHEN** `teaching_flow_advance()` is called on a flow in `quizzing` status
- **AND** at least one mind map node has `mastery_status IN ('unseen', 'diagnosed', 'learning')` and all its prerequisite nodes are mastered
- **THEN** the flow transitions to `teaching` and `current_node_id` is set to the highest-priority frontier node
- **AND** `current_phase` is set to `explaining`

#### Scenario: Transition from quizzing to completed when all nodes mastered

- **WHEN** `teaching_flow_advance()` is called on a flow in `quizzing` status
- **AND** all nodes in the mind map have `mastery_status = 'mastered'`
- **THEN** the flow transitions to `completed`
- **AND** the corresponding mind map row is updated to `status = 'completed'`

#### Scenario: Transition from reviewing branches on frontier

- **WHEN** `teaching_flow_advance()` is called on a flow in `reviewing` status
- **AND** the frontier has at least one unmastered node with all prerequisites mastered
- **THEN** the flow transitions to `teaching` with `current_node_id` pointing to the top frontier node
- **AND** `current_phase` is set to `explaining`

#### Scenario: Abandon from any non-terminal state

- **WHEN** `teaching_flow_abandon()` is called on a flow with status `teaching`
- **THEN** the flow status is set to `abandoned`
- **AND** all pending review scheduled tasks associated with the mind map are deleted
- **AND** the mind map row is updated to `status = 'abandoned'`

### Requirement: Flow State Persistence in KV Store

The full flow state MUST be written to the core state store on every transition. The state value is a JSON object conforming to the schema below. The state store key is `flow:{mind_map_id}`. Writes MUST use CAS (compare-and-swap) semantics to prevent concurrent session races.

State schema:

```json
{
    "status": "<pending|diagnosing|planning|teaching|quizzing|reviewing|completed|abandoned>",
    "mind_map_id": "<uuid>",
    "current_node_id": "<uuid|null>",
    "current_phase": "<explaining|questioning|evaluating|null>",
    "diagnostic_results": {
        "<node_id>": { "quality": "<int 0-5>", "inferred_mastery": "<float 0.0-1.0>" }
    },
    "session_count": "<int>",
    "started_at": "<ISO-8601 UTC>",
    "last_session_at": "<ISO-8601 UTC>"
}
```

`current_node_id` MUST be non-null when `status` is `teaching`, `quizzing`, or `reviewing`. `current_phase` MUST be non-null when `status` is `teaching` or `quizzing`; it MUST be `null` in all other states.

#### Scenario: State written atomically on transition

- **WHEN** `teaching_flow_advance()` transitions a flow from `planning` to `teaching`
- **THEN** a single atomic CAS write updates the KV store entry with the new status, `current_node_id`, `current_phase = 'explaining'`, and incremented `session_count`
- **AND** `last_session_at` is set to the current UTC timestamp

#### Scenario: CAS conflict on concurrent session update

- **WHEN** two ephemeral sessions attempt to write flow state simultaneously for the same `mind_map_id`
- **THEN** the second write fails the CAS check
- **AND** the losing session retries once after a short backoff, then logs an error and exits without corrupting state

#### Scenario: State key matches mind map id

- **WHEN** a flow is initialized for mind map `"abc-123"`
- **THEN** `teaching_flow_get(pool, "abc-123")` returns the current state
- **AND** the KV store key `"flow:abc-123"` holds the serialized JSON

#### Scenario: current_node_id required in teaching status

- **WHEN** `teaching_flow_advance()` would produce a `teaching` status with `current_node_id = null`
- **THEN** a `ValueError` is raised before writing to the state store

### Requirement: Session Context Assembly

Before each ephemeral session, the spawner assembles a structured context block that is injected into the session prompt. The context block MUST include four components in order: (1) current flow state from the KV store, (2) current mind map frontier from a DB query, (3) recent quiz responses from a DB query, (4) memory context from the memory module. The ephemeral session MUST NOT rely on prior session transcripts for continuity.

#### Scenario: Flow state included in session prompt

- **WHEN** the spawner invokes an ephemeral session for a flow in `teaching` status
- **THEN** the session prompt contains the full flow state JSON, including `status`, `current_node_id`, `current_phase`, `session_count`, and `last_session_at`

#### Scenario: Frontier nodes included in session prompt

- **WHEN** the spawner assembles the context block
- **THEN** the prompt includes the result of the frontier query: all nodes with `mastery_status IN ('unseen', 'diagnosed', 'learning')` whose prerequisite nodes are all mastered, ordered by `depth ASC, effort_minutes ASC NULLS LAST`

#### Scenario: Recent quiz responses included in session prompt

- **WHEN** the spawner assembles the context block
- **THEN** the prompt includes the most recent 10 quiz responses for the current node, including `question_text`, `user_answer`, `quality`, `response_type`, and `responded_at`

#### Scenario: Memory context included in session prompt

- **WHEN** the memory module is enabled and context is available
- **THEN** the memory context is appended to the session prompt, providing relevant user preferences, prior learning history, and entity associations

#### Scenario: Memory context failure is fail-open

- **WHEN** `fetch_memory_context()` raises an exception during context assembly
- **THEN** the failure is logged and the session proceeds with the remaining three context components

#### Scenario: No prior session transcript is required

- **WHEN** a user resumes a flow after 3 days of inactivity
- **THEN** the spawner assembles context purely from the KV store, DB queries, and memory module
- **AND** the ephemeral session can continue the flow without access to previous session logs

### Requirement: Flow Initialization — PENDING and DIAGNOSING

`teaching_flow_start(pool, topic, goal?)` creates a new mind map record, writes initial flow state at `pending`, immediately transitions to `diagnosing`, and returns the flow state dict. The LLM session in `diagnosing` phase MUST generate 3–7 adaptive probe questions using a binary-search strategy targeting the topic's concept inventory.

#### Scenario: Flow created at PENDING then immediately advances to DIAGNOSING

- **WHEN** `teaching_flow_start(pool, topic="Python", goal=None)` is called
- **THEN** a new mind map row is inserted with `title = "Python"` and `status = 'active'`
- **AND** the KV store entry `flow:{mind_map_id}` is written with `status = 'pending'`
- **AND** `teaching_flow_advance()` is called immediately, transitioning the state to `diagnosing`
- **AND** the returned dict reflects `status = 'diagnosing'`

#### Scenario: User receives first diagnostic probe question

- **WHEN** the spawner fires an ephemeral session for a flow in `diagnosing` status
- **THEN** the session sends one probe question via `notify(channel="telegram", intent="send", message=...)` targeting a median-difficulty concept for the topic
- **AND** the session does not send multiple questions at once

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

### Requirement: Curriculum Planning — PLANNING

While in `planning` status, the ephemeral session decomposes the topic into a concept DAG and populates the mind map. The session MUST call `mind_map_node_create()` and `mind_map_edge_create()` for each node and edge. After the DAG is fully populated, the session calls `teaching_flow_advance()` to transition to `teaching`.

#### Scenario: DAG created with nodes and prerequisite edges

- **WHEN** the session is in `planning` status for topic "Python"
- **THEN** the session creates at least 5 mind map nodes covering foundational to advanced concepts
- **AND** prerequisite edges are created such that, for example, "Functions" is a prerequisite of "Decorators"
- **AND** the resulting graph is a valid DAG (no cycles)

#### Scenario: DAG acyclicity enforced at edge creation

- **WHEN** the session attempts to create a `mind_map_edge` that would form a cycle
- **THEN** `mind_map_edge_create()` raises a `ValueError` before persisting the edge
- **AND** the session re-prompts itself to correct the dependency

#### Scenario: Learning order assigned via topological sort

- **WHEN** the planning session finishes populating the DAG
- **THEN** each node is assigned a `sequence` integer based on topological sort order, with ties broken by depth ascending, then effort ascending, then diagnosed mastery descending

#### Scenario: Flow advances to TEACHING after planning

- **WHEN** the session has completed DAG construction
- **THEN** `teaching_flow_advance()` is called, transitioning status to `teaching`
- **AND** `current_node_id` is set to the first frontier node (lowest sequence, all prerequisites mastered or none)
- **AND** `current_phase` is set to `explaining`

### Requirement: Teaching Phase — Explain, Question, Evaluate

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

### Requirement: Multi-Channel Delivery via notify()

All user-facing messages from the education butler MUST be delivered via the `notify()` abstraction. Telegram is the primary channel for interactive teaching messages. Email is used for weekly progress digests. Direct DM reactions (emoji acknowledgment) are used for correct-answer feedback.

#### Scenario: Teaching explanation delivered via Telegram send

- **WHEN** the session is in `explaining` phase and sends a concept explanation
- **THEN** `notify(channel="telegram", intent="send", message=<explanation_text>)` is called
- **AND** the message is delivered to the owner's Telegram chat

#### Scenario: Quiz question delivered via Telegram send

- **WHEN** the session is in `questioning` or `quizzing` phase and sends a quiz question
- **THEN** `notify(channel="telegram", intent="send", message=<question_text>)` is called

#### Scenario: Answer evaluation delivered via Telegram reply

- **WHEN** the session evaluates a user's answer
- **THEN** `notify(channel="telegram", intent="reply", message=<feedback_text>, request_context=<original_message_context>)` is called so the feedback threads under the user's answer

#### Scenario: Correct answer acknowledged via Telegram react

- **WHEN** the evaluated answer has `quality >= 3`
- **THEN** `notify(channel="telegram", intent="react", emoji="✅", request_context=<original_message_context>)` is called in addition to the reply feedback

#### Scenario: Weekly progress digest delivered via email

- **WHEN** the `weekly-progress-digest` scheduled task fires
- **THEN** the session reads analytics snapshots for the past 7 days and composes a digest
- **AND** `notify(channel="email", intent="send", message=<digest_content>, subject="Your weekly learning progress")` is called
- **AND** the digest includes mastery percentage, velocity (nodes per week), retention rate, and struggling nodes

#### Scenario: Notify respects user's preferred channel if overridden

- **WHEN** the owner contact's `shared.contact_info` has a `preferred_channel` override
- **THEN** `notify()` uses that channel instead of the default Telegram

### Requirement: Flow Tool API — Start, Get, Advance, Abandon

The four core flow tools (`teaching_flow_start`, `teaching_flow_get`, `teaching_flow_advance`, `teaching_flow_abandon`) MUST be exposed as MCP tools on the education butler's MCP server. Each tool operates on the education butler's PostgreSQL pool and KV state store.

#### Scenario: teaching_flow_start creates flow and returns initial state

- **WHEN** `teaching_flow_start(pool, topic="Rust", goal="Learn systems programming")` is called
- **THEN** a mind map row is created with `title = "Rust"` and the goal stored in `metadata`
- **AND** the KV store is initialized with `status = 'pending'`, `session_count = 0`, and `started_at = now()`
- **AND** the tool immediately advances to `diagnosing` and returns the flow state dict

#### Scenario: teaching_flow_get returns None for unknown mind_map_id

- **WHEN** `teaching_flow_get(pool, mind_map_id="nonexistent-uuid")` is called
- **THEN** the tool returns `None`

#### Scenario: teaching_flow_get returns current state for known flow

- **WHEN** `teaching_flow_get(pool, mind_map_id=<valid_id>)` is called
- **THEN** the tool returns the full state dict from the KV store, including all fields

#### Scenario: teaching_flow_advance updates last_session_at on every call

- **WHEN** `teaching_flow_advance()` successfully transitions a flow
- **THEN** `last_session_at` is set to the current UTC timestamp in the returned state
- **AND** `session_count` is incremented by 1

#### Scenario: teaching_flow_abandon removes review schedules

- **WHEN** `teaching_flow_abandon(pool, mind_map_id=<id>)` is called
- **THEN** all `scheduled_tasks` rows with names matching `"review-<node_id>-rep*"` for nodes in the mind map are deleted
- **AND** the KV store entry is updated to `status = 'abandoned'`
- **AND** the function returns `None`

### Requirement: Flow Listing and Management

`teaching_flow_list(pool, status?)` returns a list of flow state dicts for the education butler's mind maps, optionally filtered by status. The list MUST include at minimum: `mind_map_id`, `title`, `status`, `session_count`, `started_at`, `last_session_at`, and current mastery percentage computed from node counts.

#### Scenario: List all active flows

- **WHEN** `teaching_flow_list(pool, status="teaching")` is called
- **THEN** the tool returns all flow state dicts where `status = 'teaching'`
- **AND** each entry includes `mastery_pct` computed as `mastered_nodes / total_nodes`

#### Scenario: List without filter returns all flows

- **WHEN** `teaching_flow_list(pool, status=None)` is called
- **THEN** all flows are returned regardless of status, ordered by `last_session_at DESC NULLS LAST`

#### Scenario: Empty list returned when no flows exist

- **WHEN** `teaching_flow_list(pool)` is called and no mind maps exist
- **THEN** the tool returns an empty list

#### Scenario: Completed flows included in listing

- **WHEN** `teaching_flow_list(pool, status="completed")` is called
- **THEN** completed flows are included with `mastery_pct = 1.0` (all nodes mastered)

### Requirement: Cross-Session Coherence

Each ephemeral session MUST be fully self-contained: it assembles all required context from durable storage at startup and persists all state changes before exit. The education butler MUST NOT require session transcripts or in-memory state from prior sessions to function correctly.

#### Scenario: Session resumes teaching after 48-hour gap

- **WHEN** a session fires 48 hours after the previous session for a flow in `teaching` status
- **THEN** the session reads `flow:{mind_map_id}` from the KV store and reconstructs `current_node_id` and `current_phase`
- **AND** the session continues with the correct phase (e.g., `questioning`) without re-explaining the concept

#### Scenario: Session reads frontier fresh on each invocation

- **WHEN** a new ephemeral session is started for a flow in `teaching` status
- **THEN** the frontier query is re-executed against the current DB state
- **AND** if nodes have been mastered since the last session (e.g., by a concurrent review session), they are correctly excluded from the frontier

#### Scenario: Diagnostic results survive session boundaries

- **WHEN** a diagnostic session writes `diagnostic_results` to the flow state in `diagnosing` status
- **AND** the next session transitions to `planning`
- **THEN** the planning session reads `diagnostic_results` from the KV store to seed initial mastery scores on newly created nodes

#### Scenario: Session count accurately tracks total sessions

- **WHEN** 12 sessions have fired for a mind map (teaching, quizzing, and review sessions combined)
- **THEN** `teaching_flow_get()` returns `session_count = 12`
- **AND** each invocation of `teaching_flow_advance()` increments the count by exactly 1

#### Scenario: Concurrent review and teaching sessions do not corrupt flow state

- **WHEN** a review session and a teaching session both attempt to write state for the same mind map simultaneously
- **THEN** the CAS mechanism ensures exactly one write succeeds
- **AND** the losing session retries with the latest state, detects the conflict, and exits gracefully without overwriting the winning session's updates
