## ADDED Requirements

### Requirement: Quiz response recording

The system SHALL persist every quiz interaction as a row in the `quiz_responses` table. A quiz response MUST capture the node, mind map, question text, user answer, SM-2 quality score (integer 0–5), response type, optional session ID, and timestamp. The `quality` column MUST be constrained to values between 0 and 5 inclusive. The `mastery_record_response()` function MUST be the sole write path for quiz responses and MUST return the UUID of the newly created row.

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

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=3, response_type="teach", session_id=some_uuid)` is called
- **THEN** the resulting row MUST have `session_id` set to `some_uuid`

#### Scenario: Quality below 0 is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=-1, response_type="review")` is called
- **THEN** the function MUST raise a validation error or the database MUST reject the insert with a check constraint violation

#### Scenario: Quality above 5 is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=6, response_type="review")` is called
- **THEN** the function MUST raise a validation error or the database MUST reject the insert with a check constraint violation

#### Scenario: Node deletion cascades to quiz responses

- **WHEN** a `mind_map_nodes` row is deleted
- **THEN** all `quiz_responses` rows referencing that `node_id` MUST be automatically deleted via `ON DELETE CASCADE`

---

### Requirement: Response type classification

Every quiz response MUST be classified into one of three types: `diagnostic`, `teach`, or `review`. The `response_type` column MUST default to `'review'` when not supplied. The type is determined by the caller at record time and MUST NOT be inferred or overwritten by the storage layer. Analytics queries MUST be able to filter by `response_type` to compute type-specific metrics (e.g., retention rate using only `review` responses).

#### Scenario: Diagnostic probe recorded with type diagnostic

- **WHEN** a quiz response is recorded during diagnostic assessment and `response_type="diagnostic"` is passed
- **THEN** the stored row MUST have `response_type = 'diagnostic'`

#### Scenario: Teaching quiz recorded with type teach

- **WHEN** a quiz response is recorded during a first-exposure teaching session and `response_type="teach"` is passed
- **THEN** the stored row MUST have `response_type = 'teach'`

#### Scenario: Spaced repetition review recorded with type review

- **WHEN** a quiz response is recorded during a spaced repetition review session and `response_type="review"` is passed
- **THEN** the stored row MUST have `response_type = 'review'`

#### Scenario: Default response type is review

- **WHEN** `mastery_record_response()` is called without a `response_type` argument
- **THEN** the stored row MUST have `response_type = 'review'`

#### Scenario: Invalid response type is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=3, response_type="exam")` is called
- **THEN** the function MUST raise a validation error

---

### Requirement: Mastery score computation

After each quiz response is recorded, the system MUST recompute the `mastery_score` for the affected `mind_map_nodes` row. The mastery score SHALL be the weighted average of the quality scores from the node's most recent five responses, divided by 5.0, where more recent responses are weighted higher using exponential decay. The updated `mastery_score` MUST be written to `mind_map_nodes.mastery_score` atomically within the same transaction as the `quiz_responses` insert.

#### Scenario: Mastery score computed from single response

- **WHEN** a node has no prior quiz responses and a response with `quality=4` is recorded
- **THEN** the node's `mastery_score` MUST be updated to `4 / 5.0 = 0.8`

#### Scenario: Mastery score computed from five responses with recency weighting

- **WHEN** a node has responses with qualities `[2, 3, 4, 4, 5]` (oldest to newest)
- **THEN** the node's `mastery_score` MUST reflect that the most recent response (quality=5) is weighted more heavily than the oldest (quality=2)
- **AND** the resulting score MUST be greater than `(2+3+4+4+5) / (5*5.0) = 0.72` (equal-weight baseline)

#### Scenario: Mastery score capped at 1.0

- **WHEN** a node has five perfect responses each with `quality=5`
- **THEN** the node's `mastery_score` MUST equal `1.0`

#### Scenario: Mastery score floored at 0.0

- **WHEN** a node has five complete-blackout responses each with `quality=0`
- **THEN** the node's `mastery_score` MUST equal `0.0`

#### Scenario: Mastery score uses at most five most recent responses

- **WHEN** a node has eight recorded responses
- **THEN** only the five most recent (ordered by `responded_at DESC`) MUST be used in the weighted average

