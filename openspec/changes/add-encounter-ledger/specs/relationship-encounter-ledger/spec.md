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
`updated_at`, and a guest removed from the event SHALL lose the row.

ID: REQ-relationship-encounter-ledger-002
Source: bu-5ii4gj.3 slice E2a; design behavior matrix (precedence, idempotence)
Scope: v1-mandatory

#### Scenario: A guest's decline survives an answered debrief

- **WHEN** the owner answered a debrief `captured` and one guest declined
- **THEN** the guest SHALL be `declined_by_them` and every other recordable guest `happened_attested`

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
CHECK. Nothing writes the two states until the answer tool changes in the readers slice, and no
reader SHALL consult the ledger in this slice: `calendar_prep`, the meeting card, `interaction_sync`
and the Dunbar and stale-contact weights switch to it in slice E2b.

ID: REQ-relationship-encounter-ledger-004
Source: bu-5ii4gj.3 slice E2a; design (migration widens meeting_debriefs state CHECK)
Scope: v1-mandatory

#### Scenario: Upgrade, downgrade and re-upgrade

- **WHEN** the migration is upgraded twice, downgraded with a `moved` row present, then upgraded again
- **THEN** every step SHALL succeed, the row SHALL be `expired` after the downgrade, and the ledger
  table SHALL exist only while upgraded
