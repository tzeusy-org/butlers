## ADDED Requirements

### Requirement: Owner-Time Source Gate for Conflict Candidates

Before overlap, back-to-back or overloaded-day detection the radar SHALL exclude an event that does
not assert owner time under `asserts_owner_time` for its source's role, in addition to the existing
provenance, RSVP and transparency filters. The workspace projection SHALL continue to return the
excluded rows; only analysis skips them.

ID: REQ-calendar-conflict-overcommitment-radar-002
Source: bu-5ii4gj.1 (run 26 move 1), slice S1
Scope: v1-mandatory

#### Scenario: A partner's calendar adds no conflict or load

- **WHEN** a confirmed event on a `household_member` source, with no `self` attendee, overlaps an owner-calendar event
- **THEN** no overlap or back-to-back issue is reported
- **AND** its hours do not contribute to an overloaded-day issue

#### Scenario: An invited event on another calendar still counts

- **WHEN** the same event lists the owner as a `self` attendee who has not declined
- **THEN** it remains a radar candidate

#### Scenario: Skipped unclassified calendars are counted

- **WHEN** events on `unclassified` sources are left out because the owner is not invited
- **THEN** the scan reports how many calendars and events were skipped, so an empty issue list is not read as a clear window

### Requirement: Household Context Beside the Scan

The conflict scan SHALL carry, beside its issues, the household context events overlapping its
window (REQ-module-calendar-038), attributed to their subject and opaque. They SHALL NOT form
conflicts, back-to-back gaps or overloaded-day hours, and SHALL NOT change any issue, hours total
or coverage count.

ID: REQ-calendar-conflict-overcommitment-radar-003
Source: bu-5ii4gj.1 (run 26 move 1), slice S3
Scope: v1-mandatory

#### Scenario: Context beside an unchanged scan

- **WHEN** a household event overlaps an owner meeting
- **THEN** the scan reports no overlap and the same day-load hours as without it, and lists the event under household context
