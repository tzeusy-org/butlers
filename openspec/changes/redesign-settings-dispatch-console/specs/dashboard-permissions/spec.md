## ADDED Requirements

### Requirement: Permissions Page
The dashboard SHALL have a page at `/settings/permissions` rendered in the Dispatch design language containing the full permissions matrix, an audit reel, data operations (export, wipe), and a webhook registry.

#### Scenario: Permissions page layout
- **WHEN** a user navigates to `/settings/permissions`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Permissions & data", mono eyebrow "system · permissions".
  - **Matrix section**: Permissions × Butlers grid. Rows are permissions (`memory.read`, `memory.write`, `sessions.spawn`, `butlers.logs`, `metrics.read`, `audit.write`, `tools.invoke`, etc.). Columns are active butlers. Cells render as `on`/`off`/`inherited`; inherited cells render dim, explicit cells render foreground.
  - **Audit reel**: last 15 entries from `GET /api/audit-log?limit=15`. Mono timestamps, sans actor, serif description. Link "Full audit log →" navigates to `/audit` (a separate page; this page only shows the reel).
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
- **AND** on a matching phrase, the system drops every butler schema, the model catalog, runtime config, the permissions table, the spend ledger, the webhooks registry, and finally the audit log (in that order)
- **AND** the last write before the audit log drop is `audit.append("data.wipe")`.

### Requirement: Webhooks Registry API
The dashboard SHALL expose CRUD and a test action for webhooks.

#### Scenario: Webhook CRUD
- **WHEN** `GET /api/webhooks` is called → list with no secrets included in response (only `secret_hash` prefix for identification)
- **WHEN** `POST /api/webhooks {endpoint, events, retry_policy}` is called → a webhook is created with a freshly-generated secret returned ONCE in the response body (never returned again)
- **WHEN** `PUT /api/webhooks/{id}` is called → fields are updated atomically
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
