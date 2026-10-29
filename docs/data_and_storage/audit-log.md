# Audit writer integrity

`butlers.core.audit.append` is the application append primitive for
`public.audit_log`. API routers retain their existing re-export and wire model;
jobs import core directly. The daemon `write_audit_entry`, API
`log_audit_entry` and `emit_dashboard_audit` shims remain diagnostic best-effort
writers. They cannot substitute for a required action receipt.

Required state changes pass their acquired connection to `append` inside the
same transaction. Permissions, webhooks and retention-policy mutations retain
that contract. Runtime-config reconciliation now uses the same primitive,
keeping its conditional row lock until the update, one audit append and commit
finish. No-change and explicitly reasoned narrowing branches append nothing.
A missing connection/receipt, unmigrated table/column, denied INSERT or a known
connection-unavailable error produces `AuditTableNotAvailableError`; unrelated
errors and cancellation propagate. Genuine migrated rollback and independent
committed-readback tests are required; software mocks cannot prove SQL atomicity.

`core_279` adds nullable `writer_role` without backfilling historical identity.
For every new insert, an invoker trigger stamps the actual database role
executing the INSERT and rejects a conflicting supplied role. The trigger's
fixed `pg_catalog, pg_temp` search path does not confer a definer owner's
privileges. An existing trusted SECURITY DEFINER projection records its own
constrained INSERT role, not its initiating login or the trigger function's
owner. `actor` remains logical server-derived attribution. A restore-named
actor, action, or metadata value does not authenticate a restore result; the
private restore executor ledger remains authoritative.

Row UPDATE/DELETE guards and a statement TRUNCATE guard refuse runtime history
mutation even when `init-db.sql` re-widens public-table grants. SELECT and INSERT
privileges are preserved. These guards protect the database-role boundary;
they cannot withstand a table owner disabling triggers or replacing functions.
That privilege split belongs to `bu-dss7he.6`. Owner-authorized, reviewed
historical repairs remain separate operations; this change adds no repair or
purge path.

Downgrading only `core_279` removes its enforcement functions/triggers, while
retaining the additive writer column and every event/provenance value. It
therefore restores the older enforcement level rather than deleting history.
An upgrade re-enables the guards. Apply either direction through the reviewed
migration process; this document authorizes no live role or database change.

The stage is `bu-dss7he.3`. `bu-dss7he.18` retains the operator-table fold/drop,
and `bu-viat6h.4` retains restore-drill executor/evidence integration. No full
unification, restore drill, live ACL or hostile-owner security outcome follows
from this stage alone. See [backup/restore](../operations/backup-restore.md).

## Implementation Notes

Migration chains replay per schema against shared `public` history. Row guards
allow genuinely empty historical repair statements to remain no-ops; they do
not grant a runtime repair exception. Bootstrap may widen DML grants but must
leave all three audit guards enabled.

Disposable PostgreSQL fixtures that previously truncated audit history now use
`tests.audit_helpers.clear_audit_log_for_test`: it verifies the real table owner,
locks the table, disables only the new TRUNCATE guard for cleanup, and re-enables
it as ALWAYS inside one transaction. Cancellation/error rolls back both cleanup
and the guard change. Production code never imports this test helper; runtime
role negatives execute with every guard enabled. This is explicit test setup,
not a production bypass or proof of runtime privileges.
