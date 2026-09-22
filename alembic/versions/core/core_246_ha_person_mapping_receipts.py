"""Switchboard-owned HA person mapping receipts and mapping writes.

Revision ID: core_246
Revises: core_245
Create Date: 2026-09-22 00:00:00.000000

The shared relations are replayed by every schema-scoped core chain. Upgrade
therefore converges DDL, policy, and grants idempotently under one lock.
Row security survives ``init-db.sql`` broad regrants. The trusted table owner
used by the dashboard, migrations, and canonical pg_dump keeps PostgreSQL's
owner bypass and therefore sees the complete recoverable dataset. A connection
that assumes any runtime role is policy-fenced to Switchboard/Chronicler only.
"""

from __future__ import annotations

import re

from alembic import op

revision = "core_246"
down_revision = "core_245"
branch_labels = None
depends_on = None

_RECEIPTS = "public.ha_person_mapping_receipts"
_MAPPINGS = "connectors.home_assistant_persons"
_SWITCHBOARD = "butler_switchboard_rw"
_CHRONICLER = "butler_chronicler_rw"
_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_calendar_rw",
    "connector_writer",
    "restore_drill_executor",
)

_PROTECTED_ROLLBACK_ERROR = (
    "core_246 cannot enter non-transactional downgrade work because the protected "
    "core_198 rollback preflight failed"
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


def _core_246_installation_count() -> int:
    """Count schema-local core chains that still depend on the shared DDL."""
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
                    THEN substring(version_num FROM 6)::integer >= 246
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def _downgrade_crosses_core_198() -> bool:
    context = op.get_context()
    environment = context.environment_context
    script = context.script
    if environment is None or script is None:
        return True
    destination = environment.get_revision_argument()
    if isinstance(destination, str):
        relative = re.fullmatch(r"-(\d+)", destination)
        if relative is not None:
            revisions_above = sum(1 for _step in script.iterate_revisions(revision, "core_198"))
            return int(relative.group(1)) > revisions_above
    return any(
        step.revision == "core_198" for step in script.iterate_revisions(revision, destination)
    )


def _protected_rollback_preflight_passes(bind) -> bool:
    script = op.get_context().script
    if script is None:
        return False
    return script.get_revision("core_198").module.protected_rollback_preflight_passes(bind)


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_246:ha-person-mapping', 0))"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RECEIPTS} (
            key_digest                 BYTEA PRIMARY KEY,
            request_digest             BYTEA NOT NULL,
            receipt                    UUID NOT NULL UNIQUE,
            complete                   BOOLEAN NOT NULL,
            received_count             SMALLINT NOT NULL,
            created_count              SMALLINT NOT NULL,
            unchanged_count            SMALLINT NOT NULL,
            conflict_count             SMALLINT NOT NULL,
            invalid_reference_count    SMALLINT NOT NULL,
            outcome                    TEXT NOT NULL,
            failure_category           TEXT,
            created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_ha_person_mapping_receipts_outcome
                CHECK (outcome IN ('success', 'refused')),
            CONSTRAINT ck_ha_person_mapping_receipts_failure
                CHECK (failure_category IS NULL OR failure_category IN (
                    'reference_invalid', 'mapping_conflict'
                )),
            CONSTRAINT ck_ha_person_mapping_receipts_counts CHECK (
                received_count BETWEEN 1 AND 50
                AND created_count BETWEEN 0 AND received_count
                AND unchanged_count BETWEEN 0 AND received_count
                AND conflict_count BETWEEN 0 AND received_count
                AND invalid_reference_count BETWEEN 0 AND received_count
            )
        )
        """
    )

    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_RECEIPTS} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_MAPPINGS} FROM PUBLIC")
    for role in _RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_RECEIPTS} FROM {role}")
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_MAPPINGS} FROM {role}")
    _for_existing_role(
        _SWITCHBOARD,
        f"GRANT SELECT, INSERT ON TABLE {_RECEIPTS}, {_MAPPINGS} TO {_SWITCHBOARD}",
    )
    _for_existing_role(_CHRONICLER, f"GRANT SELECT ON TABLE {_MAPPINGS} TO {_CHRONICLER}")

    # Deliberately do not FORCE: the trusted table owner is also the dashboard,
    # migration, and canonical backup identity. PostgreSQL's owner bypass gives
    # that identity a complete snapshot while SET ROLE changes current_user and
    # subjects every runtime to the policies below.
    op.execute(f"ALTER TABLE {_RECEIPTS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_RECEIPTS} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS ha_person_mapping_receipts_authority ON {_RECEIPTS}")
    op.execute(
        f"""
        CREATE POLICY ha_person_mapping_receipts_authority ON {_RECEIPTS}
            FOR ALL TO PUBLIC
            USING (current_user = '{_SWITCHBOARD}')
            WITH CHECK (current_user = '{_SWITCHBOARD}')
        """
    )

    op.execute(f"ALTER TABLE {_MAPPINGS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_MAPPINGS} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS ha_person_mappings_select ON {_MAPPINGS}")
    op.execute(f"DROP POLICY IF EXISTS ha_person_mappings_insert ON {_MAPPINGS}")
    op.execute(
        f"""
        CREATE POLICY ha_person_mappings_select ON {_MAPPINGS}
            FOR SELECT TO PUBLIC
            USING (current_user IN ('{_SWITCHBOARD}', '{_CHRONICLER}'))
        """
    )
    op.execute(
        f"""
        CREATE POLICY ha_person_mappings_insert ON {_MAPPINGS}
            FOR INSERT TO PUBLIC
            WITH CHECK (current_user = '{_SWITCHBOARD}')
        """
    )


def downgrade() -> None:
    if _downgrade_crosses_core_198() and not _protected_rollback_preflight_passes(op.get_bind()):
        raise RuntimeError(_PROTECTED_ROLLBACK_ERROR)
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_246:ha-person-mapping', 0))"
    )
    if _core_246_installation_count() > 1:
        return
    op.execute(f"DROP POLICY IF EXISTS ha_person_mapping_receipts_authority ON {_RECEIPTS}")
    op.execute(f"DROP POLICY IF EXISTS ha_person_mappings_insert ON {_MAPPINGS}")
    op.execute(f"DROP POLICY IF EXISTS ha_person_mappings_select ON {_MAPPINGS}")
    op.execute(f"ALTER TABLE {_MAPPINGS} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_MAPPINGS} DISABLE ROW LEVEL SECURITY")
    _for_existing_role(_SWITCHBOARD, f"REVOKE INSERT ON TABLE {_MAPPINGS} FROM {_SWITCHBOARD}")
    _for_existing_role(_CHRONICLER, f"GRANT SELECT ON TABLE {_MAPPINGS} TO {_CHRONICLER}")
    op.execute(f"DROP TABLE IF EXISTS {_RECEIPTS}")
