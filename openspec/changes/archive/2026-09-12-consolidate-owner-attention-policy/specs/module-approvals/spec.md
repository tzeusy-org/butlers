## ADDED Requirements

### Requirement: Approval-Request Pushes Share the Owner Attention Policy Anchor
The approvals module SHALL use the shared global Owner Attention Policy's
end-exclusive interval and exact-end UTC anchor when it defers an
owner-targeted `approval_request` push for quiet hours. The pending action's
existing expiry, status transitions, and execution rules
SHALL remain independent of the deferred push.

#### Scenario: Approval push at the final quiet hour
- **WHEN** an approval request is parked one hour before the configured local
  `quiet_end_hour`
- **THEN** its deferred push is scheduled for that exact local end converted to
  UTC
- **AND** the pending action does not gain an hour of expiry from push timing
