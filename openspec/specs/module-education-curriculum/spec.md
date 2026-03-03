# Education Curriculum Planning

## Purpose

Defines the curriculum planning system for the education butler: LLM-driven topic decomposition into concept DAGs, structural constraints (max depth, max nodes, acyclicity), topological sort with tie-breaking, sequence numbering, re-planning, goal-directed planning, diagnostic results integration, next-node selection, and syllabus lifecycle.

## ADDED Requirements

### Requirement: Topic decomposition into concept graph

When `curriculum_generate()` is invoked, the system SHALL spawn an ephemeral LLM session using a curriculum-planning skill prompt that instructs the session to decompose the given topic into a set of concept nodes and prerequisite edges. The LLM session MUST output structured JSON representing nodes (each with `label`, `description`, and `effort_minutes`) and edges (each specifying a `parent` → `child` prerequisite relationship). The session MUST persist this graph by calling `mind_map_create()`, `mind_map_node_create()`, and `mind_map_edge_create()` tool calls. The skill prompt MUST require the LLM to produce a root node representing the topic itself, with all other nodes reachable from it via directed prerequisite edges.

#### Scenario: Basic topic decomposition creates nodes and edges

- **WHEN** `curriculum_generate(pool, mind_map_id, topic="Python Fundamentals")` is called
- **THEN** the LLM session MUST call `mind_map_node_create()` for each concept node with `label`, `description`, and `effort_minutes` populated
- **AND** the session MUST call `mind_map_edge_create()` for each prerequisite relationship with `parent_node_id` and `child_node_id`
- **AND** the resulting `mind_map_nodes` rows MUST all have `mind_map_id` matching the provided mind map
- **AND** at least one node MUST be designated as the root (stored in `mind_maps.root_node_id`)

#### Scenario: Every node has a populated effort estimate

- **WHEN** the LLM session creates nodes via `mind_map_node_create()`
- **THEN** every node MUST have `effort_minutes` set to a positive integer
- **AND** no node MAY have `effort_minutes = NULL` or `effort_minutes = 0`

#### Scenario: Root node is recorded on the mind map

- **WHEN** the LLM session completes the decomposition
- **THEN** `mind_maps.root_node_id` MUST be set to the UUID of the root concept node
- **AND** the root node MUST have `depth = 0`
- **AND** all other nodes MUST have `depth >= 1`

#### Scenario: All edges are prerequisite edges by default

- **WHEN** the LLM session calls `mind_map_edge_create()` without specifying `edge_type`
- **THEN** the persisted edge MUST have `edge_type = 'prerequisite'`

---

### Requirement: Structural constraints on concept graph

The curriculum planning system SHALL enforce the following structural constraints on every generated concept graph:

- Maximum node depth of 5 (root is depth 0; no node may exceed depth 5)
- Maximum of 30 nodes per single-topic mind map
- All edges MUST form a directed acyclic graph (DAG); cycles MUST be rejected

The skill prompt MUST communicate these constraints to the LLM session. The `mind_map_edge_create()` tool MUST perform DAG acyclicity validation before persisting any edge. The `curriculum_generate()` function MUST reject a completed graph that violates the node count or depth limits and MUST log the violation with detail.

#### Scenario: Edge creating a cycle is rejected

- **WHEN** a mind map already contains edges A → B → C
- **AND** `mind_map_edge_create(parent_node_id=C, child_node_id=A)` is called
- **THEN** the tool MUST raise an error describing the detected cycle
- **AND** the edge MUST NOT be persisted to `mind_map_edges`

#### Scenario: Graph exceeding 30 nodes is rejected

- **WHEN** `curriculum_generate()` produces an LLM decomposition with 31 or more nodes
- **THEN** the function MUST raise an error indicating the node count limit was exceeded
- **AND** the partially-created graph MUST be rolled back (no nodes or edges persisted)

#### Scenario: Node at depth exceeding 5 is rejected

- **WHEN** the LLM session attempts to create a node at depth 6 or greater via `mind_map_node_create()`
- **THEN** the tool MUST raise an error indicating the maximum depth has been exceeded
- **AND** the node MUST NOT be persisted

#### Scenario: A graph with exactly 30 nodes and depth 5 is accepted

- **WHEN** `curriculum_generate()` produces exactly 30 nodes with no node exceeding depth 5 and no cycles
- **THEN** the function MUST succeed and persist all nodes and edges
- **AND** `curriculum_generate()` MUST return a dict summarising the created mind map

#### Scenario: A self-loop edge is rejected

- **WHEN** `mind_map_edge_create(parent_node_id=X, child_node_id=X)` is called for any node X
- **THEN** the tool MUST raise an error indicating a self-loop is not permitted
- **AND** the edge MUST NOT be persisted

---

### Requirement: Topological sort with tie-breaking

