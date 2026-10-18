## MODIFIED Requirements

### Requirement: Quiz response recording

The system SHALL persist every quiz interaction as a row in the `quiz_responses` table. A quiz response MUST capture the node, mind map, question text, user answer, SM-2 quality score (integer 0–5, or absent only for a captured-but-ungraded answer, see "Owner evidence gates mastery movement"), response type, the writing invocation's lineage taken only from the runtime's verified provenance for the call (never from a caller-supplied session identifier, and null where the runtime supplies none), optional evaluator notes (free-text rationale recorded by the grading session, added in migration `002_add_evaluator_notes`), and timestamp. The `quality` column MUST be constrained to values between 0 and 5 inclusive. The `mastery_record_response()` function MUST be the sole write path for quiz responses and MUST return the UUID of the newly created row.

Every quiz response is evidence-bearing, because every response type can move a node's mastery. `user_answer` MUST therefore be present and nonblank (not `NULL`, not empty, not whitespace-only), and the function MUST reject a null or blank answer, together with any call that does not meet the owner-evidence requirement ("Owner evidence gates mastery movement"), before it writes any row: no `quiz_responses` row, no node projection change, no transition, and no review card SHALL result from a rejected call. A response with no owner answer is not a score of 0; it is no evidence.

ID: REQ-module-education-mastery-001
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Successful response recording returns UUID

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="What is a list comprehension?", user_answer="A compact way to build lists", quality=4, response_type="review")` is called
- **THEN** the function MUST return a UUID corresponding to the newly inserted `quiz_responses` row
- **AND** querying `quiz_responses` by that UUID MUST return a row with all supplied field values
- **AND** `responded_at` MUST be set to the current timestamp (within a few seconds of the call)

#### Scenario: Response recorded with null user answer

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="Define recursion", user_answer=None, quality=0, response_type="review")` is called
- **THEN** the function MUST raise a validation error
- **AND** no `quiz_responses` row MUST be inserted
- **AND** the node's `mastery_status` and `mastery_score` MUST be unchanged
- **AND** no mastery transition and no review card MUST be written

#### Scenario: Response with blank user answer is rejected without effect

- **WHEN** `mastery_record_response()` is called with `user_answer="   "`
- **THEN** the function MUST raise a validation error before any database write

#### Scenario: Response recorded with optional session ID

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=3, response_type="teach")` is called with a lineage or session identifier among its arguments
- **THEN** any identifier supplied in the call arguments MUST be ignored for attribution
- **AND** the writing invocation's lineage, where the runtime records one, MUST come only from the runtime's verified provenance for the call and MUST be null when the runtime supplies none

#### Scenario: Quality below 0 is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=-1, response_type="review")` is called
- **THEN** the function MUST raise a validation error or the database MUST reject the insert with a check constraint violation

#### Scenario: Quality above 5 is rejected

- **WHEN** `mastery_record_response(pool, node_id, mind_map_id, question_text="...", user_answer="...", quality=6, response_type="review")` is called
- **THEN** the function MUST raise a validation error or the database MUST reject the insert with a check constraint violation

#### Scenario: Node deletion cascades to quiz responses

- **WHEN** a `mind_map_nodes` row is deleted
- **THEN** all `quiz_responses` rows referencing that `node_id` MUST be automatically deleted via `ON DELETE CASCADE`

### Requirement: Mastery status state machine

The `mastery_status` column on `mind_map_nodes` SHALL follow the state machine defined by module-education-mind-map "Mastery status state machine", which is the single source for the set of permitted transitions. The `mastery_record_response()` function MUST evaluate and apply the appropriate state transition after every quiz response, and it applies only these response-driven transitions from that set:

- `unseen` → `diagnosed`: after a diagnostic response is recorded for the node
- `unseen` → `learning`: when a `teach` response is recorded and no prior diagnostic response exists
- `diagnosed` → `learning`: when a `teach` response is recorded, OR when a quiz response reveals poor understanding (quality < 3) on a node in `diagnosed` status (self-correction)
- `learning` → `reviewing`: when a quiz response has `quality >= 3` on a node currently in `learning` status
- `reviewing` → `mastered`: when the mastery threshold is met (see mastery threshold requirement)
- `reviewing` → `learning`: when a quiz response has `quality < 3` on a node currently in `reviewing` status (regression)
- `mastered` → `reviewing`: when an owner-evidenced `review` response has `quality < 3` on a node currently in `mastered` status (regression on failed retention)

