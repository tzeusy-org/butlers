# Dashboard Approvals — Delta

## MODIFIED Requirements

### Requirement: Approvals Policy (Quiet Hours)

The dashboard SHALL expose `GET/PUT /api/approvals/policy` to manage the
owner-default notification quiet-hours policy.

#### Scenario: Read policy

- **WHEN** `GET /api/approvals/policy` is called
- **THEN** it returns `ApiResponse[ApprovalsPolicy]` with `quiet_start_hour`,
  `quiet_end_hour`, and IANA `timezone`

#### Scenario: Update policy

- **WHEN** `PUT /api/approvals/policy` is called with that shape
- **THEN** the singleton row is updated and `audit.append("approvals.policy")`
  is invoked

#### Scenario: Quiet hours defer a routine owner-default notification

- **WHEN** the notification dispatcher handles a routine implicit-owner
  `send` or `insight` call with priority other than `high`
- **AND** the current local hour is within the inclusive policy window
- **THEN** it parks the full envelope in the originating schema's
  `deferred_notifications` table for the first whole hour after quiet hours
- **AND** it returns the established `deferred` result rather than silently
  dropping the page
- **AND** high-priority and explicit-target notifications retain their existing
  immediate behavior

#### Scenario: Approval-request pushes retain their dedicated behavior

- **WHEN** an approval gate emits an `approval_request` push during quiet hours
- **THEN** its existing decision-loop deferral behavior and pending-action
  expiry semantics remain unchanged
- **AND** it is not reclassified as a routine `send` or `insight` hold