After the concept graph is created, `curriculum_generate()` SHALL compute a learning sequence by performing a topological sort of the DAG. When multiple nodes are topologically equivalent (i.e., they can be ordered freely without violating prerequisite constraints), the system MUST break ties using the following ordered criteria:

1. **Depth first** (shallower nodes rank earlier — breadth-first traversal through the prerequisite graph)
2. **Effort second** (lower `effort_minutes` ranks earlier within the same depth — quick wins build momentum)
3. **Diagnostic mastery third** (nodes with `mastery_status IN ('diagnosed', 'learning')` rank before `'unseen'` nodes at the same depth and effort — reinforce partially-known concepts before introducing unknown ones)

This ordering is deterministic given the same graph and mastery state.

#### Scenario: Shallower node ranks before deeper node at same topological level

- **WHEN** two nodes A (depth 1) and B (depth 2) are both topologically available (all their prerequisites are satisfied)
- **THEN** node A MUST receive a lower `sequence` number than node B

#### Scenario: Lower-effort node ranks before higher-effort node at same depth

- **WHEN** two nodes C (depth 2, effort 10 min) and D (depth 2, effort 45 min) are at the same depth and both topologically available
- **THEN** node C MUST receive a lower `sequence` number than node D

#### Scenario: Diagnosed node ranks before unseen node at same depth and effort

- **WHEN** two nodes E and F are both at depth 2 with `effort_minutes = 20`
- **AND** node E has `mastery_status = 'diagnosed'` and node F has `mastery_status = 'unseen'`
- **THEN** node E MUST receive a lower `sequence` number than node F

#### Scenario: Topological constraint takes precedence over all tie-breaking criteria

- **WHEN** node G (depth 1, effort 5 min) has an unmastered prerequisite node H
- **AND** node I (depth 3, effort 60 min) has all prerequisites mastered
- **THEN** node I MUST receive a lower `sequence` number than node G (topological order overrides depth and effort)

#### Scenario: Sort is deterministic across multiple calls

- **WHEN** `curriculum_generate()` is called twice for the same topic with identical graph structure and mastery state
- **THEN** both calls MUST produce identical `sequence` assignments on all nodes

---

### Requirement: Sequence numbering on nodes

The computed learning order SHALL be persisted as a `sequence` integer column on `mind_map_nodes`. Sequence numbers MUST start at 1 for the first node to learn and increment without gaps. After `curriculum_generate()` or `curriculum_replan()` completes, every node in the mind map MUST have a unique, non-null `sequence` value. Already-mastered nodes SHALL receive a sequence value representing their historical position (they are not re-ordered to the front) or be assigned the lowest available sequence numbers at the time of initial generation before any mastery occurs.

#### Scenario: All nodes receive unique sequence numbers after generation

- **WHEN** `curriculum_generate()` completes successfully for a 15-node mind map
- **THEN** every row in `mind_map_nodes` for that mind map MUST have a unique integer `sequence` value
- **AND** the set of sequence values MUST be exactly `{1, 2, ..., 15}` (no gaps, no duplicates)

#### Scenario: Sequence numbers are stable across node additions

- **WHEN** `curriculum_replan()` adds 2 new nodes to a 10-node mind map
- **THEN** the existing 10 nodes MUST retain their original `sequence` assignments unless replan logic explicitly reorders them
- **AND** the 2 new nodes MUST receive `sequence` values 11 and 12 (or be inserted with adjusted values if the replan determines a different position)

#### Scenario: Sequence is queryable and sortable via SQL

- **WHEN** `SELECT * FROM mind_map_nodes WHERE mind_map_id = $1 ORDER BY sequence ASC` is executed
- **THEN** the result MUST return all nodes in the prescribed learning order
- **AND** no two rows MUST share the same `sequence` value

---

### Requirement: Re-planning updates sequence without destroying structure

The `curriculum_replan()` function SHALL re-compute node sequence numbers in response to updated mastery state (e.g., after analytics identifies a struggling subtree) without modifying the existing DAG structure (nodes and edges). Re-planning MAY optionally add new nodes if the LLM identifies missing prerequisite concepts, or mark existing nodes as skippable based on current mastery. The DAG structure (edges) MUST remain intact and valid after re-planning. Re-planning MUST NOT delete any existing node unless it is explicitly flagged as removable by the re-plan logic.

#### Scenario: Re-plan reorders sequence after mastery state change

- **WHEN** node A has progressed from `mastery_status = 'unseen'` to `mastery_status = 'mastered'` since the last plan
- **AND** `curriculum_replan(pool, mind_map_id)` is called
- **THEN** node A MUST be removed from the active learning sequence (its `sequence` value MAY be reassigned or marked as complete)
- **AND** previously-blocked nodes whose only prerequisite was node A MUST now receive earlier sequence positions

