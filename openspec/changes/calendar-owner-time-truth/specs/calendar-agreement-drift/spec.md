## ADDED Requirements

### Requirement: The owner's accepted terms are recorded

The calendar module SHALL record, in `calendar_owner_agreements`, what the owner accepted when their
response becomes `accepted` or `tentative` for a provider event they did not organise: the provider
identity, the occurrence's original start, its start and end, a digest of the location, a digest of
the join details, a digest of the other attendees, and when it was observed. A row SHALL be unique
on (provider identity, original start). A later sync that changes nothing SHALL write nothing, and
the owner changing their answer to `declined` or `needsAction` SHALL end the agreement. No row SHALL
hold a location, a join link or an address, only digests.

ID: REQ-calendar-agreement-drift-001
Source: bu-5ii4gj.10 slice A-S1; design "Changed since you said yes"
Scope: v1-mandatory

#### Scenario: Accepting records the terms once

- **WHEN** the owner accepts an event and the event is synced again unchanged
- **THEN** exactly one agreement row SHALL exist for that occurrence

#### Scenario: Declining ends the agreement

- **WHEN** the owner declines an event they had accepted
- **THEN** no condition SHALL open for it and any open condition SHALL resolve with no card

### Requirement: A material change after acceptance opens one condition

A deterministic job SHALL compare each agreed occurrence with its current projection and the
connector's revision journal, and SHALL reconcile `public.owner_conditions` under the source
`calendar:changed_since_accepted`, identity `{provider identity, original start}`. A change is
material when the start moves by 15 minutes or more, the location digest changes, the join digest
changes, or every other attendee is removed; a change to the title or description alone SHALL NOT
be material. When the revision journal cannot be read, or the calendar source is stale, the job
SHALL reconcile nothing and SHALL record an incomplete tick, never a resolved condition. Copies of
one provider event across schemas SHALL be one condition.

ID: REQ-calendar-agreement-drift-002
Source: bu-5ii4gj.10 slice A-S2; design "Changed since you said yes"
Scope: v1-mandatory

#### Scenario: A moved event opens one condition

- **WHEN** the organizer moves an accepted event by 60 minutes
- **THEN** one condition SHALL open and one candidate SHALL be proposed carrying an `owner_condition` premise

#### Scenario: A title change is not material

- **WHEN** only the title of an accepted event changes
- **THEN** no condition SHALL open

#### Scenario: An unreadable journal reconciles nothing

- **WHEN** the revision journal read fails
- **THEN** the job SHALL record an incomplete tick, SHALL resolve no condition and SHALL open none

### Requirement: The change is told once and corrected in place

Each open condition SHALL yield one insight candidate whose premise is the condition, in the timed
lane when the occurrence starts within 24 hours and the digest lane otherwise; a dark timed lane
SHALL fall back to the digest lane and never call notify directly. A change that reverts before
delivery SHALL withdraw the candidate with reason `premise_false`; a revert after delivery SHALL
amend the delivered message in place, struck through, once. A replayed cycle SHALL change nothing a
second time. Title, location and attendee text SHALL reach the card only through the inert-text and
delivery-defang helpers, and description text SHALL never reach it.

ID: REQ-calendar-agreement-drift-003
Source: bu-5ii4gj.10 slice A-S4; design "Changed since you said yes"
Scope: v1-mandatory

#### Scenario: Reverted before the cycle

- **WHEN** the event moves back before the delivery cycle
- **THEN** the candidate SHALL be withdrawn with reason `premise_false`, notify SHALL NOT be called, and the attention ledger SHALL record `withdrawn`

#### Scenario: Reverted after delivery

- **WHEN** the event moves back after the card was delivered
- **THEN** one amendment SHALL exist and the message SHALL be edited with a strike-through, and a replay SHALL make no second edit
