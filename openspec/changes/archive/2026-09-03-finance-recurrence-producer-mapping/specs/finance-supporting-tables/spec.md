## ADDED Requirements

### Requirement: Recurring groups require complete producer provenance for absence

A Finance recurring group MUST derive expected-signal authority from the complete set of active
transactions that contributed to its `last_seen_date`, interval, and `next_expected_date`. Exactly
one recognized server-attested producer MAY make the group measurable; missing, unsupported,
copied, mixed, or unreadable provenance MUST make it `unmeasurable`.

ID: REQ-finance-supporting-tables-001
Source: RFC 0012 §Expected-signal producer provenance; RFC 0029 §Initial adoption
Scope: v1-mandatory

#### Scenario: Gmail-only attested group maps to Gmail

- **WHEN** every contributing transaction in a recurring group carries valid server-attested Gmail
  ingress provenance for the same exact source endpoint
- **THEN** the group's sole expected-signal producer MUST be `connector:gmail`
- **AND** its `producer_endpoint_identity` MUST equal that server-derived source endpoint

#### Scenario: Owner-only attested group maps to owner

- **WHEN** every contributing transaction in a recurring group carries valid server-attested owner
  provenance
- **THEN** the group's sole expected-signal producer MUST be `owner`
- **AND** absence MUST describe only a missing owner-recorded observation, never merchant or payment
  behavior

#### Scenario: Mixed endpoint or unprovable group fails closed

- **WHEN** a recurring group includes more than one producer or endpoint, any un-attested
  transaction, an unsupported source, or unreadable provenance
- **THEN** the group MUST be `unmeasurable`
- **AND** row order, majority source, newest transaction, merchant, account freshness, or another
  healthy Finance endpoint MUST NOT choose a producer

#### Scenario: Derived dates carry no source authority

- **WHEN** `last_seen_date` and `next_expected_date` are derived from transaction intervals
- **THEN** those dates MUST remain recurrence-model outputs rather than producer evidence
- **AND** elapsing `next_expected_date` MUST NOT imply paid, unpaid, failed, cancelled, paused, or
  stopped status

#### Scenario: Current recurring groups remain unmeasurable

- **WHEN** a current recurring-group row has derived dates but no complete server-attested
  contributing producer set
- **THEN** the group MUST remain `unmeasurable`
- **AND** the system MUST NOT backfill its producer from current transaction source labels or
  account metadata

#### Scenario: Subscription property facts are outside current recurrence inputs

- **WHEN** the registered `track_subscription_fact` tool writes a Finance subscription property
  fact
- **THEN** current subscription audit and renewal evaluation MUST continue to use the dedicated
  subscription table rather than that property fact
- **AND** any future recurrence consumer of the property fact MUST classify it as `unmeasurable`
  unless reserved server producer and endpoint attestation is present
