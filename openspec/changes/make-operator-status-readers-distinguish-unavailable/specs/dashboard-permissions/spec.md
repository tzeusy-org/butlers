## MODIFIED Requirements

### Requirement: Permissions Page
The dashboard SHALL have a page at `/settings/permissions` rendered in the Dispatch design language containing the full permissions matrix, an audit reel, data operations (export; wipe disabled), and a webhook registry.

#### Scenario: Permissions page layout
- **WHEN** a user navigates to `/settings/permissions`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Permissions & data", mono eyebrow "system · permissions".
  - **Matrix section**: Permissions × Butlers grid. Rows are the runtime-enforced permissions, exactly `calendar.write`, `cross_butler`, `email.send`, `notify`, `spawn` (the set enforced by `src/butlers/core/permissions.py`; no decorative permission rows that no code reads). Columns are active butlers. Cells render as `on`/`off`/`inherited`; inherited cells render dim, explicit cells render foreground.
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
- **THEN** it requests a privileged-action-only view (e.g. `GET /api/audit-log?limit=15&kind=privileged`) so that high-frequency operational rows, butler/switchboard heartbeats and routine GET traffic, are excluded
- **AND** the rows shown are mutation/security actions (`permission.set`, `data.export`, `webhook.create|update|delete|test`, and similar), so a reader of a security surface sees security-relevant activity rather than heartbeat spam
- **AND** when no privileged actions exist yet, the reel shows its empty state rather than padding with noise.

#### Scenario: Audit reel distinguishes no history from an unavailable source
- **WHEN** the privileged audit request succeeds with zero entries
- **THEN** the reel SHALL show its calm no-history state and SHALL NOT render a degraded or retry state
- **WHEN** the privileged audit request fails and no cached response is available
- **THEN** the reel SHALL name the source as unavailable or degraded and provide a retry action that re-queries the privileged audit window
- **AND** the reel MUST NOT render its no-history state, or imply that the failed request proves an empty audit history.

#### Scenario: Audit reel retains cached rows under a degraded refresh
- **WHEN** a previously successful privileged audit response is cached and a subsequent refresh fails
- **THEN** the reel SHALL retain the cached privileged-action rows
- **AND** it SHALL visibly label the source as unavailable or degraded and provide a retry action
- **AND** it MUST NOT render its no-history state while the query is errored.

## Source References
- Non-Negotiable Rule 1 (user-federated owner sovereignty)
- Non-Negotiable Rule 4 (deterministic, debuggable infrastructure)
- RFC 0007 (Dashboard and API Surface)
