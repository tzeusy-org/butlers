## ADDED Requirements

### Requirement: Stored Owner Attention Policy Holds Are Not Re-gated
The scheduler SHALL treat a due owner-default policy hold as a durable delivery
decision. It SHALL dispatch the stored resolved envelope when its persisted UTC
`deliver_at` is due and SHALL NOT re-evaluate the Owner Attention Policy,
recalculate an anchor, or move the row because the policy has changed. This
requirement applies only to owner-default holds and does not alter per-butler
`delivery_preferences` or existing retry behavior.

#### Scenario: Policy changes after a durable hold
- **WHEN** a routine owner-default notification was stored with a
  policy-derived UTC `deliver_at`
- **AND** the Owner Attention Policy changes before that timestamp becomes due
- **THEN** the scheduler uses the stored envelope and stored `deliver_at`
- **AND** it does not invoke a fresh policy gate before dispatch
