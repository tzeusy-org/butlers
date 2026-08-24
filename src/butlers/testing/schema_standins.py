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

A stand-in mirrors columns, primary keys and CHECK constraints -- the surface a
query binds to, and the surface that decides whether a row is accepted.  It
deliberately does not mirror foreign keys, indexes, triggers, views or seed
data.  Foreign keys are excluded so each table stays independently creatable
(``pending_actions`` and ``approval_rules`` reference each other in the real
chain, via a DEFERRABLE constraint a stand-in has no way to reproduce alone);
a fixture that needs an index, a trigger or referential integrity adds it next
to the ``ddl()`` call, or takes the real chain via
:func:`butlers.testing.migration.create_migrated_test_db`.  ``STANDINS``
therefore cannot catch index drift -- ``approvals_013``'s unique partial index
on ``deduplication_key`` is real and is not mirrored here.

Chains may be shared (``core``), roster (``switchboard``) or module
(``approvals``, under ``src/butlers/modules/<chain>/migrations/``);
``create_migrated_test_db`` resolves all three by name.

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


# The approvals tables come from a MODULE chain
# (src/butlers/modules/approvals/migrations/), not a roster one: approvals_001
# creates all three, 005 adds blast_radius/reversibility and their CHECKs, 012
# adds the 'abandoned' status and 'action_abandoned' event type, 013 adds
# deduplication_key.
PENDING_ACTIONS = TableStandin(
    table="pending_actions",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::PENDING_ACTIONS",
    # approvals_001 order, then the columns later revisions appended.
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("tool_name", "TEXT NOT NULL"),
        ("tool_args", "JSONB NOT NULL"),
        ("agent_summary", "TEXT"),
        ("session_id", "UUID"),
        ("status", "VARCHAR NOT NULL DEFAULT 'pending'"),
        ("requested_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("expires_at", "TIMESTAMPTZ"),
        ("decided_by", "TEXT"),
        ("decided_at", "TIMESTAMPTZ"),
        ("execution_result", "JSONB"),
        ("why", "TEXT"),
        ("evidence", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        ("approval_rule_id", "UUID"),
        ("blast_radius", "TEXT"),
        ("reversibility", "TEXT"),
        ("deduplication_key", "TEXT"),
    ),
    table_constraints=(
        (
            "CONSTRAINT pending_actions_status_check CHECK (status IN ("
            "'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned'))"
        ),
        (
            "CONSTRAINT pending_actions_blast_radius_check CHECK ("
            "blast_radius IS NULL OR blast_radius IN "
            "('none', 'self', 'contact', 'external'))"
        ),
        (
            "CONSTRAINT pending_actions_reversibility_check CHECK ("
            "reversibility IS NULL OR reversibility IN "
            "('reversible', 'compensable', 'irreversible'))"
        ),
    ),
)


APPROVAL_RULES = TableStandin(
    table="approval_rules",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::APPROVAL_RULES",
    columns=(
        ("id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("tool_name", "TEXT NOT NULL"),
        ("arg_constraints", "JSONB NOT NULL"),
        ("description", "TEXT NOT NULL"),
        ("created_from", "UUID"),
        ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
        ("expires_at", "TIMESTAMPTZ"),
        ("max_uses", "INT"),
        ("use_count", "INT NOT NULL DEFAULT 0"),
        ("active", "BOOL NOT NULL DEFAULT true"),
    ),
)


APPROVAL_EVENTS = TableStandin(
    table="approval_events",
    chains=("core", "approvals"),
    real_schema="public",
    constant_path="src/butlers/testing/schema_standins.py::APPROVAL_EVENTS",
    columns=(
        ("event_id", "UUID PRIMARY KEY DEFAULT gen_random_uuid()"),
        ("action_id", "UUID"),
        ("rule_id", "UUID"),
        ("event_type", "TEXT NOT NULL"),
        ("actor", "TEXT NOT NULL"),
        ("reason", "TEXT"),
        ("event_metadata", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
        ("occurred_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
    ),
    table_constraints=(
        (
            "CONSTRAINT approval_events_type_check CHECK (event_type IN ("
            "'action_queued', 'action_auto_approved', 'action_approved', "
            "'action_rejected', 'action_expired', 'action_abandoned', "
            "'action_execution_succeeded', 'action_execution_failed', "
            "'rule_created', 'rule_revoked', "
            "'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed', "
            "'promotion_superseded', 'demotion_suggested', 'demotion_confirmed', "
            "'demotion_dismissed'))"
        ),
        (
            "CONSTRAINT approval_events_link_check CHECK ("
            "action_id IS NOT NULL OR rule_id IS NOT NULL OR event_type IN ("
            "'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed', "
            "'promotion_superseded', 'demotion_suggested', 'demotion_confirmed', "
            "'demotion_dismissed'))"
        ),
    ),
)


STANDINS: dict[str, TableStandin] = {
    standin.table: standin
    for standin in (CONNECTOR_REGISTRY, PENDING_ACTIONS, APPROVAL_RULES, APPROVAL_EVENTS)
}
"""Every declared stand-in, keyed by table name. Both guards iterate this."""


__all__ = [
    "APPROVAL_EVENTS",
    "APPROVAL_RULES",
    "CONNECTOR_REGISTRY",
    "PENDING_ACTIONS",
    "STANDINS",
    "TableStandin",
]
