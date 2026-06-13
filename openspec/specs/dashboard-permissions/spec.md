# dashboard-permissions

## Purpose

This capability provides the `/settings/permissions` surface of the Dispatch-language operator control plane: the full Permissions × Butlers matrix, a last-15 audit reel, data operations (encrypted-zip export and irreversible wipe under strict guards), and a webhook registry with a test action. It answers "what can this system do, to whom, and on whose authority?" while enforcing the doctrine rule that no privileged mutation occurs without a recorded reason. Every mutation in this capability is audited via the shared `audit.append()` primitive.

## Requirements

### Requirement: Permissions Page
The dashboard SHALL have a page at `/settings/permissions` rendered in the Dispatch design language containing the full permissions matrix, an audit reel, data operations (export, wipe), and a webhook registry.

#### Scenario: Permissions page layout
- **WHEN** a user navigates to `/settings/permissions`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Permissions & data", mono eyebrow "system · permissions".
  - **Matrix section**: Permissions × Butlers grid. Rows are permissions (`memory.read`, `memory.write`, `sessions.spawn`, `butlers.logs`, `metrics.read`, `audit.write`, `tools.invoke`, etc.). Columns are active butlers. Cells render as `on`/`off`/`inherited`; inherited cells render dim, explicit cells render foreground.
  - **Audit reel**: last 15 entries from `GET /api/audit-log?limit=15`. Mono timestamps, sans actor, serif description. Link "Full audit log →" navigates to `/audit-log` (a future top-level page, out of scope for this change; this Permissions page only shows the reel).
  - **Data ops sub-grid**: export (scope picker → signed URL), wipe (phrase input).
  - **Webhooks table**: list with add/edit/test/delete actions.

#### Scenario: Matrix cell flip requires reason
- **WHEN** a user flips a matrix cell from off to on or on to off
- **THEN** a modal prompts for a `reason` text field
- **AND** the modal's submit button is disabled while `reason.trim()` is empty
- **AND** on submit, `PUT /api/permissions/{butler}/{perm}` is called with `{granted, reason}`.

### Requirement: Permissions Matrix API
The dashboard SHALL expose CRUD over the permissions matrix.

#### Scenario: Read full matrix
- **WHEN** `GET /api/permissions` is called
- **THEN** the response is `ApiResponse[PermissionsMatrix]` containing `butlers: string[]`, `permissions: string[]`, and `cells: {butler: {perm: PermissionCell}}` where `PermissionCell = {granted: bool, reason: str | null, updated_at: timestamp | null, inherited: bool}`.

#### Scenario: Set permission requires reason
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called
- **THEN** the request body is `{granted: bool, reason: str}` and `reason` MUST be a non-empty string after trimming whitespace
- **AND** if `reason` is empty or missing, the response is `422 Unprocessable Entity` with body `{error: "reason_required"}`
- **AND** on success, `audit.append("permission.set", target=f"{butler}.{perm}", note=reason)` is invoked
- **AND** the response includes the updated cell.

#### Scenario: Reason field rejects credential patterns
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called with a `reason` that matches the case-insensitive pattern `(password|token|secret|api[_-]?key|credential|private[_-]?key)`
- **THEN** the response is `422 Unprocessable Entity` with body `{error: "reason_contains_credential"}`
- **AND** no state change occurs; no audit row is written.
- **AND** the check is implemented as `validate_no_secrets(text)` in `src/butlers/api/security.py` and reused by any future endpoint that takes free-text reason input.

#### Scenario: Inherited cells become explicit on mutation
- **WHEN** an inherited cell is flipped
- **THEN** the resulting row in `public.permissions` is explicit (not inherited) and the matrix re-fetch shows the cell as foreground.

### Requirement: Data Operations API
The dashboard SHALL expose data export and wipe endpoints under strict guards.

#### Scenario: Encrypted export
- **WHEN** `POST /api/data/export {scope}` is called with `scope ∈ {full, memory, audit, config}`
- **THEN** the response is `ApiResponse[ExportResult]` with `signed_url` valid for 60 minutes and `expires_at`
- **AND** the underlying job produces an encrypted zip containing the requested scope
- **AND** `audit.append("data.export", note=scope)` is invoked.

