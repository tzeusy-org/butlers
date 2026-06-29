# Butler Chronicler — Spec delta for chronicler-intent-evidence-activity

## MODIFIED Requirements

### Requirement: Retrospective-Only Scope

The chronicler SHALL remain retrospective: it MUST NOT plan, ingest external
data, own a connector, or send proactive/coaching nudges. The **single
sanctioned owner-facing message** is the existing once-daily *retrospective*
day-close summary; this is a scheduled recap, not a proactive notification, and
no other owner-facing messages are permitted. **Amendment:** the chronicler MAY
synthesize durable insights into **its own schema** via the memory module, and
MAY *propose* entity-enrichment facts to the `relationship` butler **over MCP**.
These derived write-backs add no new owner-facing messages, do not constitute
ingestion or scheduler authority, and never write another butler's schema
directly.

#### Scenario: Synthesized insight stays within own schema

- **WHEN** the chronicler synthesizes a durable insight at day-close
- **THEN** it is written only to the chronicler's own schema
- **AND** no external data is ingested and the owner is not notified

#### Scenario: Cross-butler enrichment is an MCP proposal

- **WHEN** the chronicler has a candidate entity fact worth sharing
- **THEN** it is proposed to `relationship` over MCP
- **AND** the chronicler does not write `entity_facts` directly

### Requirement: Storage Shape

Episodes and point events SHALL retain their existing shape, with two additions:
every episode MUST carry a `layer` (`intent` | `evidence` | `activity`) and every
`activity`-layer episode MUST carry a `confidence` (`high` | `medium` | `low`) and
`evidence_refs[]`. Overlapping episodes SHALL remain permitted.

#### Scenario: Episode records its layer and confidence

- **WHEN** an inferred activity is stored
- **THEN** its `layer` is `activity`
- **AND** it carries a `confidence` and links to its corroborating evidence

#### Scenario: Overlapping episodes permitted

- **WHEN** two episodes from different sources cover overlapping time
- **THEN** both SHALL be stored
- **AND** neither SHALL be merged or discarded at storage time

### Requirement: Calendar Scheduled Blocks Are Not Attendance Assertions

Calendar blocks SHALL project to the `intent` layer and MUST NOT be counted as
lived time on their own. Lived time SHALL be counted only from the `activity`
layer; a calendar block contributes time to an aggregate solely when an
independent activity corroborates it, attributed to that activity's lane.

#### Scenario: Calendar block never asserts attendance

- **WHEN** a calendar block is projected
- **THEN** it is layer `intent`
- **AND** it is excluded from lived-time totals unless an activity corroborates
  it
- **AND** corroborated time is attributed to the activity's lane, not "calendar"

## Source References

- Non-Negotiable Rules (vision.md): schema isolation; MCP-only inter-butler
  communication.
- RFC 0014 (Chronicler Time Butler).
