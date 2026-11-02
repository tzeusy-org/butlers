"""Record a trusted receipt when QA publication refuses before retention (bu-mc01y5.28).

Revision ID: core_302
Revises: core_301
Create Date: 2026-11-02 00:00:00.000000

The case API claims ``unpublished`` only with ledger proof that no PR egress landed *and*
proof that the trusted publisher handled the attempt (pre-ledger attempts have neither
ledger rows nor a retained export, so an empty ledger alone proves nothing).  core_294's
retained export is that proof once the publisher reaches retention, but a refusal before
retention (a denied whitelist, an unverifiable export, residual sensitive content, an
unretainable export) leaves no export, so those attempts read ``ambiguous``.

``public.qa_publication_refusals`` holds one receipt per attempt: the publisher's
content-free refusal status and category, written before it returns the refusal.  The
first receipt wins and the row is immutable, so a later run cannot rewrite it.  A receipt
only proves the publisher handled the attempt; the ledger absence proof still decides
whether a PR can exist.

A receipt may exist only for an attempt that never reached retention: the insert policy
refuses one when the attempt already has a ledger operation, a retained export, or a PR
number.  ``category`` is a snake_case code (``^[a-z][a-z0-9_]{0,63}$``), never prose.

The fence follows core_294.  ``scripts/init-db.sql`` re-grants public-schema DML to every
runtime role, so forced row security is the durable fence: only ``butler_qa_rw`` inserts,
every role reads (the row is a status and a fixed category, no proposal content).  There
is no UPDATE or DELETE policy, and a BEFORE UPDATE trigger refuses rewrites from a
privileged login too.  ``deploy/backup/pg_dump.sh`` excludes the table like the ledger.

The core chain runs once per butler schema over one shared table, so only the last schema
to leave core_302 drops it, and only while no receipt exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_302"
down_revision = "core_301"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_refusals"
_QA_ROLE = "butler_qa_rw"
_OTHER_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_calendar_rw",
    "connector_writer",
)
_GUARD = "public.qa_publication_refusals_immutable"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_302:qa-refusals', 0))"

_ROLLBACK_REFUSAL = (
    "core_302 cannot drop public.qa_publication_refusals while a QA publication refusal "
    "receipt exists: the case API would lose its proof that the attempt was never "
    "published. Keep core_302 installed."
)


def _for_existing_role(role: str, statement: str) -> None:
    escaped = statement.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{escaped}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$
        """
    )


def _core_302_installation_count() -> int:
    """Count schema-local core chains that still depend on the shared table (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 302
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            attempt_id  UUID PRIMARY KEY
                REFERENCES public.healing_attempts(id) ON DELETE RESTRICT,
            status      TEXT NOT NULL,
            category    TEXT NOT NULL,
            refused_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_qa_publication_refusals_status
                CHECK (status IN ('refused', 'anonymization_failed')),
            CONSTRAINT ck_qa_publication_refusals_category
                CHECK (category ~ '^[a-z][a-z0-9_]{{0,63}}$')
        )
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {_GUARD}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'QA publication refusal receipt is immutable'
                USING ERRCODE = 'check_violation';
        END
        $$
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS qa_publication_refusals_immutable ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER qa_publication_refusals_immutable
            BEFORE UPDATE ON {_TABLE}
            FOR EACH ROW EXECUTE FUNCTION {_GUARD}()
        """
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    for role in _OTHER_RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {role}")
        _for_existing_role(role, f"GRANT SELECT ON TABLE {_TABLE} TO {role}")
    _for_existing_role(_QA_ROLE, f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {_QA_ROLE}")
    _for_existing_role(_QA_ROLE, f"GRANT SELECT, INSERT ON TABLE {_TABLE} TO {_QA_ROLE}")

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    for policy in ("read", "insert"):
        op.execute(f"DROP POLICY IF EXISTS qa_publication_refusals_{policy} ON {_TABLE}")
    op.execute(f"CREATE POLICY qa_publication_refusals_read ON {_TABLE} FOR SELECT USING (true)")
    op.execute(
        f"""
        CREATE POLICY qa_publication_refusals_insert ON {_TABLE}
            FOR INSERT WITH CHECK (
                current_user = '{_QA_ROLE}'
                AND NOT EXISTS (
                    SELECT 1 FROM public.qa_publication_operations AS op
                    WHERE op.attempt_id = qa_publication_refusals.attempt_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM public.qa_publication_exports AS x
                    WHERE x.attempt_id = qa_publication_refusals.attempt_id
                )
                AND NOT EXISTS (
                    SELECT 1 FROM public.healing_attempts AS a
                    WHERE a.id = qa_publication_refusals.attempt_id
                      AND a.pr_number IS NOT NULL
                )
            )
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    # Earlier schemas still run head code against the shared table; only the last drops it.
    if _core_302_installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NOT NULL THEN
                IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                    RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}()")
