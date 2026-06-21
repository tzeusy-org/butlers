# dashboard-permissions

## Purpose

This capability provides the `/settings/permissions` surface of the Dispatch-language operator control plane: the full Permissions × Butlers matrix, a last-15 audit reel, data operations (encrypted-zip export; irreversible wipe is **temporarily disabled** — see the Data Operations requirement), and a webhook registry with a test action. It answers "what can this system do, to whom, and on whose authority?" while enforcing the doctrine rule that no privileged mutation occurs without a recorded reason. Every mutation in this capability is audited via the shared `audit.append()` primitive.

## Requirements

### Requirement: Permissions Page
The dashboard SHALL have a page at `/settings/permissions` rendered in the Dispatch design language containing the full permissions matrix, an audit reel, data operations (export; wipe disabled), and a webhook registry.

#### Scenario: Permissions page layout
- **WHEN** a user navigates to `/settings/permissions`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Permissions & data", mono eyebrow "system · permissions".
  - **Matrix section**: Permissions × Butlers grid. Rows are the runtime-enforced permissions — exactly `calendar.write`, `cross_butler`, `email.send`, `notify`, `spawn` (the set enforced by `src/butlers/core/permissions.py`; no decorative permission rows that no code reads). Columns are active butlers. Cells render as `on`/`off`/`inherited`; inherited cells render dim, explicit cells render foreground.
  - **Audit reel**: last 15 **privileged-action** entries from `GET /api/audit-log?limit=15&kind=privileged`. The reel filters out high-frequency operational noise (e.g. `*_heartbeat`, `GET /api/switchboard/heartbeat`) and surfaces only mutation/security actions (`permission.set`, `data.*`, `webhook.*`, and other non-heartbeat audit rows). Mono timestamps, sans actor, serif description. Link "Full audit log →" navigates to `/audit-log`.
  - **Data ops sub-grid**: export (scope picker → signed URL). The **wipe** control is disabled (not rendered, or rendered disabled with a "temporarily disabled" note); see the Data Operations requirement.
  - **Webhooks table**: list with add/edit/test/delete actions.

#### Scenario: Matrix cell flip requires reason
- **WHEN** a user flips a matrix cell from off to on or on to off
- **THEN** a modal prompts for a `reason` text field
- **AND** the modal's submit button is disabled while `reason.trim()` is empty
- **AND** on submit, `PUT /api/permissions/{butler}/{perm}` is called with `{granted, reason}`.

#### Scenario: Audit reel filters operational noise
- **WHEN** the audit reel loads its last-15 window
- **THEN** it requests a privileged-action-only view (e.g. `GET /api/audit-log?limit=15&kind=privileged`) so that high-frequency operational rows — butler/switchboard heartbeats and routine GET traffic — are excluded
- **AND** the rows shown are mutation/security actions (`permission.set`, `data.export`, `webhook.create|update|delete|test`, and similar), so a reader of a security surface sees security-relevant activity rather than heartbeat spam
- **AND** when no privileged actions exist yet, the reel shows its empty state rather than padding with noise.

### Requirement: Permissions Matrix API
The dashboard SHALL expose CRUD over the permissions matrix.

#### Scenario: Read full matrix
- **WHEN** `GET /api/permissions` is called
- **THEN** the response is `ApiResponse[PermissionsMatrix]` containing `butlers: string[]`, `permissions: string[]`, and `cells: {butler: {perm: PermissionCell}}` where `PermissionCell = {granted: bool, reason: str | null, updated_at: timestamp | null, inherited: bool}`
- **AND** `butlers` is the full set of active butlers and `permissions` is the full enforced set (`calendar.write`, `cross_butler`, `email.send`, `notify`, `spawn`) — the matrix is dense (every active-butler × enforced-permission pair has a cell), not built only from rows that happen to exist in `public.permissions`.

#### Scenario: Inherited vs explicit cells
- **WHEN** a butler × permission pair has **no explicit row** in `public.permissions`
- **THEN** that cell is returned with `inherited: true` and `granted` set to the system default for that permission, so the UI can render it dim/non-editable to distinguish a default from an operator-set value
- **AND** a pair that **does** have a row is returned with `inherited: false` (explicit), rendered foreground.

