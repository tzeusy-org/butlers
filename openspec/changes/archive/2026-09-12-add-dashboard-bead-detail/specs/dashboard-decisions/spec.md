## ADDED Requirements

### Requirement: Decision and blocker drill-downs stay same-origin

The Decisions lane SHALL provide an explicit same-origin detail route for each
listed decision and each escalation blocker. Each target SHALL be constructed
only from the safe Bead ID as `/beads/:id`; it MUST NOT use a source-derived
external URL, `external_ref`, tracker URL, or arbitrary href. Existing inline
selection, j/k roving behavior, and read-only decision context SHALL remain
available.

#### Scenario: A decision row opens the safe detail route

- **WHEN** an owner encounters a decision row in `/decisions`
- **THEN** its detail link targets `/beads/<encoded-decision-id>` on the same
  origin
- **AND** the row's selection control remains keyboard-operable

#### Scenario: An escalation blocker opens the safe detail route

- **WHEN** a selected decision displays an escalation blocker
- **THEN** its blocker link targets `/beads/<encoded-blocker-id>` on the same
  origin
- **AND** no external tracker link is rendered
