"""Provision the declared Chronicler private namespace for its existing role.

Revision ID: mem_015
Revises: mem_014

Only fixed objects used by the current memory implementation receive DML.
UUID/text keys require no sequence grants. Trigger/function ACLs, ownership,
public/sibling schemas, migration tables and future objects remain unchanged.

Downgrade intentionally refuses on the private target: PostgreSQL merges
identical grants by one grantor, so their prior provenance cannot be recovered
without additional persistent state. Blind REVOKE would erase preexisting
identical grants. A reviewed managed ACL repair is the supported rollback;
this revision never drops data or pretends that such a reversal succeeded.
"""

from __future__ import annotations

from alembic import op

revision = "mem_015"
down_revision = "mem_014"
branch_labels = None
depends_on = None

# Derived from storage CRUD/decay, append-only audit/link writes, predicate
# registration, mem_010's invoker trigger, and knowledge_gaps lifecycle writes.
# Configuration policy rows are read, never edited by the runtime.
_TABLE_PRIVILEGES = (
    ("episodes", "SELECT, INSERT, UPDATE, DELETE"),
    ("facts", "SELECT, INSERT, UPDATE, DELETE"),
    ("rules", "SELECT, INSERT, UPDATE, DELETE"),
    ("memory_links", "SELECT, INSERT"),
    ("memory_events", "SELECT, INSERT"),
    ("predicate_registry", "SELECT, INSERT, UPDATE"),
    ("memory_policies", "SELECT"),
    ("episode_tombstones", "SELECT, INSERT"),
    ("knowledge_gaps", "SELECT, INSERT, UPDATE"),
    ("knowledge_gap_origins", "SELECT, INSERT, UPDATE"),
)


def _targets_private_schema() -> bool:
    # current_schema() is the first *accessible* search-path entry. Losing
    # access to the selected private schema must not turn a refusal into a
    # successful no-op on public. Use the runner's explicit Alembic target.
    # An absent option is the supported unscoped/default-public runner mode;
    # it must never infer private grant authority from an effective search_path.
    config = op.get_context().config
    target = config.get_main_option("butlers.target_schema") if config is not None else None
    return target is not None and target.strip() == "chronicler_mem"


def upgrade() -> None:
    if not _targets_private_schema():
        return
    # Both values below come only from this fixed source tuple, never from a
    # database row, caller declaration, discovered table or configurable role.
    values = ",\n".join(f"('{table}', '{privileges}')" for table, privileges in _TABLE_PRIVILEGES)
    op.execute(
        f"""
        DO $$
        DECLARE
            owner_oid OID;
            runtime_oid OID;
            relation_name TEXT;
            privileges TEXT;
        BEGIN
            IF current_schema() IS DISTINCT FROM 'chronicler_mem' THEN
                RAISE EXCEPTION 'mem_015 requires access to the selected private target'
                    USING ERRCODE = '55000';
            END IF;

            SELECT oid INTO owner_oid FROM pg_catalog.pg_roles
            WHERE rolname = current_user
              AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls;
            IF owner_oid IS NULL OR NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_namespace
                WHERE nspname = 'chronicler_mem' AND nspowner = owner_oid
            ) THEN
                RAISE EXCEPTION 'mem_015 requires the normal private-schema migration owner'
                    USING ERRCODE = '55000';
            END IF;

            SELECT oid INTO runtime_oid FROM pg_catalog.pg_roles
            WHERE rolname = 'butler_chronicler_rw'
              AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb
              AND NOT rolreplication AND NOT rolbypassrls;
            IF runtime_oid IS NULL THEN
                RAISE EXCEPTION 'mem_015 requires the existing normal Chronicler runtime role'
                    USING ERRCODE = '55000';
            END IF;

            -- Validate the whole fixed surface before issuing any grant. A
            -- shadow view or foreign-owned same-name table is not a witness.
            FOR relation_name, privileges IN
                SELECT * FROM (VALUES {values}) AS required(name, privileges)
            LOOP
                IF NOT EXISTS (
                    SELECT 1 FROM pg_catalog.pg_class AS c
                    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'chronicler_mem' AND c.relname = relation_name
                      AND c.relkind = 'r' AND c.relpersistence = 'p'
                      AND c.relowner = owner_oid
                ) THEN
                    RAISE EXCEPTION 'mem_015 requires the owned private memory relations'
                        USING ERRCODE = '55000';
                END IF;
            END LOOP;

            GRANT USAGE ON SCHEMA chronicler_mem TO butler_chronicler_rw;
            FOR relation_name, privileges IN
                SELECT * FROM (VALUES {values}) AS required(name, privileges)
            LOOP
                EXECUTE format('GRANT %s ON TABLE %I.%I TO %I',
                    privileges, 'chronicler_mem', relation_name, 'butler_chronicler_rw');
            END LOOP;
        END
        $$;
        """
    )


def downgrade() -> None:
    # A failed DO statement leaves ACLs, rows and the Alembic version unchanged
    # within the existing migration transaction. Non-private chains are no-ops.
    if not _targets_private_schema():
        return
    op.execute(
        """
        DO $$
        BEGIN
            RAISE EXCEPTION 'mem_015 has no lossless automatic private ACL downgrade'
                USING ERRCODE = '55000';
        END
        $$;
        """
    )
