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
- **WHEN** `GET /api/audit-log?since=&actor=&action=&limit=` is called
- **THEN** the response is `PaginatedResponse[AuditEntry]` with rows ordered `ts DESC`
- **AND** `limit` defaults to 100 and is clamped to `≤ 1000`
- **AND** `since` accepts an ISO 8601 timestamp; `actor` and `action` accept exact-match strings.

#### Scenario: Get audit entry by id
- **WHEN** `GET /api/audit-log/{id}` is called
- **THEN** the response is `ApiResponse[AuditEntry]` if the row exists, else `404`.

### Requirement: Audit Log Retention
The audit log SHALL be retained indefinitely. No retention job, no expiry, no deletes.

#### Scenario: No retention policy applies
- **WHEN** the system runs the daily maintenance job
- **THEN** no rows are removed from `audit_log`
- **AND** no row is updated in place (the table is append-only).

## Source References
- PLAN.md §6 Phase 1 Foundations: audit log primitive.
- Doctrine: `about/heart-and-soul/security.md` (audit trail discipline for any privileged operation).
- The audit primitive is the prerequisite for permissions, model priority changes, spend rules/ceiling changes, webhook CRUD, approval verbs, and data ops.
