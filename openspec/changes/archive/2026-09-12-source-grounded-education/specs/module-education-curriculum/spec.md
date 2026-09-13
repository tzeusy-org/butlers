## MODIFIED Requirements

### Requirement: Topic decomposition into concept graph

Curriculum planning SHALL be split into two layers:
- 1. LLM orchestration layer (butler session level). An ephemeral LLM session, spawned by the curriculum-planning skill prompt, decomposes the given topic into a set of concept nodes and prerequisite edges. The session MUST output structured JSON representing nodes (each with `label`, `description`, and `effort_minutes`) and edges (each specifying a `parent` → `child` prerequisite relationship), and MUST persist this graph by calling `mind_map_create()`, `mind_map_node_create()`, and `mind_map_edge_create()` tool calls. The skill prompt MUST require the LLM to produce a root node representing the topic itself, with all other nodes reachable from it via directed prerequisite edges.
- 2. Validation and sequencing layer (`curriculum_generate()`). After the nodes and edges are persisted, `curriculum_generate(pool, mind_map_id)` validates the persisted graph against the structural constraints, applies diagnostic mastery seeding, computes the learning order via topological sort with tie-breaking, assigns sequence numbers, and transitions the mind map to `active`. `curriculum_generate()` SHALL NOT spawn the LLM session: all concept nodes and prerequisite edges MUST already exist in the database before it is called.
- During curriculum generation, the curriculum planner SHALL assign a `concept_type` (`factual`, `procedural`, `conceptual`, or `creative`) to each mind map node's metadata, inferred from the node's label and description, leaving `concept_type` unset when classification confidence is low (defaulting to Socratic in the teaching phase). When the owner has registered source material relevant to the topic, the planner SHALL attempt to map concepts to source locations and populate initial `source_refs` in node metadata on a best-effort basis from model knowledge of the source.

ID: REQ-module-education-curriculum-001
Source: Education MANIFESTO.md amendment (evidence-based pedagogy)
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
