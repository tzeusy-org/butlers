# Education Mind Map Data Model

## Purpose

Defines the mind map CRUD operations, node and edge management, DAG invariants, frontier computation, subtree queries, lifecycle transitions, and mastery status state machine for the education butler's concept graph.

## ADDED Requirements

### Requirement: Mind map creation

The system SHALL provide a `mind_map_create(pool, title)` function that inserts a new row into the `mind_maps` table with a generated UUID primary key, the given title, `status = 'active'`, and NULL `root_node_id`. The function MUST return the UUID of the created mind map.

#### Scenario: Create a mind map returns its UUID

- **WHEN** `mind_map_create(pool, "Python Fundamentals")` is called
- **THEN** a new row MUST exist in `mind_maps` with `title = 'Python Fundamentals'` and `status = 'active'`
- **AND** the function MUST return the UUID of the newly created row
- **AND** `root_node_id` MUST be NULL

#### Scenario: Create a mind map sets timestamps

- **WHEN** `mind_map_create(pool, "Algebra Basics")` is called
- **THEN** the created row MUST have `created_at` and `updated_at` set to the current timestamp
- **AND** both timestamps MUST be equal at creation time

---

### Requirement: Mind map retrieval

The system SHALL provide a `mind_map_get(pool, mind_map_id)` function that returns a dict of the mind map row for the given UUID, or `None` if no such row exists.

#### Scenario: Get an existing mind map

- **WHEN** `mind_map_get(pool, <existing_id>)` is called
- **THEN** the function MUST return a dict containing `id`, `title`, `root_node_id`, `status`, `created_at`, and `updated_at`
- **AND** the returned `id` MUST equal the requested `mind_map_id`

#### Scenario: Get a non-existent mind map

- **WHEN** `mind_map_get(pool, <random_uuid_not_in_db>)` is called
- **THEN** the function MUST return `None`

---

### Requirement: Mind map listing

The system SHALL provide a `mind_map_list(pool, status=None)` function that returns a list of mind map dicts. When `status` is provided, only mind maps with that status SHALL be returned. When `status` is `None`, all mind maps SHALL be returned regardless of status.

#### Scenario: List all mind maps with no filter

- **WHEN** `mind_map_list(pool)` is called and there are three mind maps with statuses `active`, `completed`, and `abandoned`
- **THEN** the function MUST return all three mind maps

#### Scenario: List mind maps filtered by status

- **WHEN** `mind_map_list(pool, status='active')` is called
- **AND** there are two `active` mind maps and one `completed` mind map
- **THEN** the function MUST return exactly the two `active` mind maps
- **AND** the `completed` mind map MUST NOT appear in the result

#### Scenario: List returns empty list when no mind maps exist

- **WHEN** `mind_map_list(pool)` is called on an empty database
- **THEN** the function MUST return an empty list

---

### Requirement: Mind map status transitions

The system SHALL provide a `mind_map_update_status(pool, mind_map_id, status)` function that updates the `status` column for the given mind map and refreshes `updated_at`. Valid target status values are `active`, `completed`, and `abandoned`. The function MUST update `updated_at` to the current timestamp on every call.

#### Scenario: Transition active mind map to completed

- **WHEN** `mind_map_update_status(pool, <id>, 'completed')` is called on an `active` mind map
- **THEN** the mind map's `status` MUST be `'completed'`
- **AND** `updated_at` MUST be refreshed to the current timestamp

#### Scenario: Transition active mind map to abandoned

- **WHEN** `mind_map_update_status(pool, <id>, 'abandoned')` is called on an `active` mind map
- **THEN** the mind map's `status` MUST be `'abandoned'`
- **AND** `updated_at` MUST be refreshed to the current timestamp

#### Scenario: Status update on non-existent mind map raises error

- **WHEN** `mind_map_update_status(pool, <random_uuid>, 'completed')` is called
- **THEN** the function MUST raise an error indicating the mind map does not exist

---

### Requirement: Node creation with auto-depth

The system SHALL provide a `mind_map_node_create(pool, mind_map_id, label, description=None, depth=None, effort_minutes=None, metadata=None)` function that inserts a new row into `mind_map_nodes`. When `depth` is provided it SHALL be stored as-is. When `depth` is `None` the node SHALL be stored with `depth = 0`. The function MUST return the UUID of the created node.

#### Scenario: Create a node with explicit depth

