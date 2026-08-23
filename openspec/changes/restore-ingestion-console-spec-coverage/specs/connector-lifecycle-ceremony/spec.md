# Connector Lifecycle Ceremony

## Purpose

Defines the per-action gate matrix, credential-handling contract, and
soft-delete semantics for the connector lifecycle actions exposed by the
dashboard API under `/api/ingestion/connectors`. Lifecycle actions differ in
blast radius: pausing is reversible and observable; disconnecting removes a
connector from every active surface; rotating a credential is destructive to
the previous credential. This capability owns the action gates. It depends on
`module-approvals` for approval enforcement and on `connector-base-spec` for
the underlying connector primitives. The `reauth` action depends on the
not-yet-ratified `connector-oauth-scope-surface` capability and is blocked
until that spec exists — `add-connector-oauth-scope-surface` cites this
capability as its normative source for that gate.

## ADDED Requirements

### Requirement: Per-action lifecycle gate matrix

The system SHALL enforce the following gate matrix for connector lifecycle
actions invoked through `POST /api/ingestion/connectors/{connector_type}/{endpoint_identity}/{action}`:

| Action | Gate | Success status |
|--------|------|----------------|
| `pause` | audit-log-only | 200 |
| `run-now` | audit-log-only (defined as "resume from pause" — see Run-now semantics) | 200 |
| `archive` | audit-log-only | 200 |
| `unarchive` | audit-log-only | 200 |
| `disconnect` | Approvals-gated; parked as a pending action, never executed inline | 202 |
| `rotate-token` | Rejected with HTTP 409 — no safe replayable command exists | never succeeds |
| `reauth` | BLOCKED with HTTP 503 until `connector-oauth-scope-surface` is ratified | never succeeds |

Audit-log-only actions SHALL execute immediately and still emit an
`audit.append()` entry. The dashboard API SHALL NOT decide or enforce approval
itself: for `disconnect` it SHALL park a pending action carrying the
replayable command contract, and approval enforcement and execution SHALL
happen at the MCP tool layer on Switchboard, which is not bypassable from the
dashboard API. Every lifecycle action SHALL first verify the target connector
exists and is not soft-deleted, returning HTTP 404 when it does not and HTTP
503 when the connector registry pool is unavailable.

#### Scenario: Pause is audit-only

- **WHEN** an operator invokes `pause` on an existing connector
- **THEN** the handler updates the registry row immediately and returns HTTP
  200 with `connector_type`, `endpoint_identity`, and `state`
- **AND** an audit entry is written with `action = 'connector.pause'`
- **AND** no pending approval action is created

#### Scenario: Disconnect is parked for approval, not executed

- **WHEN** an operator invokes `disconnect`
- **THEN** the handler parks a pending action for the `connector_disconnect`
  command with a 72-hour expiry and returns HTTP 202 with
  `status = 'pending_approval'` and the `action_id`
- **AND** the connector row is left untouched until the approval resolves
- **AND** HTTP 503 is returned when the approvals subsystem is unavailable

#### Scenario: Approval enforcement lives at the MCP layer

- **WHEN** a parked `disconnect` approval is granted
- **THEN** the approved action is executed by invoking the Switchboard MCP
  tool named by the command contract
- **AND** the command contract's declared argument set is validated against the
  registered tool signature at butler startup, so a drifted contract fails
  fast rather than producing an unexecutable approval

#### Scenario: Rotate-token is refused

- **WHEN** an operator invokes `rotate-token` on an existing connector
- **THEN** the handler returns HTTP 409 with a reason stating that token
  rotation cannot be queued because no safe replayable command is available
- **AND** no pending approval action is created

#### Scenario: Reauth is blocked

- **WHEN** an operator invokes `reauth`
- **THEN** the handler returns HTTP 503 with a body naming
  `connector-oauth-scope-surface` as the blocking spec dependency
- **AND** no database read or write and no approval entry occurs
- **AND** the response SHALL NOT include a `Retry-After` header (no time-based
  recovery is meaningful)

#### Scenario: Unknown connector is rejected before any effect

- **WHEN** any lifecycle action names a connector that does not exist or whose
  `deleted_at` is set
- **THEN** the handler returns HTTP 404 and performs no state change

### Requirement: Run-now semantics

The `run-now` action SHALL be defined as "resume from pause". It SHALL be
invokable only on a connector whose `state` is `paused`, and its sole effect
SHALL be to clear that paused state; the connector self-reports its real state
on its next heartbeat, so the row is set to `unknown` rather than to a
remembered prior state. Nothing is pushed to the connector process. The
dashboard SHALL NOT expose `run-now` as a general "poll immediately outside
the schedule" command. The state read SHALL be taken under a row lock so a
concurrent `pause` or `run-now` cannot interleave.

#### Scenario: Run-now resumes a paused connector

- **WHEN** an operator invokes `run-now` on a connector whose state is `paused`
- **THEN** the paused state is cleared and the row's state becomes `unknown`
- **AND** the handler returns HTTP 200
- **AND** an audit entry is written with `action = 'connector.run_now'`

#### Scenario: Run-now on a non-paused connector is rejected

- **WHEN** an operator invokes `run-now` on a connector that is not paused
- **THEN** the handler returns HTTP 409
- **AND** the response body names the connector's actual current state

#### Scenario: Concurrent lifecycle writes are serialized

- **WHEN** two lifecycle writes target the same connector concurrently
- **THEN** the state read is taken with a row-level lock so the second write
  observes the first one's result

