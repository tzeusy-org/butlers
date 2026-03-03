# Education Diagnostic Assessment

## Purpose

The diagnostic assessment capability runs a short, adaptive probe sequence before teaching begins. It generates a concept inventory for the requested topic, uses a binary-search probe strategy to locate the user's knowledge frontier in 3-7 questions, and seeds conservative mastery scores onto mind map nodes. Results persist in the teaching flow's state store entry and transition the flow from DIAGNOSING to PLANNING.

## ADDED Requirements

### Requirement: Concept Inventory Generation

The system SHALL generate a topic concept inventory of 10-15 key concepts spanning beginner to expert difficulty levels when a diagnostic assessment is started.

#### Scenario: Inventory generated within bounds

- **WHEN** `diagnostic_start(pool, mind_map_id)` is called for a topic
- **THEN** the returned concept inventory MUST contain between 10 and 15 concept entries
- **AND** each entry MUST include a `node_id`, `label`, `description`, and `difficulty_rank` (integer 1–N ordered from easiest to hardest)
- **AND** the inventory MUST span at least three distinct difficulty levels (beginner, intermediate, expert)

#### Scenario: Inventory persisted to flow state

- **WHEN** `diagnostic_start(pool, mind_map_id)` completes
- **THEN** the flow state at key `flow:{mind_map_id}` MUST be initialized with `status = 'DIAGNOSING'`
- **AND** the flow state MUST contain `concept_inventory` listing all generated concepts with their difficulty ranks
- **AND** the flow state MUST contain `probes_issued = 0` and `diagnostic_results = {}`

#### Scenario: Inventory requires existing mind map

- **WHEN** `diagnostic_start(pool, mind_map_id)` is called with a `mind_map_id` that does not exist in the database
- **THEN** the call MUST raise an error indicating the mind map was not found
- **AND** no flow state MUST be written

#### Scenario: Inventory generation is LLM-driven

- **WHEN** the concept inventory is produced
- **THEN** concept labels and difficulty rankings MUST be determined by the ephemeral LLM session, not by a hardcoded rubric
- **AND** the skill prompt supplied to the session MUST instruct it to order concepts from foundational to expert, accounting for prerequisite relationships within the topic domain

---

### Requirement: Adaptive Probe Sequencing

The diagnostic session SHALL select probe targets using a binary-search strategy on the concept inventory's difficulty axis, converging in 3-7 total questions.

#### Scenario: First probe targets median difficulty

- **WHEN** a diagnostic session begins and no probes have been issued yet
- **THEN** the first probe MUST target the concept at the median `difficulty_rank` in the concept inventory (i.e., the concept at position `floor(N / 2)` when ranked 1–N)

#### Scenario: Correct response shifts probe to harder concept

- **WHEN** a probe is answered correctly (quality >= 3)
- **THEN** the next probe MUST target a concept with a strictly higher `difficulty_rank` than the current probe's concept
- **AND** the selection MUST bisect the remaining unprobed harder half of the inventory

#### Scenario: Incorrect response shifts probe to easier concept

- **WHEN** a probe is answered incorrectly (quality < 3)
- **THEN** the next probe MUST target a concept with a strictly lower `difficulty_rank` than the current probe's concept
- **AND** the selection MUST bisect the remaining unprobed easier half of the inventory

#### Scenario: Ambiguous boundary triggers targeted follow-up probes

- **WHEN** adjacent difficulty-rank concepts produce conflicting results (one correct, one incorrect)
- **THEN** the session MAY issue 1-3 additional targeted probes around the ambiguous boundary
- **AND** the total probe count MUST NOT exceed 7

#### Scenario: Session terminates after convergence

- **WHEN** the probe sequence has converged (binary search exhausted or 7 questions reached)
- **THEN** no additional probe questions SHALL be issued
- **AND** `diagnostic_complete(pool, mind_map_id)` MUST be called to finalize the session

#### Scenario: Session terminates after minimum probes

- **WHEN** 3 probes have been issued and the binary search has unambiguously located the user's knowledge frontier
- **THEN** the session MAY terminate early by calling `diagnostic_complete(pool, mind_map_id)` without issuing further probes

---

### Requirement: Mastery Score Seeding

Probe results SHALL be mapped onto mind map nodes as conservative mastery seeds in the range 0.3–0.7, never reaching 1.0.

#### Scenario: Correct high-confidence probe seeds mastery at 0.7 maximum

- **WHEN** a probe is answered correctly with high confidence (quality = 5)
- **THEN** `diagnostic_record_probe(pool, mind_map_id, node_id, quality=5, inferred_mastery=...)` MUST record `inferred_mastery` no greater than 0.7
- **AND** the corresponding `mind_map_nodes` row MUST have `mastery_score` updated to the recorded `inferred_mastery` value
- **AND** `mastery_status` MUST be set to `'diagnosed'`