#### Scenario: Set permission requires reason
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called
- **THEN** the request body is `{granted: bool, reason: str}` and `reason` MUST be a non-empty string after trimming whitespace
- **AND** if `reason` is empty or missing, the response is `422 Unprocessable Entity` with body `{detail: {error: "reason_required"}}` (FastAPI wraps the `HTTPException.detail` payload; the frontend reads `body.detail.error`)
- **AND** on success, `audit.append("permission.set", target=f"{butler}.{perm}", note=reason)` is invoked
- **AND** the response includes the updated cell.

#### Scenario: Reason field rejects credential patterns
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called with a `reason` that matches the case-insensitive pattern `(password|token|secret|api[_-]?key|credential|private[_-]?key)`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "reason_contains_credential"}}` (FastAPI wraps the `HTTPException.detail` payload; the frontend reads `body.detail.error`)
- **AND** no state change occurs; no audit row is written.
- **AND** the check is implemented as `validate_no_secrets(text)` in `src/butlers/api/security.py` and reused by any future endpoint that takes free-text reason input.

#### Scenario: Inherited cells become explicit on mutation
- **WHEN** an inherited cell is flipped
- **THEN** the resulting row in `public.permissions` is explicit (not inherited) and the matrix re-fetch shows the cell as foreground.

### Requirement: Data Operations API
The dashboard SHALL expose data export and wipe endpoints under strict guards.

#### Scenario: Encrypted export
- **WHEN** `POST /api/data/export {scope}` is called with `scope ∈ {all, memory, audit, config}` (`full` accepted as an alias of `all`)
- **THEN** the response is `ApiResponse[ExportResult]` with `signed_url` valid for 60 minutes and `expires_at`
- **AND** the download the signed URL serves is an **encrypted zip** (not plaintext NDJSON), and any UI copy describing it ("encrypted zip") is therefore truthful
- **AND** `audit.append("data.export", note=scope)` is invoked.

#### Scenario: Every export scope yields its real data
- **WHEN** the signed URL for a given `scope` is downloaded
- **THEN** the archive contains the actual data for that scope, never an empty/near-empty file behind a success response:
  - `memory` → the memory butler's facts/rules/episodes data
  - `audit` → `public.audit_log`
  - `config` → runtime/config tables (`public.runtime_config`, `public.model_catalog`, `public.permissions`)
  - `all` → the union of every scope above
- **AND** a scope that resolves to zero rows is reported as such (explicit empty marker), but a *known* scope MUST NOT silently map to "no tables" — the export must cover the data the scope name promises.

#### Scenario: Wipe feature disabled
- **WHEN** the `/settings/permissions` page renders
- **THEN** no usable "wipe all data" control is presented to the operator (the panel is removed, or rendered disabled with a "temporarily disabled" note) — the UI MUST NOT offer a button that triggers an irreversible wipe.
- **WHEN** `DELETE /api/data/wipe` is called while the feature is disabled
- **THEN** the endpoint refuses with `503 Service Unavailable` body `{error: "wipe_disabled"}` and performs no destruction, regardless of the phrase supplied
- **AND** no schemas or tables are dropped.

> **Deferred — re-enable requirements (do NOT build now; tracked as a non-ready backlog bead).**
> When the wipe feature is re-enabled it MUST satisfy all of:
> - **Atomicity**: drop every butler schema, model catalog, runtime config, permissions, spend ledger, webhooks, then audit log, wrapped in a **single SQL transaction**; any failed `DROP` rolls the whole thing back (`500 {error: "wipe_partial_failure", failed_at}`), never a partial wipe reported as success.
> - **Phrase guard**: exact match of `WIPE EVERYTHING IRREVERSIBLY` (no trim, no case-fold) → else `422 {error: "phrase_mismatch"}`.
> - **Fail-closed auth**: reject without a valid `X-API-Key` (`401`) before any phrase check; `503 {error: "auth_unconfigured"}` when `DASHBOARD_API_KEY` is unset. This is the single carve-out from the network-trust doctrine, justified by the irreversibility of the action.
> - A non-transactional `audit.append("data.wipe")` attempt row is written BEFORE the transaction so the attempt survives a rollback.

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
- **AND** after exhaustion, the failure is recorded and an `attention` item with `kind="webhook_failure"` surfaces via the Settings Console aggregator (`src/butlers/api/routers/settings_console.py`).

## Source References
- PLAN.md §5 `/settings/permissions` API surface and §6 Phase 4 implementation order.
- Visual reference: the `DataExpanded` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log; every mutation in this capability is audited.
- Doctrine: `about/heart-and-soul/security.md` — "no privileged mutation without a reason" reflected in the matrix endpoint's mandatory `reason` field.