### Requirement: Credential masking on rotate-token

The `rotate-token` handler SHALL NOT accept, parse, read, log, or return any
credential value. Its safety property SHALL be structural — the endpoint
declares no request-body model and never reads the request body — rather than
dependent on a redaction pass. Any body a caller submits SHALL be discarded
unread. The refusal SHALL be recorded on the audit trail with the rejection
reason and no credential material.

#### Scenario: Submitted credential is never read

- **WHEN** a caller posts a body containing a token to `rotate-token`
- **THEN** the handler does not deserialize or inspect the body
- **AND** no field of that body appears in the response, the audit note, or any
  log record

#### Scenario: Refusal is audited without credential material

- **WHEN** `rotate-token` refuses a request for an existing connector
- **THEN** an audit entry is written with
  `action = 'connector.rotate_token.unreplayable'`, an error result, and a
  fixed reason string derived only from the connector identity
- **AND** HTTP 503 is returned instead of 409 if that audit write itself fails,
  so a refusal is never silently unrecorded

#### Scenario: Response carries only the rejection reason

- **WHEN** `rotate-token` returns
- **THEN** the response is HTTP 409 whose detail is the rejection reason string
- **AND** the response contains no credential, token, or secret value

### Requirement: Connector soft-delete semantics

The `disconnect` action SHALL be soft-delete only. On approval, execution SHALL
set `connector_registry.deleted_at` to the current timestamp and leave the row
in place. Dashboard reads SHALL exclude rows with `deleted_at IS NOT NULL` from
every default-active query while retaining them for audit and lineage. No hard
`DELETE` on a `connector_registry` row SHALL be exposed through any dashboard
surface. Soft-delete is distinct from archival: `archived_at` marks a connector
as retired from fleet-health rollups while it remains listed in the roster,
whereas `deleted_at` removes it from the roster entirely.

#### Scenario: Approved disconnect sets deleted_at

- **WHEN** an approved `disconnect` executes
- **THEN** `connector_registry.deleted_at` is set to `now()` for the target row
  where it was previously NULL
- **AND** the row is not removed from the table

#### Scenario: Re-disconnecting is idempotent

- **WHEN** an approved `disconnect` executes against a row whose `deleted_at`
  is already set
- **THEN** the execution reports an already-disconnected outcome rather than
  failing or re-stamping the timestamp

#### Scenario: Default queries exclude soft-deleted connectors

- **WHEN** the connector roster, cross-summary, per-connector events,
  incidents, or routing-rules endpoints query the registry
- **THEN** the query restricts to `deleted_at IS NULL`

#### Scenario: Archived is not deleted

- **WHEN** a connector is archived rather than disconnected
- **THEN** it remains visible in the roster listing
- **AND** it is excluded from fleet-health rollups

### Requirement: Audit emission for all lifecycle actions

Every lifecycle action SHALL emit an `audit.append()` entry to
`public.audit_log` carrying the actor, the action string, the target connector
identity as `{connector_type}/{endpoint_identity}`, and the originating client
address. Audit entries SHALL be retained indefinitely. For state-changing
audit-only actions the audit write SHALL happen after the state transaction
commits and SHALL be best-effort: a failure to append SHALL be logged and
SHALL NOT roll back the state change. The action strings SHALL be:

- `connector.pause`
- `connector.run_now`
- `connector.archive`
- `connector.unarchive`
- `connector.disconnect` (on submission, carrying the parked `action_id`)
- `connector.rotate_token.unreplayable` (on refusal)

#### Scenario: Pause emits a single audit entry

- **WHEN** `pause` executes
- **THEN** exactly one audit entry is written with `action = 'connector.pause'`

#### Scenario: Disconnect submission is audited with the approval id

- **WHEN** `disconnect` parks a pending action
- **THEN** an audit entry is written with `action = 'connector.disconnect'`
  and a note identifying the parked `action_id`

#### Scenario: Audit failure does not roll back state

- **WHEN** the audit append fails after a `pause` transaction has committed
- **THEN** the connector remains paused and the response is still HTTP 200
- **AND** the audit failure is logged

#### Scenario: Reauth emits no audit entry

- **WHEN** `reauth` is invoked
- **THEN** the request is refused before any audit or database call, so no
  audit entry is written

### Requirement: No credentials in lifecycle API responses

No lifecycle action handler SHALL return a credential, token, secret, or OAuth
refresh value in its response body, across every gate. Response bodies SHALL be
limited to the connector identity, a state or status string, a timestamp, the
parked approval id where applicable, and a human-readable message. No registry
column carrying sensitive material SHALL be projected into a lifecycle
response.

#### Scenario: No credential in any lifecycle response

- **WHEN** any lifecycle action handler returns, whether success, 4xx, or 5xx
- **THEN** the response body contains no field whose value is a credential,
  token, secret, or OAuth refresh token

#### Scenario: Approval arguments are declared sensitive

- **WHEN** a parked `disconnect` approval is presented to the owner
- **THEN** its arguments are rendered through the approvals sensitivity
  declaration for that tool rather than echoed raw

## Source References

- Non-Negotiable Rule 1 (user-federated sovereignty — the owner's credentials
  never leave the credential store through a lifecycle response)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication — approval
  execution happens through the Switchboard MCP tool, not the dashboard API)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0007 (Dashboard and API surface)
- RFC 0021 (Decision loop, one-tap approvals and decision memory)
