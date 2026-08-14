# Connector Lifecycle Ceremony — Reauth Unblocking Delta

This delta supersedes the `reauth` gate matrix entry and the "Reauth is
blocked" scenario in `connector-lifecycle-ceremony` once the
`connector-oauth-scope-surface` capability is ratified. The HTTP 503 block
is replaced with a delegating handler that invokes the OAuth scope-surface
contract.

**Archive-order note:** the `connector-lifecycle-ceremony` capability lives
in the sibling change `redesign-ingestion-dispatch-console` and may not yet
exist in `openspec/specs/` when this change archives. If this change archives
FIRST, OpenSpec's archive step for this delta is a no-op (no target spec to
modify) — the modification logically applies when `connector-lifecycle-ceremony`
later archives, and the lifecycle-ceremony change's tasks.md should reference
this delta as an absorbed amendment. If `connector-lifecycle-ceremony`
archives FIRST, this delta applies cleanly on this change's archive.

## MODIFIED Requirements

### Requirement: Per-action lifecycle gate matrix

The system SHALL enforce the following gate matrix for connector lifecycle actions invoked via the dashboard or its API. The `reauth` row replaces the prior "BLOCKED with HTTP 503 until `connector-oauth-scope-surface` spec exists" entry. Its resolution is connector-specific: generic OAuth is Approvals-gated, Spotify remains a connector-owned Passport journey, and non-OAuth connectors reject the operation before Approvals. The detailed behavior lives in `connector-oauth-scope-surface/spec`.

| Action | Gate |
|--------|------|
| `pause` | audit-log-only |
| `run-now` | audit-log-only (defined as "resume from pause") |
| `disconnect` | Approvals-gated |
| `rotate-token` | reject before parking until a safe replay command exists |
| `reauth` | Generic OAuth: Approvals-gated; Spotify: connector-owned Passport journey; non-OAuth: reject before Approvals; delegates to `connector-oauth-scope-surface/spec` |

Audit-log-only actions SHALL still emit an `audit.append()` entry. Approvals-gated actions SHALL pass through the Approvals module at the MCP server level (not bypassable from the dashboard API). An action rejected as unreplayable SHALL append a redacted error audit entry and SHALL NOT create a pending action.

#### Scenario: Pause is audit-only

- **WHEN** an operator invokes the `pause` action on a connector
- **THEN** the handler executes immediately
- **AND** an audit entry is written with `action = 'connector.pause'`, actor, target connector identity, reason, and request_id
- **AND** no Approvals-module call is made

#### Scenario: Disconnect requires approval

- **WHEN** an operator invokes the `disconnect` action
- **THEN** the handler routes the request through the Approvals module before executing
- **AND** until approval resolves, the connector remains in its prior state

#### Scenario: Rotate-token has no durable replay reference

- **WHEN** an operator invokes `rotate-token` without an authorized credential reference and deterministic provider operation
- **THEN** the endpoint returns HTTP 409 before calling the approvals park path
- **AND** it appends a redacted error audit entry identifying the unreplayable rotation
- **AND** no `connector_rotate_token` action reaches owner approval

#### Scenario: Generic OAuth reauth is Approvals-gated

- **WHEN** an operator invokes the `reauth` action for a generic OAuth-bound
  connector other than Spotify
- **THEN** the handler SHALL route the request through the Approvals module before executing
- **AND** on approval, the handler SHALL invoke the OAuth reauth flow per
  `connector-oauth-scope-surface/spec`'s
  `§Reauth endpoint contract for generic OAuth connectors` requirement
- **AND** the handler SHALL NOT return HTTP 503 (the blocking-pending-spec condition is no longer met once `connector-oauth-scope-surface` is ratified)
- **AND** audit emissions SHALL follow `connector-oauth-scope-surface/spec`'s `§Audit trail` requirement for reauth

#### Scenario: Spotify reauth stays connector-owned

- **WHEN** the dashboard resolves the `reauth` action for Spotify
- **THEN** it SHALL navigate to the content-blind connector-owned Passport
  projection at `/secrets?focus=u:spotify`
- **AND** the projection's connect or reconnect action SHALL call
  `POST /api/connectors/spotify/oauth/start`
- **AND** the connector-owned callback SHALL be
  `GET /api/connectors/spotify/oauth/callback`
- **AND** it SHALL NOT return the generic `{auth_url, state, expires_in}` response
- **AND** it SHALL NOT create, validate, or consume a generic OAuth `state` token
- **AND** it SHALL NOT invoke a generic OAuth callback

#### Scenario: Non-OAuth reauth is rejected before approval

- **WHEN** an operator invokes the `reauth` action for a connector whose
  `auth_status = unsupported`
- **THEN** the handler SHALL return the structured unsupported response per
  `connector-oauth-scope-surface/spec`'s
  `§Reauth endpoint contract for non-OAuth connectors` requirement
- **AND** the response SHALL be produced before approval submission
- **AND** the request SHALL NOT pass through the Approvals module

## Source References

- Parent capability — `connector-oauth-scope-surface/spec` (this change)
- Approvals module dependency — `openspec/specs/module-approvals/spec.md`
- Connector base spec — `openspec/specs/connector-base-spec/spec.md`
- Audit log infrastructure — `openspec/specs/dashboard-api/spec.md:530-541`
- Non-Negotiable Rule 1 (user-federated) — `about/heart-and-soul/vision.md:60-63`
- Security model — credential masking — `about/heart-and-soul/security.md:96-147`