#### Scenario: Re-plan with reason triggers optional LLM node addition

- **WHEN** `curriculum_replan(pool, mind_map_id, reason="user struggling with recursion subtree")` is called
- **AND** the LLM session determines a missing prerequisite node "Stack Frames" is needed
- **THEN** the new node MUST be created via `mind_map_node_create()` with a valid `depth` and `effort_minutes`
- **AND** prerequisite edges from the new node to the struggling nodes MUST be created via `mind_map_edge_create()`
- **AND** the full sequence MUST be recomputed after the new node is inserted

#### Scenario: Re-plan preserves existing edge structure

- **WHEN** `curriculum_replan()` is called on a 20-node mind map
- **THEN** the count of rows in `mind_map_edges` for that mind map MUST be equal to or greater than the pre-replan count (edges are never silently deleted)
- **AND** no existing edge MUST be modified or deleted unless the replan explicitly removes a node

#### Scenario: Re-plan marks a node as skippable based on mastery

- **WHEN** a node has `mastery_status = 'mastered'` and `mastery_score >= 0.9`
- **AND** `curriculum_replan()` is called
- **THEN** the node's `metadata` MUST be updated to include `{"skippable": true}`
- **AND** the node MUST be excluded from the active learning sequence ordering (not assigned a future sequence position)

#### Scenario: Re-planning is idempotent on a stable graph

- **WHEN** no mastery state has changed since the last replan
- **AND** `curriculum_replan()` is called
- **THEN** all sequence numbers MUST be identical to those assigned in the previous plan
- **AND** no nodes or edges MUST be created or deleted

---

### Requirement: Goal-directed planning optimizes path to stated goal

When the caller provides a `goal` argument to `curriculum_generate()`, the skill prompt MUST instruct the LLM session to scope the concept graph toward that goal. Nodes that are not on the prerequisite path to the goal MUST be excluded or marked as optional. The sequence ordering MUST prioritize the critical path to the goal over comprehensive coverage.

#### Scenario: Goal-scoped plan excludes off-path nodes

- **WHEN** `curriculum_generate(pool, mind_map_id, topic="Python", goal="build a REST API")` is called
- **THEN** the generated concept graph MUST include nodes for HTTP concepts, routing, and request/response handling
- **AND** the graph MUST NOT include nodes for GUI programming, game development, or other Python subfields unrelated to REST APIs
- **AND** the total node count MUST be lower than a goal-less decomposition of the same topic

#### Scenario: Goal is recorded on the mind map metadata

- **WHEN** `curriculum_generate()` is called with `goal="pass the AWS Solutions Architect exam"`
- **THEN** `mind_maps` row for the generated map MUST store the goal in its `metadata` JSONB column as `{"goal": "pass the AWS Solutions Architect exam"}`

#### Scenario: Goal-directed sequence puts critical-path nodes first

- **WHEN** a goal-directed plan is generated
- **THEN** nodes directly on the prerequisite path to the goal concept MUST have lower `sequence` numbers than optional supplementary nodes at the same depth
- **AND** `curriculum_next_node()` MUST return a critical-path node before any optional node when both are on the frontier

#### Scenario: No-goal plan covers the topic comprehensively

- **WHEN** `curriculum_generate(pool, mind_map_id, topic="Python Fundamentals")` is called without a `goal`
- **THEN** the generated graph MUST include concept nodes spanning beginner through intermediate Python topics
- **AND** no node MUST be marked as `optional` in its `metadata`

---

### Requirement: Diagnostic results inform sequence ordering

When `curriculum_generate()` is called with a `diagnostic_results` argument, those results SHALL be used to seed initial mastery state on newly-created nodes before the topological sort runs. Nodes that the diagnostic identified as already known (quality score >= 3) MUST receive `mastery_status = 'diagnosed'` and a `mastery_score` proportional to the diagnostic quality. This seeded mastery state MUST then influence the tie-breaking step of the topological sort (per the diagnostic mastery criterion in the Topological Sort requirement).

#### Scenario: High-scoring diagnostic node gets diagnosed status

- **WHEN** `curriculum_generate()` is called with `diagnostic_results={"node_label": "Variables", "quality": 4}`
- **AND** the LLM decomposes the topic and creates a node labelled "Variables"
- **THEN** that node MUST have `mastery_status = 'diagnosed'` after `curriculum_generate()` completes
- **AND** `mastery_score` MUST be set to a value between 0.3 and 0.9 (never 1.0 — diagnostic results never fully certify mastery)

#### Scenario: Low-scoring diagnostic node remains unseen

- **WHEN** `curriculum_generate()` is called with `diagnostic_results={"node_label": "Decorators", "quality": 1}`
- **THEN** the "Decorators" node MUST retain `mastery_status = 'unseen'` after `curriculum_generate()` completes
- **AND** `mastery_score` MUST remain at `0.0`

