## ADDED Requirements

### Requirement: Overlay freshness and failure are stated

ID: REQ-calendar-overlay-aggregation-006
Source: bu-5ii4gj.13 (run 26 move 13), slice S1
Scope: v1-mandatory

Every overlay envelope SHALL carry `generated_at` (the instant the producing job ran) and
`cadence_seconds` (how often that job is scheduled). The workspace read SHALL derive, per
contributing butler, one of five states and SHALL return them with the overlay lane: `ok` (an
envelope exists and `now` is within twice the cadence of `generated_at`), `stale` (an envelope
exists but is older than that), `partial` (the envelope is marked incomplete, because the producer
could not read all of its records), `missing` (the view was read and holds no envelope for the
butler), and `failed` (the view could not be read, so every contributor is `failed` and
`overlays_available` is false). A `stale`, `partial` or `failed` butler SHALL be named in the
response, and its entries SHALL NOT be presented as current. A failed read is never presented as "no
domain context": `overlays_available=false` disambiguates, and the empty value stays reserved for
`missing` and for `ok` with no entries. An envelope written before this requirement, with no
`generated_at`, SHALL read as `stale`. Per-butler reads of the view are later work, if ever.

#### Scenario: A frozen producer reads as stale

- **WHEN** a butler's job has not run for longer than twice its cadence
- **THEN** the response names that butler as stale and marks none of its entries as current

#### Scenario: An incomplete envelope reads as partial

- **WHEN** a butler's envelope is marked incomplete
- **THEN** the response names that butler as partial and marks its entries as not current

#### Scenario: An unreadable view is not an empty one

- **WHEN** the overlay view cannot be read
- **THEN** every contributor is `failed`, `overlays_available` is false, and `has_domain_context` is not read as "no domain context"

### Requirement: A domain record owns one claim

ID: REQ-calendar-overlay-aggregation-007
Source: bu-5ii4gj.13 (run 26 move 13), slice S2
Scope: v1-mandatory

The overlay envelope SHALL be schema version 2: besides `butler`, `date`, `generated_at`,
`cadence_seconds` and `has_entries`, it carries `claims`, an array with one claim per domain record.
A claim SHALL carry `claim_key` (`<domain>:<record_kind>:<record_id>`, stable for the record's
life), `record_version` (changes when the time, amount or status the claim depends on changes),
`kind`, `label`, `priority`, a `span` (start and end, or an all-day date), `presence`
(`overlay`, `block` or `reminder`), `reminder_offsets` (for `reminder`), `owner_action` (whether the
owner must act), `status` (`active`, `settled` or `cancelled`) and a `door` naming the route and the
record reference. Claims SHALL live under the existing `calendar/overlay/<date>` key in the owning
butler's own state, read through the existing view; no new view, grant or migration is introduced.
Labels SHALL be limited to what the overlay already carries (payee, trip name, person name), and the
person-posture filter SHALL still apply. No model SHALL write or reconcile a claim.

#### Scenario: One record, one claim

- **WHEN** a producer emits claims for a day
- **THEN** each domain record appears once, under one `claim_key`, whatever its presence

### Requirement: A claim is moved or retracted with its record

ID: REQ-calendar-overlay-aggregation-008
Source: bu-5ii4gj.13 (run 26 move 13), slice S2
Scope: v1-mandatory

The producing job SHALL be the one that reconciles: it reads its own domain tables, emits the
complete set of claims for its window, and reconciles the calendar rows for them in the same run
(REQ-module-calendar-061). A claim whose record is settled, cancelled or gone, or whose person is
quiet or no-contact, SHALL be emitted with its settled or cancelled status or omitted, and its
calendar row SHALL be retracted in that run. A record whose `record_version` is unchanged SHALL cause
no write. A run that fails, or reads a source only partially, SHALL keep the previous claims, mark the
envelope incomplete, and retract nothing.

#### Scenario: A settled bill is retracted

- **WHEN** a pending bill's claim has a calendar row and the bill is auto-settled
- **THEN** the next run retracts that row and the provider receives the delete

#### Scenario: A rebooked flight moves the same row

- **WHEN** a leg's departure changes
- **THEN** the next run updates the same row in place (same `claim_key`) and the provider receives a patch, not a new event

#### Scenario: A partial read retracts nothing

- **WHEN** a producer cannot read part of its source in a run
- **THEN** no calendar row is retracted and the envelope is marked incomplete

### Requirement: Claim producers

ID: REQ-calendar-overlay-aggregation-009
Source: bu-5ii4gj.13 (run 26 move 13), slices S3 to S5
Scope: v1-mandatory

The producers SHALL be the butlers that own the record: finance (bills, subscription renewals,
`cancel_by`), travel (legs, accommodations, reservations, check-in reminders, document expiry),
health (visits), relationship (important dates, follow-ups) and education (one aggregated daily
"N reviews due" claim, never one per node). The contributing set of the existing job requirement
widens to include education for this claim only. The finance and travel agent instructions SHALL no
longer tell a model to create reminders or block flights for records a claim now covers.

#### Scenario: Education claims are aggregated

- **WHEN** education has reviews due on a day
- **THEN** it emits one claim for that day carrying the count, not one per item

### Requirement: The workspace shows a record once, with a door

ID: REQ-calendar-overlay-aggregation-010
Source: bu-5ii4gj.13 (run 26 move 13), slice S6
Scope: v1-mandatory

The workspace SHALL deduplicate the overlay lane and the butler lane on `claim_key`, so one record
renders once (the calendar row when one exists, else the overlay entry). An item that came from a
claim SHALL offer a door to its record through the claim's `door`.

#### Scenario: No duplicate between lanes

- **WHEN** a claim has presence `block` and also appears in the overlay
- **THEN** the workspace renders one item for its `claim_key`

### Requirement: An owner override survives a re-emit until the record changes

ID: REQ-calendar-overlay-aggregation-011
Source: bu-5ii4gj.13 (run 26 move 13), slice S6
Scope: v1-mandatory

When the owner snoozes or moves a claim's calendar row, the override SHALL be kept with that row
(through the existing butler-event tools, so no new write path) and SHALL hold across re-emits of
the same `record_version`. A changed `record_version` that moves the claim's time SHALL drop the
override. An in-place update SHALL keep the owner's override fields unless the claim's span changed.

#### Scenario: Snooze survives an unchanged record

- **WHEN** the owner snoozes a reminder claim and the producer re-emits the same `record_version`
- **THEN** the snooze is kept
