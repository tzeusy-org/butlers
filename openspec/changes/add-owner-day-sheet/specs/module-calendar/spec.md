## ADDED Requirements

### Requirement: Owner Day Sheet

One pure projection, `build_day_sheet`, SHALL turn a set of calendar occurrences, the per-source read
state and an owner zone and local date into a `DaySheet`, and one fixed-template renderer,
`render_day_sheet_text`, SHALL turn it into text. Every surface that says what an owner's day holds
SHALL use them; no model composes the sheet.

The sheet's day SHALL be the owner-local calendar day in the owner's IANA zone, so a day with a
daylight-saving shift is 23 or 25 hours long. An occurrence SHALL be listed only when
`asserts_owner_time` holds for its source role and metadata, it is not cancelled, it is not a status
event (out of office, working location), and the owner has not declined it. A transparent event SHALL
be omitted. Items SHALL be in start order, all-day items first. A tentative event SHALL be listed and
marked tentative; a butler-lane or butler-generated event SHALL be listed as `butler`. Only attended
items, clipped to the day, SHALL count toward `load.attended_minutes`, and overlaps SHALL count once.
Free gaps SHALL be computed inside the day window against attended, tentative and butler items.

The sheet's `freshness.state` SHALL be `unreadable` when any source could not be read or when no source
is known, `stale` when every source was read but one's last sync is older than its staleness bound,
and `current` otherwise, and it SHALL name the unreadable sources. The text SHALL say "Nothing on your
calendar" only when the state is `current` and the sheet has no items. When every source is unreadable
the text SHALL say the calendar is unreadable and SHALL list no items; when some are, it SHALL list the
readable items and say how many sources are unreadable. A stale sheet's text SHALL be prefixed with its
as-of time. Third-party event titles SHALL be defanged in the text. The sheet SHALL carry a digest over
its day, zone and items that is equal for equal content.

ID: REQ-module-calendar-036
Source: bu-5ii4gj.2 (run 26 move 2), slice S1
Scope: v1-mandatory

#### Scenario: Only the owner's own time is listed

- **WHEN** the day holds an accepted event, a declined one, a transparent one, a butler-lane hold and an event on another person's calendar
- **THEN** the sheet lists the accepted event and the butler hold in start order and nothing else

#### Scenario: An unreadable calendar is never a clear day

- **WHEN** a source could not be read and no item was found
- **THEN** `freshness.state` is `unreadable`, the text names the failure, and it never says "Nothing on your calendar"

#### Scenario: A genuinely empty day is said once it is proven

- **WHEN** every source was read recently and the day has no owner-time items
- **THEN** the text says "Nothing on your calendar"

#### Scenario: A stale read says so

- **WHEN** a source's last sync is older than its staleness bound
- **THEN** the text begins "As of HH:MM"

#### Scenario: Equal content has an equal digest

- **WHEN** the same occurrences are projected twice, in a different input order
- **THEN** the digests are equal
