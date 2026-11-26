## ADDED Requirements

### Requirement: Overlap resolution options are compiled deterministically

Each `overlap` issue in a healthy scan SHALL carry one to five resolution options compiled by a pure
function of the issue, both events' provenance and the owner's response to each, and the free slots
on every owner-busy calendar. No model, provider call or randomness SHALL take part. An option SHALL
have an id, a kind (`move_block`, `move_own`, `decline`, `tentative`, `ask_organizer`, `keep_both`),
an exact call (tool and arguments) and an authority class (`no_park_undo`, `owner_submit`,
`parked_per_event`). A Butlers block with no attendees against a primary meeting SHALL offer
`move_block` to the earliest slot free on every owner-busy calendar. An owner-organised solo event
against a meeting SHALL offer `move_own`. Two invited meetings SHALL offer `decline` or `tentative`
on either, parked per event, and `ask_organizer`. `keep_both` SHALL always be offered. When either
event recurs, an option SHALL act on that occurrence only and a series edit SHALL never be offered.
When a calendar source is stale, the issue SHALL carry no options and the reason `stale_source`.
When no free slot exists, the issue SHALL say so and keep `keep_both` and the decline options.

ID: REQ-calendar-conflict-resolution-001
Source: bu-5ii4gj.11 slice R1; design "Resolve, not Fix"
Scope: v1-mandatory

#### Scenario: A Butlers block against a primary meeting offers a move

- **WHEN** a Butlers block with no attendees overlaps a primary-calendar meeting
- **THEN** the issue SHALL offer `move_block` to a slot free on both calendars with authority `no_park_undo`

#### Scenario: A recurring event is only ever resolved per occurrence

- **WHEN** one of the overlapping events is a recurring instance
- **THEN** every option SHALL carry that occurrence's scope and none SHALL edit the series

#### Scenario: A stale source offers nothing

- **WHEN** a calendar source is stale
- **THEN** the issue SHALL have no options and the reason `stale_source`, and SHALL remain informational

### Requirement: The conflicts read carries a digest and resolve is bound to it

`GET /api/calendar/workspace/conflicts` SHALL return, per overlap issue, `options[]` and an
`issue_digest` over both events' provider etags and the option set. `POST
/api/calendar/workspace/conflicts/{pair_id}/resolve` SHALL accept an option id and the digest,
SHALL recompute the options server side, SHALL return 409 `issue_changed` with no write when the
digest does not match, and otherwise SHALL run the option's exact call on the owning butler through
the Switchboard as the owner's own submit. The response SHALL carry a receipt (event id and the new
etag) and an undo (tool and arguments). A provider 412 on the move SHALL be reported as declined
`changed_since_check` and SHALL NOT be retried; an uncertain write SHALL be reported `uncertain` and
SHALL NOT be resent.

ID: REQ-calendar-conflict-resolution-002
Source: bu-5ii4gj.11 slice R2; design "Resolve, not Fix"
Scope: v1-mandatory

#### Scenario: Resolving moves the block and the next scan is clear

- **WHEN** the owner resolves a Butlers-block overlap with `move_block` and the current digest
- **THEN** the block SHALL move with an etag-bound update, the next scan SHALL show no issue, and the returned undo SHALL restore it

#### Scenario: A stale digest writes nothing

- **WHEN** a resolve arrives with a digest computed before one event's etag changed
- **THEN** the response SHALL be 409 `issue_changed` and no provider update SHALL be sent

#### Scenario: A provider conflict is not retried

- **WHEN** the provider answers 412 to the move
- **THEN** the outcome SHALL be declined `changed_since_check` and the call SHALL NOT be repeated

### Requirement: Keeping both is a recorded premise that ends on any change

`public.calendar_conflict_acknowledgements` SHALL record, for an overlap, the pair identity, both
events' etags, when the owner acknowledged it and when it was revoked. A pair acknowledged at its
current etags SHALL NOT appear as a radar issue, SHALL NOT count in the verdict and SHALL resolve
its owner condition. A change to either event's etag SHALL void the acknowledgement and the issue
SHALL reappear. The dashboard's session-local dismiss SHALL be replaced by this persisted decision
and SHALL write a receipt.

ID: REQ-calendar-conflict-resolution-003
Source: bu-5ii4gj.11 slice R2; design "Resolve, not Fix"
Scope: v1-mandatory

#### Scenario: Keep both clears the issue and its condition

- **WHEN** the owner chooses `keep_both` on an overlap
- **THEN** the issue and its owner condition SHALL clear and a receipt SHALL exist

#### Scenario: A change reopens it

- **WHEN** one of the acknowledged events' etags changes
- **THEN** the issue and its owner condition SHALL both reappear

### Requirement: Options that reach other people are parked per event

A `decline` or `tentative` option SHALL go through `calendar_respond`, always parked per event, and
SHALL never be sent as a batch approval. `ask_organizer` SHALL use an owner-authored standing-reply
template whose only variable parts are typed datetime slots, SHALL be parked per event, and SHALL NOT
be offered until standing-reply templates are active. No third-party event text SHALL enter a prompt
or a message body, and no option SHALL send free text.

ID: REQ-calendar-conflict-resolution-004
Source: bu-5ii4gj.11 slices R3 and R4; design "Resolve, not Fix"
Scope: v1-mandatory

#### Scenario: Declining an invited meeting is parked

- **WHEN** the owner picks `decline` on an invited meeting
- **THEN** the response SHALL be parked per event for approval and no provider write SHALL occur until it is approved

#### Scenario: The organizer template carries only typed slots

- **WHEN** `ask_organizer` is compiled
- **THEN** its message SHALL be the owner's template with typed datetime slots filled and no event text
