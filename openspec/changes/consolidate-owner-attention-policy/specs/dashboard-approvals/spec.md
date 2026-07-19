## MODIFIED Requirements

### Requirement: Approvals Policy (Quiet Hours)

The dashboard SHALL expose the stable `GET/PUT /api/approvals/policy` endpoint
to manage the global Owner Attention Policy. The policy controls routine
owner-attention suppression; it does not configure per-butler
`delivery_preferences`.

#### Scenario: Read policy

- **WHEN** `GET /api/approvals/policy` is called
- **THEN** the response is `ApiResponse[ApprovalsPolicy]` with
  `quiet_start_hour: int` (0–23), `quiet_end_hour: int` (0–23), and
  `timezone: str` (IANA)
- **AND** its semantics are an end-exclusive
  `[quiet_start_hour, quiet_end_hour)` Owner Attention Policy interval

#### Scenario: Update complete policy

- **WHEN** `PUT /api/approvals/policy` is called with both hour fields in
  0–23 and a recognized IANA timezone
- **THEN** the singleton row is updated and `audit.append("approvals.policy")`
  is invoked

#### Scenario: Reject incomplete or invalid policy

- **WHEN** `PUT /api/approvals/policy` supplies only one quiet hour or an
  unrecognized IANA timezone
- **THEN** validation rejects the request without mutating the singleton row

#### Scenario: Quiet hours defer a routine owner-default notification

- **WHEN** the notification dispatcher handles a routine implicit-owner `send`
  or `insight` call with priority other than `high`
- **AND** the current local time is within the end-exclusive policy interval
- **THEN** it parks the full envelope in the originating schema's
  `deferred_notifications` table for the exact configured quiet end
- **AND** it returns the established `deferred` result rather than silently
  dropping the page
- **AND** high-priority and explicit-target notifications retain their existing
  immediate behavior

#### Scenario: Approval-request pushes retain their dedicated behavior

- **WHEN** an approval gate emits an `approval_request` push during Owner
  Attention Policy quiet hours
- **THEN** its existing decision-loop deferral behavior uses the exact policy
  end while its pending-action expiry semantics remain unchanged
- **AND** it is not reclassified as a routine `send` or `insight` hold
