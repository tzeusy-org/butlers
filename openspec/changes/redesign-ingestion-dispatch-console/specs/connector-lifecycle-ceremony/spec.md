# Connector Lifecycle Ceremony

## Purpose
Defines the per-action gate matrix, credential-masking contract, and soft-delete semantics for connector lifecycle actions exposed through the dashboard. Lifecycle actions vary in blast radius: pausing a connector is reversible and observable; rotating a credential is destructive to the previous credential and can cut off ingestion if mis-handled. This capability owns the action gates; it depends on the existing `module-approvals` capability for approval enforcement and on `connector-base-spec` for the underlying connector primitives. The `reauth` action additionally depends on a future `connector-oauth-scope-surface` capability and is blocked until that spec exists.

## ADDED Requirements

### Requirement: Per-action lifecycle gate matrix
The system SHALL enforce the following gate matrix for connector lifecycle actions invoked via the dashboard or its API:

| Action | Gate |
|--------|------|
| `pause` | audit-log-only |
| `run-now` | audit-log-only (defined as "resume from pause" — see Run-now semantics below) |
| `disconnect` | Approvals-gated |
| `rotate-token` | Approvals-gated; `is_sensitive=True` masking mandatory |
| `reauth` | Approvals-gated; BLOCKED with HTTP 503 until `connector-oauth-scope-surface` spec exists |

Audit-log-only actions SHALL still emit an `audit.append()` entry. Approvals-gated actions SHALL pass through the Approvals module at the MCP server level (not bypassable from the dashboard API).

#### Scenario: Pause is audit-only
- **WHEN** an operator invokes the `pause` action on a connector
- **THEN** the handler executes immediately
- **AND** an audit entry is written with `action = 'connector.pause'`, actor, target connector identity, reason, and request_id
- **AND** no Approvals-module call is made

#### Scenario: Disconnect requires approval
- **WHEN** an operator invokes the `disconnect` action
- **THEN** the handler routes the request through the Approvals module before executing
- **AND** until approval resolves, the connector remains in its prior state

#### Scenario: Rotate-token requires approval
- **WHEN** an operator invokes the `rotate-token` action
- **THEN** the handler routes the request through the Approvals module before executing

#### Scenario: Reauth is blocked
- **WHEN** an operator invokes the `reauth` action and `connector-oauth-scope-surface/spec` is not yet ratified
- **THEN** the handler returns HTTP 503 with a body identifying the blocking spec dependency
- **AND** no Approvals-module call is made (the request is rejected before approval entry)
- **AND** the response SHALL NOT include a `Retry-After` header (no time-based recovery is meaningful)

### Requirement: Run-now semantics
The `run-now` action SHALL be defined as "resume from pause" — it SHALL only be invokable on a connector currently in the `paused` state and its effect SHALL be to clear the pause and restart the connector's next poll cycle. The dashboard SHALL NOT expose `run-now` as a general "trigger an immediate poll outside the schedule" command.

#### Scenario: Run-now resumes paused connector
- **WHEN** an operator invokes `run-now` on a connector whose state is `paused`
- **THEN** the pause is cleared
- **AND** the connector enters its next poll cycle immediately
- **AND** an audit entry is written with `action = 'connector.run_now'`

#### Scenario: Run-now on non-paused connector rejected
- **WHEN** an operator invokes `run-now` on a connector that is not currently paused
- **THEN** the handler returns HTTP 409
- **AND** the response body identifies the connector's actual state

### Requirement: Credential masking on rotate-token
The `rotate-token` action handler SHALL NOT return any credential, secret, OAuth refresh token, or other sensitive value in its response body. The handler's success response SHALL contain only an acknowledgement and the timestamp of the new credential's installation. The handler's parameters and any logged values SHALL be marked `is_sensitive=True` so the framework's logging and tracing layers redact the values automatically.

#### Scenario: Successful rotation response shape
- **WHEN** the `rotate-token` handler completes successfully
- **THEN** the response body contains `{"status": "ok", "rotated_at": "<iso-8601-timestamp>"}` and no other fields
- **AND** no credential value appears anywhere in the response

#### Scenario: is_sensitive masking applied
- **WHEN** the `rotate-token` handler is invoked
- **THEN** any parameter carrying the new credential is declared `is_sensitive=True`
- **AND** the framework's logging layer redacts the value in all log records and traces

#### Scenario: Failed rotation also masks values
- **WHEN** the `rotate-token` handler raises an error
- **THEN** the error message and any structured error body SHALL NOT contain the credential value
- **AND** the error reason is recorded in the audit log without the credential

### Requirement: Connector soft-delete semantics
The `disconnect` action SHALL be soft-delete only: it SHALL set `connector_registry.deleted_at` to the current timestamp and SHALL leave the row in place. The dashboard and APIs SHALL exclude rows with `deleted_at IS NOT NULL` from default-active queries but SHALL retain them for audit, lineage, and undo purposes. No hard DELETE on `connector_registry` rows SHALL be exposed through any dashboard surface.

#### Scenario: Disconnect sets deleted_at
- **WHEN** a `disconnect` action is approved and executes
- **THEN** `connector_registry.deleted_at` is set to NOW() for the target connector
- **AND** the row is NOT removed from the table

#### Scenario: Default queries exclude deleted connectors
- **WHEN** the connector roster list endpoint queries `connector_registry`
- **THEN** rows with `deleted_at IS NOT NULL` are excluded by default

#### Scenario: Lineage queries include soft-deleted connectors
- **WHEN** a lineage or audit-trail query references a connector by id (e.g. to resolve a historical filtered event's origin)
- **THEN** the soft-deleted row is still resolvable

### Requirement: Audit emission for all lifecycle actions
Every lifecycle action (pause, run-now, disconnect, rotate-token, reauth) SHALL emit an `audit.append()` entry to `public.audit_log` with `actor`, `action`, `target` (connector identity), `reason` (operator-supplied free text), and `request_id`. Approvals-gated actions SHALL emit one entry on submission (with the approval id) and a second entry on resolution (approved or denied). Audit entries SHALL be retained indefinitely.

#### Scenario: Pause emits single audit entry
- **WHEN** `pause` executes
- **THEN** one audit entry is written with `action = 'connector.pause'`

#### Scenario: Disconnect emits submission and resolution entries
- **WHEN** `disconnect` is submitted to the Approvals module
- **THEN** an audit entry is written with `action = 'connector.disconnect.submit'` and the approval id
- **WHEN** the approval resolves (approved or denied)
- **THEN** a second audit entry is written with `action = 'connector.disconnect.approved'` or `action = 'connector.disconnect.denied'`

### Requirement: No credentials in lifecycle API responses
No lifecycle action handler SHALL return credential, token, secret, or OAuth refresh values in its response body. This SHALL apply across all gates (pause, run-now, disconnect, rotate-token, reauth). Response bodies SHALL be limited to status, timestamp, approval id (where applicable), and human-readable acknowledgement strings.

#### Scenario: No credential in any lifecycle response
- **WHEN** any lifecycle action handler returns
- **THEN** the response body contains no field whose value is a credential, token, secret, or OAuth refresh token
- **AND** no `connector_registry` columns marked sensitive are projected into the response
