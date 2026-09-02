## ADDED Requirements

### Requirement: Finance proactive recurrence output does not infer missing payment state

Finance proactive output MUST keep forward-looking declared renewals and predicted bills distinct
from expected-signal absence. No current alert policy consumes an elapsed recurrence signal as a
missed renewal, failed payment, cancellation, pause, or stopped subscription.

ID: REQ-finance-alerts-001
Source: RFC 0011; RFC 0029 §Initial adoption
Scope: v1-mandatory

#### Scenario: Existing tracked annual renewal reminder remains forward-looking

- **WHEN** an active yearly tracked subscription has a declared `next_renewal` within 14 days
- **THEN** the existing `subscription-renewal` candidate policy MAY run with its current priority,
  deduplication, expiry, and wording
- **AND** that reminder MUST NOT claim whether the future charge was or will be observed

#### Scenario: Existing predicted-bill policy remains forward-looking

- **WHEN** an untracked regular payment has a predicted date within the existing 30-day horizon
- **THEN** the existing `bill-predicted` candidate policy MAY run unchanged
- **AND** the prediction MUST NOT be converted into tracked subscription, payment, or cancellation
  state

#### Scenario: Unmeasurable elapsed recurrence produces no candidate

- **WHEN** `next_expected_date` has elapsed and its producer is stale, dead/offline, unhealthy,
  missing, unsupported, mixed, or unreadable
- **THEN** Finance MUST NOT propose any missed-recurrence or missed-renewal candidate
- **AND** Finance MUST NOT attribute the gap to owner behavior or merchant/payment state

#### Scenario: Healthy absent recurrence has no implicit alert consumer

- **WHEN** a healthy/current single producer yields an `absent` recurrence signal
- **THEN** Finance MUST persist or expose that state only through the RFC 0029 contract
- **AND** it MUST NOT emit a candidate until a separately approved alert requirement names that
  consumer, wording, priority, deduplication, cooldown, and expiry
