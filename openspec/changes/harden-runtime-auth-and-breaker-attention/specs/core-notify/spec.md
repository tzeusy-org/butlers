## ADDED Requirements

### Requirement: Confirmed Delivery Cannot Be Reclassified by Bookkeeping

The Switchboard notification route SHALL distinguish the external transport
result from post-send observability work. Once Messenger confirms an external
delivery, the route SHALL return a confirmed-delivery result containing its
safe receipt even if routing-log, registry, notification-log, audit, or
attention-ledger persistence later fails. Such later failures SHALL be caught
and recorded as safe telemetry failures; they SHALL not turn the send into a
retryable delivery failure.

ID: REQ-core-notify-027
Source: heart-and-soul/vision.md Rule 3; RFC 0003; RFC 0011 Amendment 1; design.md Decision 5
Scope: v1-mandatory

#### Scenario: Post-send routing-log ACL failure preserves confirmation

- **WHEN** Messenger returns a successful send receipt and a subsequent
  routing-log write is rejected by database permissions
- **THEN** the caller receives confirmed delivery with the safe receipt
- **AND** the error is logged as non-fatal telemetry associated with the
  delivery attempt
- **AND** the caller does not retry solely because that bookkeeping write
  failed

#### Scenario: Pre-send failure remains distinguishable from uncertain transport

- **WHEN** route construction or recipient resolution fails before external
  transport begins
- **THEN** the route returns a safe not-attempted failure classification
- **AND** it does not claim external delivery or synthesize a receipt
- **WHEN** transport may have begun but no confirmation is available
- **THEN** it returns an explicit uncertain classification rather than a
  generic retryable failure

#### Scenario: Delivery ownership remains Switchboard-mediated

- **WHEN** a non-Switchboard component needs an external notification
- **THEN** it uses the existing Switchboard/Messenger delivery boundary rather
  than querying Switchboard private tables or opening a direct Messenger path
- **AND** a post-send telemetry failure does not widen that component's
  database permissions