Every transition above SHALL be driven only by a response that meets the owner-evidence requirement, and every transition that actually changes `mastery_status` SHALL be appended to the mastery transition ledger in the same transaction as the projection update. A response that leaves `mastery_status` unchanged SHALL NOT write a ledger entry.

ID: REQ-module-education-mastery-002
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

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

#### Scenario: Mastered node regresses only on owner-evidenced failed retention

- **WHEN** a node has `mastery_status = 'mastered'`
- **AND** `mastery_record_response()` is called with `response_type="review"`, `quality=1` and owner evidence
- **THEN** the node's `mastery_status` MUST be updated to `'reviewing'`
- **AND** one ledger entry from `mastered` to `reviewing` MUST be appended

#### Scenario: Mastered node is not regressed

- **WHEN** a node has `mastery_status = 'mastered'`
- **AND** a call carries `quality=1` but no owner evidence
- **THEN** the node's `mastery_status` MUST remain `'mastered'`
- **AND** no ledger entry MUST be appended

#### Scenario: Diagnosed node does not skip to reviewing

- **WHEN** a node has `mastery_status = 'diagnosed'`
- **AND** `mastery_record_response()` is called with `response_type="review"` and `quality=5`
- **THEN** the node's `mastery_status` MUST NOT be set to `'reviewing'` or `'mastered'`
- **AND** the transition to `reviewing` MUST only occur after the node passes through `learning`

### Requirement: Map-level mastery summary

The `mastery_get_map_summary(pool, mind_map_id)` function SHALL return aggregate mastery statistics for all nodes in a mind map. The returned dict MUST include at minimum: `total_nodes`, `mastered_count`, `learning_count`, `reviewing_count`, `unseen_count`, `diagnosed_count`, `avg_mastery_score`, `measured_over`, and `struggling_node_ids` (list of UUIDs for nodes currently flagged as struggling per the struggle detection rules). The function MUST compute all statistics from the current state of `mind_map_nodes` and `quiz_responses` at query time.

`measured_over` MUST be the number of nodes in the map that have at least one graded, owner-evidenced `quiz_responses` row, and `avg_mastery_score` MUST be the mean `mastery_score` over exactly those nodes. When `measured_over` is `0`, `avg_mastery_score` MUST be `None` rather than `0.0`: a mean over no evidence is not a score. A `0.0` average MUST be returned only when `measured_over > 0` and the measured scores average zero.

ID: REQ-module-education-mastery-003
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Map summary counts nodes by mastery status

- **WHEN** a mind map has 10 nodes: 3 mastered, 4 reviewing, 2 learning, 1 unseen
- **THEN** `mastery_get_map_summary()` MUST return `mastered_count=3`, `reviewing_count=4`, `learning_count=2`, `unseen_count=1`, `diagnosed_count=0`

#### Scenario: Map summary total nodes matches sum of all status counts

- **WHEN** `mastery_get_map_summary(pool, mind_map_id)` is called
- **THEN** `total_nodes` MUST equal the sum of `mastered_count + reviewing_count + learning_count + unseen_count + diagnosed_count`

#### Scenario: Map summary avg_mastery_score is mean of all node mastery scores

- **WHEN** a mind map has 3 nodes with graded `mastery_score` values `0.6`, `0.8`, and `1.0`
- **THEN** `mastery_get_map_summary()` MUST return `avg_mastery_score` approximately equal to `0.8` (within floating-point tolerance)
- **AND** `measured_over` MUST equal `3`

#### Scenario: Map summary average excludes nodes with no graded evidence

- **WHEN** a mind map has 4 nodes, of which 2 have graded scores `0.5` and `1.0` and 2 have never been graded
- **THEN** `mastery_get_map_summary()` MUST return `measured_over=2` and `avg_mastery_score` approximately equal to `0.75`

#### Scenario: Map summary includes struggling node IDs

- **WHEN** two nodes in a mind map are flagged as struggling (per struggle detection rules)
- **THEN** `mastery_get_map_summary()` MUST return `struggling_node_ids` containing exactly those two node UUIDs

#### Scenario: Map summary for empty mind map

- **WHEN** a mind map has zero nodes
- **THEN** `mastery_get_map_summary()` MUST return `total_nodes=0`, `mastered_count=0`, `measured_over=0`, `avg_mastery_score=None`, and `struggling_node_ids=[]`

#### Scenario: Map summary with nodes but no graded evidence has a null average

