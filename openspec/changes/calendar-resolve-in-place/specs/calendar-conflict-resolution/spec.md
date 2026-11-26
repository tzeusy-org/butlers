## ADDED Requirements

### Requirement: Overlap resolution options are compiled deterministically

ID: REQ-calendar-conflict-resolution-001
Source: bu-5ii4gj.11 slice R1; design "Resolve, not Fix"
Scope: v1-mandatory

Each `overlap` issue in a healthy scan SHALL carry one to five resolution options, always including
`keep_both`, compiled by a pure function of the issue, both events' provenance and the owner's
response to each, and the free slots on every owner-busy calendar. No model, provider call or
randomness SHALL take part. An option SHALL have an id, a kind (`move_block`, `move_own`, `decline`,
`tentative`, `keep_both`, and the reserved `ask_organizer`), an exact call (tool and arguments) and
an authority class (`no_park_undo`, `owner_submit`, `parked_per_event`). A Butlers block with no
attendees against a primary meeting SHALL offer `move_block` to the earliest slot free on every
owner-busy calendar. An owner-organised solo event against a meeting SHALL offer `move_own`. Two
invited meetings SHALL offer `decline` or `tentative` on either, parked per event. `ask_organizer`
is a reserved kind only: it SHALL NOT be offered while standing-reply templates (bu-jv1moa.28) are
parked. When either event recurs, an option SHALL act on that occurrence only and a series edit
SHALL never be offered. An issue whose calendar source is stale SHALL carry no options at all,
`keep_both` included (an acknowledgement is never recorded against etags read while stale), and the
reason `stale_source`. When no free slot exists, the issue SHALL carry the reason `no_free_slot` and
keep `keep_both` and the decline options. Free slots SHALL be on a 15-minute grid so equal inputs
within one grid step give equal digests.

#### Scenario: A Butlers block against a primary meeting offers a move

- **WHEN** a Butlers block with no attendees overlaps a primary-calendar meeting
- **THEN** the issue SHALL offer `move_block` to a slot free on both calendars with authority `no_park_undo`

#### Scenario: A recurring event is only ever resolved per occurrence

- **WHEN** one of the overlapping events is a recurring instance
- **THEN** every option SHALL carry that occurrence's scope and none SHALL edit the series

#### Scenario: A stale source offers nothing

- **WHEN** a calendar source is stale
- **THEN** the issue SHALL have no options, `keep_both` included, and the reason `stale_source`, and SHALL remain informational

#### Scenario: No free slot is stated

- **WHEN** a Butlers block overlaps a meeting and no slot is free on every owner-busy calendar
- **THEN** the issue SHALL carry the reason `no_free_slot` and still offer `keep_both`

#### Scenario: ask_organizer is never offered

- **WHEN** an overlap of two invited meetings is compiled while standing-reply templates are parked
- **THEN** no option SHALL have the kind `ask_organizer`

### Requirement: The conflicts read carries a digest and resolve is bound to it

ID: REQ-calendar-conflict-resolution-002
Source: bu-5ii4gj.11 slice R2; design "Resolve, not Fix"
Scope: v1-mandatory

`GET /api/calendar/workspace/conflicts` SHALL return, per overlap issue, `options[]` and an
`issue_digest` over both events' provider etags and the option set. `POST
/api/calendar/workspace/conflicts/resolve` SHALL accept a pair id (the provider-identity pair),
an option id and the digest, SHALL recompute the issue server side, and SHALL return 409
`issue_changed` with no write when the issue is gone or its digest does not match. `keep_both`
SHALL be recorded locally with no provider write. Every option that writes to a provider SHALL
be parked as an approvals card bound by its arguments digest and approved by the owner in the
existing approvals surface, and the route SHALL NOT write the provider itself (the owner's
decision bu-kf3n4i retired dashboard event updates). The etag the owner saw SHALL be bound to the
parked action and kept outside the call's arguments: when the approved move is applied it SHALL
be declined `changed_since_check` and not retried if the event's etag has moved. An uncertain
write SHALL be reported `uncertain` and SHALL NOT be resent. A `decline` raised from the radar
SHALL send no updates to the organizer (`send_updates` `none`): the response is shown on the
owner's own calendar and no email is sent.

#### Scenario: Resolving a move parks an approvals card

- **WHEN** the owner resolves a Butlers-block overlap with `move_block` and the current digest
- **THEN** an approvals card for the exact etag-bound update SHALL be parked and no provider write SHALL occur until the owner approves it

#### Scenario: A stale digest writes nothing

- **WHEN** a resolve arrives with a digest computed before one event's etag changed
- **THEN** the response SHALL be 409 `issue_changed` and nothing SHALL be written or parked

#### Scenario: A provider conflict is not retried

- **WHEN** the approved move finds the event's etag changed, or the provider answers 412
- **THEN** the outcome SHALL be declined `changed_since_check` and the call SHALL NOT be repeated

### Requirement: Keeping both is a recorded premise that ends on any change

ID: REQ-calendar-conflict-resolution-003
Source: bu-5ii4gj.11 slice R2; design "Resolve, not Fix"
Scope: v1-mandatory

`public.calendar_conflict_acknowledgements` SHALL record, for an overlap, the pair identity, the
issue digest the owner saw (which covers both events' etags), the option and when it was
acknowledged. A pair acknowledged at its current digest SHALL NOT appear as a radar issue and SHALL
NOT count in the verdict or the overcommitment producer. A change to either event's etag, time or
attendees SHALL move the digest, void the acknowledgement and make the issue reappear. The
dashboard's session-local dismiss SHALL be replaced by this persisted decision and SHALL write a
receipt. A repeat of an applied acknowledgement SHALL be an idempotent success.

#### Scenario: Keep both clears the issue and its condition

- **WHEN** the owner chooses `keep_both` on an overlap
- **THEN** the issue and its owner condition SHALL clear and a receipt SHALL exist

#### Scenario: A change reopens it

- **WHEN** one of the acknowledged events' etags changes
- **THEN** the issue and its owner condition SHALL both reappear

### Requirement: Options that reach other people are parked per event

ID: REQ-calendar-conflict-resolution-004
Source: bu-5ii4gj.11 slice R3; design "Resolve, not Fix"
Scope: v1-mandatory

A `decline` or `tentative` option SHALL go through `calendar_respond`, always parked per event, and
SHALL never be sent as a batch approval. No third-party event text SHALL enter a prompt or a
message body, and no option SHALL send free text. Any future message to an organizer
(`ask_organizer`, reserved and not offered while standing-reply templates are parked) SHALL be an
owner-authored standing-reply template whose only variable parts are typed datetime slots, parked
per event.

#### Scenario: Declining an invited meeting is parked

- **WHEN** the owner picks `decline` on an invited meeting
- **THEN** the response SHALL be parked per event for approval and no provider write SHALL occur until it is approved

#### Scenario: A radar decline sends no organizer email

- **WHEN** a radar `decline` is approved
- **THEN** the response SHALL be sent with updates `none` and no email SHALL reach the organizer
