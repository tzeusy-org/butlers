## ADDED Requirements

### Requirement: Failure-Semantic Audit Outcomes
The dashboard SHALL persist an explicit outcome for the credential probe,
model verification, approval decision, and model mutation audit writers that
feed operational evidence. A failure-semantic writer MUST store
`result = "error"`; a successful outcome writer in those families MUST store
`result = "success"`.

#### Scenario: Credential probe records an observable failure
- **WHEN** a credential probe finishes unsuccessfully
- **THEN** its audit row has `action = "failed"`, `result = "error"`, and the
  safe probe diagnostic in `error`
- **AND** a successful credential probe writes `action = "verified"` with
  `result = "success"`
- **AND** no raw credential value is written to either field.

#### Scenario: Model verification records an observable failure
- **WHEN** a verify-all run has one or more failed model checks
- **THEN** its `models.verify_all` audit row has `result = "error"` and a
  bounded aggregate failure summary in `error`
- **AND** a run with no failed checks, including an empty enabled-model set,
  writes `result = "success"`.

#### Scenario: Consequential mutation records success
- **WHEN** an approval decision or model mutation commits successfully
- **THEN** its audit row records `result = "success"` in the same outcome
  boundary as the mutation
- **AND** the existing transaction and audit-unavailable rollback contract is
  unchanged.

#### Scenario: Historical failed rows receive a narrow one-shot repair
- **WHEN** the core outcome-repair migration runs against historical audit data
- **THEN** it updates only rows where `action = "failed" AND result IS NULL`,
  setting `result = "error"`
- **AND** it does not alter `ts`, `actor`, `action`, `target`, `note`, `error`,
  or any non-matching row
- **AND** rerunning it is a no-op for already repaired rows.

### Requirement: Owner-Timezone Audit Day Bounds
The audit log read API SHALL accept owner-timezone `from_date` and `to_date`
filters in addition to the existing ISO timestamp `since` filter.

#### Scenario: Bare audit day keys resolve to owner-timezone boundaries
- **WHEN** a bare `YYYY-MM-DD` value is passed as `from_date`
- **THEN** the audit query compares `ts >=` the start of that owner-local day
- **WHEN** a bare `YYYY-MM-DD` value is passed as `to_date`
- **THEN** the audit query compares `ts <=` the final microsecond of that
  owner-local day
- **AND** a full ISO timestamp for either parameter is used as-is, while an
  invalid value returns HTTP 422.

#### Scenario: Audit From equals To includes the full owner day
- **WHEN** `from_date` and `to_date` name the same owner-local day
- **THEN** audit entries throughout that full calendar day are returned
- **AND** the dashboard From and To date inputs send those parameters without
  replacing a legacy `since` deep link.

## MODIFIED Requirements

### Requirement: Audit Log Read API
The dashboard SHALL expose paginated read access to the audit log.

#### Scenario: List audit entries
- **WHEN** `GET /api/audit-log?since=&from_date=&to_date=&actor=&action=&key=&result=&kind=&limit=` is called
- **THEN** the response is `PaginatedResponse[AuditLogEntry]` with rows ordered `ts DESC`
- **AND** `limit` defaults to 100 and is clamped to `≤ 1000`
- **AND** `since` accepts an ISO 8601 timestamp; `from_date` and `to_date`
  accept owner-timezone calendar-day keys or full ISO timestamps; `actor`,
  `action`, and `result` accept exact-match strings (`result` filters on the
  outcome column added by `core_122`, e.g. `success`/`error`)
- **AND** `key` filters by normalised credential key
- **AND** `kind=privileged` returns only consequence-bearing actions
  (`approval.*`, the legacy `approvals.policy` mutation, `model.*`,
  `permission.*`, `data.*`, `webhook.*`, and the defined credential lifecycle
  actions) or rows with `result = 'error'`
- **AND** omitting `kind` returns the complete audit history, including routine
  cadence rows, preserving the `?noise=all` dashboard opt-out
- **AND** each returned `AuditLogEntry` projects `metadata`/`result`/`error`
  (added by `core_122`) alongside the base columns, defaulting to `null` for
  rows that never populated them.

#### Scenario: Get audit entry by id
- **WHEN** `GET /api/audit-log/{id}` is called
- **THEN** the response is `ApiResponse[AuditLogEntry]` if the row exists, else
  `404`.

#### Scenario: Drill into an audit-derived issue group's occurrences
- **WHEN** `GET /api/issues/{issue_key}/occurrences?window=&offset=&limit=` is called for an active `audit_error_group:*` or `scheduled_task_failure:*` issue group
- **THEN** the response is `PaginatedResponse[AuditLogEntry]` containing the individual `public.audit_log` rows behind that group's occurrence count, newest first, with `meta.total` reflecting the group's true occurrence count within `window`
- **AND** the group is re-derived from the same grouping CTE used to build the Issues feed, applying the same `window` time bound (`<N>h`, `<N>d`, default `7d`, or `all`) and the same row cap as `GET /api/issues` (bu-hmdqz.4), so the occurrences and their total can never disagree with what the feed showed under that window
- **AND** `limit` defaults to 50 and is clamped to `≤ 500`; the frontend renders "Showing X of N" and a "Load more" control while more rows remain
- **AND** an `issue_key` that does not match any currently-active group within `window` returns `404`.

#### Scenario: Tolerant metadata deserialization for poisoned rows
- **WHEN** `AuditLogEntry.from_record` projects the `metadata` column and `jsonb_typeof(metadata) = 'string'` (a since-fixed write path double-JSON-encoded the value for a contiguous 2026-06-14 -> 07-05 band, bu-hmdqz.4)
- **THEN** the string is decoded as JSON; if it decodes to an object, that object is used
- **AND** if it does not decode to an object (invalid JSON, or valid JSON that isn't an object), the raw string is wrapped losslessly as `{"_raw": <string>}` instead of raising
- **AND** this MUST NOT 500 the response — a poisoned `metadata` value on any row must never take down a list or detail read of the surrounding table.

### Requirement: Audit Log Retention
The audit log SHALL be retained indefinitely. No retention job, no expiry, no deletes.

#### Scenario: No retention policy applies
- **WHEN** the system runs the daily maintenance job
- **THEN** no rows are removed from `audit_log`
- **AND** no row is updated in place (the table is append-only), except for a
  documented one-shot data-integrity repair below.

#### Scenario: One-shot structural metadata repair is not a retention violation
- **WHEN** a write-path defect causes a contiguous band of `audit_log` rows to store `metadata` as JSON-encoded text instead of an object (`jsonb_typeof(metadata) = 'string'`, bu-hmdqz.4)
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
