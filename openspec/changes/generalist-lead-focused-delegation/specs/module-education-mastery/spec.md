## MODIFIED Requirements

### Requirement: Quiz response recording

The system SHALL persist every quiz interaction as a row in the `quiz_responses` table. A quiz response MUST capture the node, mind map, question text, user answer, SM-2 quality score (integer 0–5), response type, the writing invocation's lineage (`goal_id` and `invocation_id`) taken only from the verified broker assertion and null for a write with no assertion, optional evaluator notes (free-text rationale recorded by the grading session, added in migration `002_add_evaluator_notes`), and timestamp. A caller-supplied session ID MUST NOT be accepted as attribution; the legacy `session_id` column remains readable on rows written before cutover. A row written under an exposed assertion MUST be stamped tainted. The `quality` column MUST be constrained to values between 0 and 5 inclusive. The `mastery_record_response()` function MUST be the sole write path for quiz responses and MUST return the UUID of the newly created row.

Source: RFC 0038 D6, D7
Scope: v1-mandatory

#### Scenario: Successful response recording returns UUID

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="What is a list comprehension?", user_answer="A compact way to build lists", quality=4, response_type="review")` is called
- **THEN** the function MUST return a UUID corresponding to the newly inserted `quiz_responses` row
- **AND** querying `quiz_responses` by that UUID MUST return a row with all supplied field values
- **AND** `responded_at` MUST be set to the current timestamp (within a few seconds of the call)

#### Scenario: Response recorded with null user answer

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="Define recursion", user_answer=None, quality=0, response_type="review")` is called
- **THEN** the insert MUST succeed
- **AND** the resulting row MUST have `user_answer = NULL` and `quality = 0`

#### Scenario: Response recorded with optional session ID

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=3, response_type="teach")` runs inside a capability call carrying a verified assertion
- **THEN** the resulting row MUST record that assertion's `goal_id` and `invocation_id`
- **AND** any session or lineage identifier supplied in the call arguments MUST be ignored for attribution
- **AND** a row written under an exposed assertion MUST be stamped tainted

#### Scenario: Quality below 0 is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=-1, response_type="review")` is called
- **THEN** the function MUST raise a validation error or the database MUST reject the insert with a check constraint violation

#### Scenario: Quality above 5 is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=6, response_type="review")` is called
- **THEN** the function MUST raise a validation error or the database MUST reject the insert with a check constraint violation

#### Scenario: Node deletion cascades to quiz responses

- **WHEN** a `mind_map_nodes` row is deleted
- **THEN** all `quiz_responses` rows referencing that `node_id` MUST be automatically deleted via `ON DELETE CASCADE`

### Requirement: Node history retrieval

The `mastery_get_node_history(pool, node_id, limit?)` function SHALL return the quiz response history for a single node, ordered with the most recent response first. Each dict in the returned list MUST include at minimum: `id`, `question_text`, `user_answer`, `quality`, `response_type`, `goal_id`, `invocation_id`, the legacy `session_id` (null for rows written after cutover), the row's taint stamp, and `responded_at`. The `limit` parameter SHALL be optional; when provided it MUST cap the number of returned records. When omitted the function MUST return all responses for the node.

Source: RFC 0038 D6, D7
Scope: v1-mandatory

#### Scenario: Node history returned most recent first

- **WHEN** a node has responses recorded at T+0, T+10min, and T+20min
- **THEN** `mastery_get_node_history(pool, node_id)` MUST return them in order: T+20min, T+10min, T+0

#### Scenario: Node history respects limit parameter

- **WHEN** a node has 10 recorded responses
- **AND** `mastery_get_node_history(pool, node_id, limit=3)` is called
- **THEN** exactly 3 responses MUST be returned
- **AND** they MUST be the 3 most recent

#### Scenario: Node history returns all responses when limit omitted

- **WHEN** a node has 8 recorded responses
- **AND** `mastery_get_node_history(pool, node_id)` is called without a limit
- **THEN** all 8 responses MUST be returned

#### Scenario: Node history returns empty list for node with no responses

- **WHEN** a node has no recorded quiz responses
- **THEN** `mastery_get_node_history(pool, node_id)` MUST return `[]`

#### Scenario: Node history includes all required fields

- **WHEN** `mastery_get_node_history(pool, node_id, limit=1)` is called and a response exists
- **THEN** the returned dict MUST contain keys: `id`, `question_text`, `user_answer`, `quality`, `response_type`, `goal_id`, `invocation_id`, `session_id`, the taint stamp, and `responded_at`