#### Scenario: Correct low-confidence probe seeds mastery at minimum 0.3

- **WHEN** a probe is answered correctly with low confidence (quality = 3)
- **THEN** `inferred_mastery` MUST be at least 0.3
- **AND** the node's `mastery_status` MUST be set to `'diagnosed'`

#### Scenario: Mastery score never reaches 1.0 from diagnosis

- **WHEN** any probe is recorded regardless of quality score
- **THEN** the recorded `inferred_mastery` MUST be strictly less than 1.0
- **AND** `mastery_status` MUST NOT be set to `'mastered'` as a result of diagnostic assessment alone

#### Scenario: Failed probe leaves node at unseen

- **WHEN** a probe is answered incorrectly (quality < 3)
- **THEN** the corresponding `mind_map_nodes` row MUST NOT have its `mastery_status` changed from `'unseen'`
- **AND** `mastery_score` MUST NOT be increased as a result of the failed probe

#### Scenario: Inferred mastery is proportional to quality score

- **WHEN** a probe quality score is in range 3–5
- **THEN** a quality of 3 MUST yield lower `inferred_mastery` than a quality of 4
- **AND** a quality of 4 MUST yield lower `inferred_mastery` than a quality of 5
- **AND** all values MUST remain in [0.3, 0.7)

---

### Requirement: Flow State Integration

The diagnostic assessment SHALL integrate with the teaching flow state machine, beginning in the DIAGNOSING phase and transitioning to PLANNING upon completion.

#### Scenario: Flow state initialized to DIAGNOSING on start

- **WHEN** `diagnostic_start(pool, mind_map_id)` is called
- **THEN** the flow state entry `flow:{mind_map_id}` MUST contain `status = 'DIAGNOSING'`
- **AND** `started_at` MUST be set to the current UTC timestamp
- **AND** any pre-existing flow state for the same `mind_map_id` MUST be overwritten only if its status is `'PENDING'`; otherwise the call MUST raise an error

#### Scenario: Flow state rejects start when already diagnosing

- **WHEN** `diagnostic_start(pool, mind_map_id)` is called and the existing flow state has `status = 'DIAGNOSING'`
- **THEN** the call MUST raise an error indicating the assessment is already in progress
- **AND** the existing flow state MUST remain unchanged

#### Scenario: Flow transitions to PLANNING on complete

- **WHEN** `diagnostic_complete(pool, mind_map_id)` is called
- **THEN** the flow state MUST be updated with `status = 'PLANNING'`
- **AND** `diagnostic_results` in the flow state MUST contain one entry per probed node with keys `quality` and `inferred_mastery`
- **AND** `last_session_at` MUST be updated to the current UTC timestamp

#### Scenario: Complete rejected when no probes recorded

- **WHEN** `diagnostic_complete(pool, mind_map_id)` is called and `probes_issued = 0` in the flow state
- **THEN** the call MUST raise an error indicating no diagnostic probes were recorded
- **AND** the flow state MUST remain in `DIAGNOSING` status

#### Scenario: Probe recording updates flow state counters

- **WHEN** `diagnostic_record_probe(pool, mind_map_id, node_id, quality, inferred_mastery)` is called
- **THEN** `probes_issued` in the flow state MUST be incremented by 1
- **AND** `diagnostic_results[node_id]` MUST be set to `{quality: <value>, inferred_mastery: <value>}`
- **AND** if a prior probe result already exists for the same `node_id`, it MUST be overwritten with the newer result

---

### Requirement: Question Format Constraints

Each probe question issued during a diagnostic session SHALL be formatted as either multiple choice or short answer, with exactly one question per message.

#### Scenario: Multiple choice format

- **WHEN** the ephemeral session selects the multiple choice format for a probe
- **THEN** the question message MUST contain exactly one question stem followed by labeled answer options (A, B, C, D or similar)
- **AND** the message MUST NOT contain any other question or request for user action

#### Scenario: Short answer format

- **WHEN** the ephemeral session selects the short answer format for a probe
- **THEN** the question message MUST contain exactly one open-ended question with no more than one sub-question
- **AND** the message MUST NOT contain follow-up or clarifying questions in the same message

#### Scenario: One question per message enforced by skill prompt

- **WHEN** the skill prompt is provided to the ephemeral diagnostic session
- **THEN** the prompt MUST include an explicit instruction that only one question may be sent per message
- **AND** the prompt MUST include explicit scoring criteria so the session can evaluate the user's answer and assign a quality score (0-5) before recording the probe result

#### Scenario: Explicit scoring criteria required

- **WHEN** the session evaluates a user answer
- **THEN** it MUST assign a quality score on the SM-2 scale (0=complete blackout, 5=perfect recall with no hesitation)
- **AND** the quality score MUST be recorded via `diagnostic_record_probe()` before issuing the next probe or calling `diagnostic_complete()`