- **WHEN** a mind map has 5 nodes and no graded owner-evidenced response
- **THEN** `mastery_get_map_summary()` MUST return `total_nodes=5`, `measured_over=0` and `avg_mastery_score=None`

#### Scenario: Map summary scoped to requested mind map only

- **WHEN** the database contains two mind maps, each with nodes at varying mastery states
- **AND** `mastery_get_map_summary(pool, mind_map_id=map_A)` is called
- **THEN** the returned counts and scores MUST reflect only nodes belonging to `map_A`

## ADDED Requirements

### Requirement: Owner evidence gates mastery movement

A node's `mastery_status` and `mastery_score` SHALL move only on **owner evidence**: a nonblank answer whose provenance the runtime itself derives and verifies for the call, identifying the owner as its originator; the property is that the runtime vouches for the origin, not which mechanism carries the proof. A caller-supplied session or lineage identifier, `evidence_kind` or `evidence_ref` is not authority and SHALL NOT, by itself, establish owner evidence. The recorded `evidence_kind` and `evidence_ref` SHALL be those the server derived.

An invocation that was started by the scheduler, or that was model-initiated, has no owner-answer authority. Such an invocation MAY create or ask a review card, but SHALL NOT grade mastery unless it presents a distinct owner-originated evidence reference. The language model MAY grade a captured owner answer; it SHALL NOT manufacture the answer, its provenance, a delivery outcome, a curriculum association, or a transition timestamp.

A call that lacks owner evidence SHALL be rejected before any database write (see "Quiz response recording"). Replaying the same owner evidence reference SHALL be idempotent: it SHALL yield exactly one `quiz_responses` row and at most one ledger entry, and SHALL return the existing result. Two different owner answers for the same node SHALL be serialized with a row lock on the node, so the second is evaluated against the committed result of the first and no update is lost.

When an owner answer has been captured but grading fails, the answer SHALL be retained as a `quiz_responses` row with its `user_answer` and owner evidence reference and with a null `quality` (the 0–5 constraint applies only when `quality` is present), for every response type including `teach` and `diagnostic`. Such an ungraded row SHALL NOT move `mastery_status` or `mastery_score`, SHALL NOT advance SM-2 state, SHALL NOT write a ledger entry, and SHALL NOT count toward `measured_over`, the summary average, or velocity. When the response was a review, the corresponding review card is `answered`. A later grading call carrying the same owner evidence reference SHALL complete the existing row rather than inserting a second one. When a failure occurs before the answer is captured, no answer and no mastery claim SHALL be invented.

ID: REQ-module-education-mastery-004
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Owner answer moves mastery

- **WHEN** a nonblank answer with server-derived owner provenance is graded `quality=4` on a `learning` node
- **THEN** the node's `mastery_status` SHALL become `reviewing`

#### Scenario: Scheduled invocation alone cannot grade

- **WHEN** a scheduler-started invocation calls `mastery_record_response()` with a nonblank answer it composed itself and no owner-originated evidence reference
- **THEN** the call SHALL be rejected before any write
- **AND** mastery SHALL be unchanged

#### Scenario: Caller-supplied provenance is not authority

- **WHEN** a call supplies `evidence_kind="owner_message"` and an `evidence_ref` that the server's captured context does not support
- **THEN** the call SHALL be rejected before any write

#### Scenario: Replay of the same evidence is idempotent

- **WHEN** `mastery_record_response()` is called twice with the same owner evidence reference
- **THEN** exactly one `quiz_responses` row SHALL exist
- **AND** at most one ledger entry SHALL exist
- **AND** the second call SHALL return the existing result

#### Scenario: Different answers for one node serialize

- **WHEN** two different owner answers for the same node arrive concurrently
- **THEN** they SHALL be applied one after the other under a row lock on the node
- **AND** the second SHALL be evaluated against the node state the first committed

#### Scenario: Grader failure preserves an ungraded answer

- **WHEN** an owner answer was captured and the grader fails
- **THEN** the answer SHALL remain recorded as a `quiz_responses` row with null `quality`, for a `teach` or `diagnostic` response as well as a `review`
- **AND** `mastery_status` and `mastery_score` SHALL be unchanged
- **AND** no ledger entry SHALL be written and no SM-2 field SHALL change
- **AND** the row SHALL NOT be counted in `measured_over`

#### Scenario: A later grading call completes the ungraded row

