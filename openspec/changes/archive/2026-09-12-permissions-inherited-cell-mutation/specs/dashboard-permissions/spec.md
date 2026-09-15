## MODIFIED Requirements

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
- **WHEN** a user flips an explicit or inherited matrix cell from off to on or on to off
- **THEN** the native cell button remains keyboard-operable and opens a modal that prompts for a `reason` text field
- **AND** inherited cells remain visibly dim before mutation rather than being disabled, while explicit cells render foreground
- **AND** the modal's submit button is disabled while `reason.trim()` is empty
- **AND** on submit, `PUT /api/permissions/{butler}/{perm}` is called with `{granted, reason}`.

#### Scenario: First inherited mutation becomes an explicit foreground cell
- **WHEN** an operator submits a valid first grant or revoke for an inherited cell
- **THEN** the matrix optimistically renders the requested `granted` value as explicit foreground state
- **AND** a failed write restores the previous inherited dim state
- **AND** a successful write retains the explicit state without requiring a new permissions model.

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
- **THEN** that cell is returned with `inherited: true` and `granted` set to the system default for that permission, so the UI can render it dim and allow the owner to create an explicit override
- **AND** a pair that **does** have a row is returned with `inherited: false` (explicit), rendered foreground.

#### Scenario: Set permission requires reason
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called
- **THEN** the request body is `{granted: bool, reason: str}` and `reason` MUST be a non-empty string after trimming whitespace
- **AND** if `reason` is empty or missing, the response is `422 Unprocessable Entity` with body `{detail: {error: "reason_required"}}` (FastAPI wraps the `HTTPException.detail` payload; the frontend reads `body.detail.error`)
- **AND** on success, `audit.append("permission.set", target=f"{butler}.{perm}", note=reason)` is invoked
- **AND** the response includes the updated cell.

#### Scenario: Mutation rejects values outside the live matrix vocabulary
- **WHEN** `PUT /api/permissions/{butler}/{perm}` names a permission outside `ENFORCED_PERMISSIONS`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "permission_not_enforced"}}`
- **AND** no permissions row, `permission.set` audit row, or webhook dispatch is written.
- **WHEN** the permission is enforced but `butler` has no row in `butler_registry`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "butler_not_registered"}}`
- **AND** no permissions row, `permission.set` audit row, or webhook dispatch is written.

#### Scenario: Reason field rejects credential patterns
- **WHEN** `PUT /api/permissions/{butler}/{perm}` is called with a `reason` that matches the case-insensitive pattern `(password|token|secret|api[_-]?key|credential|private[_-]?key)`
- **THEN** the response is `422 Unprocessable Entity` with body `{detail: {error: "reason_contains_credential"}}` (FastAPI wraps the `HTTPException.detail` payload; the frontend reads `body.detail.error`)
- **AND** no state change occurs; no audit row is written.
- **AND** the check is implemented as `validate_no_secrets(text)` in `src/butlers/api/security.py` and reused by any future endpoint that takes free-text reason input.

#### Scenario: Inherited cells become explicit on mutation
- **WHEN** an inherited cell is flipped
- **THEN** the resulting row in `public.permissions` is explicit (not inherited) and the matrix re-fetch shows the cell as foreground.
