## ADDED Requirements

### Requirement: Calendar Producer Provenance Candidate Selection

The calendar `meeting`/`focused` producer SHALL remain the deterministic
general-butler producer added by `context-bus-producers`, but it SHALL derive a
signal only from an active confirmed human meeting candidate. It SHALL exclude
an all-day event, a legacy locally-midnight-aligned event spanning at least 24
hours, and an event with explicit
`metadata.butler_generated=true`. The producer SHALL clear its own meeting and
focused signals when no eligible event is active.

`metadata.butler_generated` is the only generated-event exclusion authority;
source names, title prefixes, and inferred ownership SHALL NOT substitute for
it. A timed event without that explicit marker SHALL retain normal
meeting/focused behavior. Malformed metadata SHALL be treated as no explicit
generated assertion, and an invalid or missing timezone SHALL make only the
legacy-midnight inference unavailable; neither condition may raise or invent a
new context signal.

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