- **WHEN** a grading call arrives with the same owner evidence reference as an ungraded row
- **THEN** that row SHALL be graded in place and exactly one `quiz_responses` row SHALL exist for the reference

### Requirement: Mastery transition ledger

Every actual change of a node's `mastery_status` SHALL append one entry to `education.mastery_transitions`, in the same Education-schema transaction as the projection update, the `quiz_responses` row, the SM-2 state update and the creation of the next review card. If any of these fails, none of them SHALL persist.

An entry SHALL carry `id`, `node_id`, `mind_map_id`, `from_status`, `to_status`, `quiz_response_id`, `review_card_id`, `evidence_kind`, `evidence_ref`, `origin` (`evidence` or `legacy_unknown`) and `occurred_at`. `occurred_at` SHALL be set by the database at write time and SHALL NOT be supplied by a caller or a language model. A non-null `quiz_response_id` SHALL be unique across the ledger, so one response yields at most one entry. A response that leaves `mastery_status` unchanged SHALL write no entry.

`mind_map_nodes.mastery_status` and `mastery_score` are the current projection; the ledger is the audit history that explains it. A node that was already in a non-`unseen` status before the ledger existed MAY receive exactly one entry marked `legacy_unknown`, which records only that the node was already in that status. A `legacy_unknown` entry SHALL carry no `quiz_response_id`, no `review_card_id`, and no fabricated evidence, and its `occurred_at` records when the ledger row was written, never when mastery was reached. Backfill SHALL be idempotent: re-running it SHALL NOT add a second legacy entry for a node.

Accumulated ledger entries SHALL NOT be deleted by a rollback of the code that writes them.

ID: REQ-module-education-mastery-005
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: A status change appends one entry in the same transaction

- **WHEN** an owner-evidenced response moves a node from `learning` to `reviewing`
- **THEN** exactly one entry from `learning` to `reviewing` SHALL exist with the response's `quiz_response_id` and `evidence_kind`
- **AND** the entry, the projection update and the `quiz_responses` row SHALL commit together or not at all

#### Scenario: A no-op grade writes no entry

- **WHEN** an owner-evidenced response leaves the node's `mastery_status` unchanged
- **THEN** no ledger entry SHALL be written

#### Scenario: A failure rolls the whole write back

- **WHEN** the ledger insert fails after the projection update
- **THEN** the projection update and the `quiz_responses` row SHALL NOT persist

#### Scenario: One response yields at most one entry

- **WHEN** two concurrent calls carry the same owner evidence reference
- **THEN** the ledger SHALL contain at most one entry with that `quiz_response_id`

#### Scenario: Legacy mastered node is marked, not proved

- **WHEN** the ledger is introduced and a node is already `mastered`
- **THEN** it SHALL receive one entry with `origin` `legacy_unknown` and null `quiz_response_id` and `review_card_id`
- **AND** re-running the backfill SHALL NOT create a second entry

### Requirement: One public evidence writer

The public Education tool surface SHALL expose exactly one path that changes mastery projection from evidence: `mastery_record_response()`. All evidence-bearing producers (diagnostic assessment, teaching sessions, review sessions, curriculum diagnostic seeding, and SM-2 recording) SHALL reach the learning state through one shared internal transaction that implements the owner-evidence gate and the transition ledger.

`spaced_repetition_record_response()` SHALL NOT be a second public writer of mastery state; the pure SM-2 interval calculation MAY remain as an internal function. The generic node-update tool (`mind_map_node_update`) SHALL NOT accept `mastery_status` or `mastery_score` writes. Skills and instructions SHALL NOT direct a session to call both a mastery writer and a spaced-repetition writer for one answer.

ID: REQ-module-education-mastery-006
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Generic node update cannot set mastery

- **WHEN** `mind_map_node_update()` is called with a `mastery_status` or `mastery_score` argument
- **THEN** the call SHALL be rejected
- **AND** the node's mastery SHALL be unchanged

#### Scenario: Diagnostic seeding uses the shared transaction

- **WHEN** curriculum generation seeds diagnostic mastery on a node
- **THEN** the change SHALL pass through the same owner-evidence gate and ledger as a review response
- **AND** a seed with no owner evidence SHALL leave the node `unseen`

#### Scenario: Only one public tool records a graded answer

- **WHEN** the registered Education tool names are listed
- **THEN** `mastery_record_response` SHALL be present
- **AND** no other registered tool SHALL write `mastery_status`, `mastery_score`, or a ledger entry
