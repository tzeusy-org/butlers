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

ID: REQ-context-bus-017
Source: RFC 0009 §Signal Producers (calendar → meeting/focused); bu-s11n0s.4 attendance outcome
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
