## MODIFIED Requirements

### Requirement: Topic decomposition into concept graph

Curriculum planning SHALL be split into two layers:
- 1. LLM orchestration layer (goal invocation level). A goal invocation — the lead, or a depth-1 child it briefs with the curriculum-planning domain guidance attached by reference — decomposes the given topic into a set of concept nodes and prerequisite edges. The education daemon SHALL NOT spawn this session. The invocation MUST output structured JSON representing nodes (each with `label`, `description`, and `effort_minutes`) and edges (each specifying a `parent` → `child` prerequisite relationship), and MUST persist this graph by calling the `mind_map_create()`, `mind_map_node_create()`, and `mind_map_edge_create()` capabilities within its grant. The curriculum-planning guidance MUST require the LLM to produce a root node representing the topic itself, with all other nodes reachable from it via directed prerequisite edges. The mind map created by `mind_map_create()` SHALL remain `draft` for the whole of this layer; the session MUST NOT attempt to activate it.
- 2. Validation and sequencing layer (`curriculum_generate()`). After the nodes and edges are persisted, `curriculum_generate(pool, mind_map_id)` validates the persisted graph against the structural constraints, applies diagnostic mastery seeding, computes the learning order via topological sort with tie-breaking, assigns sequence numbers, and transitions the mind map to `active`. That transition SHALL be from `draft` to `active`. `curriculum_generate()` SHALL NOT start a reasoning session: all concept nodes and prerequisite edges MUST already exist in the database before it is called.
- During curriculum generation, the curriculum planner SHALL assign a `concept_type` (`factual`, `procedural`, `conceptual`, or `creative`) to each mind map node's metadata, inferred from the node's label and description, leaving `concept_type` unset when classification confidence is low (defaulting to Socratic in the teaching phase). When the owner has registered source material relevant to the topic, the planner SHALL attempt to map concepts to source locations and populate initial `source_refs` in node metadata on a best-effort basis from model knowledge of the source.

`curriculum_generate()` SHALL request activation only through `mind_map_update_status()`, the single application owner of the activation invariant. The independently required explicit lifecycle transitions through that tool and the status endpoint remain permitted for populated maps. Before requesting activation, curriculum generation MUST verify that the map has at least one node, and it MUST raise rather than activate an empty graph, leaving the map in `draft`. The transition itself goes through `mind_map_update_status()`, which enforces the same guard independently — the check here exists so the caller gets a curriculum-shaped error message, not so the invariant has a second owner.

ID: REQ-module-education-curriculum-001
Source: Education MANIFESTO.md amendment (evidence-based pedagogy); RFC 0038 D2
Scope: v1-mandatory

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

#### Scenario: Concept type assigned during curriculum generation

- **WHEN** `curriculum_generate` decomposes a topic into concept nodes
- **THEN** each node's metadata includes a `concept_type` field when the
  planner can confidently classify it
- **AND** nodes the planner cannot classify have no `concept_type` in their
  metadata (graceful degradation to default)

#### Scenario: Source mapping during curriculum generation

- **WHEN** `curriculum_generate` runs and the owner has registered source
  material covering the topic
- **THEN** the planner populates `source_refs` on nodes it can map to
  specific source locations
- **AND** unmappable nodes have no `source_refs` (no fabricated locations)
