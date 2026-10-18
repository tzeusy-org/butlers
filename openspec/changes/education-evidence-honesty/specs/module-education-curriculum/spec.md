## ADDED Requirements

### Requirement: Curriculum plan identity and digest

A mind map produced by a curriculum request SHALL record the identity of the work that produced it, so that a curriculum receipt can attest to that exact map and never to a map merely created around the same time. `education.mind_maps` SHALL carry nullable `curriculum_request_id` (the `education.curriculum_requests` receipt that asked for it), `created_session_id` (the runtime session that created it) and `plan_digest`; `education.curriculum_requests` SHALL carry a nullable `plan_digest` copied from the map the receipt settled on. The columns SHALL be nullable so that mind maps and receipts created before this requirement stay readable; a null value means "no evidence recorded", never "verified".

`curriculum_generate()` SHALL compute `plan_digest` as `sha256:<hex>` over a canonical serialization of the persisted plan: the map's nodes (identified by their stable sequence position and label), their `concept_type` and source references, the prerequisite edges, and the assigned sequence numbers, in a fixed order that does not depend on row insertion order or on UUID values. The digest SHALL be computed only after every plan write has succeeded, and SHALL be stored in the same step that activates the map. Two calls over an identical persisted plan SHALL produce the same digest; any change to the nodes, edges, sequence, concept types or source references SHALL change it.

`curriculum_generate()` SHALL refuse to activate a map for which it cannot compute a digest, leaving the map in `draft`.

A mind map SHALL satisfy the **Ready predicate** for a receipt only when it: carries that receipt's id as `curriculum_request_id`; has a `created_session_id` equal to the session the receipt triggered; has `status = 'active'`; contains at least one node; and carries a nonblank `plan_digest`. The at-least-one-node and `active` conditions are already enforced for every map by the mind-map content invariant (`education-mind-map-lifecycle-integrity`); this requirement adds the identity link and the digest, and SHALL NOT be read as replacing that invariant. Time-window correlation, including "the newest map created after the trigger", SHALL NOT be used to identify the map of a receipt.

Mind maps created outside a curriculum request (for example by teaching-flow start) SHALL leave `curriculum_request_id` null and SHALL NOT be treated as unverified or broken for that reason.

ID: REQ-module-education-curriculum-002
Source: bu-h40h2b.4 (education-evidence-honesty); bu-6e8j89
Scope: v1-mandatory

#### Scenario: Activation stores the identity link and a canonical digest

- **WHEN** `curriculum_generate()` activates a draft map created by session S for receipt R
- **THEN** the map SHALL carry `curriculum_request_id = R`, `created_session_id = S` and a `plan_digest` of the form `sha256:<hex>`

#### Scenario: The digest is deterministic

- **WHEN** the digest is computed twice over the same persisted plan, with the rows read in different orders
- **THEN** both digests SHALL be equal

#### Scenario: The digest changes when the plan changes

- **WHEN** a node label, a prerequisite edge, a sequence number, a `concept_type` or a source reference changes
- **THEN** the recomputed digest SHALL differ from the stored one

#### Scenario: An unrelated recent map does not satisfy the predicate

- **WHEN** receipt R was triggered at time T
- **AND** a different active, populated, digested map was created after T by another session
- **THEN** that map SHALL NOT satisfy the Ready predicate for R

#### Scenario: A map outside any request carries no identity link

- **WHEN** a mind map is created by teaching-flow start rather than by a curriculum request
- **THEN** its `curriculum_request_id` SHALL be null
- **AND** it SHALL remain usable and listable

#### Scenario: Activation refuses when the digest cannot be computed

- **WHEN** the persisted plan cannot be serialized for the digest
- **THEN** `curriculum_generate()` SHALL raise
- **AND** the map SHALL remain `draft`
