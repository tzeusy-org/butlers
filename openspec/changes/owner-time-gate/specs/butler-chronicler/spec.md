## ADDED Requirements

### Requirement: Calendar episodes are the owner's own time

The calendar adapter SHALL project a completed instance into an episode only when
`counts_as_owner_time(source role, event metadata)` holds: an event on a partner, household, team or
unclassified calendar with no accepted owner seat, a holiday or birthday feed, an event the owner
declined and an event marked free SHALL NOT become an episode. A skipped instance SHALL still advance
the adapter's watermark so it is not read again. Interaction sync and the encounter ledger SHALL
apply the role gate (`asserts_owner_time`) only, so a free-marked meeting the owner accepted still
counts as one that happened.

ID: REQ-butler-chronicler-024
Source: bu-5ii4gj.16; docs/redesigns calendar pursuit run 26
Scope: v1-mandatory

#### Scenario: A partner calendar's event makes no episode

- **WHEN** an event on a partner calendar lists the owner's address without an owner seat
- **THEN** no episode, interaction or encounter SHALL be created for it, and a primary-calendar event SHALL still create one

#### Scenario: A free accepted meeting still counts for interactions

- **WHEN** the owner accepted a meeting marked free
- **THEN** interaction sync SHALL log it and the chronicler SHALL NOT project it
