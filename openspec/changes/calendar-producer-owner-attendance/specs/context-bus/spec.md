## MODIFIED Requirements

### Requirement: Calendar Producer Provenance Candidate Selection

The calendar `meeting`/`focused` producer SHALL remain the deterministic
general-butler producer added by `context-bus-producers`, but it SHALL derive a
signal only from an active confirmed human meeting candidate. It SHALL exclude
an all-day event, a legacy locally-midnight-aligned event spanning at least 24
hours, and an event with explicit
`metadata.butler_generated=true`. The producer SHALL clear its own meeting and
focused signals when no eligible event is active.

A meeting candidate SHALL also be time the owner will attend as busy: the
producer SHALL exclude an event whose own `self` attendee declined it and an
event marked free (`metadata.transparency = "transparent"`), applying the same
attendance rule the conflict radar and day load use. When an excluded event
overlaps an eligible event, the eligible event SHALL supply the signal and its
expiry.

`metadata.butler_generated` is the only generated-event exclusion authority;
source names, title prefixes, and inferred ownership SHALL NOT substitute for
it. A timed event without that explicit marker SHALL retain normal
meeting/focused behavior. Malformed metadata SHALL be treated as no explicit
generated assertion, and an invalid or missing timezone SHALL make only the
legacy-midnight inference unavailable; neither condition may raise or invent a
new context signal.

Only the owner's own decline excludes an event for attendance: a guest's
decline, an owner `tentative` or `needsAction` answer, and absent or malformed
attendee metadata SHALL keep the event eligible. Both the projected
`response_status` and the raw provider `responseStatus` spellings SHALL count.

Typed Google status events are calendar context, not meetings (bu-u33sb7):
an `outOfOffice` or `workingLocation` event SHALL never be a meeting or focus
candidate. An active confirmed out-of-office event, timed or all-day and not
butler-generated, SHALL suppress `focused` and SHALL NOT itself assert
`meeting` or `focused`. An attended meeting overlapping it SHALL keep
`meeting`, since the owner's own meeting is the stronger signal; with no such
meeting the producer clears both. It SHALL NOT write the legacy `away` signal
(which maps to `owner.place`) and SHALL NOT enter an owner mode; mode entry
from an out-of-office block is reserved to an owner-confirmed proposal. A
typed `focusTime` event SHALL assert `focused` regardless of its title.

A working-location declaration is persisted (core_341) and readable through a
typed read. The producer SHALL NOT publish it as a context signal. Publishing it
as a signal is deferred to a perception kind (`owner.work_location`) at the
M7/.21 switch. The typed read (`current_working_location`) SHALL write nothing.
It SHALL select among active, confirmed, not butler-generated, not self-declined
working-location events that carry a normalized location. It SHALL prefer timed
over all-day, then the latest start, then the shortest duration, then the
smallest event id. It SHALL bound the answer to the owner-local day: the earlier
of the event end and the next local midnight in the owner's zone, using real
zone boundaries across daylight-saving changes.

A failed source read is not absence: the producer SHALL raise before any set
or clear, leaving earlier signals to their own expiry. Running the whole
transition in one transaction under a scoped lock is superseded on this
retiring bus. It belongs to the successor perception writer (CAS with a
per-slot serializer) at the RFC 0038 switch.

ID: REQ-context-bus-017
Source: RFC 0009 §Signal Producers (calendar → meeting/focused); bu-s11n0s.4 attendance and event-type outcomes; bu-u33sb7
Scope: v1-mandatory

#### Scenario: Butler-generated event does not assert context

- **WHEN** the active projected event has `metadata.butler_generated=true`
- **THEN** the calendar producer does not set `meeting` or `focused` from it
- **AND** it clears any prior signal it owns when no other eligible event is
  active

#### Scenario: Equivalent human timed event remains a meeting candidate

- **WHEN** an active confirmed timed event has no explicit
  `metadata.butler_generated=true` marker
- **THEN** the producer continues to publish `meeting` or `focused` according
  to its title classifier and uses the event end as its expiry

#### Scenario: Legacy midnight event is not a meeting

- **WHEN** an active legacy event has `all_day=false`, lasts at least 24 hours,
  and starts and ends at local midnight in its valid IANA timezone
- **THEN** the producer treats it as a non-meeting and does not assert context

#### Scenario: Malformed provenance degrades without an invented exclusion

- **WHEN** an otherwise valid timed event has malformed metadata or an invalid
  timezone
- **THEN** the producer does not raise
- **AND** malformed metadata alone does not exclude the event as generated
- **AND** an invalid timezone alone does not make the event a legacy all-day
  event

#### Scenario: Owner-declined or free event does not assert context

- **WHEN** the only active confirmed timed event has a `self` attendee who
  declined it, or is marked `transparency = "transparent"`
- **THEN** the producer does not set `meeting` or `focused` from it
- **AND** it clears any prior signal it owns

#### Scenario: Attended event wins over an excluded overlap

- **WHEN** a later-starting owner-declined or free event overlaps an active
  attended opaque event
- **THEN** the producer publishes the signal from the attended event and uses
  the attended event's end as its expiry
- **AND** once the attended event ends, the still-active excluded event does
  not keep `meeting` or `focused` asserted

#### Scenario: Only the owner's own decline excludes

- **WHEN** an active event's guest declined, or the owner's answer is
  `tentative` or `needsAction`, or its attendee metadata is absent or malformed
- **THEN** the event remains an eligible meeting candidate

#### Scenario: Out-of-office asserts nothing and suppresses focus

- **WHEN** an active confirmed out-of-office event overlaps only a focus block,
  a declined meeting, or nothing
- **THEN** the producer clears `meeting` and `focused`
- **AND** it writes no `away` signal and enters no owner mode

#### Scenario: An attended meeting keeps meeting during out-of-office

- **WHEN** an active confirmed out-of-office event overlaps an attended meeting
- **THEN** the producer publishes `meeting` from that meeting, with its end as
  the expiry, and clears `focused`

#### Scenario: Status events are never meeting candidates

- **WHEN** the only active event is a working-location event
- **THEN** the producer does not set `meeting` or `focused` from it

#### Scenario: Working location is a typed read, not a signal

- **WHEN** an all-day home-office declaration and a timed custom-location
  declaration are both active
- **THEN** the typed read returns the timed declaration's type and label, bounded
  by its own end
- **AND** a multi-day declaration is bounded by the next owner-local midnight
- **AND** no context signal is written for either

#### Scenario: An unreadable calendar changes nothing

- **WHEN** the calendar source read fails while `meeting` is active
- **THEN** the producer run fails without setting or clearing any signal
- **AND** the `meeting` row reads back unchanged

#### Scenario: Out-of-office alone holds no insight under meeting

- **WHEN** the only active calendar event is out of office
- **THEN** the producer writes no `meeting`, and the insight broker's
  suppression read finds no calendar hold

#### Scenario: Typed focus time asserts focused

- **WHEN** the active eligible event has `event_type = "focusTime"`
- **THEN** the producer publishes `focused` whatever its title says