#### Scenario: Mastery score updated atomically with quiz response

- **WHEN** `mastery_record_response()` is called
- **THEN** the `quiz_responses` insert and the `mind_map_nodes.mastery_score` update MUST succeed or fail together in the same database transaction

---

### Requirement: Mastery status state machine

The `mastery_status` column on `mind_map_nodes` SHALL follow a defined state machine. Only valid transitions MUST be applied; invalid transitions MUST NOT modify the column. The `mastery_record_response()` function MUST evaluate and apply the appropriate state transition after every quiz response. The valid transitions are:

- `unseen` → `diagnosed`: after a diagnostic response is recorded for the node
- `unseen` → `learning`: when a `teach` response is recorded and no prior diagnostic response exists
- `diagnosed` → `learning`: when a `teach` response is recorded, OR when a quiz response reveals poor understanding (quality < 3) on a node in `diagnosed` status (self-correction)
- `learning` → `reviewing`: when a quiz response has `quality >= 3` on a node currently in `learning` status
- `reviewing` → `mastered`: when the mastery threshold is met (see mastery threshold requirement)
- `reviewing` → `learning`: when a quiz response has `quality < 3` on a node currently in `reviewing` status (regression)

#### Scenario: Unseen node transitions to diagnosed on diagnostic response

- **WHEN** a node has `mastery_status = 'unseen'`
- **AND** `mastery_record_response()` is called with `response_type="diagnostic"`
- **THEN** the node's `mastery_status` MUST be updated to `'diagnosed'`

#### Scenario: Unseen node transitions to learning on teach response without prior diagnostic

- **WHEN** a node has `mastery_status = 'unseen'` and has no recorded `diagnostic` responses
- **AND** `mastery_record_response()` is called with `response_type="teach"`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`

#### Scenario: Diagnosed node transitions to learning on teach response

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `response_type="teach"`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`

#### Scenario: Learning node transitions to reviewing on successful quiz

- **WHEN** a node has `mastery_status = 'learning'`
- **AND** `mastery_record_response()` is called with `quality=3`
- **THEN** the node's `mastery_status` MUST be updated to `'reviewing'`

#### Scenario: Learning node remains learning on failed quiz

- **WHEN** a node has `mastery_status = 'learning'`
- **AND** `mastery_record_response()` is called with `quality=2`
- **THEN** the node's `mastery_status` MUST remain `'learning'`

