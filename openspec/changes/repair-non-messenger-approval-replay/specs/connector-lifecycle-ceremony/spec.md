# Connector Lifecycle Ceremony — Replayable Rotation Delta

This delta supersedes the `rotate-token` gate-matrix entry while no durable,
authorized credential-reference command exists. It is intentionally narrower
than a future token-rotation implementation: that implementation must add its
own credential-reference and provider-operation contract before re-enabling
approval parking.

**Archive-order note:** the target capability currently lives in the active
`redesign-ingestion-dispatch-console` lineage and may not yet exist in
`openspec/specs/`. If this change archives first, its lifecycle delta cannot be
synced until that parent capability is canonical; retain this delta for the
parent change's archive/sync reconciliation rather than inventing a parallel
main spec.

## MODIFIED Requirements

### Requirement: Per-action lifecycle gate matrix

The system SHALL enforce the following gate matrix for connector lifecycle
actions invoked through the dashboard or its API.

| Action | Gate |
| --- | --- |
| `pause` | audit-log-only |
| `run-now` | audit-log-only (defined as "resume from pause") |
| `disconnect` | Approvals-gated |
| `rotate-token` | reject before parking until a safe replay command exists |
| `reauth` | governed by its dedicated OAuth scope-surface contract |

Audit-log-only actions SHALL emit an `audit.append()` entry. An action that is
rejected as unreplayable SHALL append a redacted error audit entry and SHALL
NOT create a pending action.

#### Scenario: Rotate-token has no durable replay reference

- **WHEN** an operator invokes `rotate-token` without an authorized credential
  reference and deterministic provider operation that can safely run later
- **THEN** the endpoint returns HTTP 409 before calling the approvals park path
- **AND** it appends an error audit entry identifying the unreplayable rotation
  without including credential material
- **AND** no `connector_rotate_token` action can reach owner approval
