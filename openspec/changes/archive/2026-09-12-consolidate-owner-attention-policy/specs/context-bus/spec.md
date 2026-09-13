## ADDED Requirements

### Requirement: Sleep Context Uses the Owner Attention Policy Anchor
The deterministic health sleep producer SHALL derive both its quiet-window
membership and `sleeping` signal expiry from the shared Owner Attention Policy
predicate and exact-end UTC anchor. It SHALL not duplicate timezone conversion
or add an hour to the configured end. Missing, incomplete, invalid, or
unreadable policy data SHALL cause the producer to clear/avoid its owned sleep
signal rather than infer a replacement timezone or wake time.

#### Scenario: Sleep expires at the exact end-exclusive boundary
- **WHEN** the current local time falls inside the Owner Attention Policy
  `[quiet_start_hour, quiet_end_hour)` interval
- **THEN** the health producer sets `sleeping` with the shared exact
  `quiet_end_hour` UTC anchor as `expires_at`
- **AND** the signal is not active at the exact local end

#### Scenario: Invalid persisted zone fails open for sleep context
- **WHEN** the Owner Attention Policy contains an unrecognized persisted IANA
  timezone
- **THEN** the health producer does not publish a derived sleeping signal
- **AND** it does not silently substitute UTC or another timezone