#### Scenario: Wipe phrase enforcement
- **WHEN** `DELETE /api/data/wipe {phrase}` is called
- **THEN** the request is rejected with `422 Unprocessable Entity` and body `{error: "phrase_mismatch"}` unless `phrase` equals the literal string `WIPE EVERYTHING IRREVERSIBLY` (exact match, no trim, no case-fold)
- **AND** on a matching phrase, the system drops every butler schema, the model catalog, runtime config, the permissions table, the spend ledger, the webhooks registry, and finally the audit log (in that order, wrapped in a single SQL transaction)
- **AND** the last write before the audit log drop is `audit.append("data.wipe")`.

#### Scenario: Wipe requires authentication
- **WHEN** `DELETE /api/data/wipe` is called without an `X-API-Key` header (or with an invalid key)
- **THEN** the request is rejected with `401 Unauthorized` BEFORE any phrase check happens.
- **WHEN** the endpoint receives a request and the server's `DASHBOARD_API_KEY` environment variable is unset
- **THEN** the endpoint refuses with `503 Service Unavailable` body `{error: "auth_unconfigured"}` regardless of phrase.

#### Scenario: Wipe partial-drop failure rolls back
- **WHEN** wipe is in progress and any individual `DROP` statement fails (e.g., a butler schema is held by an open connection)
- **THEN** the entire transaction rolls back; no schemas are dropped
- **AND** the HTTP response is `500 Internal Server Error` with body `{error: "wipe_partial_failure", failed_at: "<step>"}`
- **AND** the audit_log retains an entry showing the wipe attempt was made and failed (the audit append occurs at the start of the transaction and only commits if all subsequent drops succeed; the attempt log is preserved via a separate non-transactional write to `audit_log` BEFORE the transaction starts).

### Requirement: Webhooks Registry API
The dashboard SHALL expose CRUD and a test action for webhooks.

#### Scenario: Webhook CRUD
- **WHEN** `GET /api/webhooks` is called → list with NO `secret` field in any row; only a `secret_prefix` (first 6 chars + ellipsis) for human identification
- **WHEN** `GET /api/webhooks/{id}` is called → response also omits `secret`; only `secret_prefix` for identification
- **WHEN** `POST /api/webhooks {endpoint, events, retry_policy}` is called → a webhook is created with a freshly-generated secret returned ONCE in the response body (never returned again by any subsequent endpoint)
- **WHEN** `PUT /api/webhooks/{id} {regenerate_secret: true}` is called → a new secret is generated and returned ONCE in the response
- **WHEN** `PUT /api/webhooks/{id}` is called without `regenerate_secret` → other fields are updated atomically; secret stays unchanged and is never echoed
- **WHEN** `DELETE /api/webhooks/{id}` is called → the row is removed
- **AND** every mutation calls `audit.append("webhook.<verb>", target=webhook_id)`.

#### Scenario: Webhook test
- **WHEN** `POST /api/webhooks/{id}/test` is called
- **THEN** the system synthesizes a `webhook.test` event, runs the signed-payload dispatch (HMAC-SHA256), and returns `{status_code, latency_ms, ok: bool}`
- **AND** `last_test_at` and `last_test_ok` are updated on the webhook row.

#### Scenario: Webhook delivery retry
- **WHEN** a webhook event dispatch fails (non-2xx response)
- **THEN** the dispatcher retries per the row's `retry_policy.max_attempts` with `retry_policy.backoff_seconds` linear backoff
- **AND** after exhaustion, the failure is recorded and an `attention` item with `kind="webhook"` surfaces on `/settings/permissions` (via the Console aggregator).

## Source References
- PLAN.md §5 `/settings/permissions` API surface and §6 Phase 4 implementation order.
- `pr/overview/settings-refactor/settings-expanded.jsx :: DataExpanded` is the visual reference.
- Reuses `audit.append()` from dashboard-audit-log; every mutation in this capability is audited.
- Doctrine: `about/heart-and-soul/security.md` — "no privileged mutation without a reason" reflected in the matrix endpoint's mandatory `reason` field.
