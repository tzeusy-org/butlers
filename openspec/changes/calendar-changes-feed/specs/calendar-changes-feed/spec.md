## ADDED Requirements

### Requirement: The feed lists organizer-made changes with before and after

ID: REQ-calendar-changes-feed-001
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

`GET /api/calendar/workspace/changes?since=<timestamp>` (`since` defaulting to seven days ago) SHALL
return one item for each journaled change or resync revision, observed after `since`, whose start, end, status or location differs from the same
event's previous journaled revision and whose organizer is not the owner. Each item SHALL carry the
prior and new value of each field that changed, the event id and calendar id as evidence, the time it
was observed and whether a prior version was known. A revision that changes none of those fields
(an RSVP, a retitle, an attendee edit) SHALL NOT be an item, and neither SHALL a revision of an event
the owner has declined: a meeting the owner is not going to is not moved under them. A baseline revision SHALL never be an item, and neither SHALL a revision with no earlier journaled
version: a newly seen invitation belongs to the invitations list, not here. A location SHALL be compared only when both the revision and its predecessor kept their raw
payload (a readable-to-blocked flip is never a location change). A resync revision SHALL be
diffed against its previous revision like any other, so a burst of resyncs that changes no named field
yields nothing. An item SHALL be returned only while the event's new start (its prior start, for a
cancellation) is still in the future; a client-held seen cursor hides them in that browser, and
there is no persisted dismiss until bu-5ii4gj.11.2, so items age out and do not clear. A `since` in
the future (client clock skew) SHALL read as now, and an age up to five minutes past the bound SHALL
still be served.

One organizer change SHALL be one item across the owner's calendars and accounts: items SHALL be
keyed on the event's iCalUID (the event id when the journal row has none) with the new start, end,
status and location, keeping the earliest observation. Items SHALL be ordered by observation
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

A revision of an event the owner organizes SHALL NOT be an item (the organizer is the owner when the
provider says so or when the organizer address is one of the owner's connected account addresses), whoever applied it (the owner in the
provider's own app, or a Butlers write). A revision whose organizer cannot be told (a
policy-blocked row keeps structure and times only) SHALL be included: it SHALL be returned with its
times and status, `organizer_self` null (shown as "organizer unknown") and no location, so the owner
can see a move; it SHALL never be returned as the
owner's own edit and SHALL never be silently dropped.

#### Scenario: A meeting journaled on two of the owner's calendars is one item

- **WHEN** the organizer moves a meeting the owner holds on two connected accounts
- **THEN** the feed SHALL return one item

#### Scenario: A new invitation is not a change

- **WHEN** a revision has no earlier journaled version
- **THEN** the feed SHALL return no item for it

#### Scenario: The owner reschedules their own meeting

- **WHEN** the owner moves an event they organize
- **THEN** the feed SHALL return no item for it

#### Scenario: An unknown organizer is shown, not hidden

- **WHEN** a policy-blocked revision changes an event's times
- **THEN** the feed SHALL return it with `organizer_self` null ("organizer unknown") and no location

### Requirement: The journal is read through a narrow reader

ID: REQ-calendar-changes-feed-003
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

The changes read SHALL reach the revision journal only through a `SECURITY DEFINER` function with a
fixed `search_path` that returns the event id, calendar id, observation time, prior and new start,
end, status and location, whether a prior version was known, the organizer flag (`organizer_self`,
true when the organizer is the calendar's owner or one of the owner's own connected account addresses
the caller passes in, null when the row keeps structure only), and no raw
payload, attendee, description or conference data. It SHALL leave out the owner's own revisions and the revisions of events the owner has declined, and
SHALL fetch one row past its cap so the route can say the result was truncated, and SHALL clamp its
window. The calling role SHALL
have execute on that function and no direct grant on the journal. Titles SHALL come from the calendar
projection the owner can already read, not from the journal, and an event the projection holds as a
quarantined invitation, or does not hold at all, SHALL be shown with no title, entry or location.

#### Scenario: The reader exposes only the named fields

- **WHEN** the function is called
- **THEN** its result columns SHALL be exactly the named fields, with no raw payload or description column, and it SHALL return at most its cap

### Requirement: An unreadable feed is unknown, never empty

ID: REQ-calendar-changes-feed-004
Source: bu-5ii4gj.11 track N, slice N3
Scope: v1-mandatory

When the journal read fails, the route SHALL answer with `changes_available: false` and no items, and a result cut at the cap SHALL carry `truncated: true`, and an unreadable list of the owner's account addresses `owners_complete: false`, which the queue SHALL treat as a lower bound ("+"),
and the Needs-you queue SHALL then name `changes` as unknown and show its count as a lower bound; it
SHALL NOT read the failure as no changes. A change item SHALL show its before and after, SHALL open
the event as its evidence, and SHALL NOT bind an accept or decline key.

#### Scenario: A failed read does not look like calm

- **WHEN** the journal read fails
- **THEN** the route SHALL return `changes_available: false` and the queue SHALL name changes as unknown
