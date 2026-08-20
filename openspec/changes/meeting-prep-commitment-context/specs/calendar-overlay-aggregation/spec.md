## MODIFIED Requirements

### Requirement: Meeting-Prep Contribution Schema and State Key Convention

Each contributing specialist butler SHALL write a structured per-event
meeting-prep envelope into its own `state` store under the key
`calendar/prep/<event_id>`. The envelope MUST be deterministic and contain no
generated prose. Each attendee entry MUST carry, in addition to existing fields,
a `commitments` list containing active commitment-class `owner_conditions` rows
where the attendee's `entity_id` matches
`metadata->>'counterparty_entity_id'`. Each commitment entry MUST carry `kind`,
`direction`, `summary` (the condition's `label`), `deadline` (from metadata,
nullable), `escalation_level`, and `fingerprint`. The list MUST be capped at a
configurable maximum per attendee (default 10, highest-escalation first) and
MUST be empty — not absent — when no active commitments exist for the attendee.

ID: REQ-calendar-overlay-aggregation-005
Source: RFC 0026 §Out of Scope ("Moment Prep integration — consumes commitment query surface")
Scope: v1-mandatory

#### Scenario: Prep envelope includes active commitments per attendee

- **WHEN** the relationship `calendar_prep_contribution` job runs for an
  entity-linked event whose attendee has active commitment-class
  `owner_conditions` rows
- **THEN** the prep envelope's attendee entry carries a `commitments` list with
  each commitment's `kind`, `direction`, `summary`, `deadline`,
  `escalation_level`, and `fingerprint`
- **AND** commitments are ordered by escalation level descending (highest
  urgency first), capped at `MAX_COMMITMENTS_PER_ATTENDEE`
- **AND** no LLM session is spawned

#### Scenario: Attendee with no commitments gets an empty list

- **WHEN** the prep job runs for an attendee who has no active commitment-class
  `owner_conditions` rows
- **THEN** the attendee's `commitments` field is an empty list `[]`, not absent
  from the envelope
- **BECAUSE** downstream consumers distinguish "no commitments" from "commitments
  not yet populated" by field presence

#### Scenario: Commitment query failure degrades gracefully

- **WHEN** the query against `public.owner_conditions` fails during the prep job
- **THEN** the prep envelope is still written with an empty `commitments` list
  per attendee and the failure is logged at WARNING level
- **AND** existing prep context (notes, Dunbar tier, last-met, message context)
  is unaffected
- **BECAUSE** the prep rail's honest empty-state contract requires fail-open
  behavior per RFC-0020
