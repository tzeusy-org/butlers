"""Single-source definitions for tables that tests provision by hand.

Some integration tests need one table from *another* butler's migration chain
without paying for that whole chain -- an endpoint under test reads
``connector_registry`` (switchboard), but the test itself is about core-chain
behaviour.  The historical answer was a per-file ``CREATE TABLE`` naming only
the columns that file's endpoint happened to query.

That answer drifts.  Each copy is correct on the day it is written, passes in
isolation forever after, and breaks silently the next time the real chain gains
a column: the endpoint's widened ``SELECT`` raises, the route returns its
DEGRADED envelope, and the test dies much later on a missing response key.  The
proximate symptom points at the assertion, never at the DDL.  ``sw_031`` cost
five of the nine failures on PR #3853 exactly this way (bu-r8opr).

So a stand-in is declared once, here, and imported.  Two guards in
``tests/config/test_schema_standin_parity.py`` keep that honest: one diffs each
declaration against the table the real chain builds (naming the drifted
columns), the other refuses a new hand-written copy anywhere under ``tests/``.

A stand-in mirrors columns and table constraints -- the surface a query binds
to.  It deliberately does not mirror indexes, triggers, views or seed data; a
test that needs those wants the real chain via
:func:`butlers.testing.migration.create_migrated_test_db`.

Usage::

    from butlers.testing.schema_standins import CONNECTOR_REGISTRY

    await pool.execute(CONNECTOR_REGISTRY.ddl())                     # public
    await pool.execute(CONNECTOR_REGISTRY.ddl(schema="switchboard"))  # qualified
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TableStandin:
    """One table's test-side shape, plus where the real definition lives."""

    table: str
    """Unqualified table name, identical to the migration chain's."""

    chains: tuple[str, ...]
    """Migration chains that must run for the real table to exist."""

    real_schema: str
    """Schema the real table lands in when :attr:`chains` run in a test DB."""

    constant_path: str
    """Where a reconciling engineer edits this declaration."""

    columns: tuple[tuple[str, str], ...]
    """``(name, type-and-column-constraints)`` in migration-chain order."""

    table_constraints: tuple[str, ...] = ()
    """Table-level constraint clauses (primary key, checks)."""

    def ddl(self, *, schema: str | None = None) -> str:
        """Return ``CREATE TABLE IF NOT EXISTS`` DDL, optionally schema-qualified."""
        qualified = f"{schema}.{self.table}" if schema else self.table
        clauses = [f"{name} {definition}" for name, definition in self.columns]
        clauses.extend(self.table_constraints)
        body = ",\n    ".join(clauses)
        return f"CREATE TABLE IF NOT EXISTS {qualified} (\n    {body}\n)"


CONNECTOR_REGISTRY = TableStandin(
    table="connector_registry",
    chains=("core", "switchboard"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::CONNECTOR_REGISTRY",
    # Mirrors roster/switchboard/migrations: sw_002 (base table), sw_012
    # (deleted_at, replay_safe), sw_022 (archived_at), sw_031 (operational_role,
    # parent_endpoint_identity, valid_operational_role).
    columns=(
        ("connector_type", "TEXT NOT NULL"),
        ("endpoint_identity", "TEXT NOT NULL"),
        ("instance_id", "UUID"),
        ("version", "TEXT"),
        ("state", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("error_message", "TEXT"),
        ("uptime_s", "INTEGER"),
        ("last_heartbeat_at", "TIMESTAMPTZ"),
        ("first_seen_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("registered_via", "TEXT NOT NULL DEFAULT 'self'"),
        ("counter_messages_ingested", "BIGINT DEFAULT 0"),
        ("counter_messages_failed", "BIGINT DEFAULT 0"),
        ("counter_source_api_calls", "BIGINT DEFAULT 0"),
        ("counter_checkpoint_saves", "BIGINT DEFAULT 0"),
        ("counter_dedupe_accepted", "BIGINT DEFAULT 0"),
        ("checkpoint_cursor", "TEXT"),
        ("checkpoint_updated_at", "TIMESTAMPTZ"),
        ("capabilities", "JSONB DEFAULT NULL"),
        ("settings", "JSONB DEFAULT NULL"),
        ("deleted_at", "TIMESTAMPTZ NULL"),
        ("replay_safe", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("archived_at", "TIMESTAMPTZ NULL"),
        ("operational_role", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("parent_endpoint_identity", "TEXT NULL"),
    ),
    table_constraints=(
        "PRIMARY KEY (connector_type, endpoint_identity)",
        (
            "CONSTRAINT valid_operational_role CHECK ("
            "operational_role IN ('runtime_instance', 'checkpoint', 'unknown'))"
        ),
    ),
)


STANDINS: dict[str, TableStandin] = {CONNECTOR_REGISTRY.table: CONNECTOR_REGISTRY}
"""Every declared stand-in, keyed by table name. Both guards iterate this."""


__all__ = ["CONNECTOR_REGISTRY", "STANDINS", "TableStandin"]
