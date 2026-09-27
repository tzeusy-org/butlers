## ADDED Requirements

### Requirement: Audit Log Page

The dashboard SHALL render the audit log at `/audit-log` as a list page backed by `GET /api/audit-log`, with every filter serialised in the URL querystring so the visible controls and the request can never disagree.

- The filter bar MUST offer free-text **Actor** and **Action** inputs (debounced before querying), **From** and **To** date inputs, a **Noise** toggle, and a "Clear filters" action.
- The page MUST default to `kind=privileged`; the Noise toggle sets `?noise=all` to show every row, including routine cadence rows.
- `?key=` and `?result=` MUST be honoured as deep-link filters, and active `key` and `actor` filters MUST render as removable chips.
- The table MUST show Time, Actor, Action, Outcome, and Target columns; clicking a row expands its detail, and results page with Previous/Next controls.

#### Scenario: Free-text actor and action filters

- **WHEN** the owner types `owner` into the Actor input and `model.priority` into the Action input
- **THEN** the URL MUST carry `?actor=owner&action=model.priority`
- **AND** after the debounce, the page MUST request `GET /api/audit-log` with those `actor` and `action` values

#### Scenario: Privileged by default

- **WHEN** the owner opens `/audit-log` without `?noise=all`
- **THEN** the request MUST include `kind=privileged`
- **AND** toggling Noise MUST drop `kind` and set `?noise=all`

#### Scenario: Deep-linked filters are visible and removable

- **WHEN** the page is opened at `/audit-log?key=u:google&actor=owner`
- **THEN** removable `key: u:google` and `actor: owner` chips MUST render
- **AND** removing a chip MUST drop that parameter from the URL and the request

## MODIFIED Requirements

### Requirement: Audit Log Retention
The audit log SHALL be retained indefinitely. No retention job, no expiry, no deletes.

#### Scenario: No retention policy applies
- **WHEN** the system runs the daily maintenance job
- **THEN** no rows are removed from `audit_log`
- **AND** no row is updated in place (the table is append-only), except for a
  documented one-shot data-integrity repair below.

#### Scenario: One-shot structural metadata repair is not a retention violation
- **WHEN** a write-path defect causes a contiguous band of `audit_log` rows to store `metadata` as JSON-encoded text instead of an object (`jsonb_typeof(metadata) = 'string'`)
- **THEN** a one-shot, batched, idempotent migration MAY normalize just the `metadata` column of the affected rows back to the correct object shape, preserving the original content losslessly (decoding valid JSON back to an object, or wrapping non-object content under `_raw`)
- **AND** this is a data-integrity repair of a poisoned write path, not an ordinary update — it MUST NOT touch `ts`, `actor`, `action`, `target`, `result`, or `error`, and MUST NOT be used as precedent for any other kind of edit
- **AND** the retention/append-only guarantee otherwise stands: no row is ever deleted, and no column other than a proven-poisoned `metadata` is ever rewritten.

#### Scenario: One-shot missing-outcome repair is not a retention violation
- **WHEN** credential probe writers historically wrote `action = 'failed'` without the required `result = 'error'` outcome
- **THEN** one idempotent migration MAY set only the missing `result` value to
  `error` for rows matching `action = 'failed' AND result IS NULL`
- **AND** it MUST NOT touch `ts`, `actor`, `action`, `target`, `note`, `error`,
  or any non-matching row, and MUST NOT be used as precedent for inferred
  outcome repairs
- **AND** the retention/append-only guarantee otherwise stands: no row is ever
  deleted or updated in place.
