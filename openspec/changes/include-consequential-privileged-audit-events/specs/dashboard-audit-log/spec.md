# dashboard-audit-log

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
  `permission.*`, `data.*`, `webhook.*`, the
  `runtime_config_patch` runtime-config PATCH action, the
  `PUT /api/butlers/{name}/model-overrides` model-override PUT action, and the
  defined credential lifecycle actions) or rows with `result = 'error'`
- **AND** ordinary successful reads and unrelated non-consequential successful
  mutation noise, including other `GET` rows and cadence rows, remain
  excluded from the privileged action-family allowlist; rows with
  `result = 'error'` remain included by the preceding error rule
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
