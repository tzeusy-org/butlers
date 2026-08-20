## MODIFIED Requirements

### Requirement: Teaching Phase — Explain, Question, Evaluate

The teaching phase SHALL select a primary pedagogical technique based on the concept node's `metadata.concept_type` when set (`factual` → retrieval practice, `procedural` → worked example then guided practice, `conceptual` → Socratic questioning with analogy, `creative` → divergent prompts then critique), falling back to Socratic questioning when unset. The teaching session SHALL explain its technique choice when the owner asks, citing the pedagogical principle; SHALL include source citations in explanations when relevant registered or model-recalled sources exist; and SHALL suggest reading pathways after concept explanation completes.

ID: REQ-module-education-teaching-flows-006
Source: Education MANIFESTO.md amendment (evidence-based pedagogy)
Scope: v1-mandatory

#### Scenario: Technique selection by concept type

- **WHEN** the teaching session begins explaining a concept node with
  `metadata.concept_type = "procedural"`
- **THEN** the session uses a worked-example-then-practice approach rather
  than starting with Socratic questions
- **AND** the flow state records the technique used

#### Scenario: Default technique when concept type is unset

- **WHEN** the teaching session begins explaining a concept node without a
  `concept_type` in its metadata
- **THEN** the session uses Socratic questioning as the default technique
- **AND** existing teaching behavior is unchanged (backward compatible)

#### Scenario: Pedagogy transparency on request

- **WHEN** the owner asks "why are you teaching it this way?" during a
  teaching session
- **THEN** the butler explains its technique choice by naming the concept
  type, the selected technique, and the pedagogical principle (e.g.,
  "this is a procedural skill, so I'm using worked examples — research
  on cognitive load theory shows this reduces extraneous processing")
- **AND** the explanation does not interrupt the teaching flow (the session
  continues from where it was after the explanation)

#### Scenario: Source citation during explanation

- **WHEN** the teaching session explains a concept and a relevant source
  exists (registered or model-recalled)
- **THEN** the explanation includes an inline citation ("as described in
  [title], [location]")
- **AND** a `source_refs` entry is written to the node's metadata if not
  already present