- **WHEN** `mind_map_node_create(pool, <map_id>, "List Comprehensions", depth=2)` is called
- **THEN** a new row MUST exist in `mind_map_nodes` with `label = 'List Comprehensions'` and `depth = 2`
- **AND** the function MUST return the new node's UUID

#### Scenario: Create a node with default depth

- **WHEN** `mind_map_node_create(pool, <map_id>, "Introduction")` is called without a `depth` argument
- **THEN** the created node MUST have `depth = 0`

#### Scenario: Create a node initialises mastery defaults

- **WHEN** `mind_map_node_create(pool, <map_id>, "Decorators")` is called
- **THEN** the created node MUST have `mastery_score = 0.0`, `mastery_status = 'unseen'`, `ease_factor = 2.5`, and `repetitions = 0`
- **AND** `next_review_at` and `last_reviewed_at` MUST be NULL

#### Scenario: Create a node with optional fields

- **WHEN** `mind_map_node_create(pool, <map_id>, "Generators", description="Lazy sequences", effort_minutes=30, metadata={"tags": ["advanced"]})` is called
- **THEN** the created node MUST have all supplied values stored correctly
- **AND** the `metadata` column MUST equal `{"tags": ["advanced"]}`

#### Scenario: Create a node on a non-existent mind map raises an error

- **WHEN** `mind_map_node_create(pool, <random_uuid>, "Some Node")` is called
- **THEN** the function MUST raise a foreign key violation error

---

### Requirement: Node retrieval

The system SHALL provide a `mind_map_node_get(pool, node_id)` function that returns a dict of the full node row for the given UUID, or `None` if no such row exists.

#### Scenario: Get an existing node

- **WHEN** `mind_map_node_get(pool, <existing_node_id>)` is called
- **THEN** the function MUST return a dict containing all columns of the node row
- **AND** the returned `id` MUST equal the requested `node_id`

#### Scenario: Get a non-existent node

- **WHEN** `mind_map_node_get(pool, <random_uuid_not_in_db>)` is called
- **THEN** the function MUST return `None`

---

### Requirement: Node listing with optional mastery filter

The system SHALL provide a `mind_map_node_list(pool, mind_map_id, mastery_status=None)` function that returns a list of node dicts. When `mastery_status` is provided, only nodes with that status SHALL be returned. When `mastery_status` is `None`, all nodes belonging to the mind map SHALL be returned.

#### Scenario: List all nodes for a mind map

- **WHEN** `mind_map_node_list(pool, <map_id>)` is called on a map with five nodes of mixed mastery statuses
- **THEN** the function MUST return all five nodes

#### Scenario: List nodes filtered by mastery status

- **WHEN** `mind_map_node_list(pool, <map_id>, mastery_status='mastered')` is called
- **AND** two of five nodes have `mastery_status = 'mastered'`
- **THEN** the function MUST return exactly those two nodes

#### Scenario: List nodes returns empty list for unknown mind map

- **WHEN** `mind_map_node_list(pool, <random_uuid>)` is called
- **THEN** the function MUST return an empty list

---

### Requirement: Node field update

The system SHALL provide a `mind_map_node_update(pool, node_id, **fields)` function that updates one or more of the following writable columns on the given node: `mastery_score`, `mastery_status`, `ease_factor`, `repetitions`, `next_review_at`, `last_reviewed_at`, `effort_minutes`, `metadata`. The function MUST also update `updated_at` to the current timestamp. Columns not in the writable set MUST be silently ignored or raise an error — they MUST NOT be applied to the database row.

