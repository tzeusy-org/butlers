## ADDED Requirements

### Requirement: Relationship overdue surfaces expose unmeasurable cadence honestly

Relationship dashboard surfaces MUST distinguish a complete set of measurable contacts from a
result in which stale-contact evaluation is unavailable. Unmeasurable contacts MUST NOT appear as
overdue, count toward overdue totals, or populate attention rails, and their suppression MUST NOT
be rendered as a complete cadence all-clear.

ID: REQ-dashboard-domain-pages-049
Source: relationship-stale-contact-producer-mapping design §6; heart-and-soul/vision.md
Scope: v1-mandatory

#### Scenario: Relationship Contacts tab does not turn suppression into calm

- **WHEN** the Relationship Contacts tab's overdue source includes an unmeasurable contact
- **THEN** that contact MUST NOT appear in the overdue list or overdue KPI
- **AND** the tab MUST identify cadence instrumentation or provenance as unavailable
- **AND** it MUST NOT render "Cadence all clear" or equivalent complete healthy copy

#### Scenario: Plex attention rail contains only measurable overdue contacts

- **WHEN** the Plex evaluates its "Worth attention" rail with one or more unmeasurable contacts
- **THEN** those contacts MUST NOT appear as overdue attention items
- **AND** the rail MUST expose incomplete cadence availability rather than a complete all-clear

#### Scenario: Healthy elapsed source keeps the existing overdue presentation

- **WHEN** a contact has exactly one healthy mapped producer and its existing effective cadence has
  elapsed
- **THEN** the existing overdue KPI, list, and attention-rail behavior MAY render that contact
- **AND** this source mapping MUST NOT change cadence, priority, ordering, or outreach copy
