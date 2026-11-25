## ADDED Requirements

### Requirement: The encounter ledger keeps one state per person and occurrence

The Relationship butler SHALL keep `relationship.encounters`, one row per
`(entity_id, event_id, occurrence_start)`, whose `state` is one of `scheduled`, `moved`,
`cancelled`, `declined_by_owner`, `declined_by_them`, `happened_attested`, `happened_corroborated`,
`did_not_happen` or `unknown`, and whose `basis` is one of `schedule`, `rsvp`, `owner_debrief` or
`chronicler`. A row SHALL be derived, never a source of truth, and its `evidence_refs` SHALL carry
identifiers only, never an address or a name. Only an exact or manual participant that is not a
resource and whose entity posture is `active` SHALL get a row.

ID: REQ-relationship-encounter-ledger-001
Source: bu-5ii4gj.3 slice E2a; design "Encounter ledger"; docs/concepts/identity-model.md
Scope: v1-mandatory

#### Scenario: An ended meeting nobody vouches for is unknown

- **WHEN** an occurrence has started, was not cancelled and has no answered debrief
- **THEN** each recordable guest SHALL have state `unknown`, never a state that reads as met

#### Scenario: Only recordable people are recorded

- **WHEN** an occurrence has an ambiguous or unresolved address, a resource, the owner's own seat
  and a person whose posture is not `active`
- **THEN** none of them SHALL get a row

### Requirement: The reducer is deterministic and owner testimony wins

The reducer SHALL compute a person's state from the participant records, the event's status and
start, and the owner's debrief answer, in this order: a debrief `did_not_happen` or `moved`; the
guest's decline (`declined_by_them`); an answered debrief (`none_agreed` or `captured`) as
`happened_attested`; a cancelled event; the owner's decline (`declined_by_owner`); an occurrence
that has not started (`scheduled`); otherwise `unknown`. A `pending` or `expired` debrief SHALL NOT
count as testimony. Re-running the reducer over unchanged inputs SHALL change nothing, including
`updated_at`, and a guest removed from the event SHALL lose the row. A ledger row whose occurrence
the calendar no longer has (a reschedule, a deleted event, a dropped recurring instance) SHALL be
removed when its event's participants are readable; the check SHALL query the calendar per event
without the read cap, so the cap never purges valid history. The capped read SHALL prefer the
occurrences nearest to now. An entity merge SHALL repoint the ledger's rows to the survivor, a
collision on the same occurrence keeping the survivor's row.

ID: REQ-relationship-encounter-ledger-002
Source: bu-5ii4gj.3 slice E2a; design behavior matrix (precedence, idempotence)
Scope: v1-mandatory

#### Scenario: A guest's decline survives an answered debrief

- **WHEN** the owner answered a debrief `captured` and one guest declined
- **THEN** the guest SHALL be `declined_by_them` and every other recordable guest `happened_attested`

#### Scenario: A rescheduled event leaves no ghost

- **WHEN** an event moves from Tuesday to Thursday
- **THEN** the ledger SHALL hold only the Thursday occurrence

#### Scenario: Replay converges

- **WHEN** the reducer runs twice over the same inputs
- **THEN** the second run SHALL leave every row byte-identical

### Requirement: An unreadable input never changes the ledger

The reducer SHALL finish every read before it opens its one write transaction. A failed read SHALL
leave every prior row unchanged and the run SHALL report `complete: false` so no caller advances a
checkpoint past the gap. An event whose participant records are not ready at the current resolver
version SHALL keep its prior rows. A failed write SHALL roll the whole run back.

ID: REQ-relationship-encounter-ledger-003
Source: bu-5ii4gj.3 slice E2a; bu-2mpps3 (an unreadable input is not an empty one)
Scope: v1-mandatory

#### Scenario: A failed participant read

- **WHEN** the participant read raises
- **THEN** no row SHALL be written or removed and the result SHALL say `complete: false`

### Requirement: The debrief state vocabulary admits the two ledger outcomes

`relationship.meeting_debriefs.state` SHALL admit `did_not_happen` and `moved` beside `pending`,
`none_agreed`, `captured` and `expired`. The migration SHALL replace the CHECK by name so a re-run is
a no-op. Its downgrade SHALL map the two new states to `expired` before restoring the four-state
CHECK. Nothing writes the two states until the answer tool changes in a later slice. In slice E2a
no reader consulted the ledger; slice E2b (REQ-relationship-encounter-ledger-005) switches prep and
the meeting card. `interaction_sync`, the Dunbar weights and the stale-contact weights keep the
interim rule until the owner decides retraction and weighting.

ID: REQ-relationship-encounter-ledger-004
Source: bu-5ii4gj.3 slice E2a; design (migration widens meeting_debriefs state CHECK)
Scope: v1-mandatory

#### Scenario: Upgrade, downgrade and re-upgrade

- **WHEN** the migration is upgraded twice, downgraded with a `moved` row present, then upgraded again
- **THEN** every step SHALL succeed, the row SHALL be `expired` after the downgrade, and the ledger
  table SHALL exist only while upgraded

### Requirement: Prep and the meeting card read last met from the ledger

The relationship prep job SHALL derive each attendee's `last_met` only from a ledger row whose state
is `happened_attested` or `happened_corroborated`, and SHALL report the row's basis as
`you_confirmed` (owner debrief) or `evidence` (Chronicler). The latest `scheduled` or `unknown` row
SHALL be reported as `last_scheduled`, never as `last_met`. A person the ledger has no row for yet,
or any person when the ledger read fails, SHALL keep the interim rule and SHALL be labelled
`interim` (accepted, not confirmed), so a failed read is never an empty ledger and a ledger that has
not caught up never hides a meeting. A person the ledger covers but with no happened row SHALL
also get the interim rule over history older than the ledger window (before now minus the reducer's
lookback), labelled `interim`, so a rollout never erases an older meeting. A happened row with an
unrecognised basis SHALL log a warning and show the neutral basis `recorded`. A person whose posture is not `active` SHALL never be reported
from the ledger. The reducer SHALL run on an hourly schedule. The meeting card and the prep rail
SHALL render "last met" only from a vouched-for meeting (with its basis), "last met, unconfirmed"
for the interim basis, and "last scheduled" otherwise.

ID: REQ-relationship-encounter-ledger-005
Source: bu-5ii4gj.3 slice E2b; design "Encounter ledger" (relationship_person_time, last-met reads only happened_*)
Scope: v1-mandatory

#### Scenario: A meeting nobody vouches for is last scheduled

- **WHEN** a person's only past ledger row is `unknown`
- **THEN** the envelope SHALL carry `last_met: null` and the row's start as `last_scheduled`
- **AND** the card SHALL say "last scheduled", never "last met"

#### Scenario: The basis is shown

- **WHEN** the latest happened row is basis `owner_debrief`
- **THEN** the envelope SHALL carry `last_met_basis: "you_confirmed"` and the rail SHALL show that chip

#### Scenario: The ledger has not caught up

- **WHEN** a person has no ledger row, or the ledger read fails
- **THEN** the interim rule SHALL supply `last_met` with basis `interim`