#### Scenario: Update mastery score and status

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_score=0.8, mastery_status='reviewing')` is called
- **THEN** the node's `mastery_score` MUST be `0.8` and `mastery_status` MUST be `'reviewing'`
- **AND** `updated_at` MUST be refreshed to the current timestamp

#### Scenario: Update SM-2 fields after a review

- **WHEN** `mind_map_node_update(pool, <node_id>, ease_factor=2.7, repetitions=3, last_reviewed_at=<now>, next_review_at=<future>)` is called
- **THEN** all four fields MUST be stored with the provided values

#### Scenario: Update metadata replaces the entire JSONB value

- **WHEN** `mind_map_node_update(pool, <node_id>, metadata={"tags": ["core"], "notes": "revised"})` is called
- **THEN** the node's `metadata` column MUST equal `{"tags": ["core"], "notes": "revised"}` exactly

#### Scenario: Update non-writable field does not corrupt the row

- **WHEN** `mind_map_node_update(pool, <node_id>, mind_map_id=<other_map_id>)` is called
- **THEN** the node's `mind_map_id` MUST remain unchanged
- **AND** no other column MUST be modified

#### Scenario: Update on a non-existent node raises an error

- **WHEN** `mind_map_node_update(pool, <random_uuid>, mastery_score=1.0)` is called
- **THEN** the function MUST raise an error indicating the node does not exist

---

### Requirement: Edge creation

The system SHALL provide a `mind_map_edge_create(pool, parent_node_id, child_node_id, edge_type='prerequisite')` function that inserts a row into `mind_map_edges`. The function MUST enforce the primary key constraint `(parent_node_id, child_node_id)`. Edges between nodes belonging to different mind maps MUST be rejected. The default `edge_type` SHALL be `'prerequisite'`; the only other permitted value is `'related'`.

#### Scenario: Create a prerequisite edge between two nodes

- **WHEN** `mind_map_edge_create(pool, <parent_id>, <child_id>)` is called for two nodes in the same map
- **THEN** a row MUST exist in `mind_map_edges` with `parent_node_id = <parent_id>`, `child_node_id = <child_id>`, and `edge_type = 'prerequisite'`

#### Scenario: Create a related edge

- **WHEN** `mind_map_edge_create(pool, <parent_id>, <child_id>, edge_type='related')` is called
- **THEN** the inserted row MUST have `edge_type = 'related'`

#### Scenario: Duplicate edge is rejected

- **WHEN** `mind_map_edge_create(pool, <parent_id>, <child_id>)` is called a second time for the same pair
- **THEN** the function MUST raise an error (primary key violation)

#### Scenario: Edge across different mind maps is rejected

- **WHEN** `mind_map_edge_create(pool, <node_in_map_A>, <node_in_map_B>)` is called for nodes belonging to different maps
- **THEN** the function MUST raise an error indicating the cross-map edge is not permitted

---

### Requirement: Edge deletion

The system SHALL provide a `mind_map_edge_delete(pool, parent_node_id, child_node_id)` function that removes the edge with the given `(parent_node_id, child_node_id)` pair from `mind_map_edges`. If no such edge exists the function MUST complete without error (idempotent).

#### Scenario: Delete an existing edge

- **WHEN** `mind_map_edge_delete(pool, <parent_id>, <child_id>)` is called for an existing edge
- **THEN** the row MUST no longer exist in `mind_map_edges`

#### Scenario: Delete a non-existent edge is a no-op

- **WHEN** `mind_map_edge_delete(pool, <parent_id>, <child_id>)` is called for a pair that has no edge
- **THEN** the function MUST complete without raising an error

---

### Requirement: Depth recomputation on edge changes

When an edge is created or deleted, the depth of the child node and all of its descendants MUST be recomputed. Depth is defined as the length of the longest path from any root node (a node with no incoming `prerequisite` edges) to the given node. The recomputed depth values MUST be persisted to `mind_map_nodes.depth` for all affected nodes.

#### Scenario: Child depth updated on edge creation

- **WHEN** a node `C` at `depth = 0` has an edge created from parent `P` at `depth = 2`
- **THEN** `C`'s `depth` MUST be updated to `3` after the edge is created

#### Scenario: Descendant depths updated transitively on edge creation

- **WHEN** an edge from `P` (depth 1) to `C` (depth 0) is created and `C` has a child `G` at depth 1
- **THEN** after the edge creation, `C`'s `depth` MUST be `2` and `G`'s `depth` MUST be `3`

#### Scenario: Depths recomputed on edge deletion

- **WHEN** an edge from `P` (depth 2) to `C` is deleted
- **AND** `C` has no other incoming prerequisite edges
- **THEN** `C`'s `depth` MUST be recomputed to reflect the longest remaining path from any root (or `0` if `C` has no remaining parents)

---

### Requirement: DAG acyclicity invariant

`mind_map_edge_create` MUST validate that adding the new edge would not introduce a cycle in the directed graph. If the proposed edge would create a cycle, the function MUST raise an error and MUST NOT persist any change to the database.

#### Scenario: Reject self-loop edge

- **WHEN** `mind_map_edge_create(pool, <node_id>, <node_id>)` is called with the same UUID for both parent and child
- **THEN** the function MUST raise an error indicating a cycle would be created
- **AND** no row MUST be inserted into `mind_map_edges`

#### Scenario: Reject edge that creates a two-node cycle

- **WHEN** an edge A → B already exists
- **AND** `mind_map_edge_create(pool, <B_id>, <A_id>)` is called
- **THEN** the function MUST raise an error indicating a cycle would be created
- **AND** the existing edge A → B MUST remain intact

#### Scenario: Reject edge that creates a multi-hop cycle

- **WHEN** edges A → B and B → C exist
- **AND** `mind_map_edge_create(pool, <C_id>, <A_id>)` is called
- **THEN** the function MUST raise an error indicating a cycle would be created
- **AND** neither the new edge nor any other edge MUST be modified

#### Scenario: Valid edge in a DAG is accepted

- **WHEN** edges A → B and A → C exist (a valid DAG with a shared parent)
- **AND** `mind_map_edge_create(pool, <B_id>, <C_id>)` is called and no cycle results
- **THEN** the function MUST succeed and persist the edge B → C

---

### Requirement: Frontier computation

The system SHALL provide a `mind_map_frontier(pool, mind_map_id)` function that returns the list of nodes in the given mind map that satisfy all of the following: (1) `mastery_status` is one of `unseen`, `diagnosed`, or `learning`; AND (2) every node connected to it via an incoming `prerequisite` edge has `mastery_status = 'mastered'` (or the node has no incoming `prerequisite` edges at all). Results MUST be ordered by `depth ASC`, then `effort_minutes ASC NULLS LAST`.

#### Scenario: Root node with no prerequisites is on the frontier

- **WHEN** a mind map has a single root node with no incoming edges and `mastery_status = 'unseen'`
- **THEN** `mind_map_frontier(pool, <map_id>)` MUST return that node

#### Scenario: Child node appears on frontier only after prerequisite is mastered

- **WHEN** a mind map has node P → node C (P is a prerequisite of C)
- **AND** P has `mastery_status = 'learning'` and C has `mastery_status = 'unseen'`
- **THEN** `mind_map_frontier` MUST include P but MUST NOT include C

#### Scenario: Child node appears on frontier when all prerequisites are mastered

- **WHEN** node C has two incoming prerequisite edges from P1 and P2
- **AND** both P1 and P2 have `mastery_status = 'mastered'`
- **AND** C has `mastery_status = 'unseen'`
- **THEN** `mind_map_frontier` MUST include C

#### Scenario: Mastered nodes are excluded from the frontier

- **WHEN** a node has `mastery_status = 'mastered'` and all its prerequisites are also mastered
- **THEN** `mind_map_frontier` MUST NOT include that node

#### Scenario: Reviewing nodes are excluded from the frontier

- **WHEN** a node has `mastery_status = 'reviewing'`
- **THEN** `mind_map_frontier` MUST NOT include that node

#### Scenario: Frontier is ordered by depth then effort

- **WHEN** the frontier contains node X (depth=1, effort_minutes=20) and node Y (depth=1, effort_minutes=10) and node Z (depth=2, effort_minutes=5)
- **THEN** `mind_map_frontier` MUST return them in the order Y, X, Z

#### Scenario: Empty frontier when all nodes are mastered

- **WHEN** every node in a mind map has `mastery_status = 'mastered'`
- **THEN** `mind_map_frontier` MUST return an empty list

---

### Requirement: Subtree queries

The system SHALL provide a `mind_map_subtree(pool, node_id)` function that returns all descendants of the given node (not including the node itself) using a recursive CTE over `mind_map_edges`. Traversal MUST follow edges in the `parent → child` direction and MUST include nodes reachable via `related` edges as well as `prerequisite` edges. The result MUST include each descendant node dict exactly once (no duplicates even in the presence of multiple paths).

#### Scenario: Subtree of a leaf node is empty

- **WHEN** `mind_map_subtree(pool, <leaf_node_id>)` is called for a node with no outgoing edges
- **THEN** the function MUST return an empty list

#### Scenario: Subtree of an internal node includes all descendants

- **WHEN** a node P has children C1 and C2, and C1 has a child G
- **THEN** `mind_map_subtree(pool, <P_id>)` MUST return C1, C2, and G
- **AND** P itself MUST NOT appear in the result

#### Scenario: Subtree deduplicates nodes reachable via multiple paths

- **WHEN** node P has edges to both B and C, and both B and C have edges to a shared child D
- **THEN** `mind_map_subtree(pool, <P_id>)` MUST include D exactly once

---

### Requirement: Mind map lifecycle — auto-completion

The system SHALL automatically transition a mind map from `active` to `completed` when all of its nodes have `mastery_status = 'mastered'`. This check MUST be performed at the end of every `mind_map_node_update` call that modifies `mastery_status`. If after the update every node in the mind map has `mastery_status = 'mastered'`, `mind_map_update_status` MUST be called internally to set the map status to `'completed'`.

#### Scenario: Mind map completes when last node is mastered

- **WHEN** a mind map has three nodes and two already have `mastery_status = 'mastered'`
- **AND** `mind_map_node_update(pool, <third_node_id>, mastery_status='mastered')` is called
- **THEN** the mind map's `status` MUST automatically transition to `'completed'`

#### Scenario: Mind map remains active when some nodes are unmastered

- **WHEN** a mind map has three nodes and only one has `mastery_status = 'mastered'`
- **AND** `mind_map_node_update(pool, <second_node_id>, mastery_status='mastered')` is called
- **THEN** the mind map's `status` MUST remain `'active'`

#### Scenario: Update to non-mastery field does not trigger completion check

- **WHEN** all nodes are mastered except one (`mastery_status = 'learning'`)
- **AND** `mind_map_node_update(pool, <that_node_id>, effort_minutes=45)` is called
- **THEN** the mind map's `status` MUST remain `'active'`

---

### Requirement: Mind map lifecycle — staleness abandonment

The system SHALL transition a mind map from `active` to `abandoned` when more than 30 days have elapsed since any activity on the map without all nodes being mastered. "Activity" is defined as any update to a node belonging to the map (i.e., the maximum `updated_at` across all nodes in the map). A scheduled weekly job MUST perform this check for all `active` mind maps and call `mind_map_update_status` to set `abandoned` where the staleness condition is met.

#### Scenario: Mind map abandoned after 30 days of inactivity

- **WHEN** a mind map is `active` and the most recent `updated_at` across all its nodes is more than 30 days ago
- **THEN** the weekly staleness job MUST set the mind map's `status` to `'abandoned'`

#### Scenario: Active mind map with recent activity is not abandoned

- **WHEN** a mind map is `active` and at least one node was updated within the past 30 days
- **THEN** the weekly staleness job MUST NOT change the map's status

#### Scenario: Completed mind map is not subject to staleness check

- **WHEN** a mind map has `status = 'completed'` and its nodes have not been updated in 60 days
- **THEN** the weekly staleness job MUST NOT modify its status

---

### Requirement: Mastery status state machine

Node `mastery_status` SHALL follow a defined state machine. The valid transitions are:
- `unseen` → `diagnosed`
- `unseen` → `learning`
- `diagnosed` → `learning`
- `diagnosed` → `mastered`
- `learning` → `reviewing`
- `learning` → `mastered`
- `reviewing` → `mastered`
- `reviewing` → `learning` (regression on failed review)
- `mastered` → `reviewing` (scheduled spaced repetition)

Any other transition MUST be rejected by `mind_map_node_update` with an error before any database write occurs.

#### Scenario: Valid transition unseen to diagnosed is accepted

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='diagnosed')` is called on a node with `mastery_status = 'unseen'`
- **THEN** the node's `mastery_status` MUST become `'diagnosed'`

