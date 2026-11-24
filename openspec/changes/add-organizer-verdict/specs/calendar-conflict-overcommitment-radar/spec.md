## ADDED Requirements

### Requirement: Quarantined Invitation Candidate Filter

Before overlap, back-to-back or overloaded-day detection the radar SHALL exclude a quarantined
invitation as defined by REQ-module-calendar-041, in addition to its existing source, provenance,
RSVP and transparency filters. The workspace projection SHALL continue to return the excluded
rows; only analysis skips them.

ID: REQ-calendar-conflict-overcommitment-radar-004
Source: bu-5ii4gj.6 (run 26 move 6), slice S2
Scope: v1-mandatory

#### Scenario: Calendar spam never counts as load

- **WHEN** a needsAction invitation from a `blocked` organizer overlaps an accepted event
- **THEN** no overlap issue is reported and its hours do not contribute to an overloaded-day issue
