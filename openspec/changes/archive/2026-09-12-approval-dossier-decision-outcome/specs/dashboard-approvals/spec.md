## MODIFIED Requirements

### Requirement: Approval Detail API

The dashboard SHALL expose `GET /api/approvals/{id}` returning the full dossier
for one approval.

#### Scenario: Detail response shape

- **WHEN** `GET /api/approvals/{id}` is called
- **THEN** the response is `ApiResponse[ApprovalDetail]` with fields `id`,
  `title`, `butler`, `status`, `created_at` (alias `ts`), `expires_at` (alias
  `expires`), `why` (string | null — serif paragraph), `evidence` (string[] |
  null — mono lines), `proposed_action` (object describing the tool call being
  approved), `session_id` (string | null — the originating session/trace, when
  known), `decided_by` (string | null), `decided_at` (timestamp | null),
  `denial_reason` (string | null), and `execution_result` (object | null).
- **AND** when `why` or `evidence` is null (legacy row), the UI renders a
  serif-italic empty state for the missing section.
- **AND** when `session_id` is present, the dossier header links to
  `/sessions/{session_id}` so the owner can inspect the originating
  session/trace before deciding.

## ADDED Requirements

### Requirement: Decision and execution outcome in approval dossier

The dashboard SHALL render an approval's retained decision provenance and safe
terminal outcome from the approval-detail response. It SHALL not add a durable
copy of an audit-event rejection reason or broaden retry behavior.

#### Scenario: Rejection reason comes from the latest immutable event

- **WHEN** a rejected action has one or more immutable `action_rejected`
  `approval_events` with a recorded reason
- **THEN** the detail response includes the reason from the latest such event
  as `denial_reason`
- **AND** the dossier renders that recorded denial reason alongside its existing
  `decided_by` and `decided_at` provenance.

#### Scenario: Legacy or unavailable rejection event does not break detail

- **WHEN** an action has no readable `action_rejected` event, including a legacy
  row or a pool where the optional event lookup is unavailable
- **THEN** the detail response includes `denial_reason: null`
- **AND** the remaining dossier detail remains available without synthesizing a
  reason from presentation text.

#### Scenario: Execution outcome is redacted before presentation

- **WHEN** an action has a persisted `execution_result`
- **THEN** the detail response and dossier render only the result after the
  established approvals redaction contract is applied
- **AND** an execution-result `error` does not expose its raw message or any
  secret-derived text.

#### Scenario: Retry is offered only to an approved unexecuted action

- **WHEN** the dossier detail reports `status = "approved"` and
  `execution_result = null`
- **THEN** the dossier renders its Retry dispatch control.

#### Scenario: Retry is absent after an execution record or a non-approved decision

- **WHEN** the dossier detail has a non-null `execution_result` or a status
  other than `approved`
- **THEN** the dossier does not render Retry dispatch
- **AND** an executed failure is not made retryable by this dashboard surface.