#### Scenario: Reviewing node regresses to learning on failed review

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** `mastery_record_response()` is called with `quality=2`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`

#### Scenario: Reviewing node remains reviewing on successful review below mastery threshold

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** `mastery_record_response()` is called with `quality=4`
- **AND** the mastery threshold conditions are NOT yet met
- **THEN** the node's `mastery_status` MUST remain `'reviewing'`

#### Scenario: Mastered node is not regressed

- **WHEN** a node has `mastery_status = 'mastered'`
- **AND** `mastery_record_response()` is called with `quality=1`
- **THEN** the node's `mastery_status` MUST remain `'mastered'` (mastered nodes are not demoted via this mechanism)

#### Scenario: Diagnosed node does not skip to reviewing

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `response_type="review"` and `quality=5`
- **THEN** the node's `mastery_status` MUST NOT be set to `'reviewing'` or `'mastered'`
- **AND** the transition to `reviewing` MUST only occur after the node passes through `learning`

---

### Requirement: Mastery threshold for graduation to mastered

A node in `reviewing` status SHALL be promoted to `mastered` when BOTH of the following conditions are simultaneously true after recording a quiz response:

1. `mastery_score >= 0.85`
2. The three most recent `quiz_responses` for the node (ordered by `responded_at DESC`) all have `quality >= 4`

Both conditions MUST be evaluated atomically within `mastery_record_response()` and MUST be checked using only `review`-type responses for the last-three-quality condition. The promotion MUST happen in the same transaction as the quiz response insert.

#### Scenario: Node promoted to mastered when both threshold conditions met

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** the three most recent `review` responses all have `quality >= 4`
- **AND** the resulting `mastery_score >= 0.85`
- **THEN** after `mastery_record_response()` completes the node's `mastery_status` MUST be `'mastered'`

#### Scenario: Node not promoted when mastery score below threshold

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** the three most recent `review` responses all have `quality=4`
- **AND** the resulting `mastery_score = 0.80` (below 0.85)
- **THEN** the node's `mastery_status` MUST remain `'reviewing'`

#### Scenario: Node not promoted when last three reviews not all quality 4 or above

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** the three most recent `review` responses have qualities `[4, 3, 4]`
- **AND** the resulting `mastery_score = 0.88` (above 0.85)
- **THEN** the node's `mastery_status` MUST remain `'reviewing'` (quality=3 in last three disqualifies graduation)

#### Scenario: Fewer than three review responses never triggers mastery graduation

- **WHEN** a node has `mastery_status = 'reviewing'`
- **AND** the node has only two recorded `review` responses, both with `quality=5`
- **AND** `mastery_score = 1.0`
- **THEN** the node's `mastery_status` MUST remain `'reviewing'`

#### Scenario: Non-review responses excluded from last-three check

- **WHEN** a node in `reviewing` status has review responses with qualities `[5, 5, 5]` followed by a `teach` response with `quality=2`
- **THEN** the `teach` response MUST NOT be included in the last-three-reviews check
- **AND** if `mastery_score >= 0.85`, the node MUST be promoted to `'mastered'`

---

### Requirement: Struggle detection

The system SHALL identify nodes as "struggling" when either of the following conditions is true at the time of query:

1. The three most recent quiz responses for the node (any `response_type`) have `quality <= 2`
2. The node's `mastery_score` has decreased over the last three responses (i.e., the score computed from each rolling window of responses is lower than the previous)

The `mastery_detect_struggles(pool, mind_map_id)` function MUST return a list of dicts, each describing a struggling node, including the node's `id`, `label`, `mastery_score`, `mastery_status`, and the reason for the struggle flag (`consecutive_low_quality` or `declining_score`). Nodes with `mastery_status = 'mastered'` MUST be excluded from struggle detection.

#### Scenario: Node flagged for consecutive low quality responses

- **WHEN** a node's three most recent quiz responses all have `quality <= 2`
- **AND** the node has `mastery_status != 'mastered'`
- **THEN** `mastery_detect_struggles()` MUST include that node in the returned list with `reason = 'consecutive_low_quality'`

#### Scenario: Node flagged for declining mastery score

- **WHEN** a node's mastery score has decreased across its last three responses
- **AND** the node has `mastery_status != 'mastered'`
- **THEN** `mastery_detect_struggles()` MUST include that node in the returned list with `reason = 'declining_score'`

#### Scenario: Node flagged for both reasons

- **WHEN** a node satisfies both the consecutive low quality and declining score conditions
- **THEN** `mastery_detect_struggles()` MUST include the node once and the `reason` field MUST indicate both conditions (e.g., both reasons listed or a combined value)

#### Scenario: Mastered node excluded from struggle detection

- **WHEN** a node has `mastery_status = 'mastered'` and its last three responses all have `quality=1` (e.g., from re-quiz after mastery)
- **THEN** `mastery_detect_struggles()` MUST NOT include that node

#### Scenario: Node with fewer than three responses not flagged

- **WHEN** a node has only two recorded quiz responses, both with `quality=0`
- **THEN** `mastery_detect_struggles()` MUST NOT flag that node (insufficient history for three-consecutive check)

#### Scenario: Detect struggles returns empty list for map with no struggling nodes

- **WHEN** all nodes in a mind map have consistently high quality responses
- **THEN** `mastery_detect_struggles(pool, mind_map_id)` MUST return an empty list `[]`

---

### Requirement: Diagnosed node self-correction

When a `quiz_response` is recorded for a node in `diagnosed` status and the quiz performance reveals poor understanding (`quality < 3`), the node's `mastery_status` MUST be demoted from `diagnosed` to `learning`. This self-correction applies to responses of any `response_type`. The diagnostic `mastery_score` seed (typically 0.3–0.7) MUST be overwritten by the recomputed score based on actual quiz performance.

#### Scenario: Diagnosed node demoted on poor quiz result

- **WHEN** a node has `mastery_status = 'diagnosed'` with `mastery_score = 0.6` (seeded by diagnostic)
- **AND** `mastery_record_response()` is called with `quality=1`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'`
- **AND** the node's `mastery_score` MUST be recomputed using the new quality=1 response