#### Scenario: Valid transition learning to reviewing is accepted

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='reviewing')` is called on a node with `mastery_status = 'learning'`
- **THEN** the node's `mastery_status` MUST become `'reviewing'`

#### Scenario: Valid regression from reviewing to learning is accepted

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='learning')` is called on a node with `mastery_status = 'reviewing'`
- **THEN** the node's `mastery_status` MUST become `'learning'`

#### Scenario: Valid spaced repetition trigger mastered to reviewing is accepted

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='reviewing')` is called on a node with `mastery_status = 'mastered'`
- **THEN** the node's `mastery_status` MUST become `'reviewing'`

#### Scenario: Invalid transition unseen to mastered is rejected

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='mastered')` is called on a node with `mastery_status = 'unseen'`
- **THEN** the function MUST raise an error indicating the transition is not permitted
- **AND** the node's `mastery_status` MUST remain `'unseen'`

#### Scenario: Invalid transition mastered to learning is rejected

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='learning')` is called on a node with `mastery_status = 'mastered'`
- **THEN** the function MUST raise an error indicating the transition is not permitted
- **AND** the node's `mastery_status` MUST remain `'mastered'`

#### Scenario: Invalid transition reviewing to diagnosed is rejected

- **WHEN** `mind_map_node_update(pool, <node_id>, mastery_status='diagnosed')` is called on a node with `mastery_status = 'reviewing'`
- **THEN** the function MUST raise an error indicating the transition is not permitted
- **AND** the node's `mastery_status` MUST remain `'reviewing'`
