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

#### Scenario: Session delivers explanation for current node

- **WHEN** `current_phase = 'explaining'`
- **THEN** the session sends a focused explanation of the concept named by `current_node_id` via `notify(channel="telegram", intent="send", message=...)`
- **AND** the explanation covers only the target concept, not the full curriculum
- **AND** the session updates `current_phase` to `questioning` in the KV store

#### Scenario: Session asks comprehension question after explanation

- **WHEN** `current_phase = 'questioning'`
- **THEN** the session sends one comprehension question via `notify(channel="telegram", intent="send", message=...)`
- **AND** the question is directly about the concept just explained
- **AND** the session updates `current_phase` to `evaluating` in the KV store

#### Scenario: Session evaluates user answer and gives feedback

- **WHEN** `current_phase = 'evaluating'` and the user's answer arrives
- **THEN** the session evaluates the answer and sends feedback via `notify(channel="telegram", intent="reply", message=..., request_context=...)`
- **AND** if the answer is correct, the session sends a positive acknowledgment via `notify(channel="telegram", intent="react", emoji="✅", request_context=...)`
- **AND** a `quiz_responses` row is inserted with `response_type = 'teach'` and the appropriate `quality` score (0–5)

#### Scenario: Mastery updated after teaching evaluation

- **WHEN** the evaluation is complete
- **THEN** `mind_map_node_update()` sets `mastery_score` and `mastery_status` based on the quality score
- **AND** if `quality >= 3`, `mastery_status` advances toward `reviewing`; if `quality < 3`, `mastery_status` remains `learning`

#### Scenario: Flow advances to QUIZZING after teaching evaluation

- **WHEN** the evaluation step is complete
- **THEN** `teaching_flow_advance()` transitions the flow to `quizzing`
- **AND** `current_phase` is set to `null`
