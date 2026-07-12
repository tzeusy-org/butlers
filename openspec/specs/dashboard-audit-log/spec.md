# dashboard-audit-log

## Purpose

`dashboard-audit-log` is the audit-log infrastructure primitive introduced by the settings dispatch console redesign. It is not a dashboard capability per se; it is cross-cutting infrastructure shared by every mutation endpoint in the settings refactor and all future write-bearing endpoints. It owns the `public.audit_log` table (append-only, indefinitely retained), the `audit.append()` helper that every state-changing endpoint calls inside its own transaction, and the `/api/audit-log` read API. The primitive is the prerequisite for permissions, model priority changes, spend rules/ceiling changes, webhook CRUD, approval verbs, and data ops.

## Requirements

### Requirement: Audit Log Primitive
The dashboard SHALL maintain a single, append-only audit log used by every mutation endpoint that changes system state.

#### Scenario: Audit log table shape
- **WHEN** the audit log table is provisioned
- **THEN** `public.audit_log` exists with columns `id BIGSERIAL PRIMARY KEY`, `ts TIMESTAMPTZ NOT NULL DEFAULT now()`, `actor TEXT NOT NULL`, `action TEXT NOT NULL`, `target TEXT`, `note TEXT`, `ip INET`, `request_id UUID`, `metadata JSONB`, `result TEXT`, `error TEXT` (the last three added by migration `core_122` for writer unification)
- **AND** indexes exist on `(ts DESC)`, `(action)`, and `(actor)`
- **AND** no DELETE statement against `audit_log` exists anywhere in the repository (verified by a static-check test).

#### Scenario: audit.append helper contract
- **WHEN** a mutation endpoint succeeds
- **THEN** it calls `audit.append(pool_or_conn, actor, action, *, target=None, note=None, ip=None, request_id=None, metadata=None, result=None, error=None) -> int` returning the new row id (the first positional argument is an asyncpg pool or an already-acquired connection; passing a connection lets the audit insert participate in the caller's open transaction)
- **AND** the call is made INSIDE the same SQL transaction as the state change (commit only after the audit row is written)
- **AND** Prometheus counter `audit_log_appended_total{action}` is incremented after commit.

#### Scenario: audit.append raises on missing table
- **WHEN** `audit.append()` is called and `public.audit_log` does not exist (migration failed or rolled back)
- **THEN** the helper SHALL raise `AuditTableNotAvailableError` (or the equivalent SQLAlchemy `ProgrammingError`)
- **AND** the helper SHALL NOT silently skip or log-and-continue
- **AND** the calling endpoint propagates the exception; the HTTP response is `503 Service Unavailable` with body `{error: "audit_unavailable"}`
- **AND** because the transaction includes both the state change and the audit append, the state change is rolled back automatically.

### Requirement: Audit Log Read API
The dashboard SHALL expose paginated read access to the audit log.

#### Scenario: List audit entries
- **WHEN** `GET /api/audit-log?since=&actor=&action=&key=&result=&kind=&limit=` is called
- **THEN** the response is `PaginatedResponse[AuditLogEntry]` with rows ordered `ts DESC`
- **AND** `limit` defaults to 100 and is clamped to `≤ 1000`
- **AND** `since` accepts an ISO 8601 timestamp; `actor`, `action`, and `result` accept exact-match strings (`result` filters on the outcome column added by `core_122`, e.g. `success`/`error`)
- **AND** `key` filters by normalised credential key; `kind=privileged` excludes `*_heartbeat` and `GET /*` noise rows
- **AND** each returned `AuditLogEntry` projects `metadata`/`result`/`error` (added by `core_122`) alongside the base columns, defaulting to `null` for rows that never populated them.

#### Scenario: Get audit entry by id
- **WHEN** `GET /api/audit-log/{id}` is called
- **THEN** the response is `ApiResponse[AuditLogEntry]` if the row exists, else `404`.

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
- **AND** no row is updated in place (the table is append-only).

#### Scenario: One-shot structural metadata repair is not a retention violation
- **WHEN** a write-path defect causes a contiguous band of `audit_log` rows to store `metadata` as JSON-encoded text instead of an object (`jsonb_typeof(metadata) = 'string'`, bu-hmdqz.4)
- **THEN** a one-shot, batched, idempotent migration MAY normalize just the `metadata` column of the affected rows back to the correct object shape, preserving the original content losslessly (decoding valid JSON back to an object, or wrapping non-object content under `_raw`)
- **AND** this is a data-integrity repair of a poisoned write path, not an ordinary update — it MUST NOT touch `ts`, `actor`, `action`, `target`, `result`, or `error`, and MUST NOT be used as precedent for any other kind of edit
- **AND** the retention/append-only guarantee otherwise stands: no row is ever deleted, and no column other than a proven-poisoned `metadata` is ever rewritten.

## Source References
- PLAN.md §6 Phase 1 Foundations: audit log primitive.
- Doctrine: `about/heart-and-soul/security.md` (audit trail discipline for any privileged operation).
- The audit primitive is the prerequisite for permissions, model priority changes, spend rules/ceiling changes, webhook CRUD, approval verbs, and data ops.
