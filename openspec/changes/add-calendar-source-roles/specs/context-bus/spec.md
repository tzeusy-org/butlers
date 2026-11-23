## ADDED Requirements

### Requirement: Calendar Producer Owner-Time Source Gate

The calendar `meeting`/`focused` producer SHALL treat an event as a candidate only when it
asserts owner time under `asserts_owner_time` for its source's role, in addition to every
existing provenance, RSVP and transparency exclusion. An active `outOfOffice` event SHALL suppress
`focused` only when it asserts owner time, so another person's out of office SHALL NOT suppress the
owner's focus. An event excluded by the source gate SHALL leave the signal as if it were absent.

ID: REQ-context-bus-018
Source: bu-5ii4gj.1 (run 26 move 1), slice S1
Scope: v1-mandatory

#### Scenario: A partner's meeting sets no meeting context

- **WHEN** a timed confirmed event on an `unclassified` source, with no `self` attendee, is active now
- **THEN** the producer sets no `meeting` signal and clears any stale one

#### Scenario: A colleague's out of office does not suppress focus

- **WHEN** an active `outOfOffice` event on an `unclassified` source has no `self` attendee, and an owner focus-time event is active
- **THEN** the producer sets `focused`
