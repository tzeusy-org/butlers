## MODIFIED Requirements

### Requirement: Safe retry refreshes confirmed approval state

The dashboard SHALL reuse the existing Retry dispatch action only when an
approval has `status = approved` and an explicitly null `execution_result`.
It SHALL NOT render Retry for an executed failure, any non-approved status, or
an unknown/missing execution result. The Retry control SHALL remain pending
without locally removing the row while its server request is in flight.
- After a successful server response, the dashboard SHALL invalidate all flat
approval query variants, the affected approval dossier, every isolated Stalled
direct-link verifier generation for that id, approval history, and approval
metrics so the count, lane, history, and dossier reconcile from
server-authoritative data. It SHALL not optimistically remove the row or
invalidate those views when the retry request fails.
- For a chat-linked action, the server-projected durable attempt fence MUST also explicitly permit retry. The approved/null-result condition below SHALL NOT alone enable Retry for an unresolved/unknown started attempt. Both dashboard Retry routes and the owning dispatcher MUST enforce the same restriction; the UI SHALL preserve the ambiguous receipt and expose inspection rather than a blind retry control.

ID: REQ-dashboard-approvals-005
Source: dashboard-approvals Safe retry refreshes confirmed approval state; chat-domain-actions REQ-chat-domain-actions-006
Scope: v1-mandatory

#### Scenario: Retry stays bounded to an approved action without a result

- **WHEN** a stalled row has `status = approved` and
  `execution_result = null`
- **AND** any chat-linked attempt has explicit server-verified retry permission
- **THEN** the dashboard renders the existing Retry dispatch control
- **AND** when the same row has a non-null or missing execution result, or a
  different status, the dashboard renders no Retry control.

#### Scenario: Confirmed retry reconciles every approval read

- **WHEN** the owner activates Retry dispatch and the server returns a
  successful response
- **THEN** the UI reports whether dispatch ran from that response
- **AND** it invalidates the waiting and stalled flat views, history, the
  affected dossier, any isolated Stalled direct-link verifier for that id, and
  metrics after that completion.

#### Scenario: Failed retry leaves the server-authoritative row visible

- **WHEN** the Retry dispatch request fails
- **THEN** the dashboard reports the returned error without claiming a
  dispatch cause
- **AND** it does not optimistically remove the row or invalidate the
  approval read caches.

#### Scenario: Chat-linked ambiguity removes Retry even with null result

- **WHEN** a chat-linked action is approved with a null execution_result but its attempt is unresolved or retry permission is unknown
- **THEN** neither chat nor the approval page renders an enabled Retry dispatch control
- **AND** a direct request to either Retry endpoint is refused by the same owning fence without invocation or loss of receipt/audit evidence.

## Source References

- Non-Negotiable Rules 3, 4 and 6; heart-and-soul/security.md Approval Gates.
- RFC 0023 (durable approval-delivery recovery), RFC 0030 (system-plane read exception).
- chat-domain-actions and this changeset design.md.
