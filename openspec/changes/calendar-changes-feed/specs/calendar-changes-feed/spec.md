## ADDED Requirements

### Requirement: The feed lists organizer-made changes with before and after

ID: REQ-calendar-changes-feed-001
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

`GET /api/calendar/workspace/changes?since=<timestamp>` SHALL return one item for each journaled
revision, observed after `since`, whose start, end, status or location differs from the same
event's previous journaled revision and whose organizer is not the owner. Each item SHALL carry the
prior and new value of each field that changed, the event id and calendar id as evidence, the time it
was observed and whether a prior version was known. A revision that changes none of those fields
(an RSVP, a retitle, an attendee edit) SHALL NOT be an item. Items SHALL be ordered by observation
time, newest first, and the read SHALL be bounded in both age and count: a window past the bound
SHALL be refused with a stated reason, never silently shortened.

#### Scenario: An organizer moves a meeting

- **WHEN** the organizer moves an event the owner was invited to
- **THEN** the feed SHALL return one item with the prior and new start and end and the event id

#### Scenario: A revision that changes nothing named is not an item

- **WHEN** an attendee responds to an event, or the organizer retitles it
- **THEN** the feed SHALL return no item for that revision

### Requirement: The owner's own edits are not changes

ID: REQ-calendar-changes-feed-002
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

A revision of an event the owner organizes SHALL NOT be an item, whoever applied it (the owner in the
provider's own app, or a Butlers write). A revision whose organizer cannot be told (a
policy-blocked row keeps structure and times only) SHALL be returned with its times and status, an
unknown organizer, and no location, so the owner can see a move; it SHALL never be returned as the
owner's own edit and SHALL never be silently dropped.

#### Scenario: The owner reschedules their own meeting

- **WHEN** the owner moves an event they organize
- **THEN** the feed SHALL return no item for it

#### Scenario: An unknown organizer is shown, not hidden

- **WHEN** a policy-blocked revision changes an event's times
- **THEN** the feed SHALL return it with an unknown organizer and no location

### Requirement: The journal is read through a narrow reader

ID: REQ-calendar-changes-feed-003
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

The changes read SHALL reach the revision journal only through a `SECURITY DEFINER` function with a
fixed `search_path` that returns the event id, calendar id, observation time, prior and new start,
end, status and location, whether a prior version was known and the organizer flag, and no raw
payload, attendee, description or conference data. It SHALL cap its result. The calling role SHALL
have execute on that function and no direct grant on the journal. Titles SHALL come from the calendar
projection the owner can already read, not from the journal.

#### Scenario: The reader exposes only the named fields

- **WHEN** the function is called
- **THEN** its result columns SHALL be exactly the named fields and it SHALL return at most its cap

### Requirement: An unreadable feed is unknown, never empty

ID: REQ-calendar-changes-feed-004
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

When the journal read fails, the route SHALL answer with `changes_available: false` and no items,
and the Needs-you queue SHALL then name `changes` as unknown and show its count as a lower bound; it
SHALL NOT read the failure as no changes. A change item SHALL show its before and after, SHALL open
the event as its evidence, and SHALL NOT bind an accept or decline key.

#### Scenario: A failed read does not look like calm

- **WHEN** the journal read fails
- **THEN** the route SHALL return `changes_available: false` and the queue SHALL name changes as unknown
