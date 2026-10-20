## MODIFIED Requirements

### Requirement: Flow Initialization — PENDING and DIAGNOSING

`teaching_flow_start(pool, topic, goal?)` creates a new mind map record and
writes initial flow state at `pending` **as one atomic operation**, then
immediately transitions to `diagnosing`, and returns the flow state dict. The
LLM session in `diagnosing` phase MUST generate 3–7 adaptive probe questions
using a binary-search strategy targeting the topic's concept inventory.

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

---

### Requirement: Curriculum Planning — PLANNING

While in `planning` status, the ephemeral session decomposes the topic into a
concept DAG and populates the mind map. The session MUST call
`mind_map_node_create()` and `mind_map_edge_create()` for each node and edge.
After the DAG is fully populated, the session calls `curriculum_generate()` to
validate, sequence, and activate the map, and then calls
`teaching_flow_advance()` to transition to `teaching`.

The transition out of `planning` is gated on the mind map actually holding a
graph. `teaching_flow_advance()` MUST reject a `planning` → `teaching`
transition when the mind map's `status` is not `active`, which — by the mind
map content invariant — is only possible once the map has at least one node. A
planning session that produced no nodes MUST NOT be able to advance the flow
to `teaching`, and MUST leave the flow in `planning` and the map in `draft`
for the staleness sweep to abandon.

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

- **WHEN** the session has completed DAG construction and `curriculum_generate()` has activated the mind map
- **THEN** `teaching_flow_advance()` is called, transitioning status to `teaching`
- **AND** the mind map's `status` is `'active'`
- **AND** `current_node_id` is set to the first frontier node (lowest sequence, all prerequisites mastered or none)
- **AND** `current_phase` is set to `explaining`

#### Scenario: Advancing to TEACHING with an empty mind map is rejected

- **WHEN** a session in `planning` status calls `teaching_flow_advance()` while the mind map still has zero nodes and `status = 'draft'`
- **THEN** the transition MUST be rejected with an error stating the curriculum has not been generated
- **AND** the flow MUST remain in `planning`
- **AND** the mind map's `status` MUST remain `'draft'`

---

### Requirement: Flow Tool API — Start, Get, Advance, Abandon

The four core flow tools (`teaching_flow_start`, `teaching_flow_get`,
`teaching_flow_advance`, `teaching_flow_abandon`) MUST be exposed as MCP tools
on the education butler's MCP server. Each tool operates on the education
butler's PostgreSQL pool and KV state store.

`teaching_flow_start` MUST create the mind map row and the flow state entry in
a single transaction, as specified in "Flow Initialization — PENDING and
DIAGNOSING". It MUST NOT return a mind map id whose flow state was not
committed alongside it.

#### Scenario: teaching_flow_start creates flow and returns initial state

- **WHEN** `teaching_flow_start(pool, topic="Rust", goal="Learn systems programming")` is called
- **THEN** a mind map row is created with `title = "Rust"`, `status = 'draft'`, and the goal stored in `metadata`
- **AND** the KV store is initialized with `status = 'pending'`, `session_count = 0`, and `started_at = now()`
- **AND** both writes are committed together
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

---

### Requirement: Staleness Detection and Auto-Abandonment

The implementation SHALL provide the behavior described by this requirement.
A weekly scheduled task (`stale-flow-check`) checks all active flows. Any flow with `last_session_at` more than 30 days before the check time is automatically abandoned. All pending review schedules for the abandoned mind map are deleted.

A weekly scheduled task (`stale-flow-check`) checks for unfinished learning
that has gone quiet. Its work list SHALL be drawn from rows in
`education.mind_maps` whose status is `draft` or `active` — **not** from the
set of `flow:*` KV keys. Keying the sweep off flow state made a mind map whose
flow state was never written structurally unreachable by it; enumerating mind
map rows closes that gap.

For each map in the work list:

- A map with flow state whose `last_session_at` is more than 30 days before
  the check time is abandoned via `teaching_flow_abandon()`.
- A `draft` map with zero nodes whose `created_at` is more than 24 hours
  before the check time is abandoned, whether or not it has flow state.
- A map with no flow state at all is abandoned through
  `mind_map_update_status()` directly, since there is no flow to abandon.

All pending review schedules for an abandoned mind map are deleted.

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
- **THEN** mind maps with `status IN ('completed', 'abandoned')` are not evaluated for staleness

#### Scenario: Mind map with no flow state is still swept

- **WHEN** the `stale-flow-check` fires
- **AND** a `draft` mind map created more than 24 hours ago has zero nodes and no `flow:{mind_map_id}` KV entry
- **THEN** the task MUST still evaluate that map, because the work list comes from `education.mind_maps`
- **AND** the map's `status` MUST be set to `'abandoned'` via `mind_map_update_status()`

#### Scenario: Stalled draft with flow state is abandoned through the flow

- **WHEN** the `stale-flow-check` fires
- **AND** a `draft` mind map created more than 24 hours ago has zero nodes and a `flow:{mind_map_id}` entry stuck in `diagnosing`
- **THEN** `teaching_flow_abandon()` MUST be called for that flow
- **AND** the mind map's `status` MUST become `'abandoned'`

#### Scenario: Freshly started draft is not swept

- **WHEN** the `stale-flow-check` fires
- **AND** a `draft` mind map was created 3 hours ago and its flow is in `diagnosing`
- **THEN** the map and flow MUST be left unchanged
