## MODIFIED Requirements

### Requirement: Topic decomposition into concept graph

During curriculum generation, the curriculum planner SHALL assign a `concept_type` (`factual`, `procedural`, `conceptual`, or `creative`) to each mind map node's metadata, inferred from the node's label and description, leaving `concept_type` unset when classification confidence is low (defaulting to Socratic in the teaching phase). When the owner has registered source material relevant to the topic, the planner SHALL attempt to map concepts to source locations and populate initial `source_refs` in node metadata on a best-effort basis from model knowledge of the source.

ID: REQ-module-education-curriculum-001
Source: Education MANIFESTO.md amendment (evidence-based pedagogy)
Scope: v1-mandatory

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
