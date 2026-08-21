## MODIFIED Requirements

### Requirement: Topic decomposition into concept graph

Curriculum planning SHALL be split into two layers:

1. LLM orchestration layer (butler session level). An ephemeral LLM session, spawned by the curriculum-planning skill prompt, decomposes the given topic into a set of concept nodes and prerequisite edges. The session MUST output structured JSON representing nodes (each with `label`, `description`, and `effort_minutes`) and edges (each specifying a `parent` → `child` prerequisite relationship), and MUST persist this graph by calling `mind_map_create()`, `mind_map_node_create()`, and `mind_map_edge_create()` tool calls. The skill prompt MUST require the LLM to produce a root node representing the topic itself, with all other nodes reachable from it via directed prerequisite edges. The mind map created by `mind_map_create()` is `draft` for the whole of this layer; the session MUST NOT attempt to activate it.

2. Validation and sequencing layer (`curriculum_generate()`). After the nodes and edges are persisted, `curriculum_generate(pool, mind_map_id)` validates the persisted graph against the structural constraints, applies diagnostic mastery seeding, computes the learning order via topological sort with tie-breaking, assigns sequence numbers, and transitions the mind map from `draft` to `active`. `curriculum_generate()` SHALL NOT spawn the LLM session: all concept nodes and prerequisite edges MUST already exist in the database before it is called.

`curriculum_generate()` SHALL be the only path that transitions a mind map to
`active`. Before requesting that transition it MUST verify that the map has at
least one node, and it MUST raise rather than activate an empty graph, leaving
the map in `draft`. The transition itself goes through
`mind_map_update_status()`, which enforces the same guard independently — the
check here exists so the caller gets a curriculum-shaped error message, not so
the invariant has a second owner.

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

#### Scenario: The map remains draft until curriculum_generate activates it

- **WHEN** the LLM session has created some but not all of its concept nodes
- **THEN** the mind map's `status` MUST still be `'draft'`
- **AND** the map MUST NOT appear in `mind_map_list(pool, status='active')`

#### Scenario: curriculum_generate refuses to activate an empty graph

- **WHEN** `curriculum_generate(pool, mind_map_id)` is called on a `draft` mind map with zero nodes
- **THEN** the function MUST raise an error stating that a curriculum cannot be generated from an empty graph
- **AND** the mind map's `status` MUST remain `'draft'`
- **AND** no mind map MUST exist with `status = 'active'` and zero nodes as a result of the call

---

### Requirement: Syllabus lifecycle governs mind map state transitions

A mind map SHALL progress through a defined lifecycle: `draft` (the map row
exists and its graph is being generated) → `active` (learning is in progress)
→ `completed` (all nodes mastered) or `abandoned` (user or system terminates
the plan). A `draft` map MAY also go directly to `abandoned` when its
generation never produced a graph.

`draft` is a real, stored `mind_maps.status` value, not an implicit phase: the
map is addressable, listable, and abandonable while in it. State transitions
MUST be enforced. Only `active` mind maps are eligible for
`curriculum_next_node()`, `curriculum_replan()`, and spaced repetition
scheduling. A `draft`, `abandoned`, or `completed` mind map MUST NOT accept
new sequence updates or node mastery changes via the curriculum planning
tools.

#### Scenario: Mind map is draft before curriculum_generate runs

- **WHEN** `mind_map_create()` has been called and `curriculum_generate()` has not
- **THEN** `mind_maps.status` MUST be `'draft'`
- **AND** `curriculum_next_node()` called on that mind map MUST raise an error indicating the map is not active

#### Scenario: Mind map transitions to active after curriculum_generate completes

- **WHEN** `curriculum_generate()` completes successfully
- **THEN** `mind_maps.status` MUST be `'active'`
- **AND** the map MUST have at least one node

#### Scenario: Mind map transitions to completed when all nodes are mastered

- **WHEN** the last unmastered node in a mind map is updated to `mastery_status = 'mastered'`
- **THEN** `mind_maps.status` MUST automatically transition to `'completed'`
- **AND** `curriculum_next_node()` called on that mind map MUST return `None`

#### Scenario: Draft mind map can be abandoned directly

- **WHEN** a `draft` mind map is abandoned because its generation never produced a graph
- **THEN** `mind_maps.status` MUST be `'abandoned'`
- **AND** the transition MUST NOT require the map to pass through `'active'` first

#### Scenario: curriculum_replan rejects abandoned mind maps

- **WHEN** `curriculum_replan()` is called on a mind map with `status = 'abandoned'`
- **THEN** the function MUST raise an error indicating the mind map is abandoned
- **AND** no sequence updates or node additions MUST occur

#### Scenario: curriculum_replan rejects draft mind maps

- **WHEN** `curriculum_replan()` is called on a mind map with `status = 'draft'`
- **THEN** the function MUST raise an error indicating the mind map has no curriculum to re-plan
- **AND** no sequence updates or node additions MUST occur

#### Scenario: curriculum_generate returns the mind map dict on success

- **WHEN** `curriculum_generate()` completes without error
- **THEN** the return value MUST be a dict containing at least `mind_map_id`, `node_count`, `edge_count`, and `status`
- **AND** `node_count` MUST be greater than zero
- **AND** `status` MUST be `'active'`
