## ADDED Requirements

### Requirement: Calendar Event Participants

Every provider calendar event SHALL have one participant record per attendee address in
`calendar_event_participants`, written by the calendar projection when it writes the event. A row
SHALL carry the normalized address, the organizer-supplied display name only for an `ambiguous` or
`unresolved` address (untrusted, never shown to a reader), a role (`organizer`, `required`, `optional` or
`resource`), the attendee's response status, a resolution state (`exact`, `ambiguous`,
`unresolved`, `owner`, `resource` or `manual`), the resolved entity id when the state is `exact`,
`owner` or `manual`, the resolver version and the resolution time. An empty attendee list SHALL
delete the provider-derived rows of that event and keep its `manual` rows. The same payload
synced twice SHALL produce identical rows. A participant row SHALL be an "invited" claim: it
SHALL NOT be written as an interaction, an encounter or an owner assertion. A quarantined
invitation (REQ-module-calendar-041) SHALL have no provider-derived participant rows. Readers
SHALL receive entity ids and the entity's own canonical name, resolved at read time and never the
organizer-supplied display name; a normalized address SHALL NOT appear in a prompt,
a log line or an MCP response, and unresolved attendees SHALL be reported as a count.

ID: REQ-module-calendar-043
Source: bu-5ii4gj.3 (run 26 move 3), slice S1
Scope: v1-mandatory

#### Scenario: A synced meeting resolves its attendees

- **WHEN** a Google event whose attendees are the owner, a known contact with one active `has-email` fact, an unknown address and a room resource is projected
- **THEN** four rows exist with resolutions `owner`, `exact` (carrying the contact's entity id), `unresolved` and `resource`

#### Scenario: A replay changes nothing

- **WHEN** the same payload is projected twice
- **THEN** the participant rows are identical

#### Scenario: An empty attendee list keeps manual links

- **WHEN** a provider update arrives with no attendees for an event that has provider rows and a manual row
- **THEN** the provider rows are deleted and the manual row is kept

#### Scenario: A quarantined invitation mints nothing

- **WHEN** an unanswered invitation from a `first_contact` organizer is projected
- **THEN** no provider-derived participant row exists for it, and a later accept by the owner derives them

### Requirement: Deterministic Attendee Resolution

Attendee addresses SHALL be resolved by a pure function over exact normalized (trimmed,
case-folded) addresses and the active, live `has-email` claimants returned for them. An address
with exactly one live claimant SHALL resolve to it; an address with two or more live claimants
SHALL be `ambiguous` and SHALL name no entity; an address with none SHALL be `unresolved`. The
resolver SHALL NOT use a model, a name, a domain, a prefix or any fuzzy comparison, and SHALL NOT
create an entity. The claimants SHALL come from the SECURITY DEFINER function
`public.attendee_identity_claims(text[])`, which SHALL accept at most 200 addresses, match them
exactly, return no names, exclude merged, deleted and unidentified entities, and be executable by
the calendar-hosting butler roles only. When that read fails, the event write SHALL still commit,
the event's prior participant rows and resolver version SHALL stay, a failure counter SHALL
increment, and readers SHALL be told the participants are unavailable rather than shown an empty
list.

ID: REQ-module-calendar-044
Source: bu-5ii4gj.3 (run 26 move 3), slice S1
Scope: v1-mandatory

#### Scenario: Two claimants never guess

- **WHEN** an attendee address is an active `has-email` of two live entities
- **THEN** the row's resolution is `ambiguous` and its entity id is null

#### Scenario: A failed read does not lose the event

- **WHEN** the claimant read raises while a provider event is projected
- **THEN** the event row is written, existing participant rows are unchanged, and the participants read as unavailable

#### Scenario: The read cannot enumerate

- **WHEN** the definer is called with more than 200 addresses or by a role that is not a calendar-hosting butler role
- **THEN** it refuses

### Requirement: Participants Follow Entity Rebind

Entity rebind SHALL repoint `calendar_event_participants` rows from the source entity to the
survivor in the same step that repoints `calendar_event_entities`, deleting a `manual` row that
would duplicate the survivor's on the same event.

ID: REQ-module-calendar-045
Source: bu-5ii4gj.3 (run 26 move 3), slice S1
Scope: v1-mandatory

#### Scenario: A merge repoints participants

- **WHEN** entity A is merged into entity B and an event has participant rows for A
- **THEN** those rows carry B's entity id afterwards

### Requirement: Participants Are Re-resolved When Claimants Change

A change to who holds an address (a `has-email` fact that appears, is retracted, or gains a second
holder) SHALL reach the participant rows of events whose attendee list did not change. A bounded
sweep run after each successful provider sync SHALL re-derive the provider rows of non-quarantined
provider events starting within 30 days before and 14 days after now that already have current
provider rows (an event with no attendees is skipped, so it cannot spend the batch), nearest to
now first, at most a fixed batch per run, skipping an event checked within the last hour. An unchanged event SHALL be left untouched, `manual` rows SHALL be kept, a failed
claimant read SHALL leave the prior rows in place (never an empty list), and a sweep failure SHALL
NOT fail the sync. Re-resolution SHALL NOT rewrite any interaction or encounter already written
from earlier rows.

ID: REQ-module-calendar-050
Source: bu-5ii4gj.3 (run 26 move 3), slice S4
Scope: v1-mandatory

#### Scenario: A late has-email resolves an unresolved attendee

- **WHEN** an event was synced with an address no entity held, then an entity gains that address as an active `has-email` fact, and the sweep runs
- **THEN** the participant row for that address has resolution `exact` and that entity's id

#### Scenario: A second holder makes the address ambiguous

- **WHEN** a second entity gains an address an event's participant row resolved `exact`, and the sweep runs
- **THEN** the row is `ambiguous` and carries no entity id

#### Scenario: A lost claim unresolves the address

- **WHEN** the only active `has-email` fact for an address is retracted and the sweep runs
- **THEN** the participant row for that address is `unresolved`

#### Scenario: The sweep is bounded and leaves the rest alone

- **WHEN** the sweep runs
- **THEN** it checks at most the batch size, skips events checked within the hour, ignores events outside the window and quarantined events, keeps `manual` rows, and writes nothing for an event whose rows are unchanged