#### Scenario: Diagnosed nodes rank before unseen nodes in sequence

- **WHEN** a mind map is generated with some nodes seeded as `diagnosed` and others as `unseen`
- **AND** those nodes are at the same depth with equal `effort_minutes`
- **THEN** diagnosed nodes MUST have lower `sequence` numbers than unseen nodes

#### Scenario: Diagnostic results for unknown node labels are ignored

- **WHEN** `diagnostic_results` references a concept label that does not appear in the LLM-generated node set
- **THEN** the unmatched diagnostic result MUST be silently discarded
- **AND** all generated nodes MUST still receive correct `mastery_status` based on matching diagnostic results

#### Scenario: Mastery seeded conservatively from diagnostic scores

- **WHEN** a diagnostic result carries `quality = 5` (perfect recall)
- **THEN** the corresponding node's `mastery_score` MUST be set to at most 0.9
- **AND** `mastery_status` MUST be `'diagnosed'`, NOT `'mastered'`
- **AND** the node MUST NOT be excluded from the learning sequence (it still appears for reinforcement)

---

### Requirement: Next-node selection returns highest-priority frontier node

`curriculum_next_node(pool, mind_map_id)` SHALL return the next concept node the user should study. It MUST compute the active frontier (nodes whose every prerequisite has `mastery_status = 'mastered'` and whose own `mastery_status` is in `{'unseen', 'diagnosed', 'learning'}`) and then select the frontier node with the lowest `sequence` number. The function MUST return `None` when the frontier is empty (all nodes are mastered or the mind map is completed/abandoned).

#### Scenario: Returns frontier node with lowest sequence

- **WHEN** the frontier contains nodes with sequence numbers 3, 7, and 12
- **THEN** `curriculum_next_node()` MUST return the node with `sequence = 3`

#### Scenario: Non-frontier node is never returned even if it has a low sequence number

- **WHEN** node X has `sequence = 1` but has an unmastered prerequisite node Y (`mastery_status = 'learning'`)
- **AND** node Z has `sequence = 4` and all its prerequisites are mastered
- **THEN** `curriculum_next_node()` MUST return node Z, not node X

#### Scenario: Returns None when all nodes are mastered

- **WHEN** every node in the mind map has `mastery_status = 'mastered'`
- **THEN** `curriculum_next_node()` MUST return `None`

#### Scenario: Returns None for a completed or abandoned mind map

- **WHEN** `mind_maps.status` is `'completed'` or `'abandoned'`
- **THEN** `curriculum_next_node()` MUST return `None` without querying the frontier

#### Scenario: Root node is on the frontier for a fresh mind map

- **WHEN** a mind map has just been generated and no node has been studied yet
- **THEN** the root node (depth 0, with no prerequisites) MUST be on the frontier
- **AND** `curriculum_next_node()` MUST return the root node (it has the lowest sequence by definition)

#### Scenario: Node in learning state is returned by next-node if on frontier

- **WHEN** node A has `mastery_status = 'learning'`, all prerequisites mastered, and `sequence = 5`
- **AND** no other frontier node has `sequence < 5`
- **THEN** `curriculum_next_node()` MUST return node A (learning nodes remain in the frontier until mastered)

---

### Requirement: Syllabus lifecycle governs mind map state transitions

A mind map SHALL progress through a defined lifecycle: `creation` (graph is being generated) → `active` (learning is in progress) → `completed` (all nodes mastered) or `abandoned` (user or system terminates the plan). State transitions MUST be enforced: only `active` mind maps are eligible for `curriculum_next_node()`, `curriculum_replan()`, and spaced repetition scheduling. An `abandoned` or `completed` mind map MUST NOT accept new sequence updates or node mastery changes via the curriculum planning tools.

#### Scenario: Mind map transitions to active after curriculum_generate completes

- **WHEN** `curriculum_generate()` completes successfully
- **THEN** `mind_maps.status` MUST be `'active'`

#### Scenario: Mind map transitions to completed when all nodes are mastered

- **WHEN** the last unmastered node in a mind map is updated to `mastery_status = 'mastered'`
- **THEN** `mind_maps.status` MUST automatically transition to `'completed'`
- **AND** `curriculum_next_node()` called on that mind map MUST return `None`

#### Scenario: curriculum_replan rejects abandoned mind maps

- **WHEN** `curriculum_replan()` is called on a mind map with `status = 'abandoned'`
- **THEN** the function MUST raise an error indicating the mind map is abandoned
- **AND** no sequence updates or node additions MUST occur

#### Scenario: curriculum_generate returns the mind map dict on success

- **WHEN** `curriculum_generate()` completes without error
- **THEN** the return value MUST be a dict containing at least `mind_map_id`, `node_count`, `edge_count`, and `status`
