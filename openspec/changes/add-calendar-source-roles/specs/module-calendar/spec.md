## ADDED Requirements

### Requirement: Calendar Source Roles

Every `calendar_sources` row SHALL carry a `time_role` from the closed set `owner`,
`household_member`, `other_person`, `team`, `subscription`, `holidays`, `birthdays`, `butler` and
`unclassified`, and a `role_basis` of `provider_inferred` or `owner_confirmed`. The default for a
row that names no role SHALL be `unclassified`.

Discovery SHALL assign the role from provider facts only, never from a calendar's title or
display name, and SHALL NOT infer household membership: the primary calendar is `owner`; the id
suffix `#holiday@group.v.calendar.google.com` is `holidays`; the id
`addressbook#contacts@group.v.calendar.google.com` is `birthdays`; the resolved Butlers calendar
and every butler-lane internal source is `butler`; any other calendar, including one the account
may write and one it may only read, is `unclassified`. A provider-inferred role SHALL NOT make
another person's calendar `owner`. Rediscovery and resync SHALL replace a `provider_inferred` role
and SHALL NOT overwrite an `owner_confirmed` one.

ID: REQ-module-calendar-034
Source: bu-5ii4gj.1 (run 26 move 1), slice S1
Scope: v1-mandatory

#### Scenario: Primary and shared calendars are classified from provider facts

- **WHEN** discovery lists the account's primary calendar, a partner's shared calendar the account may only read, a team calendar it may write, a holidays calendar and the contacts birthdays calendar
- **THEN** their roles are `owner`, `unclassified`, `unclassified`, `holidays` and `birthdays` with basis `provider_inferred`

#### Scenario: A confirmed role survives rediscovery

- **WHEN** a source's role is `owner_confirmed` and discovery runs again with a different inferred role
- **THEN** the stored role and basis are unchanged

#### Scenario: Existing sources are backfilled

- **WHEN** the migration runs on a ledger that predates roles
- **THEN** each row is classified from its stored discovery facts with basis `provider_inferred`, and a row the facts cannot classify is `unclassified`

### Requirement: One Owner-Time Predicate

One pure predicate, `asserts_owner_time(time_role, metadata)`, SHALL decide whether an event on a
source is the owner's own time. The roles `owner` and `butler` SHALL assert it. The roles
`household_member`, `other_person`, `team` and `unclassified` SHALL assert it only when the event
has an attendee marked `self` who has not declined. The roles `holidays`, `birthdays` and
`subscription` SHALL never assert it. An absent or unknown role SHALL be `unclassified`.
`counts_as_owner_time` SHALL compose it with the owner's RSVP and transparency rule.

Every reader that derives owner state from calendar events SHALL use it: the context producer, the
conflict radar and day load, the declared working location, the meeting card, prep and the meeting
debrief. An event excluded by it SHALL remain in the workspace projection, so it can be shown as
attributed context; only analysis skips it. `calendar_find_free_slots` with no `calendar_ids` SHALL
span only the sources whose role is `owner` or `butler`, and an explicit list SHALL be honored.

ID: REQ-module-calendar-035
Source: bu-5ii4gj.1 (run 26 move 1), slice S1
Scope: v1-mandatory

#### Scenario: Another person's event is not the owner's time

- **WHEN** a confirmed timed event on an `unclassified` or `household_member` source has no attendee marked `self`
- **THEN** it sets no meeting context, adds no conflict or day load, removes no free slot, and gets no card, prep or debrief

#### Scenario: Being invited makes it the owner's time

- **WHEN** the same event has a `self` attendee who has accepted, or has not answered
- **THEN** it counts as owner time
- **AND** a `self` attendee who declined takes it out again

#### Scenario: Holidays and birthdays are never meetings

- **WHEN** an event is on a `holidays` or `birthdays` source
- **THEN** it never counts as owner time, whatever its attendees

#### Scenario: Reclassifying takes effect without a resync

- **WHEN** the owner's role for a source changes
- **THEN** the next reader run applies the new role to every stored event on that source

#### Scenario: Free slots default to owner time

- **WHEN** `calendar_find_free_slots` is called without `calendar_ids`
- **THEN** the free/busy lookup spans the `owner` and `butler` sources only

### Requirement: Owner Role Assignment

The owner SHALL be able to set the role of a calendar source, which SHALL be stored with
`role_basis` `owner_confirmed`. `POST /api/calendar/sources/role` SHALL take a `source_key` and a
`role` from the closed role set, or a null role to clear the confirmation, and SHALL apply it to
the matching source in every calendar butler schema, because the same provider calendar is a source
in each. The write SHALL fail with 404 when no schema has the source and SHALL be audited. Clearing
SHALL set the role to `unclassified` with basis `provider_inferred` so the next discovery can infer
it again. The source list SHALL carry each source's `time_role` and `role_basis` so the owner can
see what is assumed. Nothing SHALL assign a role to a non-primary calendar on its own, including from
its access role.

ID: REQ-module-calendar-037
Source: bu-5ii4gj.1 (run 26 move 1), slice S2
Scope: v1-mandatory

#### Scenario: The owner classifies a shared calendar

- **WHEN** the owner sets a partner's shared calendar to `household_member`
- **THEN** every schema's source for it has that role with basis `owner_confirmed`, and later discovery does not change it

#### Scenario: An unknown role is refused

- **WHEN** the request names a role outside the closed set
- **THEN** it is rejected with 422 and nothing is written

#### Scenario: Clearing returns the source to inference

- **WHEN** the owner clears a confirmed role
- **THEN** the role is `unclassified` with basis `provider_inferred`