#### Scenario: Diagnosed node promoted to learning on poor teach-type quiz

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `response_type="teach"` and `quality=2`
- **THEN** the node's `mastery_status` MUST be updated to `'learning'` (triggered by both the teach type and the self-correction rule)

#### Scenario: Diagnosed node retained on strong quiz performance

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `quality=4` and `response_type="review"`
- **THEN** the node's `mastery_status` MUST NOT be demoted to `'learning'` due to self-correction
- **AND** the state machine MUST apply normal transition logic for the `diagnosed` state

#### Scenario: Self-correction does not apply to unseen nodes

- **WHEN** a node has `mastery_status = 'unseen'`
- **AND** `mastery_record_response()` is called with `quality=0` and `response_type="review"`
- **THEN** self-correction logic MUST NOT apply; normal state machine transitions for `unseen` MUST be used

---

### Requirement: Node history retrieval

The `mastery_get_node_history(pool, node_id, limit?)` function SHALL return the quiz response history for a single node, ordered with the most recent response first. Each dict in the returned list MUST include at minimum: `id`, `question_text`, `user_answer`, `quality`, `response_type`, `session_id`, and `responded_at`. The `limit` parameter SHALL be optional; when provided it MUST cap the number of returned records. When omitted the function MUST return all responses for the node.

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
- **THEN** the returned dict MUST contain keys: `id`, `question_text`, `user_answer`, `quality`, `response_type`, `session_id`, `responded_at`

---

### Requirement: Map-level mastery summary

The `mastery_get_map_summary(pool, mind_map_id)` function SHALL return aggregate mastery statistics for all nodes in a mind map. The returned dict MUST include at minimum: `total_nodes`, `mastered_count`, `learning_count`, `reviewing_count`, `unseen_count`, `diagnosed_count`, `avg_mastery_score`, and `struggling_node_ids` (list of UUIDs for nodes currently flagged as struggling per the struggle detection rules). The function MUST compute all statistics from the current state of `mind_map_nodes` and `quiz_responses` at query time.

#### Scenario: Map summary counts nodes by mastery status

- **WHEN** a mind map has 10 nodes: 3 mastered, 4 reviewing, 2 learning, 1 unseen
- **THEN** `mastery_get_map_summary()` MUST return `mastered_count=3`, `reviewing_count=4`, `learning_count=2`, `unseen_count=1`, `diagnosed_count=0`

#### Scenario: Map summary total nodes matches sum of all status counts

- **WHEN** `mastery_get_map_summary(pool, mind_map_id)` is called
- **THEN** `total_nodes` MUST equal the sum of `mastered_count + reviewing_count + learning_count + unseen_count + diagnosed_count`

#### Scenario: Map summary avg_mastery_score is mean of all node mastery scores

- **WHEN** a mind map has 3 nodes with `mastery_score` values `0.6`, `0.8`, and `1.0`
- **THEN** `mastery_get_map_summary()` MUST return `avg_mastery_score` approximately equal to `0.8` (within floating-point tolerance)

#### Scenario: Map summary includes struggling node IDs

- **WHEN** two nodes in a mind map are flagged as struggling (per struggle detection rules)
- **THEN** `mastery_get_map_summary()` MUST return `struggling_node_ids` containing exactly those two node UUIDs

#### Scenario: Map summary for empty mind map

- **WHEN** a mind map has zero nodes
- **THEN** `mastery_get_map_summary()` MUST return `total_nodes=0`, `mastered_count=0`, `avg_mastery_score=0.0`, and `struggling_node_ids=[]`

#### Scenario: Map summary scoped to requested mind map only

- **WHEN** the database contains two mind maps, each with nodes at varying mastery states
- **AND** `mastery_get_map_summary(pool, mind_map_id=map_A)` is called
- **THEN** the returned counts and scores MUST reflect only nodes belonging to `map_A`