---

### Requirement: Probe Result Recording

The system SHALL record each probe result durably, updating both the flow state and the corresponding mind map node in a single atomic operation.

#### Scenario: Probe result persisted atomically

- **WHEN** `diagnostic_record_probe(pool, mind_map_id, node_id, quality, inferred_mastery)` is called
- **THEN** the `mind_map_nodes` row for `node_id` MUST be updated with `mastery_score` and `mastery_status` in the same database transaction as the flow state update
- **AND** if either update fails, both MUST be rolled back

#### Scenario: Quality score validated on record

- **WHEN** `diagnostic_record_probe()` is called with a `quality` value outside the range 0–5
- **THEN** the call MUST raise a validation error
- **AND** no database state MUST be modified

#### Scenario: Inferred mastery validated on record

- **WHEN** `diagnostic_record_probe()` is called with `inferred_mastery` outside the range [0.0, 1.0)
- **THEN** the call MUST raise a validation error
- **AND** no database state MUST be modified

#### Scenario: Node must belong to mind map

- **WHEN** `diagnostic_record_probe()` is called with a `node_id` that does not belong to the specified `mind_map_id`
- **THEN** the call MUST raise an error indicating the node does not belong to the mind map
- **AND** no state MUST be modified

#### Scenario: Probe result stored in quiz_responses table

- **WHEN** `diagnostic_record_probe()` is called
- **THEN** a row MUST be inserted into `quiz_responses` with `response_type = 'diagnostic'`
- **AND** the row MUST include `node_id`, `mind_map_id`, `quality`, and `responded_at`
- **AND** `question_text` and `user_answer` SHOULD be populated if available from the session context

---

### Requirement: Diagnostic Summary Generation

The system SHALL generate a structured summary of inferred mastery levels upon diagnostic completion.

#### Scenario: Summary contains all probed nodes

- **WHEN** `diagnostic_complete(pool, mind_map_id)` returns
- **THEN** the returned dict MUST contain a `summary` key mapping each probed `node_id` to `{quality, inferred_mastery, mastery_status}`
- **AND** the summary MUST include every node for which `diagnostic_record_probe()` was called during the session

#### Scenario: Summary includes unprobed node count

- **WHEN** `diagnostic_complete(pool, mind_map_id)` returns
- **THEN** the returned dict MUST include `unprobed_node_count` indicating how many nodes in the concept inventory received no probe
- **AND** it MUST include `total_concepts_in_inventory` with the full inventory size

#### Scenario: Summary includes inferred knowledge frontier

- **WHEN** `diagnostic_complete(pool, mind_map_id)` returns
- **THEN** the returned dict MUST include `inferred_frontier_rank` indicating the difficulty rank of the highest concept answered correctly
- **AND** if no concepts were answered correctly, `inferred_frontier_rank` MUST be 0

#### Scenario: Summary returned to calling session

- **WHEN** `diagnostic_complete(pool, mind_map_id)` is called from the ephemeral diagnostic session
- **THEN** the complete summary dict MUST be returned synchronously so the session can log it and communicate the transition to the user before exiting

---

### Requirement: Self-Correction of Diagnosed Mastery

Nodes seeded with `mastery_status = 'diagnosed'` SHALL be automatically demoted to `'learning'` status when a subsequent actual quiz reveals low understanding.

#### Scenario: Diagnosed node demoted on failed quiz

- **WHEN** a node with `mastery_status = 'diagnosed'` is quizzed during a teaching or review session
- **AND** the quiz result quality score is below 3
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`
- **AND** the node's `mastery_score` MUST be reduced to reflect the failed quiz result, not the original diagnostic seed

#### Scenario: Diagnosed node retained on passing quiz

- **WHEN** a node with `mastery_status = 'diagnosed'` is quizzed during a teaching or review session
- **AND** the quiz result quality score is 3 or above
- **THEN** the node's `mastery_status` MUST remain `'diagnosed'` or be promoted toward `'reviewing'` or `'mastered'` per the SM-2 progression rules
- **AND** the node's `mastery_score` MUST be updated upward from the original diagnostic seed

#### Scenario: Demotion is immediate and non-deferred

- **WHEN** a quiz quality score below 3 is recorded for a `diagnosed` node
- **THEN** `mastery_status` MUST be set to `'learning'` in the same transaction that records the quiz response
- **AND** no additional confirmation or threshold is required — a single failed quiz is sufficient to trigger demotion

#### Scenario: Diagnostic seeds are conservative to minimize false mastery

- **WHEN** the diagnostic session concludes
- **THEN** all seeded `mastery_score` values MUST be in the range [0.3, 0.7)
- **AND** this conservative range ensures that even if a diagnosed node is not re-tested promptly, the teaching flow will still schedule it for review rather than skipping it as fully mastered
